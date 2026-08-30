import argparse
import base64
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from google import genai
from jinja2 import Environment, StrictUndefined


MODEL_NAME = "gemini-3.5-flash-lite"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "splits" / "vlm"
PROMPT_PATH = PROJECT_ROOT / "prompts" / "body_measurement_prompt.j2"
SCHEMA_PATH = PROJECT_ROOT / "prompts" / "body_measurement_schema.json"
TRAILING_METADATA_COLUMNS = [
    "front_image_path",
    "side_image_path",
    "model",
    "run_name",
    "status",
]


MEASUREMENT_COLUMNS = [
    "subject_id",
    "predicted_chest_cm",
    "predicted_waist_cm",
    "predicted_hip_cm",
    "chest",
    "waist",
    "hip",
    "chest_absolute_error_cm",
    "waist_absolute_error_cm",
    "hip_absolute_error_cm",
]


def order_result_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    preferred = [column for column in MEASUREMENT_COLUMNS if column in dataframe.columns]
    trailing = [
        column for column in TRAILING_METADATA_COLUMNS if column in dataframe.columns
    ]
    middle = [
        column
        for column in dataframe.columns
        if column not in preferred and column not in trailing
    ]
    return dataframe[preferred + middle + trailing]

def load_image_part(image_path: Path) -> dict:
    return {
        "type": "image",
        "data": base64.b64encode(image_path.read_bytes()).decode("utf-8"),
        "mime_type": "image/jpeg",
    }


def render_prompt(row: pd.Series) -> str:
    template = Environment(undefined=StrictUndefined).from_string(
        PROMPT_PATH.read_text(encoding="utf-8")
    )

    return template.render(
        gender=row["gender"],
        height_cm=float(row["height"]),
        weight_kg=float(row["weight"]),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["validation", "test"], required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--run-name", required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="결과 저장 폴더. 생략하면 experiments/vlm/<model>/<split>-<run-name>입니다.",
    )
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY가 없습니다. Infisical 실행 여부를 확인하세요.")

    dataset_path = DATA_DIR / f"{args.split}_set.csv"
    df = pd.read_csv(dataset_path)

    if args.limit:
        df = df.head(args.limit)

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    client = genai.Client(api_key=api_key)

    results_dir = args.output_dir or (
        PROJECT_ROOT
        / "experiments"
        / "vlm"
        / MODEL_NAME
        / f"{args.split}-{args.run_name}"
    )
    results_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for _, row in df.iterrows():
        start_time = time.perf_counter()
        record = {
            "subject_id": row["subject_id"],
            "front_image_path": row["front_image_path"],
            "side_image_path": row["side_image_path"],
            "model": MODEL_NAME,
            "run_name": args.run_name,
            "status": "success",
            "predicted_chest_cm": None,
            "predicted_waist_cm": None,
            "predicted_hip_cm": None,
            "latency_seconds": None,
            "raw_response": None,
            "error_message": None,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        try:
            prompt = render_prompt(row)
            front_path = REPO_ROOT / row["front_image_path"]
            side_path = REPO_ROOT / row["side_image_path"]

            response = client.interactions.create(
                model=MODEL_NAME,
                input=[
                    {"type": "text", "text": prompt},
                    load_image_part(front_path),
                    load_image_part(side_path),
                ],
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": schema,
                },
            )

            prediction = json.loads(response.output_text)
            record["predicted_chest_cm"] = prediction["chest_cm"]
            record["predicted_waist_cm"] = prediction["waist_cm"]
            record["predicted_hip_cm"] = prediction["hip_cm"]
            record["raw_response"] = response.output_text

        except Exception as error:
            record["status"] = "failed"
            record["error_message"] = str(error)

        record["latency_seconds"] = round(time.perf_counter() - start_time, 3)
        results.append(record)
        print(
            f'{record["subject_id"]}: '
            f'{record["status"]} ({record["latency_seconds"]}초)'
        )

    output_path = results_dir / "predictions.csv"
    order_result_columns(pd.DataFrame(results)).to_csv(output_path, index=False)
    print(f"\n결과 저장 완료: {output_path}")


if __name__ == "__main__":
    main()





