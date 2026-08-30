"""Fashion VLM 공통 프롬프트 준비와 결과 평가 도구."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import shutil
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent.parent
TAXONOMY_PATH = REPO_ROOT / "api" / "apps" / "wardrobe" / "taxonomy.py"
OUTPUT_FIELDS = (
    "item_name",
    "category_large",
    "category_small",
    "season",
    "style",
    "color",
    "pattern",
    "fit",
    "material",
    "sleeve",
    "length",
    "usage",
    "layer_role",
    "layer_order",
)
SCALAR_FIELDS = (
    "category_large",
    "category_small",
    "color",
    "pattern",
    "fit",
    "material",
    "sleeve",
    "length",
    "layer_role",
    "layer_order",
)
ARRAY_FIELDS = ("season", "style", "usage")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def load_taxonomy():
    """Django를 초기화하지 않고 Wardrobe taxonomy 모듈만 읽는다."""
    spec = importlib.util.spec_from_file_location("wardrobe_taxonomy", TAXONOMY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"taxonomy를 불러올 수 없습니다: {TAXONOMY_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TAXONOMY = load_taxonomy()


def load_dataset(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(
            f"dataset 파일이 없습니다: {path}\n"
            "먼저 `python benchmark.py init`을 실행한 뒤 "
            "dataset.json의 정답을 검수하세요."
        )
    with path.open(encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict) or not isinstance(data.get("samples"), list):
        raise ValueError("dataset은 samples 배열을 가진 JSON 객체여야 합니다.")
    return data


def validate_dataset(
    data: dict[str, Any],
    *,
    image_dir: Path,
    require_images: bool = True,
    expected_count: int | None = None,
) -> list[str]:
    errors: list[str] = []
    samples = data["samples"]
    if expected_count is not None and len(samples) != expected_count:
        errors.append(f"샘플 수가 {expected_count}장이 아닙니다: {len(samples)}장")

    seen_ids: set[str] = set()
    seen_files: set[str] = set()
    for index, sample in enumerate(samples, start=1):
        prefix = f"samples[{index}]"
        if not isinstance(sample, dict):
            errors.append(f"{prefix}: 객체가 아닙니다.")
            continue
        sample_id = str(sample.get("id", "")).strip()
        file_name = str(sample.get("file_name", "")).strip()
        input_mode = str(sample.get("input_mode", "catalog")).strip()
        product_name = str(sample.get("product_name", "")).strip()
        expected = sample.get("expected")

        if not sample_id:
            errors.append(f"{prefix}: id가 비어 있습니다.")
        elif sample_id in seen_ids:
            errors.append(f"{prefix}: 중복 id입니다: {sample_id}")
        seen_ids.add(sample_id)

        if not file_name:
            errors.append(f"{prefix}: file_name이 비어 있습니다.")
        elif Path(file_name).suffix.lower() not in IMAGE_EXTENSIONS:
            errors.append(f"{prefix}: 지원하지 않는 이미지 확장자입니다: {file_name}")
        elif file_name in seen_files:
            errors.append(f"{prefix}: 중복 file_name입니다: {file_name}")
        seen_files.add(file_name)

        if require_images and file_name and not (image_dir / file_name).is_file():
            errors.append(f"{prefix}: 이미지 파일이 없습니다: {image_dir / file_name}")
        if input_mode not in {"catalog", "photo"}:
            errors.append(f"{prefix}: input_mode는 catalog 또는 photo여야 합니다.")
        if input_mode == "catalog" and not product_name:
            errors.append(f"{prefix}: product_name이 비어 있습니다.")
        if not isinstance(expected, dict):
            errors.append(f"{prefix}: expected가 객체가 아닙니다.")
            continue

        missing_fields = [field for field in OUTPUT_FIELDS if field not in expected]
        if missing_fields:
            errors.append(
                f"{prefix}: expected 필드가 누락됐습니다: {', '.join(missing_fields)}"
            )
        for field in taxonomy_errors(expected):
            errors.append(f"{prefix}: expected.{field} 값 또는 형식이 잘못됐습니다.")
    return errors


def build_prompt(product_name: str, input_mode: str = "catalog") -> str:
    def choices(values: Iterable[str]) -> str:
        return ", ".join(values)

    product_context = f"상품명: {product_name}\n" if input_mode == "catalog" else ""
    category_small = "\n".join(
        f"- {large}: {choices(smalls)}"
        for large, smalls in TAXONOMY.CATEGORY_SMALL.items()
    )

    return f"""{product_context}

