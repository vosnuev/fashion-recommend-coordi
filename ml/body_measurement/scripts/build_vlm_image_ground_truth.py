"""Restore same-image SizeKorea profile labels and evaluate VLM predictions.

The current image files use model numbers such as F010. Those numbers are not
present in the recovered 8th workbook, but the historical raw_test_data profile
CSVs contain the same-image measurements. This script consolidates those profile
CSVs from git history and writes explicit ground-truth columns for the fields
that are actually available.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
from pathlib import Path

import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_PROFILE_REF = "fd1f6d3^"
PROFILE_PREFIX = "ml/body_measurement/data/raw_test_data"
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
PROFILE_COLUMNS = {
    "model_no": "모델번호",
    "height": "키",
    "weight": "몸무게",
    "gender": "성별",
    "age": "나이",
    "shoulder": "어깨사이너비",
    "chest": "젖가슴둘레",
    "waist": "허리둘레",
    "hip": "엉덩이둘레",
    "crotch_height": "샅높이",
    "knee_height": "무릎높이",
}


def _git_text(ref: str, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
    )
    return result.stdout


def _profile_paths(ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", ref, PROFILE_PREFIX],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().endswith("_profile.csv")
    ]


def _read_profile(text: str, path: str) -> dict[str, object]:
    raw = pd.read_csv(io.StringIO(text), header=None)
    headers = [
        str(name).strip() if pd.notna(name) and str(name).strip() else str(fallback).strip()
        for fallback, name in zip(raw.iloc[0], raw.iloc[1], strict=True)
    ]
    values = raw.iloc[2].tolist()
    row = dict(zip(headers, values, strict=True))
    output: dict[str, object] = {}
    for field, column in PROFILE_COLUMNS.items():
        output[field] = row.get(column)
    file_model_no = Path(path).name.removesuffix("_profile.csv")
    output["profile_model_no"] = output["model_no"]
    output["model_no"] = file_model_no
    output["source_profile"] = f"{PROFILE_PREFIX}/{file_model_no}_profile.csv"
    return output


def _normalize_profiles(profiles: list[dict[str, object]]) -> pd.DataFrame:
    frame = pd.DataFrame(profiles)
    frame["model_no"] = frame["model_no"].astype(str).str.strip()
    frame["subject_id"] = frame["model_no"].str.lower().map(lambda value: f"sizkorea_{value}")
    frame["gender"] = frame["gender"].astype(str).str.upper().str.strip()
    numeric = [
        "height",
        "weight",
        "age",
        "shoulder",
        "chest",
        "waist",
        "hip",
        "crotch_height",
        "knee_height",
    ]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce")
    frame["thigh_length"] = frame["crotch_height"] - frame["knee_height"]
    for target in ("calf_length", "torso_length", "leg_length", "neck_length", "thigh_calf_ratio", "torso_leg_ratio"):
        frame[target] = pd.NA
    frame["ground_truth_note"] = (
        "same-image profile has crotch/knee heights for thigh_length; "
        "ankle, shoulder-height-to-pelvis-point, and front-neck landmarks are unavailable"
    )
    columns = [
        "subject_id",
        "model_no",
        "profile_model_no",
        "gender",
        "age",
        "height",
        "weight",
        *TARGETS,
        "crotch_height",
        "knee_height",
        "source_profile",
        "ground_truth_note",
    ]
    return (
        frame[columns]
        .sort_values(["subject_id", "source_profile"])
        .drop_duplicates("subject_id", keep="first")
        .reset_index(drop=True)
    )


def _people_subject_ids() -> set[str]:
    people_dir = DATA / "people"
    if not people_dir.exists():
        return set()
    front = {
        path.stem.replace("_front", "")
        for path in people_dir.glob("*_front.*")
    }
    side = {
        path.stem.replace("_side", "")
        for path in people_dir.glob("*_side.*")
    }
    return front & side


def build_ground_truth(ref: str) -> pd.DataFrame:
    profiles = [_read_profile(_git_text(ref, path), path) for path in _profile_paths(ref)]
    ground_truth = _normalize_profiles(profiles)
    people_subjects = _people_subject_ids()
    if not people_subjects:
        return ground_truth
    return ground_truth[ground_truth["subject_id"].isin(people_subjects)].reset_index(drop=True)


def _prediction_column(target: str) -> str:
    if target.endswith("_ratio"):
        return f"predicted_{target}"
    return f"predicted_{target}"


def evaluate(vlm_path: Path, ground_truth: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    predictions = pd.read_csv(vlm_path)
    merged = predictions.merge(
        ground_truth,
        on="subject_id",
        how="inner",
        suffixes=("", "_actual"),
    )
    metrics = []
    for target in TARGETS:
        predicted_col = _prediction_column(target)
        actual_col = target
        if predicted_col not in merged.columns:
            continue
        valid = merged[[predicted_col, actual_col]].dropna()
        if valid.empty:
            continue
        error = valid[predicted_col] - valid[actual_col]
        metrics.append(
            {
                "target": target,
                "rows": int(len(valid)),
                "mae": float(mean_absolute_error(valid[actual_col], valid[predicted_col])),
                "rmse": float(mean_squared_error(valid[actual_col], valid[predicted_col]) ** 0.5),
                "mean_error": float(error.mean()),
            }
        )
        merged[f"actual_{target}"] = merged[actual_col]
        merged[f"error_{target}"] = merged[predicted_col] - merged[actual_col]
    return merged, pd.DataFrame(metrics)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-ref", default=DEFAULT_PROFILE_REF)
    parser.add_argument(
        "--prediction-dir",
        type=Path,
        default=DATA / "vlm" / "kimi-k2.5",
    )
    args = parser.parse_args()

    raw_dir = DATA / "raw"
    label_dir = DATA / "labels"
    report_dir = DATA / "vlm" / "evaluations"
    raw_dir.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    ground_truth = build_ground_truth(args.profile_ref)
    ground_truth.to_csv(
        raw_dir / "sizekorea_vlm_image_profiles.csv",
        index=False,
        encoding="utf-8-sig",
    )
    ground_truth.to_csv(
        label_dir / "sizekorea_vlm_same_image_ground_truth.csv",
        index=False,
        encoding="utf-8-sig",
    )

    all_metrics = []
    for split in ("validation", "test"):
        path = args.prediction_dir / f"{split}-redefined-11targets" / "aligned_predictions.csv"
        if not path.exists():
            continue
        rows, metrics = evaluate(path, ground_truth)
        rows.to_csv(report_dir / f"{split}_same_image_evaluated.csv", index=False, encoding="utf-8-sig")
        metrics.insert(0, "split", split)
        all_metrics.extend(metrics.to_dict("records"))

    metrics_path = report_dir / "same_image_metrics.json"
    metrics_path.write_text(json.dumps(all_metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"ground truth rows: {len(ground_truth)}")
    print(pd.DataFrame(all_metrics).to_string(index=False))


if __name__ == "__main__":
    main()
