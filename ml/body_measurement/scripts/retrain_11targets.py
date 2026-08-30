"""8차 Size Korea 원본에서 11개 신체치수 모델을 재생성한다.

입력은 성별·키·몸무게 3개이며, 정답은 다음 11개다.
어깨너비, 가슴둘레, 허리둘레, 엉덩이둘레, 허벅지길이, 종아리길이,
상체길이, 하체길이, 목길이, 허벅지-종아리 비율, 상체-하체 비율.

이 스크립트는 기존 legacy ``processed``/``experiments`` 산출물을 읽지 않는다.
원본 workbook에서 raw CSV와 전처리 CSV를 새로 만들고, 같은 seed로 train/validation/test
를 나눈 뒤 행별 예측과 집계 지표를 ``data/hist``에 저장한다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.multioutput import MultiOutputRegressor


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW_XLSX = DATA / "raw" / "sizekorea_8th.xlsx"
RAW_CSV = DATA / "raw" / "sizekorea_8th_3d_source.csv"
PREPROCESSED = DATA / "preprocessed"
HIST = DATA / "hist"
SEED = 42

FEATURES = ["gender", "height", "weight"]
TARGETS = [
    "shoulder",
    "chest",
    "waist",
    "hip",
    "thigh_length",
    "calf_length",
    "torso_length",
    "leg_length",
    "neck_length",
    "thigh_calf_ratio",
    "torso_leg_ratio",
]
IMAGE_RESULT_COLUMNS = [
    "source_id",
    "subject_id",
    *FEATURES,
    *[f"actual_{target}" for target in TARGETS],
    *[f"predicted_{target}" for target in TARGETS],
    *[f"error_{target}" for target in TARGETS],
    "front_image_path",
    "side_image_path",
]

# 3D 측정 시트의 헤더는 7행에 있다. 키/둘레는 mm, 몸무게는 kg다.
SOURCE_COLUMNS = {
    "subject_id": "인체 데이터 ID",
    "gender": "성별",
    "height_mm": "432. 키",
    "weight_kg": "503. 몸무게",
    "shoulder_mm": "298. 어깨사이너비",
    "chest_mm": "460. 젖가슴둘레",
    "waist_mm": "463. 허리둘레",
    "hip_mm": "465. 엉덩이둘레",
    "jaw_height_mm": "140. 턱끝높이",
    "front_neck_height_mm": "143. 목앞높이",
    "crotch_height_mm": "156. 샅높이",
    "knee_height_mm": "158. 무릎뼈가운데높이",
    "ankle_height_mm": "161. 가쪽복사높이",
    "torso_shoulder_height_mm": "434. 어깨높이",
    "pelvis_point_height_mm": "438. 위앞엉덩뼈가시높이",
    "leg_crotch_height_mm": "439. 샅높이",
    "leg_ankle_height_mm": "440. 가쪽복사높이",
}


def _find_header(headers: list[str], value: str) -> int:
    matches = [i for i, header in enumerate(headers) if header == value]
    if len(matches) != 1:
        raise ValueError(f"원본 시트에서 `{value}` 컬럼을 찾지 못했습니다: {matches}")
    return matches[0]


def load_source() -> pd.DataFrame:
    if not RAW_XLSX.exists():
        raise FileNotFoundError(f"복구된 8차 원본이 없습니다: {RAW_XLSX}")
    raw = pd.read_excel(
        RAW_XLSX,
        sheet_name="(1~2차년도) 3D 측정",
        header=6,
        engine="openpyxl",
    )
    headers = [str(c).strip() for c in raw.columns]
    indexes = {name: _find_header(headers, source) for name, source in SOURCE_COLUMNS.items()}
    selected = raw.iloc[:, list(indexes.values())].copy()
    selected.columns = list(indexes)
    return selected


def preprocess(source: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()
    out["source_row_id"] = np.arange(len(source), dtype=int)
    out["subject_id"] = source["subject_id"].astype("string").str.strip()
    out["gender"] = (
        source["gender"]
        .astype("string")
        .str.strip()
        .str.upper()
        .replace({"남": "M", "남성": "M", "여": "F", "여성": "F"})
    )
    out["height"] = pd.to_numeric(source["height_mm"], errors="coerce") / 10
    out["weight"] = pd.to_numeric(source["weight_kg"], errors="coerce")
    out["shoulder"] = pd.to_numeric(source["shoulder_mm"], errors="coerce") / 10
    out["chest"] = pd.to_numeric(source["chest_mm"], errors="coerce") / 10
    out["waist"] = pd.to_numeric(source["waist_mm"], errors="coerce") / 10
    out["hip"] = pd.to_numeric(source["hip_mm"], errors="coerce") / 10

    crotch = pd.to_numeric(source["crotch_height_mm"], errors="coerce")
    knee = pd.to_numeric(source["knee_height_mm"], errors="coerce")
    ankle = pd.to_numeric(source["ankle_height_mm"], errors="coerce")
    front_neck = pd.to_numeric(source["front_neck_height_mm"], errors="coerce")
    jaw = pd.to_numeric(source["jaw_height_mm"], errors="coerce")
    torso_shoulder = pd.to_numeric(source["torso_shoulder_height_mm"], errors="coerce")
    pelvis_point = pd.to_numeric(source["pelvis_point_height_mm"], errors="coerce")
    leg_crotch = pd.to_numeric(source["leg_crotch_height_mm"], errors="coerce")
    leg_ankle = pd.to_numeric(source["leg_ankle_height_mm"], errors="coerce")
    out["thigh_length"] = (crotch - knee) / 10
    out["calf_length"] = (knee - ankle) / 10
    out["torso_length"] = (torso_shoulder - pelvis_point) / 10
    out["leg_length"] = (leg_crotch - leg_ankle) / 10
    out["neck_length"] = (jaw - front_neck) / 10
    out["thigh_calf_ratio"] = out["thigh_length"] / out["calf_length"]
    out["torso_leg_ratio"] = out["torso_length"] / out["leg_length"]

    numeric = ["height", "weight", *TARGETS]
    out[numeric] = out[numeric].apply(pd.to_numeric, errors="coerce")
    out = out[out["gender"].isin(["M", "F"])]
    out = out[out["height"].between(100, 230) & out["weight"].between(25, 300)]
    out = out.replace([np.inf, -np.inf], np.nan).dropna(subset=[*FEATURES, *TARGETS])
    out = out[
        (out["thigh_length"] > 0)
        & (out["calf_length"] > 0)
        & (out["torso_length"] > 0)
        & (out["leg_length"] > 0)
        & (out["neck_length"] > 0)
    ]
    return out.reset_index(drop=True)


def make_features(frame: pd.DataFrame) -> pd.DataFrame:
    features = frame[FEATURES].copy()
    features["gender"] = features["gender"].map({"M": 0.0, "F": 1.0})
    return features


def evaluate(model, frame: pd.DataFrame, split_name: str) -> tuple[pd.DataFrame, list[dict]]:
    x = make_features(frame)
    y = frame[TARGETS]
    pred = np.asarray(model.predict(x), dtype=float)
    rows = frame[["source_row_id", "subject_id", *FEATURES, *TARGETS]].copy()
    rows = rows.rename(columns={target: f"actual_{target}" for target in TARGETS})
    metrics = []
    for idx, target in enumerate(TARGETS):
        rows[f"predicted_{target}"] = pred[:, idx]
        rows[f"error_{target}"] = pred[:, idx] - y[target].to_numpy()
        metrics.append(
            {
                "split": split_name,
                "target": target,
                "rows": len(frame),
                "mae": float(mean_absolute_error(y[target], pred[:, idx])),
                "rmse": float(mean_squared_error(y[target], pred[:, idx]) ** 0.5),
                "r2": float(r2_score(y[target], pred[:, idx])),
            }
        )
    return rows, metrics


def _same_image_ground_truth() -> pd.DataFrame | None:
    path = DATA / "labels" / "sizekorea_vlm_same_image_ground_truth.csv"
    if not path.exists():
        return None
    columns = ["subject_id", *TARGETS]
    ground_truth = pd.read_csv(path)
    for column in columns:
        if column not in ground_truth.columns:
            ground_truth[column] = pd.NA
    ground_truth = ground_truth[columns]
    return ground_truth.rename(columns={target: f"actual_{target}" for target in TARGETS})


def main() -> None:
    for directory in (PREPROCESSED, PREPROCESSED / "splits", HIST / "models", HIST / "predictions"):
        directory.mkdir(parents=True, exist_ok=True)

    source = load_source()
    source.to_csv(RAW_CSV, index=False, encoding="utf-8-sig")
    frame = preprocess(source)
    frame.to_csv(PREPROCESSED / "sizekorea_8th_11targets.csv", index=False, encoding="utf-8-sig")

    train, holdout = train_test_split(frame, test_size=0.2, random_state=SEED)
    validation, test = train_test_split(holdout, test_size=0.5, random_state=SEED)
    for name, split in (("train", train), ("validation", validation), ("test", test)):
        split.to_csv(PREPROCESSED / "splits" / f"{name}.csv", index=False, encoding="utf-8-sig")

    estimator = MultiOutputRegressor(
        HistGradientBoostingRegressor(
            learning_rate=0.05,
            max_iter=300,
            min_samples_leaf=20,
            l2_regularization=1.0,
            random_state=SEED,
        )
    )
    estimator.fit(make_features(train), train[TARGETS])
    model_path = HIST / "models" / "hist_gradient_boosting_11targets.joblib"
    joblib.dump(estimator, model_path)

    all_metrics: list[dict] = []
    for name, split in (("validation", validation), ("test", test)):
        rows, metrics = evaluate(estimator, split, name)
        rows.to_csv(HIST / "predictions" / f"{name}_predictions.csv", index=False, encoding="utf-8-sig")
        all_metrics.extend(metrics)

    same_image_ground_truth = _same_image_ground_truth()

    # 기존 이미지 split에는 과거 둘레 정답이 섞여 있으므로 쓰지 않는다.
    # 현재 11개 계약의 같은 이미지 정답 CSV만 조인해 Hist/VLM 비교 컬럼을 맞춘다.
    for name in ("validation", "test"):
        input_path = DATA / "splits" / "vlm" / f"{name}_set.csv"
        if not input_path.exists():
            continue
        image_split = pd.read_csv(input_path)
        pred = estimator.predict(make_features(image_split.assign(gender=image_split["gender"].str.upper())))
        external = image_split[["subject_id", "gender", "height", "weight"]].copy()
        external.insert(0, "source_id", external["subject_id"])
        if same_image_ground_truth is not None:
            external = external.merge(same_image_ground_truth, on="subject_id", how="left")
        else:
            for target in TARGETS:
                external[f"actual_{target}"] = pd.NA
        for idx, target in enumerate(TARGETS):
            external[f"predicted_{target}"] = pred[:, idx]
            external[f"error_{target}"] = external[f"predicted_{target}"] - external[f"actual_{target}"]
        external["front_image_path"] = external["source_id"].map(
            lambda value: f"ml/body_measurement/data/people/{value}_front.jpg"
        )
        external["side_image_path"] = external["source_id"].map(
            lambda value: f"ml/body_measurement/data/people/{value}_side.jpg"
        )
        external[IMAGE_RESULT_COLUMNS].to_csv(
            HIST / "predictions" / f"vlm_{name}_inputs_hist_predictions.csv",
            index=False,
            encoding="utf-8-sig",
        )

    manifest = {
        "features": FEATURES,
        "targets": TARGETS,
        "source_workbook": str(RAW_XLSX),
        "source_sheet": "(1~2차년도) 3D 측정",
        "raw_csv": str(RAW_CSV),
        "rows": {"source": len(source), "cleaned": len(frame), "train": len(train), "validation": len(validation), "test": len(test)},
        "seed": SEED,
        "model": "HistGradientBoostingRegressor via MultiOutputRegressor",
        "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
    }
    (HIST / "metrics.json").write_text(json.dumps(all_metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (HIST / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(pd.DataFrame(all_metrics).to_string(index=False))


if __name__ == "__main__":
    main()
