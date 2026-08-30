"""Legacy: 과거 7개/둘레 기준 신체치수 회귀 모델 비교 CLI.

처음 기준은 `chest`, `waist`, `hip`, `thigh`, `calf`, `arm`, `shoulder`였고,
`thigh/calf/arm`은 둘레로 해석했다. 현재 API/DB/ML 기준은 11개 항목이며
`thigh_length`, `calf_length`, `torso_length`, `leg_length`, `neck_length`, 두 비율을 사용한다.
이 파일은 현재 Swagger/API 계약에 사용하지 않는 참고용 archive다.

실제 평가는 SizeKorea 정제 CSV를 전달해 실행한다. ``--demo``는 실행 배선 검증용
합성 데이터이며 모델 성능이나 실제 신체치수로 해석하면 안 된다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
import time
from datetime import UTC, datetime
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import boto3
import joblib
import numpy as np
import pandas as pd
from sklearn.base import RegressorMixin
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.multioutput import MultiOutputRegressor
from sklearn.neighbors import KNeighborsRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

FEATURES = ["gender", "height", "weight"]
TARGETS = ["chest", "waist", "hip", "thigh", "calf", "arm", "shoulder"]
ROW_ID = "source_row_id"
CLASSIC_MODELS = ("baseline", "random_forest", "hist_gradient_boosting", "knn")
HF_MODELS = ("tabpfn_v2", "nori", "tabpfn_mix")
ALL_MODELS = (*CLASSIC_MODELS, *HF_MODELS)
RANDOM_STATE = 42
DEFAULT_TEST_ROWS = 1000
DEFAULT_S3_URI = (
    "s3://skn28-cozy/22.사이즈코리아/"
    "8차 인체치수조사(2020~24)_치수데이터(공개용).xlsx"
)
DEFAULT_SHEET = "(1~2차년도) 직접측정"
SOURCE_COLUMNS = {
    "gender": "성별",
    "height": "키",
    "weight": "체중(몸무게)",
    "chest": "가슴둘레",
    "waist": "허리둘레",
    "hip": "엉덩이둘레",
    "thigh": "넙다리둘레",
    "calf": "장딴지둘레",
    "arm": "(편)위팔둘레",
    "shoulder": "어깨너비",
}
MM_COLUMNS = ["height", *TARGETS]
GENDER_CODES = {"M": 0.0, "F": 1.0}
GENDER_VALUES = {
    "M": "M",
    "F": "F",
    "MALE": "M",
    "FEMALE": "F",
    "남": "M",
    "여": "F",
    "남성": "M",
    "여성": "F",
}


class Predictor(Protocol):
    """여러 모델을 같은 방식으로 다루기 위한 최소 규칙이다.

    이 파일의 benchmark 함수는 모델 종류가 무엇인지 몰라도 fit과 predict만
    있으면 동일하게 학습하고 평가할 수 있다.
    """

    def fit(self, x: pd.DataFrame, y: pd.DataFrame) -> Any: ...

    def predict(self, x: pd.DataFrame) -> np.ndarray: ...


@dataclass
class Metric:
    """모델 하나가 target 하나를 예측했을 때의 성능 기록이다.

    예를 들어 random_forest가 chest를 예측한 MAE, RMSE, R², 속도를 한 줄로 저장한다.
    """

    model: str
    target: str
    mae: float
    rmse: float
    r2: float
    p90_absolute_error: float
    fit_seconds: float
    predict_ms_per_row: float


@dataclass
class DatasetInfo:
    """이번 실험에 사용한 데이터 출처와 정제 결과를 기록한다.

    나중에 같은 결과를 재현하려면 어떤 파일, 어떤 시트, 몇 행을 썼는지가 필요하다.
    """

    source: str
    local_path: str
    sha256: str
    sheet: str
    raw_rows: int
    cleaned_rows: int
    columns: dict[str, str]


def _classic_model(name: str) -> RegressorMixin:
    """기본 scikit-learn 모델 이름을 실제 모델 객체로 바꾼다.

    사용자가 --models random_forest처럼 문자열로 입력하면 여기서
    RandomForestRegressor 같은 실제 학습 모델을 만들어 반환한다.
    """

    if name == "baseline":
        return DummyRegressor(strategy="mean")
    if name == "random_forest":
        return RandomForestRegressor(
            n_estimators=300,
            min_samples_leaf=3,
            n_jobs=-1,
            random_state=RANDOM_STATE,
        )
    if name == "hist_gradient_boosting":
        return MultiOutputRegressor(
            HistGradientBoostingRegressor(
                learning_rate=0.05,
                max_iter=300,
                min_samples_leaf=20,
                l2_regularization=1.0,
                random_state=RANDOM_STATE,
            )
        )
    if name == "knn":
        return make_pipeline(
            StandardScaler(),
            KNeighborsRegressor(n_neighbors=25, weights="distance", p=2),
        )
    raise ValueError(f"지원하지 않는 기본 모델입니다: {name}")


class PerTargetFoundationRegressor:
    """단일 target 전용 foundation regressor를 7개 target에 반복 적용한다.

    TabPFN과 Nori는 한 번에 chest, waist, hip 전체를 동시에 예측하는
    multi-output 모델처럼 쓰기 어렵다. 그래서 target별 모델을 7개 만들어서
    각각 하나의 신체 치수만 예측하게 한다.
    """

    def __init__(self, name: str) -> None:
        """사용할 foundation 모델 이름과 target별 모델 저장 공간을 준비한다."""

        self.name = name
        self.models: dict[str, Any] = {}

    def _new_model(self) -> Any:
        """target 하나를 학습할 새 Hugging Face 계열 모델을 만든다.

        패키지가 설치되어 있지 않으면 어떤 pip install이 필요한지 알려준다.
        """

        if self.name == "tabpfn_v2":
            try:
                from tabpfn import TabPFNRegressor
            except ImportError as exc:
                raise RuntimeError(
                    "TabPFN 실행에는 `pip install tabpfn`이 필요합니다."
                ) from exc
            try:
                from huggingface_hub import hf_hub_download
            except ImportError as exc:
                raise RuntimeError(
                    "TabPFN-v2 체크포인트 다운로드에는 "
                    "`pip install huggingface-hub`가 필요합니다."
                ) from exc
            checkpoint = hf_hub_download(
                repo_id="Prior-Labs/TabPFN-v2-reg",
                filename="tabpfn-v2-regressor-v2_default.ckpt",
            )
            return TabPFNRegressor(
                model_path=checkpoint,
                random_state=RANDOM_STATE,
            )

        if self.name == "nori":
            try:
                from synthefy_nori import NoriRegressor
            except ImportError as exc:
                raise RuntimeError(
                    "Nori 실행에는 `pip install synthefy-nori`가 필요합니다."
                ) from exc
            return NoriRegressor()

        raise ValueError(f"지원하지 않는 foundation 모델입니다: {self.name}")

    def fit(self, x: pd.DataFrame, y: pd.DataFrame) -> "PerTargetFoundationRegressor":
        """7개 target마다 모델을 하나씩 만들어 학습한다.

        chest 모델은 chest만, waist 모델은 waist만 맞히도록 학습한다.
        """

        for target in TARGETS:
            model = self._new_model()
            model.fit(x, y[target])
            self.models[target] = model
        return self

    def predict(self, x: pd.DataFrame) -> np.ndarray:
        """target별 예측 결과 7개를 다시 하나의 2차원 배열로 합친다.

        최종 shape은 (예측할 사람 수, 7개 신체 치수)가 된다.
        """

        return np.column_stack(
            [np.asarray(self.models[target].predict(x)) for target in TARGETS]
        )


class TabPFNMixRegressor:
    """AutoGluon TabPFNMix를 target별로 학습하는 adapter.

    AutoGluon은 label 컬럼 하나를 기준으로 학습하는 구조라서, 여기서도
    chest, waist 같은 target마다 predictor를 따로 만든다.
    """

    def __init__(self) -> None:
        """AutoGluon predictor들을 저장할 공간과 임시 학습 폴더를 만든다."""

        self.predictors: dict[str, Any] = {}
        self._temp_dir = Path(tempfile.mkdtemp(prefix="body-tabpfnmix-"))

    def fit(self, x: pd.DataFrame, y: pd.DataFrame) -> "TabPFNMixRegressor":
        """AutoGluon TabPFNMix를 target별로 학습한다.

        AutoGluon은 train_data 안에 입력 컬럼과 정답 컬럼이 함께 있어야 하므로
        매 target마다 gender, height, weight, 정답 target 형태의 DataFrame을 만든다.
        """

        try:
            from autogluon.tabular import TabularPredictor
        except ImportError as exc:
            raise RuntimeError(
                "TabPFNMix 실행에는 `pip install "
                "'autogluon.tabular[tabpfnmix]'`가 필요합니다."
            ) from exc

        hyperparameters = {
            "TABPFNMIX": [{
                "model_path_classifier": "autogluon/tabpfn-mix-1.0-classifier",
                "model_path_regressor": "autogluon/tabpfn-mix-1.0-regressor",
                "n_ensembles": 1,
                "max_epochs": 30,
            }]
        }
        import torch

        use_gpu = torch.cuda.is_available()
        for target in TARGETS:
            train_data = x.copy()
            train_data[target] = y[target].to_numpy()
            predictor = TabularPredictor(
                label=target,
                problem_type="regression",
                path=str(self._temp_dir / target),
                verbosity=0,
            )
            predictor.fit(
                train_data=train_data,
                hyperparameters=hyperparameters,
                ag_args_fit={"num_gpus": 1} if use_gpu else None,
            )
            self.predictors[target] = predictor
        return self

    def predict(self, x: pd.DataFrame) -> np.ndarray:
        """target별 AutoGluon predictor의 예측값을 하나의 배열로 합친다."""

        return np.column_stack(
            [
                np.asarray(self.predictors[target].predict(x))
                for target in TARGETS
            ]
        )


def build_model(name: str) -> Predictor:
    """모델 이름 하나를 실제 학습 가능한 모델 객체로 변환한다.

    기본 모델, Hugging Face foundation 모델, AutoGluon 모델을 여기서 한 번에
    분기 처리해서 benchmark 함수는 모델 종류를 신경 쓰지 않아도 된다.
    """

    if name in CLASSIC_MODELS:
        return _classic_model(name)
    if name in ("tabpfn_v2", "nori"):
        return PerTargetFoundationRegressor(name)
    if name == "tabpfn_mix":
        return TabPFNMixRegressor()
    raise ValueError(f"지원하지 않는 모델입니다: {name}")


def validate_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """학습에 쓸 DataFrame이 필요한 컬럼과 정상 범위를 만족하는지 확인한다.

    숫자로 바꿀 수 없는 값과 결측값은 제거하고, 키/몸무게가 비현실적인 행도
    제외한다. 너무 적은 데이터로 학습하면 결과가 의미 없으므로 최소 50행을 요구한다.
    """

    required = [*FEATURES, *TARGETS]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"CSV 필수 컬럼이 없습니다: {', '.join(missing)}")

    cleaned = frame[required].copy()
    if ROW_ID in frame.columns:
        cleaned.insert(0, ROW_ID, frame[ROW_ID])
    else:
        cleaned.insert(0, ROW_ID, frame.index)
    cleaned[ROW_ID] = pd.to_numeric(cleaned[ROW_ID], errors="coerce")
    cleaned["gender"] = (
        cleaned["gender"]
        .astype("string")
        .str.strip()
        .str.upper()
        .map(GENDER_VALUES)
    )
    numeric_columns = ["height", "weight", *TARGETS]
    cleaned[numeric_columns] = cleaned[numeric_columns].apply(
        pd.to_numeric,
        errors="coerce",
    )
    cleaned = cleaned.dropna()
    cleaned[ROW_ID] = cleaned[ROW_ID].astype(int)
    cleaned = cleaned[
        cleaned["height"].between(100, 230)
        & cleaned["weight"].between(25, 300)
    ]
    for target in TARGETS:
        cleaned = cleaned[cleaned[target].between(1, 999.9)]
    if len(cleaned) < 50:
        raise ValueError(
            f"정제 후 행이 {len(cleaned)}개입니다. 최소 50개 이상의 실측 행이 필요합니다."
        )
    return cleaned.reset_index(drop=True)


def load_test_frame(path: Path) -> pd.DataFrame:
    """기본 모델에서 저장한 test_set.csv를 다시 평가용 DataFrame으로 읽는다."""

    raw = pd.read_csv(path)
    rename = {
        f"actual_{target}": target
        for target in TARGETS
        if f"actual_{target}" in raw.columns
    }
    return raw.rename(columns=rename)


def make_model_features(frame: pd.DataFrame) -> pd.DataFrame:
    """사람이 읽는 gender 값을 모델이 학습 가능한 숫자 feature로 바꾼다."""

    features = frame[FEATURES].copy()
    features["gender"] = features["gender"].map(GENDER_CODES)
    if features["gender"].isna().any():
        raise ValueError("gender는 M 또는 F로 정제되어 있어야 합니다.")
    return features


def validate_frame_for_sample(frame: pd.DataFrame) -> pd.DataFrame:
    """단일 예측 입력의 gender, height, weight를 검증한다."""

    missing = [column for column in FEATURES if column not in frame.columns]
    if missing:
        raise ValueError(f"입력 필수 컬럼이 없습니다: {', '.join(missing)}")

    cleaned = frame[FEATURES].copy()
    cleaned["gender"] = (
        cleaned["gender"]
        .astype("string")
        .str.strip()
        .str.upper()
        .map(GENDER_VALUES)
    )
    cleaned[["height", "weight"]] = cleaned[["height", "weight"]].apply(
        pd.to_numeric,
        errors="coerce",
    )
    cleaned = cleaned.dropna()
    cleaned = cleaned[
        cleaned["height"].between(100, 230)
        & cleaned["weight"].between(25, 300)
    ]
    if len(cleaned) != len(frame):
        raise ValueError(
            "gender(M/F), height(100~230), weight(25~300)를 확인하세요."
        )
    return cleaned.reset_index(drop=True)


def _sha256(path: Path) -> str:
    """파일 내용의 SHA-256 해시를 계산한다.

    같은 파일로 실험했는지 확인하기 위한 지문 같은 값이다.
    """

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_s3_file(s3_uri: str, cache_dir: Path) -> Path:
    """S3 파일을 로컬 캐시 폴더로 다운로드하고 로컬 경로를 반환한다.

    이미 같은 ETag의 파일이 캐시에 있으면 다시 다운로드하지 않는다.
    """

    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
        raise ValueError(f"올바른 S3 URI가 아닙니다: {s3_uri}")

    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    suffix = Path(key).suffix or ".xlsx"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.sha256(s3_uri.encode("utf-8")).hexdigest()[:16]
    destination = cache_dir / f"{cache_key}{suffix}"

    client = boto3.client("s3")
    remote = client.head_object(Bucket=bucket, Key=key)
    etag_path = destination.with_suffix(f"{destination.suffix}.etag")
    remote_etag = str(remote["ETag"]).strip('"')
    cached_etag = etag_path.read_text(encoding="utf-8") if etag_path.exists() else ""
    if destination.exists() and cached_etag == remote_etag:
        return destination

    partial = destination.with_suffix(f"{destination.suffix}.part")
    client.download_file(bucket, key, str(partial))
    partial.replace(destination)
    etag_path.write_text(remote_etag, encoding="utf-8")
    return destination


def load_sizekorea_excel(
    path: Path,
    *,
    sheet: str = DEFAULT_SHEET,
    source: str | None = None,
) -> tuple[pd.DataFrame, DatasetInfo]:
    """8차 직접측정 시트에서 과거 서비스 필드를 추출하고 mm를 cm로 변환한다.

    Excel 컬럼 번호는 바뀔 수 있으므로 번호가 아니라 한글 측정항목명으로
    컬럼 위치를 찾는다. 정제된 데이터와 실험 기록용 DatasetInfo를 함께 반환한다.
    """

    header_names = pd.read_excel(
        path,
        sheet_name=sheet,
        header=None,
        skiprows=4,
        nrows=1,
        engine="openpyxl",
    ).iloc[0]
    source_indexes: dict[str, int] = {}
    for service_name, source_name in SOURCE_COLUMNS.items():
        matches = [
            int(index)
            for index, value in header_names.items()
            if isinstance(value, str) and value.strip() == source_name
        ]
        if len(matches) != 1:
            raise ValueError(
                f"{sheet}에서 `{source_name}` 컬럼을 1개 찾을 수 없습니다: {matches}"
            )
        source_indexes[service_name] = matches[0]

    raw = pd.read_excel(
        path,
        sheet_name=sheet,
        header=6,
        usecols=sorted(source_indexes.values()),
        engine="openpyxl",
    )
    rename = {
        raw.columns[list(sorted(source_indexes.values())).index(index)]: service_name
        for service_name, index in source_indexes.items()
    }
    frame = raw.rename(columns=rename)[[*FEATURES, *TARGETS]].copy()
    numeric_columns = ["height", "weight", *TARGETS]
    frame[numeric_columns] = frame[numeric_columns].apply(
        pd.to_numeric,
        errors="coerce",
    )
    frame[MM_COLUMNS] = frame[MM_COLUMNS] / 10.0
    cleaned = validate_frame(frame)
    info = DatasetInfo(
        source=source or str(path),
        local_path=str(path.resolve()),
        sha256=_sha256(path),
        sheet=sheet,
        raw_rows=len(frame),
        cleaned_rows=len(cleaned),
        columns=SOURCE_COLUMNS.copy(),
    )
    return cleaned, info


def make_demo_data(rows: int = 600) -> pd.DataFrame:
    """배선 확인용 합성 데이터를 만든다.

    실제 SizeKorea 데이터가 없어도 CLI, 모델 학습, 지표 저장 흐름이 깨지지
    않는지 확인할 때만 쓴다. 실제 성능으로 해석하면 안 된다.
    """

    rng = np.random.default_rng(RANDOM_STATE)
    gender = rng.choice(["M", "F"], rows)
    gender_factor = np.where(gender == "F", 1.0, 0.0)
    height = np.clip(rng.normal(168, 9, rows), 145, 195)
    weight = np.clip(
        22 * (height / 100) ** 2 + rng.normal(0, 8, rows),
        40,
        120,
    )
    bmi = weight / (height / 100) ** 2
    noise = lambda scale: rng.normal(0, scale, rows)
    return pd.DataFrame(
        {
            "gender": gender,
            "height": height,
            "weight": weight,
            "chest": (
                45 + 0.28 * weight + 0.11 * height
                - 2.0 * gender_factor + noise(2.5)
            ),
            "waist": (
                18 + 1.8 * bmi + 0.12 * weight
                - 3.0 * gender_factor + noise(3.0)
            ),
            "hip": (
                50 + 0.25 * weight + 0.14 * height
                + 2.0 * gender_factor + noise(2.5)
            ),
            "thigh": (
                25 + 0.27 * weight + 0.05 * height
                + 1.0 * gender_factor + noise(2.0)
            ),
            "calf": (
                18 + 0.10 * weight + 0.06 * height
                - 0.5 * gender_factor + noise(1.3)
            ),
            "arm": (
                8 + 0.16 * weight + 0.05 * height
                - 1.0 * gender_factor + noise(1.2)
            ),
            "shoulder": (
                14 + 0.11 * weight + 0.13 * height
                - 2.0 * gender_factor + noise(1.5)
            ),
        }
    )


def benchmark(
    frame: pd.DataFrame,
    model_names: list[str],
    artifact_dir: Path,
    *,
    test_frame: pd.DataFrame | None = None,
    sample: tuple[str, float, float] | None = None,
    dataset_info: DatasetInfo | None = None,
) -> tuple[list[Metric], dict[str, dict[str, float]]]:
    """선택한 모델들을 같은 데이터 분할과 같은 지표로 비교한다.

    1. 데이터를 검증한다.
    2. train/test로 나눈다.
    3. 모델별로 학습하고 예측한다.
    4. target별 MAE, RMSE, P90 오차, 속도를 저장한다.
    5. 선택한 키/몸무게 샘플 예측도 함께 저장한다.
    """

    data = validate_frame(frame)
    if test_frame is None:
        if len(data) <= DEFAULT_TEST_ROWS:
            raise ValueError(
                f"테스트셋 {DEFAULT_TEST_ROWS}개를 만들려면 "
                f"정제 데이터가 최소 {DEFAULT_TEST_ROWS + 1}행 필요합니다. "
                f"현재 데이터: {len(data)}행"
            )
        train, test = train_test_split(
            data,
            test_size=DEFAULT_TEST_ROWS,
            random_state=RANDOM_STATE,
        )
    else:
        test = validate_frame(test_frame)
        unknown_ids = sorted(set(test[ROW_ID]) - set(data[ROW_ID]))
        if unknown_ids:
            preview = ", ".join(str(value) for value in unknown_ids[:5])
            raise ValueError(
                f"test data에 원본 데이터에 없는 {ROW_ID}가 있습니다: {preview}"
            )
        train = data[~data[ROW_ID].isin(test[ROW_ID])].copy()
        if len(train) < 50:
            raise ValueError(
                f"test data를 제외한 학습 데이터가 {len(train)}개입니다. "
                "최소 50개 이상 필요합니다."
            )
    csv_dir = artifact_dir / "csv"
    models_dir = artifact_dir / "models"
    metrics_dir = artifact_dir / "metrics"
    for directory in (csv_dir, models_dir, metrics_dir):
        directory.mkdir(parents=True, exist_ok=True)
    x_train, y_train = make_model_features(train), train[TARGETS]
    x_test, y_test = make_model_features(test), test[TARGETS]
    test_export = test[[ROW_ID, *FEATURES]].copy()
    for target in TARGETS:
        test_export[f"actual_{target}"] = test[target].to_numpy()
    test_export.to_csv(
        csv_dir / "test_set.csv",
        index=False,
        encoding="utf-8-sig",
    )
    metrics: list[Metric] = []
    sample_predictions: dict[str, dict[str, float]] = {}

    for name in model_names:
        model = build_model(name)
        fit_started = time.perf_counter()
        model.fit(x_train, y_train)
        fit_seconds = time.perf_counter() - fit_started

        predict_started = time.perf_counter()
        predictions = np.asarray(model.predict(x_test), dtype=float)
        predict_seconds = time.perf_counter() - predict_started
        if predictions.shape != y_test.shape:
            raise ValueError(
                f"{name} 예측 shape={predictions.shape}, 예상={y_test.shape}"
            )
        prediction_export = test_export.copy()
        prediction_export["fit_seconds"] = fit_seconds
        prediction_export["predict_ms_per_row"] = (
            predict_seconds / len(x_test) * 1_000
        )

        for index, target in enumerate(TARGETS):
            actual = y_test[target].to_numpy()
            predicted = predictions[:, index]
            absolute_errors = np.abs(actual - predicted)
            prediction_export[f"predicted_{target}"] = predicted
            prediction_export[f"error_{target}"] = predicted - actual
            metrics.append(
                Metric(
                    model=name,
                    target=target,
                    mae=float(mean_absolute_error(actual, predicted)),
                    rmse=float(
                        math.sqrt(mean_squared_error(actual, predicted))
                    ),
                    r2=float(r2_score(actual, predicted)),
                    p90_absolute_error=float(
                        np.quantile(absolute_errors, 0.9)
                    ),
                    fit_seconds=fit_seconds,
                    predict_ms_per_row=(
                        predict_seconds / len(x_test) * 1_000
                    ),
                )
            )
        prediction_columns = [
            ROW_ID,
            *FEATURES,
            "fit_seconds",
            "predict_ms_per_row",
            *[
                column
                for target in TARGETS
                for column in (
                    f"actual_{target}",
                    f"predicted_{target}",
                    f"error_{target}",
                )
            ],
        ]
        prediction_export = prediction_export[prediction_columns]
        prediction_export.to_csv(
            csv_dir / f"test_predictions_{name}.csv",
            index=False,
            encoding="utf-8-sig",
        )

        if sample is not None:
            gender, height, weight = sample
            sample_frame = pd.DataFrame(
                [{"gender": gender, "height": height, "weight": weight}]
            )
            sample_values = np.asarray(
                model.predict(
                    make_model_features(validate_frame_for_sample(sample_frame))
                ),
                dtype=float,
            )[0]
            sample_predictions[name] = {
                target: round(float(value), 1)
                for target, value in zip(TARGETS, sample_values, strict=True)
            }

        if name in CLASSIC_MODELS:
            joblib.dump(model, models_dir / f"{name}.joblib")

    result = [asdict(metric) for metric in metrics]
    (metrics_dir / "metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (metrics_dir / "sample_predictions.json").write_text(
        json.dumps(sample_predictions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "random_state": RANDOM_STATE,
        "features": FEATURES,
        "targets": TARGETS,
        "models": model_names,
        "train_rows": len(train),
        "test_rows": len(test),
        "test_source": "external_test_data" if test_frame is not None else "split",
        "test_set_path": str(csv_dir / "test_set.csv"),
        "test_prediction_file_pattern": "test_predictions_{model}.csv",
        "dataset": asdict(dataset_info) if dataset_info else None,
        "sample": (
            {"gender": sample[0], "height": sample[1], "weight": sample[2]}
            if sample is not None
            else None
        ),
        "versions": {
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
    }
    (metrics_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return metrics, sample_predictions


def predict_saved(
    artifact_dir: Path,
    gender: str,
    height: float,
    weight: float,
) -> dict[str, dict[str, float]]:
    """저장된 기본 모델(joblib)을 불러와 성별, 키, 몸무게로 신체 치수를 예측한다.

    Hugging Face 모델은 라이브러리마다 저장 방식이 달라 여기서는 기본
    scikit-learn 모델 3개만 재사용 대상으로 본다.
    """

    x = make_model_features(
        validate_frame_for_sample(
            pd.DataFrame(
                [{"gender": gender, "height": height, "weight": weight}]
            )
        )
    )
    result: dict[str, dict[str, float]] = {}
    for name in CLASSIC_MODELS:
        path = artifact_dir / "models" / f"{name}.joblib"
        if not path.exists():
            continue
        model = joblib.load(path)
        values = np.asarray(model.predict(x), dtype=float)[0]
        result[name] = {
            target: round(float(value), 1)
            for target, value in zip(TARGETS, values, strict=True)
        }
    if not result:
        raise FileNotFoundError(
            "저장된 모델이 없습니다. 먼저 benchmark 명령을 실행하세요."
        )
    return result


def parse_args() -> argparse.Namespace:
    """PowerShell에서 받은 명령어 옵션을 Python 객체로 바꾼다.

    예를 들어 `benchmark --data ... --models knn` 같은 입력을 해석해서
    main 함수가 사용할 수 있는 args로 만든다.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="모델을 학습하고 동일 test split에서 비교합니다.",
    )
    source = benchmark_parser.add_mutually_exclusive_group()
    source.add_argument("--data", type=Path, help="SizeKorea 정제 CSV")
    source.add_argument(
        "--excel",
        type=Path,
        help="로컬 SizeKorea 8차 Excel",
    )
    source.add_argument(
        "--s3-uri",
        default=None,
        help=f"SizeKorea Excel S3 URI (기본값: {DEFAULT_S3_URI})",
    )
    source.add_argument(
        "--demo",
        action="store_true",
        help="배선 확인용 합성 데이터 사용",
    )
    benchmark_parser.add_argument(
        "--models",
        nargs="+",
        choices=ALL_MODELS,
        default=list(CLASSIC_MODELS),
    )
    benchmark_parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "artifacts",
    )
    benchmark_parser.add_argument(
        "--test-data",
        type=Path,
        default=None,
        help=(
            "이미 생성된 test_set.csv. 지정하면 랜덤 분할 대신 이 1000개 "
            "테스트셋을 그대로 사용합니다."
        ),
    )
    benchmark_parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path.home() / ".cache" / "skn28-body-measurement",
        help="S3 원본 캐시 경로",
    )
    benchmark_parser.add_argument(
        "--gender",
        choices=["M", "F"],
        default="M",
        help="학습 직후 비교 예측할 성별(M/F)",
    )
    benchmark_parser.add_argument(
        "--height",
        type=float,
        default=170.0,
        help="학습 직후 비교 예측할 키(cm)",
    )
    benchmark_parser.add_argument(
        "--weight",
        type=float,
        default=65.0,
        help="학습 직후 비교 예측할 몸무게(kg)",
    )

    predict_parser = subparsers.add_parser(
        "predict",
        help="저장된 기본 모델로 성별·키·몸무게를 예측합니다.",
    )
    predict_parser.add_argument("--gender", choices=["M", "F"], required=True)
    predict_parser.add_argument("--height", type=float, required=True)
    predict_parser.add_argument("--weight", type=float, required=True)
    predict_parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "artifacts",
    )
    return parser.parse_args()


