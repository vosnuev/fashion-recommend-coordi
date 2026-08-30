"""VLM 행별 결과를 Hist 행별 결과와 같은 CSV 계약으로 정렬한다."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
VLM_ROOT = DATA / "vlm" / "kimi-k2.5"
HIST_PREDICTIONS = DATA / "hist" / "predictions"

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
RESULT_COLUMNS = [
    "source_id",
    "subject_id",
    "gender",
    "height",
    "weight",
    *[f"actual_{target}" for target in TARGETS],
    *[f"predicted_{target}" for target in TARGETS],
    *[f"error_{target}" for target in TARGETS],
    "front_image_path",
    "side_image_path",
]


def _series_or_none(frame: pd.DataFrame, column: str) -> pd.Series | None:
    if column in frame.columns:
        return pd.to_numeric(frame[column], errors="coerce")
    return None


def _computed_ratio(
    frame: pd.DataFrame,
    numerator_column: str,
    denominator_column: str,
    fallback_column: str | None = None,
) -> pd.Series:
    numerator = _series_or_none(frame, numerator_column)
    denominator = _series_or_none(frame, denominator_column)
    if numerator is not None and denominator is not None:
        ratio = numerator / denominator.replace(0, pd.NA)
        return ratio.round(3)
    if fallback_column and fallback_column in frame.columns:
        return pd.to_numeric(frame[fallback_column], errors="coerce")
    raise KeyError(f"{numerator_column}, {denominator_column}")


def align(split: str) -> pd.DataFrame:
    raw_path = VLM_ROOT / f"{split}-redefined-11targets" / "predictions.csv"
    if not raw_path.exists():
        raw_path = VLM_ROOT / f"{split}-redefined-9targets" / "predictions.csv"
    labels_path = DATA / "labels" / "sizekorea_vlm_same_image_ground_truth.csv"
    if not raw_path.exists():
        raise FileNotFoundError(raw_path)
    if not labels_path.exists():
        raise FileNotFoundError(labels_path)

    raw = pd.read_csv(raw_path)
    labels = pd.read_csv(labels_path)
    aligned = raw[["subject_id"]].copy()
    aligned.insert(0, "source_id", aligned["subject_id"])
    labels_by_subject = labels.set_index("subject_id")
    for field in ("gender", "height", "weight"):
        aligned[field] = labels_by_subject.reindex(aligned["subject_id"])[field].to_numpy()

    for target in TARGETS:
        if target == "thigh_calf_ratio":
            aligned[f"predicted_{target}"] = _computed_ratio(
                raw,
                "predicted_thigh_length_cm",
                "predicted_calf_length_cm",
                "predicted_thigh_calf_ratio",
            ).to_numpy()
            continue
        if target == "torso_leg_ratio":
            aligned[f"predicted_{target}"] = _computed_ratio(
                raw,
                "predicted_torso_length_cm",
                "predicted_leg_length_cm",
                "predicted_torso_leg_ratio",
            ).to_numpy()
            continue
        source = f"predicted_{target}_cm"
        if source in raw.columns:
            aligned[f"predicted_{target}"] = raw[source].to_numpy()
        else:
            aligned[f"predicted_{target}"] = pd.NA

    ground_truth = labels[["subject_id", *TARGETS]].rename(
        columns={target: f"actual_{target}" for target in TARGETS}
    )
    aligned = aligned.merge(ground_truth, on="subject_id", how="left")
    for target in TARGETS:
        aligned[f"error_{target}"] = aligned[f"predicted_{target}"] - aligned[f"actual_{target}"]
    aligned["front_image_path"] = aligned["source_id"].map(
        lambda value: f"ml/body_measurement/data/people/{value}_front.jpg"
    )
    aligned["side_image_path"] = aligned["source_id"].map(
        lambda value: f"ml/body_measurement/data/people/{value}_side.jpg"
    )

    return aligned[RESULT_COLUMNS]


def main() -> None:
    HIST_PREDICTIONS.mkdir(parents=True, exist_ok=True)
    for split in ("validation", "test"):
        aligned = align(split)
        output_dir = VLM_ROOT / f"{split}-redefined-11targets"
        output_dir.mkdir(parents=True, exist_ok=True)
        aligned.to_csv(output_dir / "aligned_predictions.csv", index=False, encoding="utf-8-sig")
        aligned.to_csv(
            HIST_PREDICTIONS / f"vlm_{split}_aligned_predictions.csv",
            index=False,
            encoding="utf-8-sig",
        )
        print(f"{split}: {len(aligned)} rows, {len(aligned.columns)} columns")


if __name__ == "__main__":
    main()
