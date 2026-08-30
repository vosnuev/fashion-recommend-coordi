"""기존 181 대체정의 모델과 정확한 3D 길이 v2를 같은 정답에서 비교한다."""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, root_mean_squared_error

from train_hist_exact_lengths_v2 import (
    DATA,
    FEATURES,
    HIST,
    RATIO_SOURCES,
    TARGETS,
    make_features,
)


LEGACY_MODEL = HIST / "models" / "hist_gradient_boosting_181.joblib"
EXACT_DATA = DATA / "preprocessed" / "sizekorea_8th_exact_lengths_v2.csv"
EXACT_CV = HIST / "predictions" / "cv_predictions_exact_lengths_v2.csv"
OUTPUT = HIST / "comparison_exact_lengths_v2.json"
LEGACY_TARGETS = [
    "chest",
    "waist",
    "hip",
    "thigh",
    "calf",
    "arm",
    "shoulder",
    *TARGETS,
]


def bucket(values: pd.Series, genders: pd.Series, thresholds: dict[str, tuple[float, float]]) -> pd.Series:
    result = pd.Series(index=values.index, dtype="string")
    for gender, (p33, p67) in thresholds.items():
        mask = genders.eq(gender)
        result.loc[mask & values.lt(p33)] = "low"
        result.loc[mask & values.ge(p33) & values.le(p67)] = "middle"
        result.loc[mask & values.gt(p67)] = "high"
    return result


def main() -> None:
    if not LEGACY_MODEL.exists():
        raise FileNotFoundError(f"기존 181 모델이 없습니다: {LEGACY_MODEL}")
    frame = pd.read_csv(EXACT_DATA)
    exact_cv = pd.read_csv(EXACT_CV)
    legacy_model = joblib.load(LEGACY_MODEL)
    legacy_matrix = np.asarray(legacy_model.predict(make_features(frame)), dtype=float)
    legacy = pd.DataFrame(legacy_matrix, columns=LEGACY_TARGETS, index=frame.index)
    new_cv = pd.DataFrame(
        {target: exact_cv[f"predicted_{target}"] for target in TARGETS},
        index=frame.index,
    )

    metric_rows = []
    for target in TARGETS:
        actual = frame[target]
        for model_name, predicted in (("legacy_181_proxy", legacy[target]), ("exact_v2_cv", new_cv[target])):
            metric_rows.append(
                {
                    "model": model_name,
                    "target": target,
                    "mae_against_exact_cm": float(mean_absolute_error(actual, predicted)),
                    "rmse_against_exact_cm": float(root_mean_squared_error(actual, predicted)),
                    "bias_against_exact_cm": float((predicted - actual).mean()),
                }
            )

    classification = {}
    for ratio, (numerator, denominator) in RATIO_SOURCES.items():
        actual = frame[ratio]
        legacy_ratio = legacy[numerator] / legacy[denominator]
        new_ratio = new_cv[numerator] / new_cv[denominator]
        thresholds = {
            gender: (
                float(group[ratio].quantile(0.33)),
                float(group[ratio].quantile(0.67)),
            )
            for gender, group in frame.groupby("gender")
        }
        actual_bucket = bucket(actual, frame["gender"], thresholds)
        classification[ratio] = {
            "thresholds": {
                gender: {"p33": values[0], "p67": values[1]}
                for gender, values in thresholds.items()
            },
            "legacy_same_bucket_rate": float(
                (bucket(legacy_ratio, frame["gender"], thresholds) == actual_bucket).mean()
            ),
            "legacy_changed_bucket_rate": float(
                (bucket(legacy_ratio, frame["gender"], thresholds) != actual_bucket).mean()
            ),
            "exact_v2_cv_same_bucket_rate": float(
                (bucket(new_ratio, frame["gender"], thresholds) == actual_bucket).mean()
            ),
            "exact_v2_cv_changed_bucket_rate": float(
                (bucket(new_ratio, frame["gender"], thresholds) != actual_bucket).mean()
            ),
        }

    report = {
        "comparison_rows": int(len(frame)),
        "features": FEATURES,
        "note": "legacy는 181명 대체 랜드마크 모델을 3D 정확 정의 정답에 직접 비교한 값",
        "length_metrics": metric_rows,
        "ratio_bucket_impact": classification,
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(pd.DataFrame(metric_rows).to_string(index=False))
    print(json.dumps(classification, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
