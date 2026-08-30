import argparse
import base64
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from jinja2 import Environment, StrictUndefined


API_URL = "https://openrouter.ai/api/v1/chat/completions"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "splits" / "vlm"
PROMPTS_DIR = PROJECT_ROOT / "prompts"

# 모델 선정 벤치마크에서 반드시 확인하는 핵심 부위.
CORE_TARGETS = ["chest", "waist", "hip"]
# 서빙에서 실제로 쓰는 저장 항목 전체. 팔뚝둘레와 허벅지·종아리 둘레는 제외한다.
RATIO_TARGETS = ["thigh_calf_ratio", "torso_leg_ratio"]
MEASUREMENT_TARGETS = [
    "shoulder",
    *CORE_TARGETS,
    "thigh_length",
    "calf_length",
    "torso_length",
    "leg_length",
    "neck_length",
]
FULL_TARGETS = [
    *MEASUREMENT_TARGETS,
    *RATIO_TARGETS,
]
VLM_RESPONSE_TARGETS = MEASUREMENT_TARGETS
RATIO_REFERENCE_RANGES = {
    "thigh_calf_ratio": (0.506, 1.026),
    "torso_leg_ratio": (0.339, 0.920),
}
GENDER_ALIASES = {
    "M": "male",
    "F": "female",
    "MALE": "male",
    "FEMALE": "female",
    "남": "male",
    "여": "female",
    "남성": "male",
    "여성": "female",
}

# core/full 이름은 기존 실행 명령 호환성을 위해 유지하며, 둘 다 새 11개 스키마를 사용한다.
PROMPT_SETS = {
    "core": {
        "targets": FULL_TARGETS,
        "prompt": PROMPTS_DIR / "body_measurement_prompt.j2",
        "schema": PROMPTS_DIR / "body_measurement_schema.json",
    },
    "full": {
        "targets": FULL_TARGETS,
        "prompt": PROMPTS_DIR / "body_measurement_prompt_full.j2",
        "schema": PROMPTS_DIR / "body_measurement_schema_full.json",
    },
}

TRAILING_METADATA_COLUMNS = [
    "front_image_path",
    "side_image_path",
    "model",
    "run_name",
    "prompt_set",
    "status",
]


def get_column_names(target: str) -> tuple[str, str]:
    if target in RATIO_TARGETS:
        return f"predicted_{target}", f"{target}_absolute_error"
    return f"predicted_{target}_cm", f"{target}_absolute_error_cm"


def measurement_columns() -> list[str]:
    """예측값 → 정답값 → 오차 순으로 보이도록 앞쪽 열 순서를 만든다."""
    pred_cols = []
    err_cols = []
    for target in [*VLM_RESPONSE_TARGETS, *RATIO_TARGETS]:
        p_col, e_col = get_column_names(target)
        pred_cols.append(p_col)
        err_cols.append(e_col)
        
    return [
        "subject_id",
        *pred_cols,
        *FULL_TARGETS,
        *err_cols,
    ]


def order_result_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    preferred = [column for column in measurement_columns() if column in dataframe.columns]
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
    encoded_image = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"},
    }


def render_prompt(row: pd.Series, prompt_path: Path) -> str:
    template = Environment(undefined=StrictUndefined).from_string(
        prompt_path.read_text(encoding="utf-8")
    )
    gender = GENDER_ALIASES.get(str(row["gender"]).strip().upper())
    if gender is None:
        raise ValueError(f"gender는 male 또는 female이어야 합니다: {row['gender']!r}")
    return template.render(
        gender=gender,
        height_cm=float(row["height"]),
        weight_kg=float(row["weight"]),
    )


def parse_prediction(content: str, targets: list[str]) -> dict:
    """응답 JSON에서 부위별 수치를 꺼낸다.
    """
    cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    prediction = json.loads(cleaned)

    missing_keys = [
        f"{target}_cm"
        for target in VLM_RESPONSE_TARGETS
        if f"{target}_cm" not in prediction
    ]
    if missing_keys:
        raise ValueError(f"응답에 필수 키가 없습니다: {missing_keys}")

    result = {}
    for target in VLM_RESPONSE_TARGETS:
        value = prediction.get(f"{target}_cm")
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            result[target] = None
            continue
        result[target] = numeric if math.isfinite(numeric) else None

    if result.get("thigh_length") and result.get("calf_length"):
        result["thigh_calf_ratio"] = round(
            result["thigh_length"] / result["calf_length"], 3
        )
    else:
        result["thigh_calf_ratio"] = None
    if result.get("torso_length") and result.get("leg_length"):
        result["torso_leg_ratio"] = round(
            result["torso_length"] / result["leg_length"], 3
        )
    else:
        result["torso_leg_ratio"] = None

    return result


