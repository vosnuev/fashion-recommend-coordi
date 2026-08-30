"""Qwen3-VL로 Fashion VLM 공통 프롬프트를 실행해 JSONL 결과를 만든다."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import torch
from transformers import AutoProcessor, Qwen3VLForConditionalGeneration


ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = "Qwen/Qwen3-VL-8B-Instruct"


def load_prompts(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: 잘못된 JSONL입니다.") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number}: JSON 객체가 아닙니다.")
            records.append(record)
    return records


def run(args: argparse.Namespace) -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU를 사용할 수 없습니다.")

    samples = load_prompts(Path(args.prompts))
    image_dir = Path(args.images)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"모델 로딩: {args.model}")
    processor = AutoProcessor.from_pretrained(
        args.model,
        min_pixels=args.min_pixels,
        max_pixels=args.max_pixels,
    )
    model = Qwen3VLForConditionalGeneration.from_pretrained(
        args.model,
        dtype=torch.bfloat16,
        device_map="auto",
        low_cpu_mem_usage=True,
        attn_implementation="sdpa",
    )
    model.eval()

    with output_path.open("w", encoding="utf-8") as output_file:
        for index, sample in enumerate(samples, start=1):
            sample_id = str(sample["sample_id"])
            image_path = (image_dir / str(sample["file_name"])).resolve()
            raw_output = ""
            latency_seconds: float | None = None
            peak_vram_mb: int | None = None
            error: str | None = None
            model_inputs = None
            generated_ids = None

            try:
                if not image_path.is_file():
                    raise FileNotFoundError(f"이미지가 없습니다: {image_path}")

                messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "url": str(image_path)},
                            {"type": "text", "text": sample["prompt"]},
                        ],
                    }
                ]
                model_inputs = processor.apply_chat_template(
                    messages,
                    tokenize=True,
                    add_generation_prompt=True,
                    return_dict=True,
                    return_tensors="pt",
                )
                model_inputs = {
                    key: value.to(model.device) if hasattr(value, "to") else value
                    for key, value in model_inputs.items()
                }

                torch.cuda.empty_cache()
                torch.cuda.reset_peak_memory_stats()
                torch.cuda.synchronize()
                started_at = time.perf_counter()

                with torch.inference_mode():
                    generated_ids = model.generate(
                        **model_inputs,
                        max_new_tokens=args.max_new_tokens,
                        do_sample=False,
                    )

                torch.cuda.synchronize()
                latency_seconds = round(time.perf_counter() - started_at, 3)
                peak_vram_mb = round(
                    torch.cuda.max_memory_allocated() / 1024**2
                )
                input_length = model_inputs["input_ids"].shape[1]
                generated_only = generated_ids[:, input_length:]
                raw_output = processor.batch_decode(
                    generated_only,
                    skip_special_tokens=True,
                    clean_up_tokenization_spaces=False,
                )[0].strip()
            except Exception as exc:  # 이미지별 오류를 남기고 다음 샘플을 계속한다.
                error = f"{type(exc).__name__}: {exc}"

            result = {
                "sample_id": sample_id,
                "model": args.model,
                "raw_output": raw_output,
                "latency_seconds": latency_seconds,
                "peak_vram_mb": peak_vram_mb,
                "error": error,
            }
            output_file.write(json.dumps(result, ensure_ascii=False) + "\n")
            output_file.flush()

            print(f"[{index}/{len(samples)}] {sample_id}")
            print(raw_output if raw_output else f"ERROR: {error}")
            print(f"시간={latency_seconds}초, VRAM={peak_vram_mb}MB")

            del model_inputs, generated_ids
            torch.cuda.empty_cache()

    print(f"결과 저장 완료: {output_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompts", default=str(ROOT / "prompts.jsonl"))
    parser.add_argument("--images", default=str(ROOT / "images"))
    parser.add_argument(
        "--output", default=str(ROOT / "results" / "qwen_14field.jsonl")
    )
    parser.add_argument("--max-new-tokens", type=int, default=768)
    parser.add_argument("--min-pixels", type=int, default=256 * 28 * 28)
    parser.add_argument("--max-pixels", type=int, default=1024 * 28 * 28)
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())