이미지의 패션 아이템 한 개를 분석하세요.
반드시 아래 목록 안의 값만 사용하세요.

category_large:
{choices(TAXONOMY.CATEGORY_LARGE)}

category_small은 category_large에 속한 값만 선택하세요:
{category_small}

season:
{choices(TAXONOMY.SEASONS)}

style:
{choices(TAXONOMY.STYLES)}

color:
{choices(TAXONOMY.COLORS)}

pattern:
{choices(TAXONOMY.PATTERNS)}

fit:
{choices(TAXONOMY.FITS)}

material:
{choices(TAXONOMY.MATERIALS)}

sleeve:
{choices(TAXONOMY.SLEEVES)}

length:
{choices(TAXONOMY.LENGTHS)}

layer_role:
{choices(TAXONOMY.LAYER_ROLES)}

규칙:
- item_name은 색상·핏·소매·소재가 드러나는 자연스러운 한국어 이름으로 작성합니다.
- season과 style은 배열이며 style은 대표 분위기 우선 최대 2개입니다.
- usage는 자유 문자열 배열이며 데일리, 외출, 출근, 운동, 홈웨어, 수면, 휴양지 같은 기존 프로젝트 표현을 사용합니다.
- fit, material, sleeve, length는 해당 없거나 이미지로 판단할 수 없으면 null입니다.
- 아우터는 layer_role="아우터", layer_order=3입니다.
- 민소매 니트처럼 레이어드용 상의는 layer_role="레이어드 상의", layer_order=2입니다.
- 일반 상의와 원피스는 layer_role="기본 상의", layer_order=1이며 그 외에는 둘 다 null입니다.
- 설명, Markdown 코드 블록, 목록을 덧붙이지 마세요.
- 다음 14개 키를 모두 포함한 JSON 객체 하나만 답하세요.

