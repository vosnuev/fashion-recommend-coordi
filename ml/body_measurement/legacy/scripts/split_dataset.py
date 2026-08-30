from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split


SEED = 42
VALIDATION_COUNT = 36

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent.parent
SOURCE_CSV = PROJECT_ROOT / "data" / "labels" / "sizekorea_vlm_subjects.csv"
OUTPUT_DIR = PROJECT_ROOT / "data" / "splits" / "vlm"


def main() -> None:
    source_df = pd.read_csv(SOURCE_CSV)

    required_columns = [
        "subject_id",
        "gender",
        "height",
        "weight",
        "chest",
        "waist",
        "hip",
        "image_front",
        "image_side",
    ]
    missing_columns = set(required_columns) - set(source_df.columns)
    if missing_columns:
        raise ValueError(f"필수 컬럼이 없습니다: {sorted(missing_columns)}")

    df = source_df.dropna(subset=required_columns).copy()
    df["front_image_path"] = df["image_front"].map(
        lambda filename: f"ml/body_measurement/data/people/{filename}"
    )
    df["side_image_path"] = df["image_side"].map(
        lambda filename: f"ml/body_measurement/data/people/{filename}"
    )

    image_exists = df.apply(
        lambda row: (REPO_ROOT / row["front_image_path"]).is_file()
        and (REPO_ROOT / row["side_image_path"]).is_file(),
        axis=1,
    )
    df = df.loc[image_exists].copy()

    test_df, validation_df = train_test_split(
        df,
        test_size=VALIDATION_COUNT,
        random_state=SEED,
        stratify=df["gender"],
        shuffle=True,
    )

    validation_df = validation_df.sort_values("subject_id").reset_index(drop=True)
    test_df = test_df.sort_values("subject_id").reset_index(drop=True)

    overlap = set(validation_df["subject_id"]) & set(test_df["subject_id"])
    if overlap:
        raise RuntimeError(f"validation/test 중복 대상자가 있습니다: {sorted(overlap)}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    validation_path = OUTPUT_DIR / "validation_set.csv"
    test_path = OUTPUT_DIR / "test_set.csv"

    validation_df.to_csv(validation_path, index=False)
    test_df.to_csv(test_path, index=False)

    print(f"total: {len(df)}")
    print(f"validation: {len(validation_df)} -> {validation_path}")
    print(f"test: {len(test_df)} -> {test_path}")


if __name__ == "__main__":
    main()
