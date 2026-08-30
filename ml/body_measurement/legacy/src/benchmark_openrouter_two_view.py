"""Run and persist one detail CSV per OpenRouter VLM on the 182-person test set."""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import csv
import io
import json
import math
import os
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[3]
DATA_PATH = ROOT / "ml/body_measurement/data/labels/sizekorea_vlm_182_labels.csv"
EXPERIMENTS_DIR = ROOT / "ml/body_measurement/experiments/vlm"
MODELS = {
    "qwen3-vl-8b": "qwen/qwen3-vl-8b-instruct",
    "gemma-3-12b": "google/gemma-3-12b-it",
    "gemini-flash-lite": "google/gemini-2.5-flash-lite",
    "gemini-flash": "google/gemini-2.5-flash",
    "kimi-k2.5": "moonshotai/kimi-k2.5",
    "grok-4.3": "x-ai/grok-4.3",
}
FIELDS = [
    "model", "subject_id", "gender", "age", "height", "weight",
    "front_image_path", "side_image_path", "actual_chest", "pred_chest", "err_chest",
    "actual_waist", "pred_waist", "err_waist", "actual_hip", "pred_hip", "err_hip",
    "latency_sec", "status", "error",
]


def image_url(path: Path) -> str:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((960, 960))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=88, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def request(model: str, row: dict[str, str], images: tuple[str, str]) -> dict[str, object]:
    started = time.perf_counter()
    result: dict[str, object] = {
        "model": model, "subject_id": row["subject_id"], "gender": row["gender"],
        "age": row["age"], "height": row["height"], "weight": row["weight"],
        "front_image_path": row["front_image_path"], "side_image_path": row["side_image_path"], "status": "failed",
    }
    sex = "female" if row["gender"] == "F" else "male"
    prompt = (
        f"Image 1 is FRONT and image 2 is SIDE of the same adult in measurement clothing. "
        f"Known: sex={sex}, height={row['height']}cm, weight={row['weight']}kg. "
        "Estimate breast/chest, natural waist, and maximum hip circumferences in cm. "
        'Return only JSON: {"chest":number,"waist":number,"hip":number}.'
    )
    payload = {
        "model": model, "temperature": 0, "max_tokens": 100, "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": images[0]}},
            {"type": "image_url", "image_url": {"url": images[1]}},
        ]}],
    }
    http_request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}", "Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(http_request, timeout=120) as response:
            content = json.loads(response.read().decode("utf-8"))["choices"][0]["message"]["content"]
        parsed = json.loads(content[content.find("{") : content.rfind("}") + 1])
        for key in ("chest", "waist", "hip"):
            actual, predicted = float(row[key]), float(parsed[key])
            result[f"actual_{key}"] = actual
            result[f"pred_{key}"] = predicted
            result[f"err_{key}"] = abs(actual - predicted)
        result["status"] = "success"
    except urllib.error.HTTPError as error:
        result["error"] = f"HTTP {error.code}: {error.read().decode(errors='replace')[:200]}"
    except Exception as error:
        result["error"] = str(error)[:240]
    result["latency_sec"] = round(time.perf_counter() - started, 3)
    return result


def summary(model_name: str, rows: list[dict[str, object]], detail_path: Path) -> dict[str, object]:
    ok = [row for row in rows if row["status"] == "success"]
    item: dict[str, object] = {"model": model_name, "subjects": len(rows), "success": len(ok), "failed": len(rows) - len(ok), "detail_file": str(detail_path.relative_to(ROOT))}
    for key in ("chest", "waist", "hip"):
        errors = [float(row[f"err_{key}"]) for row in ok]
        item[f"{key}_mae_cm"] = round(statistics.fmean(errors), 3) if errors else ""
        item[f"{key}_rmse_cm"] = round(math.sqrt(statistics.fmean([value * value for value in errors])), 3) if errors else ""
        item[f"{key}_p90_cm"] = round(sorted(errors)[math.ceil(len(errors) * 0.9) - 1], 3) if errors else ""
    item["mean_latency_sec"] = round(statistics.fmean([float(row["latency_sec"]) for row in ok]), 3) if ok else ""
    return item


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=[*MODELS, "all"], default="all")
    parser.add_argument("--limit", type=int, default=182)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY가 필요합니다. infisical run으로 실행하세요.")
    with DATA_PATH.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))[: args.limit]
    encoded = {row["subject_id"]: (image_url(ROOT / row["front_image_path"]), image_url(ROOT / row["side_image_path"])) for row in rows}
    selected = MODELS if args.model == "all" else {args.model: MODELS[args.model]}
    all_summaries = []
    for name, model in selected.items():
        run_dir = EXPERIMENTS_DIR / name / "openrouter-182-test"
        run_dir.mkdir(parents=True, exist_ok=True)
        detail_path = run_dir / "predictions.csv"
        completed = {}
        if detail_path.exists():
            with detail_path.open(encoding="utf-8-sig", newline="") as handle:
                completed = {row["subject_id"]: row for row in csv.DictReader(handle)}
        pending = [row for row in rows if row["subject_id"] not in completed]
        results = list(completed.values())
        print(f"{name}: existing={len(results)}, pending={len(pending)}", flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            for index, result in enumerate(pool.map(lambda row: request(model, row, encoded[row["subject_id"]]), pending), 1):
                results.append(result)
                if index % 10 == 0 or index == len(pending):
                    write_rows(detail_path, results)
                    print(f"{name}: {len(results)}/{len(rows)} saved", flush=True)
        all_summaries.append(summary(name, results, detail_path))
    summary_dir = EXPERIMENTS_DIR / "_summaries" / "openrouter-182-test"
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary_path = summary_dir / "all_models_summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted({key for item in all_summaries for key in item}))
        writer.writeheader()
        writer.writerows(all_summaries)
    print(f"summary: {summary_path}")


if __name__ == "__main__":
    main()