{{
  "item_name": "",
  "category_large": "",
  "category_small": "",
  "season": [],
  "style": [],
  "color": "",
  "pattern": "",
  "fit": null,
  "material": null,
  "sleeve": null,
  "length": null,
  "usage": [],
  "layer_role": null,
  "layer_order": null
}}"""


def write_prompts(data: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as file:
        for sample in data["samples"]:
            record = {
                "sample_id": sample["id"],
                "input_mode": sample.get("input_mode", "catalog"),
                "file_name": sample["file_name"],
                "product_name": sample["product_name"],
                "prompt": build_prompt(
                    sample["product_name"], sample.get("input_mode", "catalog")
                ),
            }
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


def parse_json_object(raw_output: str) -> dict[str, Any] | None:
    text = raw_output.strip()
    decoder = json.JSONDecoder()
    for position, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[position:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: 잘못된 JSONL: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: JSON 객체가 아닙니다.")
            records.append(value)
    return records


def taxonomy_errors(output: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(output.get("item_name"), str) or not output.get(
        "item_name", ""
    ).strip():
        errors.append("item_name")

    category_large = output.get("category_large")
    category_small = output.get("category_small")
    if category_large not in TAXONOMY.CATEGORY_LARGE:
        errors.append("category_large")
    if not isinstance(category_small, str) or not TAXONOMY.is_valid_pair(
        category_large, category_small
    ):
        errors.append("category_small")

    required_enum = {
        "color": TAXONOMY.COLORS,
        "pattern": TAXONOMY.PATTERNS,
    }
    nullable_enum = {
        "fit": TAXONOMY.FITS,
        "material": TAXONOMY.MATERIALS,
        "sleeve": TAXONOMY.SLEEVES,
        "length": TAXONOMY.LENGTHS,
        "layer_role": TAXONOMY.LAYER_ROLES,
    }
    for field, choices in required_enum.items():
        if output.get(field) not in choices:
            errors.append(field)
    for field, choices in nullable_enum.items():
        if output.get(field) is not None and output.get(field) not in choices:
            errors.append(field)

    for field, choices in (
        ("season", TAXONOMY.SEASONS),
        ("style", TAXONOMY.STYLES),
    ):
        value = output.get(field)
        if not isinstance(value, list) or any(item not in choices for item in value):
            errors.append(field)
    if isinstance(output.get("style"), list) and len(output["style"]) > 2:
        errors.append("style")

    usage = output.get("usage")
    if not isinstance(usage, list) or any(
        not isinstance(item, str) or not item.strip() for item in usage
    ):
        errors.append("usage")

    layer_order = output.get("layer_order")
    if layer_order is not None and (
        isinstance(layer_order, bool)
        or not isinstance(layer_order, int)
        or layer_order not in {1, 2, 3}
    ):
        errors.append("layer_order")
    return errors


def array_metrics(expected: Any, actual: Any) -> dict[str, float | bool]:
    if not isinstance(expected, list) or not isinstance(actual, list):
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "exact": False}
    expected_set = set(expected)
    actual_set = set(actual)
    if not expected_set and not actual_set:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0, "exact": True}
    if not expected_set or not actual_set:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "exact": False}
    intersection = len(expected_set & actual_set)
    precision = intersection / len(actual_set)
    recall = intersection / len(expected_set)
    f1 = 2 * precision * recall / (precision + recall) if intersection else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "exact": expected_set == actual_set,
    }


def score_results(
    dataset: dict[str, Any], result_records: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    expected_by_id = {sample["id"]: sample["expected"] for sample in dataset["samples"]}
    rows: list[dict[str, Any]] = []

    for record in result_records:
        sample_id = record.get("sample_id")
        model = str(record.get("model", "")).strip()
        if sample_id not in expected_by_id:
            raise ValueError(f"결과에 알 수 없는 sample_id가 있습니다: {sample_id!r}")
        if not model:
            raise ValueError(f"{sample_id}: model이 비어 있습니다.")

        parsed = record.get("parsed_output")
        if not isinstance(parsed, dict):
            parsed = parse_json_object(str(record.get("raw_output", "")))
        json_valid = parsed is not None
        parsed = parsed or {}
        schema_complete = json_valid and all(field in parsed for field in OUTPUT_FIELDS)
        invalid_fields = taxonomy_errors(parsed) if json_valid else list(OUTPUT_FIELDS)
        expected = expected_by_id[sample_id]
        scalar_matches = {
            field: json_valid and parsed.get(field) == expected.get(field)
            for field in SCALAR_FIELDS
        }
        array_scores = {
            field: array_metrics(expected.get(field), parsed.get(field))
            for field in ARRAY_FIELDS
        }
        item_name_present = (
            json_valid
            and isinstance(parsed.get("item_name"), str)
            and bool(parsed["item_name"].strip())
        )
        all_fields_match = all(scalar_matches.values()) and all(
            score["exact"] for score in array_scores.values()
        )
        rows.append(
            {
                "model": model,
                "sample_id": sample_id,
                "json_valid": json_valid,
                "schema_complete": schema_complete,
                "taxonomy_valid": json_valid and not invalid_fields,
                "invalid_fields": ",".join(invalid_fields),
                "item_name_present": item_name_present,
                **{
                    f"{field}_match": scalar_matches[field]
                    for field in SCALAR_FIELDS
                },
                **{
                    f"{field}_{metric}": score[metric]
                    for field, score in array_scores.items()
                    for metric in ("precision", "recall", "f1", "exact")
                },
                "all_fields_match": all_fields_match,
                "latency_seconds": record.get("latency_seconds"),
                "peak_vram_mb": record.get("peak_vram_mb"),
                "error": record.get("error") or "",
            }
        )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["model"]].append(row)

    summary: dict[str, Any] = {"models": {}}
    for model, model_rows in sorted(grouped.items()):
        total = len(model_rows)
        latencies = [
            float(row["latency_seconds"])
            for row in model_rows
            if isinstance(row.get("latency_seconds"), (int, float))
        ]
        vrams = [
            float(row["peak_vram_mb"])
            for row in model_rows
            if isinstance(row.get("peak_vram_mb"), (int, float))
        ]
        metrics: dict[str, Any] = {
            "sample_count": total,
            "json_valid_rate": sum(row["json_valid"] for row in model_rows) / total,
            "schema_complete_rate": sum(row["schema_complete"] for row in model_rows)
            / total,
            "taxonomy_valid_rate": sum(row["taxonomy_valid"] for row in model_rows) / total,
            "item_name_nonempty_rate": sum(
                row["item_name_present"] for row in model_rows
            )
            / total,
            "all_fields_accuracy": sum(row["all_fields_match"] for row in model_rows) / total,
            "mean_latency_seconds": statistics.fmean(latencies) if latencies else None,
            "max_peak_vram_mb": max(vrams) if vrams else None,
        }
        for field in SCALAR_FIELDS:
            metrics[f"{field}_accuracy"] = (
                sum(row[f"{field}_match"] for row in model_rows) / total
            )
        for field in ARRAY_FIELDS:
            for metric in ("precision", "recall", "f1"):
                metrics[f"{field}_{metric}"] = statistics.fmean(
                    row[f"{field}_{metric}"] for row in model_rows
                )
            metrics[f"{field}_exact_accuracy"] = (
                sum(row[f"{field}_exact"] for row in model_rows) / total
            )
        summary["models"][model] = metrics
    return rows, summary


def write_evaluation(
    rows: list[dict[str, Any]], summary: dict[str, Any], output_dir: Path
) -> None:
    def percent(value: float) -> str:
        return f"{value * 100:.1f}%"

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    fieldnames = list(rows[0]) if rows else []
    with (output_dir / "details.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(rows)

    lines = [
        "# Fashion VLM 비교 결과",
        "",
        "| 모델 | JSON | 14필드 | Taxonomy | 이름 생성 | 대분류 | 소분류 | 색상 | 패턴 | 핏 | 소재 | 소매 | 기장 | 계절 F1 | 스타일 F1 | 용도 F1 | 레이어 역할 | 레이어 순서 | 완전 일치 | 평균 시간(초) | 최대 VRAM(MB) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model, metrics in summary["models"].items():
        latency = metrics["mean_latency_seconds"]
        vram = metrics["max_peak_vram_mb"]
        lines.append(
            "| {model} | {json_rate} | {schema_rate} | {taxonomy_rate} | {item_name} | "
            "{category_large} | {category_small} | {color} | {pattern} | {fit} | "
            "{material} | {sleeve} | {length} | {season} | {style} | {usage} | "
            "{layer_role} | {layer_order} | {all_fields} | {latency} | {vram} |".format(
                model=model,
                json_rate=percent(metrics["json_valid_rate"]),
                schema_rate=percent(metrics["schema_complete_rate"]),
                taxonomy_rate=percent(metrics["taxonomy_valid_rate"]),
                item_name=percent(metrics["item_name_nonempty_rate"]),
                category_large=percent(metrics["category_large_accuracy"]),
                category_small=percent(metrics["category_small_accuracy"]),
                color=percent(metrics["color_accuracy"]),
                pattern=percent(metrics["pattern_accuracy"]),
                fit=percent(metrics["fit_accuracy"]),
                material=percent(metrics["material_accuracy"]),
                sleeve=percent(metrics["sleeve_accuracy"]),
                length=percent(metrics["length_accuracy"]),
                season=percent(metrics["season_f1"]),
                style=percent(metrics["style_f1"]),
                usage=percent(metrics["usage_f1"]),
                layer_role=percent(metrics["layer_role_accuracy"]),
                layer_order=percent(metrics["layer_order_accuracy"]),
                all_fields=percent(metrics["all_fields_accuracy"]),
                latency=f"{latency:.3f}" if latency is not None else "-",
                vram=f"{vram:.0f}" if vram is not None else "-",
            )
        )
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def command_validate(args: argparse.Namespace) -> None:
    data = load_dataset(Path(args.dataset))
    errors = validate_dataset(
        data,
        image_dir=Path(args.images),
        require_images=not args.allow_missing_images,
    )
    if errors:
        raise SystemExit("데이터셋 검증 실패:\n- " + "\n- ".join(errors))
    print(f"데이터셋 검증 성공: {len(data['samples'])}장")


def command_init(args: argparse.Namespace) -> None:
    source = Path(args.source)
    destination = Path(args.destination)
    if not source.is_file():
        raise FileNotFoundError(f"템플릿 파일이 없습니다: {source}")
    if destination.exists() and not args.force:
        raise SystemExit(
            f"dataset 파일이 이미 있습니다: {destination}\n"
            "기존 정답을 보존하기 위해 중단했습니다. 덮어쓰려면 --force를 사용하세요."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    print(f"dataset 생성 완료: {source} → {destination}")


def command_prepare(args: argparse.Namespace) -> None:
    data = load_dataset(Path(args.dataset))
    errors = validate_dataset(data, image_dir=Path(args.images))
    if errors:
        raise SystemExit("데이터셋 검증 실패:\n- " + "\n- ".join(errors))
    write_prompts(data, Path(args.output))
    print(f"공통 프롬프트 생성 완료: {args.output}")


def command_evaluate(args: argparse.Namespace) -> None:
    dataset = load_dataset(Path(args.dataset))
    records: list[dict[str, Any]] = []
    for result_path in args.results:
        records.extend(load_jsonl(Path(result_path)))
    rows, summary = score_results(dataset, records)
    write_evaluation(rows, summary, Path(args.output_dir))
    print(f"평가 완료: {len(rows)}건 → {args.output_dir}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize = subparsers.add_parser(
        "init", help="예제 정답지를 dataset.json으로 안전하게 복사"
    )
    initialize.add_argument("--source", default=str(ROOT / "dataset.example.json"))
    initialize.add_argument("--destination", default=str(ROOT / "dataset.json"))
    initialize.add_argument("--force", action="store_true")
    initialize.set_defaults(func=command_init)

    validate = subparsers.add_parser("validate", help="이미지와 정답지 검증")
    validate.add_argument("--dataset", default=str(ROOT / "dataset.json"))
    validate.add_argument("--images", default=str(ROOT / "images"))
    validate.add_argument("--allow-missing-images", action="store_true")
    validate.set_defaults(func=command_validate)

    prepare = subparsers.add_parser("prepare", help="모델 공통 prompts.jsonl 생성")
    prepare.add_argument("--dataset", default=str(ROOT / "dataset.json"))
    prepare.add_argument("--images", default=str(ROOT / "images"))
    prepare.add_argument("--output", default=str(ROOT / "prompts.jsonl"))
    prepare.set_defaults(func=command_prepare)

    evaluate = subparsers.add_parser("evaluate", help="모델 결과 비교표 생성")
    evaluate.add_argument("--dataset", default=str(ROOT / "dataset.json"))
    evaluate.add_argument("--results", nargs="+", required=True)
    evaluate.add_argument("--output-dir", default=str(ROOT / "results" / "evaluation"))
    evaluate.set_defaults(func=command_evaluate)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
