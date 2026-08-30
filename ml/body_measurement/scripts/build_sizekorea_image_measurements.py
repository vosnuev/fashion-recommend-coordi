"""이미지 파일명 기준으로 SizeKorea 이미지 대상자 실측 원천 컬럼을 복구한다.

git 이력의 ``raw_test_data/*_profile.csv``를 읽되 checkout은 하지 않는다.
현재 ``data/people/sizkorea_f010_front.jpg`` 같은 이미지 파일명과 맞는
대상자만 남기고, 새 길이 지표 계산에 필요한 원천 컬럼만 저장한다.
"""

from __future__ import annotations

import argparse
import io
import subprocess
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_PROFILE_REF = "fd1f6d3^"
PROFILE_PREFIX = "ml/body_measurement/data/raw_test_data"

PROFILE_COLUMNS = {
    "profile_model_no": "모델번호",
    "gender": "성별",
    "age": "나이",
    "height_cm": "키",
    "weight_kg": "몸무게",
    "shoulder_width_cm": "어깨사이너비",
    "chest_circumference_cm": "젖가슴둘레",
    "waist_circumference_cm": "허리둘레",
    "hip_circumference_cm": "엉덩이둘레",
    "crotch_height_cm": "샅높이",
    "knee_height_cm": "무릎높이",
    "lateral_malleolus_height_cm": "가쪽복사높이",
    "jaw_height_cm": "턱끝높이",
    "front_neck_height_cm": "목앞높이",
}

OUTPUT_COLUMNS = [
    "subject_id",
    "image_id",
    "model_no",
    "profile_model_no",
    "front_image_name",
    "side_image_name",
    "front_image_path",
    "side_image_path",
    "source_profile",
    "gender",
    "age",
    "height_cm",
    "weight_kg",
    "shoulder_width_cm",
    "chest_circumference_cm",
    "waist_circumference_cm",
    "hip_circumference_cm",
    "crotch_height_cm",
    "knee_height_cm",
    "lateral_malleolus_height_cm",
    "jaw_height_cm",
    "front_neck_height_cm",
]


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


def _profile_row(text: str) -> dict[str, object]:
    raw = pd.read_csv(io.StringIO(text), header=None)
    headers = [
        str(name).strip() if pd.notna(name) and str(name).strip() else str(fallback).strip()
        for fallback, name in zip(raw.iloc[0], raw.iloc[1], strict=True)
    ]
    return dict(zip(headers, raw.iloc[2].tolist(), strict=True))


def _people_pairs() -> dict[str, dict[str, str]]:
    people_dir = DATA / "people"
    front = {path.stem.removesuffix("_front"): path.name for path in people_dir.glob("*_front.*")}
    side = {path.stem.removesuffix("_side"): path.name for path in people_dir.glob("*_side.*")}
    pairs = {}
    for subject_id in sorted(set(front) & set(side)):
        pairs[subject_id] = {
            "front_image_name": front[subject_id],
            "side_image_name": side[subject_id],
            "front_image_path": f"ml/body_measurement/data/people/{front[subject_id]}",
            "side_image_path": f"ml/body_measurement/data/people/{side[subject_id]}",
        }
    return pairs


def build_image_measurements(ref: str) -> pd.DataFrame:
    people_pairs = _people_pairs()
    rows = []
    for path in _profile_paths(ref):
        model_no = Path(path).name.removesuffix("_profile.csv")
        image_id = model_no.lower()
        subject_id = f"sizkorea_{image_id}"
        if subject_id not in people_pairs:
            continue

        source = _profile_row(_git_text(ref, path))
        row: dict[str, object] = {
            "subject_id": subject_id,
            "image_id": image_id,
            "model_no": model_no,
            "source_profile": f"{PROFILE_PREFIX}/{model_no}_profile.csv",
            **people_pairs[subject_id],
        }
        for output_column, profile_column in PROFILE_COLUMNS.items():
            row[output_column] = source.get(profile_column)
        rows.append(row)

    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    frame["gender"] = frame["gender"].astype(str).str.upper().str.strip()
    numeric_columns = [
        column
        for column in OUTPUT_COLUMNS
        if column.endswith("_cm") or column in {"age", "weight_kg"}
    ]
    frame[numeric_columns] = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
    return frame[OUTPUT_COLUMNS].sort_values("subject_id").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-ref", default=DEFAULT_PROFILE_REF)
    parser.add_argument(
        "--output",
        type=Path,
        default=DATA / "raw" / "sizekorea_image_measurement_sources.csv",
    )
    args = parser.parse_args()

    frame = build_image_measurements(args.profile_ref)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False, encoding="utf-8-sig")

    print(f"rows: {len(frame)}")
    print(f"columns: {len(frame.columns)}")
    print(f"output: {args.output}")
    print("non-null source columns:")
    for column in OUTPUT_COLUMNS:
        print(f"  {column}: {int(frame[column].notna().sum())}")


if __name__ == "__main__":
    main()
