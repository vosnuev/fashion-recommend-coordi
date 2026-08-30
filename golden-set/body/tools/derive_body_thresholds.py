"""이미지와 결합된 SizeKorea 직접측정 181명에서 체형 임계값을 재현한다.

원천 로딩과 이상치 제외는 학습 스크립트의 공개 함수를 그대로 사용한다. 따라서 모델,
정제 CSV, 골든셋이 언제나 동일한 사람과 계측 정의를 공유한다.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
TRAIN_SCRIPT = ROOT / "ml/body_measurement/scripts/train_hist_181.py"
CLEAN_CSV = ROOT / "ml/body_measurement/data/preprocessed/sizekorea_172_clean.csv"
EXACT_LENGTH_MANIFEST = ROOT / "ml/body_measurement/data/hist/manifest_exact_lengths_v2.json"
OUT = ROOT / "golden-set/body/rules/body_shape_thresholds.json"
RUNTIME_OUT = ROOT / "api/apps/recommend/rules/body_shape_thresholds.json"

SHAPES = {
    "round": "둥근체형",
    "inverted_triangle": "역삼각체형",
    "triangle": "삼각체형",
    "hourglass": "모래시계체형",
    "rectangle": "사각체형(표준 포함)",
}
PRIORITY = list(SHAPES)
QUANTILES = (("p33", 1 / 3), ("p67", 2 / 3), ("p90", .9))


def load_training_module():
    spec = importlib.util.spec_from_file_location("train_hist_181", TRAIN_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"학습 스크립트를 불러올 수 없습니다: {TRAIN_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def empirical_percentile(series: pd.Series) -> pd.Series:
    """동점은 평균 순위로 처리하며 결과 범위는 (0, 1]이다."""
    return series.rank(method="average", pct=True)


def prepare(clean: pd.DataFrame) -> pd.DataFrame:
    required = ["gender", "shoulder", "chest", "waist", "hip", "neck_length",
                "thigh_calf_ratio", "torso_leg_ratio"]
    missing = sorted(set(required) - set(clean.columns))
    if missing:
        raise ValueError(f"필수 컬럼 누락: {missing}")
    if clean[required].isna().any().any():
        raise ValueError("정제 데이터에 필수값 결측이 있습니다")

    out = clean.copy()
    for _, idx in out.groupby("gender").groups.items():
        group = out.loc[idx]
        out.loc[idx, "shoulder_percentile"] = empirical_percentile(group["shoulder"])
        out.loc[idx, "chest_percentile"] = empirical_percentile(group["chest"])
        out.loc[idx, "hip_percentile"] = empirical_percentile(group["hip"])
    out["upper_lower"] = (
        .6 * out["shoulder_percentile"]
        + .4 * out["chest_percentile"]
        - out["hip_percentile"]
    )
    out["waist_definition"] = out["waist"] / ((out["chest"] + out["hip"]) / 2)
    return out


def quantiles(series: pd.Series, names=QUANTILES) -> dict[str, float]:
    return {name: round(float(series.quantile(q)), 6) for name, q in names}


def classify(row: pd.Series, thresholds: dict) -> str:
    if row["waist_definition"] >= thresholds["waist_definition"]["p90"]:
        return "round"
    if row["upper_lower"] >= thresholds["upper_lower"]["p67"]:
        return "inverted_triangle"
    if row["upper_lower"] <= thresholds["upper_lower"]["p33"]:
        return "triangle"
    if row["waist_definition"] <= thresholds["waist_definition"]["p33"]:
        return "hourglass"
    return "rectangle"


def main() -> None:
    training = load_training_module()
    profiles = training.load_profiles()
    clean, dropped = training.drop_implausible(profiles)
    CLEAN_CSV.parent.mkdir(parents=True, exist_ok=True)
    clean.to_csv(CLEAN_CSV, index=False, encoding="utf-8-sig")
    frame = prepare(clean)
    exact_manifest = json.loads(EXACT_LENGTH_MANIFEST.read_text(encoding="utf-8"))
    exact_distribution = exact_manifest["distribution"]

    thresholds = {}
    distribution = {}
    references = {}
    centroids = {}
    for gender, group in frame.groupby("gender", sort=True):
        length_stats = exact_distribution[gender]
        th = {
            "upper_lower": quantiles(group["upper_lower"]),
            "waist_definition": quantiles(group["waist_definition"]),
            "thigh_calf_ratio": {
                key: round(float(length_stats["thigh_calf_ratio"][key]), 6)
                for key in ("p33", "p67")
            },
            "torso_leg_ratio": {
                key: round(float(length_stats["torso_leg_ratio"][key]), 6)
                for key in ("p33", "p67")
            },
            "neck_length": {
                key: round(float(length_stats["neck_length"][key]), 6)
                for key in ("p33", "p67")
            },
        }
        thresholds[gender] = {"all": th}
        frame.loc[group.index, "body_shape"] = group.apply(lambda row: classify(row, th), axis=1)
        distribution[gender] = frame.loc[group.index, "body_shape"].value_counts().to_dict()
        references[gender] = {
            "sample_size": int(len(group)),
            # 런타임이 학습 때와 같은 경험 백분위를 재현하려면 분위수 몇 개가
            # 아니라 전체 기준 배열이 필요하다. 정렬해 두면 이진 탐색으로 계산한다.
            "shoulder": sorted(round(float(v), 6) for v in group["shoulder"]),
            "chest": sorted(round(float(v), 6) for v in group["chest"]),
            "hip": sorted(round(float(v), 6) for v in group["hip"]),
            "shoulder_cm": quantiles(group["shoulder"]),
            "chest_cm": quantiles(group["chest"]),
            "hip_cm": quantiles(group["hip"]),
            "empirical_percentile": "성별 내 average-rank percentile, 범위 (0, 1]",
        }
        centroids[gender] = {
            shape: {field: round(float(sub[field].mean()), 3)
                    for field in ["height", "weight", "shoulder", "chest", "waist", "hip"]}
            for shape, sub in frame.loc[group.index].groupby("body_shape")
        }

    payload = {
        "version": "4.0.0",
        "generated_by": "golden-set/body/tools/derive_body_thresholds.py",
        "source": str(CLEAN_CSV.relative_to(ROOT)).replace("\\", "/"),
        "source_generator": str(TRAIN_SCRIPT.relative_to(ROOT)).replace("\\", "/"),
        "length_threshold_source": str(EXACT_LENGTH_MANIFEST.relative_to(ROOT)).replace("\\", "/"),
        "sample_size": {"profiles": int(len(profiles)), "clean": int(len(clean)),
                        "dropped": int(len(dropped)),
                        "by_sex": clean["gender"].value_counts().sort_index().to_dict()},
        "length_threshold_sample_size": exact_manifest["rows"],
        "taxonomy": {"operational": SHAPES, "standard_mapping": "rectangle"},
        "required_inputs": ["gender", "shoulder", "chest", "waist", "hip"],
        "method": {
            "normalization": "shoulder/chest/hip을 성별별 경험 백분위로 변환",
            "upper_lower": "0.6*shoulder_percentile + 0.4*chest_percentile - hip_percentile",
            "waist_definition": "waist / ((chest + hip) / 2)",
            "priority": PRIORITY,
            "classification": [
                "waist_definition >= p90 -> round",
                "upper_lower >= p67 -> inverted_triangle",
                "upper_lower <= p33 -> triangle",
                "waist_definition <= p33 -> hourglass",
                "otherwise -> rectangle",
            ],
        },
        "thresholds": thresholds,
        "horizontal_classification_references": references,
        "distribution": distribution,
        "centroids_cm": centroids,
        "limitations": [
            "이미지가 연결된 한국 성인 172명(F 81, M 91)의 내부 기준이며 모집단 대표 규준이 아니다.",
            "성별 이분 표기(M/F)만 존재해 그 밖의 사용자에게 적용할 검증 근거가 없다.",
            "shoulder는 어깨사이너비, chest/waist/hip은 둘레이므로 원시 cm를 직접 빼지 않는다.",
            "가로 체형 기준은 이미지 연결 172명, 길이 비율과 neck_length 기준은 정확한 3D 랜드마크 4,485명에서 산출했다.",
        ],
    }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    OUT.write_text(serialized, encoding="utf-8")
    RUNTIME_OUT.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_OUT.write_text(serialized, encoding="utf-8")
    print(json.dumps({"outputs": [str(OUT), str(RUNTIME_OUT)], "sample_size": payload["sample_size"],
                      "thresholds": thresholds, "distribution": distribution}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
