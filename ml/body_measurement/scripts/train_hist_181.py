"""이미지 세트 181명으로 레거시 12개 출력 모델을 재현한다.

현재 서빙은 이 모델의 chest/waist/hip/shoulder만 사용한다. 아래 대체 길이는
과거 결과 재현용이며 길이 5개는 ``train_hist_exact_lengths_v2.py``가 소유한다.

왜 181명만 쓰나
---------------
사진(VLM) 경로와 무사진(hist) 경로가 **같은 사람·같은 계측 정의**를 쓰게 하려는 것이다.
SizeKorea 8차 3D 측정(4,545행)에는 길이 랜드마크가 풍부하지만 허벅지·종아리·팔뚝 둘레가
없고, 그 조사에는 우리 이미지 181명이 들어 있지도 않다(8개 항목 최근접 L1거리 최소 4.46cm,
0거리 0명). 두 자료를 섞으면 정의가 갈리므로 이미지가 있는 181명 한 벌만 쓴다.

길이 정의를 다시 세운 이유
--------------------------
181명 원본은 SizeKorea **직접측정** 37항목이라 높이가 7개뿐이다
(겨드랑·목뒤·무릎·샅·엉덩이·허리·신발굽). 3D 정의가 쓰는 복사뼈높이·턱높이·목앞높이가
없어서, 가진 높이만으로 같은 뜻을 내는 정의로 바꿨다. 비율 지표는 한 사람 안의 상대값이라
기준점만 일관되면 체형 분류가 성립한다.

⚠️ 이 정의로 만든 값은 3D 기준값과 **숫자가 호환되지 않는다**. 골든셋 임계값
(golden-set/body/rules/body_shape_thresholds.json)을 이 분포로 다시 뽑아야 한다.

높이는 전부 신발굽높이를 빼서 맨발/바닥 기준으로 맞춘다 — 서빙에서 사용자가 넣는 키가
맨발 기준이라, 학습 입력도 같은 기준이어야 한다.
"""

from __future__ import annotations

import csv
import glob
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
PROFILES = DATA / "raw_test_data"
PREPROCESSED = DATA / "preprocessed"
HIST = DATA / "hist"

SEED = 42
FEATURES = ["gender", "height", "weight"]
GENDER_CODES = {"M": 0.0, "F": 1.0}

CIRCUMFERENCE_TARGETS = ["chest", "waist", "hip", "thigh", "calf", "arm", "shoulder"]
LENGTH_TARGETS = [
    "thigh_length",
    "calf_length",
    "torso_length",
    "leg_length",
    "neck_length",
]
TARGETS = CIRCUMFERENCE_TARGETS + LENGTH_TARGETS
RATIO_SOURCES = {
    "thigh_calf_ratio": ("thigh_length", "calf_length"),
    "torso_leg_ratio": ("torso_length", "leg_length"),
}

# 원본 37항목 → 우리 필드. 둘레·너비는 이름만 바꾸면 된다.
DIRECT = {
    "chest": "젖가슴둘레",
    "waist": "허리둘레",
    "hip": "엉덩이둘레",
    "thigh": "넙다리둘레",
    "calf": "장딴지둘레",
    "arm": "편위팔둘레",
    "shoulder": "어깨사이너비",
}

# 사람 몸에서 나올 수 있는 범위. 벗어나면 계측·매핑 오류로 보고 뺀다.
PLAUSIBLE = {
    "chest": (60, 140),
    "waist": (50, 140),
    "hip": (60, 140),
    "shoulder": (28, 55),
    "thigh": (30, 80),
    "calf": (25, 50),
    "arm": (18, 45),
    "thigh_length": (15, 50),
    "calf_length": (28, 60),
    "torso_length": (25, 60),
    "leg_length": (55, 95),
    "neck_length": (1.5, 20),
}


def read_profile(path: str) -> dict[str, str]:
    """개인별 원본은 1행이 항목번호, 2행이 항목명, 3행이 값인 3줄짜리 CSV다."""
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    return {n.strip(): v.strip() for n, v in zip(rows[1], rows[2]) if n.strip()}


def load_profiles() -> pd.DataFrame:
    records = []
    for path in sorted(glob.glob(str(PROFILES / "*_profile.csv"))):
        p = read_profile(path)

        def num(key: str) -> float:
            try:
                return float(p[key])
            except (KeyError, ValueError):
                return np.nan

        # 신발굽을 빼서 모든 높이를 맨발/바닥 기준으로 통일한다.
        heel = num("신발굽높이")
        heel = 0.0 if np.isnan(heel) else heel
        crotch = num("샅높이") - heel
        knee = num("무릎높이") - heel
        armpit = num("겨드랑높이") - heel
        hip_h = num("엉덩이높이") - heel
        back_neck = num("목뒤높이") - heel

        row = {
            "subject_id": Path(path).stem.replace("_profile", ""),
            "gender": str(p.get("성별", "")).strip().upper()[:1],
            "height": num("키") - heel,
            "weight": num("몸무게"),
            # 길이 — 181명이 가진 높이 7개로 다시 세운 정의
            "thigh_length": crotch - knee,      # 샅선 → 무릎
            "calf_length": knee,                # 무릎 → 바닥 (복사뼈 대체)
            "torso_length": armpit - hip_h,     # 어깨선 → 골반점
            "leg_length": crotch,               # 샅선 → 바닥 (복사뼈 대체)
            "neck_length": back_neck - armpit - 8.0,  # 상체길이(머리~골반) - (어깨~골반) - 얼굴길이 시각적 목길이 (8~12cm 범위)
        }
        row.update({k: num(v) for k, v in DIRECT.items()})
        records.append(row)

    frame = pd.DataFrame(records)
    for name, (numerator, denominator) in RATIO_SOURCES.items():
        frame[name] = frame[numerator] / frame[denominator]
    return frame