def request_prediction(
    *, model: str, prompt: str, front_path: Path, side_path: Path, headers: dict
) -> tuple[dict, str]:
    """Retry once with more output space only when the response is truncated."""
    last_response_data = None
    for max_tokens in (256, 512):
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        load_image_part(front_path),
                        load_image_part(side_path),
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
            "reasoning": {"effort": "none"},
            "response_format": {"type": "json_object"},
        }
        response = requests.post(API_URL, headers=headers, json=payload, timeout=120)
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

        response_data = response.json()
        choice = response_data["choices"][0]
        content = choice["message"].get("content")
        last_response_data = response_data
        if content:
            return response_data, content
        if choice.get("finish_reason") != "length":
            break

    return last_response_data, ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--split", choices=["validation", "test"], required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--run-name", required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="결과 저장 폴더. 생략하면 data/vlm/<model>/<split>-<run-name>입니다.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--prompt-set",
        choices=sorted(PROMPT_SETS),
        default="core",
        help=(
            "core/full 모두 새 11개 질문 스키마를 사용합니다."
        ),
    )
    args = parser.parse_args()

    prompt_set = PROMPT_SETS[args.prompt_set]
    targets = prompt_set["targets"]

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY가 없습니다. Infisical 실행 여부를 확인하세요."
        )

    dataset_path = DATA_DIR / f"{args.split}_set.csv"
    df = pd.read_csv(dataset_path)
    if args.limit:
        df = df.head(args.limit)

    schema = json.loads(prompt_set["schema"].read_text(encoding="utf-8"))
    model_file_name = args.model.rsplit("/", maxsplit=1)[-1]
    results_dir = args.output_dir or (
        PROJECT_ROOT
        / "data"
        / "vlm"
        / model_file_name
        / f"{args.split}-{args.run_name}"
    )
    results_dir.mkdir(parents=True, exist_ok=True)
    output_path = results_dir / "predictions.csv"

    results = []
    if args.resume and output_path.exists():
        existing = pd.read_csv(output_path)
        successful = existing[existing["status"] == "success"].copy()
        completed_ids = set(successful["subject_id"])
        results = successful.to_dict("records")
        df = df[~df["subject_id"].isin(completed_ids)].copy()
        print(f"재개: 성공 {len(successful)}명 유지, {len(df)}명 호출 예정")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    for _, row in df.iterrows():
        start_time = time.perf_counter()
        record = {
            "subject_id": row["subject_id"],
            "front_image_path": row["front_image_path"],
            "side_image_path": row["side_image_path"],
            "model": args.model,
            "run_name": args.run_name,
            "prompt_set": args.prompt_set,
            "status": "success",
            **{
                get_column_names(target)[0]: None
                for target in [*VLM_RESPONSE_TARGETS, *RATIO_TARGETS]
            },
            "latency_seconds": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "actual_cost_usd": None,
            "raw_response": None,
            "error_message": None,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        try:
            prompt = render_prompt(row, prompt_set["prompt"])
            prompt += "\n\nRequired JSON schema:\n" + json.dumps(schema)
            front_path = REPO_ROOT / row["front_image_path"]
            side_path = REPO_ROOT / row["side_image_path"]

            response_data, content = request_prediction(
                model=args.model,
                prompt=prompt,
                front_path=front_path,
                side_path=side_path,
                headers=headers,
            )
            record["raw_response"] = content or json.dumps(response_data, ensure_ascii=False)
            if not content:
                raise ValueError("모델이 최종 텍스트 응답을 반환하지 않았습니다.")

            prediction = parse_prediction(content, targets)
            usage = response_data.get("usage", {})
            for target in [*VLM_RESPONSE_TARGETS, *RATIO_TARGETS]:
                p_col, _ = get_column_names(target)
                record[p_col] = prediction[target]
            record["prompt_tokens"] = usage.get("prompt_tokens")
            record["completion_tokens"] = usage.get("completion_tokens")
            record["total_tokens"] = usage.get("total_tokens")
            record["actual_cost_usd"] = usage.get("cost")

        except Exception as error:
            record["status"] = "failed"
            record["error_message"] = str(error)

        record["latency_seconds"] = round(time.perf_counter() - start_time, 3)
        results.append(record)
        order_result_columns(pd.DataFrame(results)).to_csv(output_path, index=False)
        print(
            f'{record["subject_id"]}: '
            f'{record["status"]} ({record["latency_seconds"]}초)'
        )

    print(f"\n결과 저장 완료: {output_path}")

    # 여기서는 호출 직후 응답률만 보여준다. 정확도는 align_vlm_predictions.py와
    # 라벨 CSV와 병합해서 계산한다.
    extra_targets = [t for t in targets if t not in CORE_TARGETS]
    if extra_targets:
        frame = pd.DataFrame(results)
        success = frame[frame["status"] == "success"]
        print(f"\n추가 부위 응답률 (성공 {len(success)}건 기준):")
        for target in extra_targets:
            p_col, _ = get_column_names(target)
            filled = success[p_col].notna().sum()
            print(f"  {target:9s} {filled}/{len(success)}")


if __name__ == "__main__":
    main()






