"""사진 파일이 실제로 있는 prediction 행만 보관한다."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PEOPLE = ROOT / "data" / "people"
PREDICTIONS = ROOT / "data" / "hist" / "predictions"


def main() -> None:
    front_ids = {p.name.removesuffix("_front.jpg") for p in PEOPLE.glob("*_front.jpg")}
    side_ids = {p.name.removesuffix("_side.jpg") for p in PEOPLE.glob("*_side.jpg")}
    complete_ids = front_ids & side_ids

    for path in sorted(PREDICTIONS.glob("*.csv")):
        frame = pd.read_csv(path)
        id_column = "source_id" if "source_id" in frame.columns else "subject_id"
        filtered = frame[frame[id_column].astype(str).isin(complete_ids)].copy()
        if filtered.empty:
            path.unlink()
            print(f"removed {path.name}: no matching front/side image")
            continue

        if "subject_id" not in filtered.columns:
            filtered.insert(0, "subject_id", filtered[id_column])
        if "source_id" not in filtered.columns:
            filtered.insert(0, "source_id", filtered["subject_id"])
        filtered["front_image_path"] = filtered["source_id"].map(
            lambda value: f"ml/body_measurement/data/people/{value}_front.jpg"
        )
        filtered["side_image_path"] = filtered["source_id"].map(
            lambda value: f"ml/body_measurement/data/people/{value}_side.jpg"
        )
        filtered.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"filtered {path.name}: {len(frame)} -> {len(filtered)}")


if __name__ == "__main__":
    main()