def drop_implausible(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    mask = frame["gender"].isin(["M", "F"]) & frame["height"].between(100, 230)
    mask &= frame["weight"].between(25, 300)
    for target, (low, high) in PLAUSIBLE.items():
        mask &= frame[target].between(low, high)
    mask &= frame[TARGETS].notna().all(axis=1)
    return frame[mask].reset_index(drop=True), frame[~mask].reset_index(drop=True)


def make_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    out["gender"] = frame["gender"].map(GENDER_CODES)
    out["height"] = frame["height"].astype(float)
    out["weight"] = frame["weight"].astype(float)
    return out[FEATURES]


def build_estimator() -> MultiOutputRegressor:
    return MultiOutputRegressor(
        HistGradientBoostingRegressor(
            learning_rate=0.05,
            max_iter=300,
            min_samples_leaf=10,  # n이 작아 4,485행 설정(20)보다 낮춘다
            l2_regularization=1.0,
            random_state=SEED,
        )
    )


def cross_validate(features: pd.DataFrame, targets: pd.DataFrame):
    """n<200 이라 홀드아웃 하나로는 검증셋이 20명 아래로 떨어져 지표를 못 믿는다."""
    folds = KFold(n_splits=5, shuffle=True, random_state=SEED)
    predictions = np.zeros_like(targets.to_numpy(dtype=float))
    for train_idx, test_idx in folds.split(features):
        model = build_estimator()
        model.fit(features.iloc[train_idx], targets.iloc[train_idx])
        predictions[test_idx] = model.predict(features.iloc[test_idx])

    metrics = []
    for i, target in enumerate(TARGETS):
        actual = targets.iloc[:, i].to_numpy(dtype=float)
        metrics.append(
            {
                "split": "cv5",
                "target": target,
                "rows": int(len(actual)),
                "mae": float(mean_absolute_error(actual, predictions[:, i])),
                "rmse": float(root_mean_squared_error(actual, predictions[:, i])),
                "r2": float(r2_score(actual, predictions[:, i])),
            }
        )
    return metrics, predictions


def main() -> None:
    joined = load_profiles()
    clean, dropped = drop_implausible(joined)
    print(f"프로필 {len(joined)}명 -> 학습 {len(clean)}명 (제외 {len(dropped)}명)")
    for _, row in dropped.iterrows():
        bad = [
            f"{t}={row[t]:.1f}"
            for t, (low, high) in PLAUSIBLE.items()
            if pd.isna(row[t]) or not low <= row[t] <= high
        ]
        print(f"  제외 {row['subject_id']}: {', '.join(bad)}")

    PREPROCESSED.mkdir(parents=True, exist_ok=True)
    (HIST / "models").mkdir(parents=True, exist_ok=True)
    (HIST / "predictions").mkdir(parents=True, exist_ok=True)
    joined.to_csv(
        PREPROCESSED / "sizekorea_181_full.csv", index=False, encoding="utf-8-sig"
    )
    clean.to_csv(
        PREPROCESSED / "sizekorea_172_clean.csv", index=False, encoding="utf-8-sig"
    )

    features, targets = make_features(clean), clean[TARGETS]
    metrics, predictions = cross_validate(features, targets)

    model = build_estimator()
    model.fit(features, targets)
    model_path = HIST / "models" / "hist_gradient_boosting_181.joblib"
    joblib.dump(model, model_path)

    frame = clean[["subject_id", *FEATURES]].copy()
    for i, target in enumerate(TARGETS):
        frame[f"actual_{target}"] = targets.iloc[:, i].to_numpy(dtype=float)
        frame[f"pred_{target}"] = predictions[:, i]
    frame.to_csv(
        HIST / "predictions" / "cv_predictions_181.csv", index=False, encoding="utf-8-sig"
    )

    manifest = {
        "source": "SizeKorea 8차 직접측정 개인별 프로필 (이미지 세트 181명)",
        "features": FEATURES,
        "targets": TARGETS,
        "ratios": {k: f"{v[0]} / {v[1]}" for k, v in RATIO_SOURCES.items()},
        "length_definitions": {
            "thigh_length": "샅높이 - 무릎높이",
            "calf_length": "무릎높이 (바닥 기준, 신발굽 제외)",
            "torso_length": "겨드랑높이 - 엉덩이높이",
            "leg_length": "샅높이 (바닥 기준, 신발굽 제외)",
            "neck_length": "목뒤높이 - 겨드랑높이 - 8.0cm(시각적 얼굴 길이 보정)",
        },
        "rows": {"profiles": int(len(joined)), "trained": int(len(clean))},
        "seed": SEED,
        "note": "3D 기준값과 숫자가 호환되지 않는다. 골든셋 임계값을 이 분포로 재산출해야 한다.",
    }
    (HIST / "manifest_181.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (HIST / "metrics_181.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n모델: {model_path}")
    print(f"{'target':14s}{'MAE':>9s}{'RMSE':>9s}{'R2':>8s}{'mean':>9s}")
    for row, target in zip(metrics, TARGETS):
        print(
            f"{target:14s}{row['mae']:9.3f}{row['rmse']:9.3f}{row['r2']:8.3f}"
            f"{clean[target].mean():9.2f}"
        )
    print("\n[비율 분포 - 골든셋 임계값 재산출용]")
    for name in RATIO_SOURCES:
        v = clean[name]
        print(f"  {name:18s} mean={v.mean():.3f}  min={v.min():.3f}  max={v.max():.3f}")


if __name__ == "__main__":
    main()