def main() -> None:
    """CLI의 시작점이다.

    command가 benchmark면 모델 비교를 실행하고, predict면 저장된 모델로
    단일 사용자 예측을 실행한다.
    """

    args = parse_args()
    if args.command == "benchmark":
        dataset_info = None
        if args.demo:
            frame = make_demo_data()
        elif args.data:
            frame = pd.read_csv(args.data)
            dataset_info = DatasetInfo(
                source=str(args.data),
                local_path=str(args.data.resolve()),
                sha256=_sha256(args.data),
                sheet="cleaned_csv",
                raw_rows=len(frame),
                cleaned_rows=len(validate_frame(frame)),
                columns={column: column for column in [*FEATURES, *TARGETS]},
            )
        else:
            source_uri = args.s3_uri or DEFAULT_S3_URI
            excel_path = (
                args.excel
                if args.excel
                else download_s3_file(source_uri, args.cache_dir)
            )
            frame, dataset_info = load_sizekorea_excel(
                excel_path,
                source=source_uri if not args.excel else str(args.excel),
            )
        metrics, sample_predictions = benchmark(
            frame,
            args.models,
            args.artifact_dir,
            test_frame=(
                load_test_frame(args.test_data)
                if args.test_data is not None
                else None
            ),
            sample=(args.gender, args.height, args.weight),
            dataset_info=dataset_info,
        )
        report = pd.DataFrame(asdict(metric) for metric in metrics)
        summary = (
            report.groupby("model", as_index=False)
            .agg(
                mean_mae=("mae", "mean"),
                mean_rmse=("rmse", "mean"),
                mean_r2=("r2", "mean"),
                mean_p90_error=("p90_absolute_error", "mean"),
                fit_seconds=("fit_seconds", "max"),
                predict_ms_per_row=("predict_ms_per_row", "max"),
            )
            .sort_values("mean_mae")
        )
        if args.demo:
            print("주의: 아래 결과는 합성 demo 데이터의 배선 확인 결과입니다.")
        print(summary.to_string(index=False))
        print("\n입력값 비교 예측")
        print(
            json.dumps(
                {
                    "input": {
                        "gender": args.gender,
                        "height": args.height,
                        "weight": args.weight,
                    },
                    "predictions": sample_predictions,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print(f"\n상세 지표: {args.artifact_dir / 'metrics' / 'metrics.json'}")
        return

    predictions = predict_saved(
        args.artifact_dir,
        gender=args.gender,
        height=args.height,
        weight=args.weight,
    )
    print(json.dumps(predictions, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
