"""사용자 랜드마크 계약으로 SizeKorea 3D 길이 모델 v2를 학습한다.

기존 181명 모델은 둘레 예측과 롤백을 위해 그대로 둔다. 이 모델은 동일 정의가
가능한 3D 측정 4,545명의 길이 5개만 예측하며, 두 비율은 예측 길이로 계산한다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score, root_mean_squared_error
from sklearn.model_selection import KFold
from sklearn.multioutput import MultiOutputRegressor


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SOURCE = DATA / "raw" / "sizekorea_8th_3d_source.csv"
PREPROCESSED = DATA / "preprocessed" / "sizekorea_8th_exact_lengths_v2.csv"
HIST = DATA / "hist"
MODEL = HIST / "models" / "hist_gradient_boosting_exact_lengths_v2.joblib"
PREDICTIONS = HIST / "predictions" / "cv_predictions_exact_lengths_v2.csv"
MANIFEST = HIST / "manifest_exact_lengths_v2.json"
METRICS = HIST / "metrics_exact_lengths_v2.json"

SEED = 42
FEATURES = ["gender", "height", "weight"]
TARGETS = [
    "thigh_length",
    "calf_length",
    "torso_length",
    "leg_length",
    "neck_length",
]
RATIO_SOURCES = {
    "thigh_calf_ratio": ("thigh_length", "calf_length"),
    "torso_leg_ratio": ("torso_length", "leg_length"),
}
LANDMARK_DEFINITIONS = {
    "thigh_length": "샅높이 - 무릎뼈가운데높이",
    "calf_length": "무릎뼈가운데높이 - 가쪽복사높이",
    "torso_length": "어깨높이 - 위앞엉덩뼈가시높이",
    "leg_length": "위앞엉덩뼈가시높이 - 가쪽복사높이",
    "neck_length": "턱끝높이 - 목앞높이",
}


def preprocess(source: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    out = pd.DataFrame(index=source.index)
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

    def mm(column: str) -> pd.Series:
        return pd.to_numeric(source[column], errors="coerce")

    out["thigh_length"] = (mm("crotch_height_mm") - mm("knee_height_mm")) / 10
    out["calf_length"] = (mm("knee_height_mm") - mm("ankle_height_mm")) / 10
    out["torso_length"] = (
        mm("torso_shoulder_height_mm") - mm("pelvis_point_height_mm")
    ) / 10
    out["leg_length"] = (mm("pelvis_point_height_mm") - mm("ankle_height_mm")) / 10
    out["neck_length"] = (mm("jaw_height_mm") - mm("front_neck_height_mm")) / 10
    for ratio, (numerator, denominator) in RATIO_SOURCES.items():
        out[ratio] = out[numerator] / out[denominator]

    before = len(out)
    out = out.replace([np.inf, -np.inf], np.nan)
    complete = out.dropna(subset=[*FEATURES, *TARGETS, *RATIO_SOURCES])
    valid = complete[
        complete["gender"].isin(["M", "F"])
        & complete["height"].between(100, 230)
        & complete["weight"].between(25, 300)
        & (complete[TARGETS] > 0).all(axis=1)
    ].copy()
    counts = {
        "source": before,
        "missing_or_infinite": before - len(complete),
        "invalid": len(complete) - len(valid),
        "cleaned": len(valid),
    }
    return valid.reset_index(drop=True), counts


def make_features(frame: pd.DataFrame) -> pd.DataFrame:
    features = frame[FEATURES].copy()
    features["gender"] = features["gender"].map({"M": 0.0, "F": 1.0})
    return features.astype(float)


def build_estimator() -> MultiOutputRegressor:
    return MultiOutputRegressor(
        HistGradientBoostingRegressor(
            learning_rate=0.05,
            max_iter=300,
            min_samples_leaf=20,
            l2_regularization=1.0,
            random_state=SEED,
        )
    )


def distribution(frame: pd.DataFrame) -> dict[str, dict[str, dict[str, float | int]]]:
    result: dict[str, dict[str, dict[str, float | int]]] = {}
    for gender, group in frame.groupby("gender", sort=True):
        result[str(gender)] = {}
        for target in [*TARGETS, *RATIO_SOURCES]:
            values = group[target]
            result[str(gender)][target] = {
                "rows": int(len(values)),
                "mean": float(values.mean()),
                "std": float(values.std()),
                "min": float(values.min()),
                "p01": float(values.quantile(0.01)),
                "p10": float(values.quantile(0.10)),
                "p33": float(values.quantile(0.33)),
                "p50": float(values.quantile(0.50)),
                "p67": float(values.quantile(0.67)),
                "p90": float(values.quantile(0.90)),
                "p99": float(values.quantile(0.99)),
                "max": float(values.max()),
            }
    return result


def cross_validate(frame: pd.DataFrame) -> tuple[np.ndarray, list[dict[str, float | int | str]]]:
    x, y = make_features(frame), frame[TARGETS]
    predictions = np.zeros_like(y.to_numpy(dtype=float))
    folds = KFold(n_splits=5, shuffle=True, random_state=SEED)
    for train_idx, test_idx in folds.split(x):
        estimator = build_estimator()
        estimator.fit(x.iloc[train_idx], y.iloc[train_idx])
        predictions[test_idx] = estimator.predict(x.iloc[test_idx])

    metrics: list[dict[str, float | int | str]] = []
    for index, target in enumerate(TARGETS):
        actual = y[target].to_numpy(dtype=float)
        predicted = predictions[:, index]
        metrics.append(
            {
                "split": "cv5",
                "target": target,
                "rows": int(len(actual)),
                "mae": float(mean_absolute_error(actual, predicted)),
                "rmse": float(root_mean_squared_error(actual, predicted)),
                "bias": float(np.mean(predicted - actual)),
                "r2": float(r2_score(actual, predicted)),
            }
        )
    return predictions, metrics


def ratio_metrics(frame: pd.DataFrame, predictions: np.ndarray) -> list[dict[str, float | int | str]]:
    predicted = pd.DataFrame(predictions, columns=TARGETS, index=frame.index)
    metrics: list[dict[str, float | int | str]] = []
    for ratio, (numerator, denominator) in RATIO_SOURCES.items():
        actual = frame[ratio].to_numpy(dtype=float)
        values = (predicted[numerator] / predicted[denominator]).to_numpy(dtype=float)
        metrics.append(
            {
                "split": "cv5_postprocess",
                "target": ratio,
                "rows": int(len(actual)),
                "mae": float(mean_absolute_error(actual, values)),
                "rmse": float(root_mean_squared_error(actual, values)),
                "bias": float(np.mean(values - actual)),
                "r2": float(r2_score(actual, values)),
            }
        )
    return metrics


def main() -> None:
    if not SOURCE.exists():
        raise FileNotFoundError(f"SizeKorea 3D source CSV가 없습니다: {SOURCE}")
    source = pd.read_csv(SOURCE)
    frame, row_counts = preprocess(source)
    if len(frame) < 100:
        raise ValueError(f"학습 가능한 행이 너무 적습니다: {len(frame)}")

    PREPROCESSED.parent.mkdir(parents=True, exist_ok=True)
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    PREDICTIONS.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(PREPROCESSED, index=False, encoding="utf-8-sig")

    predictions, metrics = cross_validate(frame)
    metrics.extend(ratio_metrics(frame, predictions))
    estimator = build_estimator()
    estimator.fit(make_features(frame), frame[TARGETS])
    joblib.dump(estimator, MODEL)

    prediction_rows = frame[["source_row_id", "subject_id", *FEATURES]].copy()
    for index, target in enumerate(TARGETS):
        prediction_rows[f"actual_{target}"] = frame[target]
        prediction_rows[f"predicted_{target}"] = predictions[:, index]
        prediction_rows[f"error_{target}"] = predictions[:, index] - frame[target]
    prediction_rows.to_csv(PREDICTIONS, index=False, encoding="utf-8-sig")

    gender_rows = {str(key): int(value) for key, value in frame["gender"].value_counts().items()}
    manifest = {
        "version": "exact_landmarks_v2",
        "source": "SizeKorea 8차 3D 측정",
        "source_csv": str(SOURCE.relative_to(ROOT)),
        "features": FEATURES,
        "model_targets": TARGETS,
        "ratios_postprocessed": {
            key: f"{numerator} / {denominator}"
            for key, (numerator, denominator) in RATIO_SOURCES.items()
        },
        "length_definitions": LANDMARK_DEFINITIONS,
        "rows": {**row_counts, "gender": gender_rows},
        "seed": SEED,
        "validation": "shuffled 5-fold cross-validation",
        "model": "HistGradientBoostingRegressor via MultiOutputRegressor",
        "model_path": str(MODEL.relative_to(ROOT)),
        "model_sha256": hashlib.sha256(MODEL.read_bytes()).hexdigest(),
        "distribution": distribution(frame),
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    METRICS.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(manifest["rows"], ensure_ascii=False))
    print(pd.DataFrame(metrics).to_string(index=False))


if __name__ == "__main__":
    main()
