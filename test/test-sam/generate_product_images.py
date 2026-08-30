"""items.json을 읽어 상품 원형 복원 이미지를 일괄 생성한다."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = (
    CURRENT_FILE.parent.parent
    if CURRENT_FILE.parent.name == "test"
    else CURRENT_FILE.parent
)

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=False)

from common.product_image_generator import (
    ProductImageGenerator,
    SUPPORTED_MODES,
    SUPPORTED_SIZES,
    build_generated_filename,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "items_json",
        help="기존 분리 파이프라인이 생성한 items.json 경로",
    )
    parser.add_argument(
        "--mode",
        default="ecommerce",
        help="예: ecommerce 또는 ecommerce,showroom",
    )
    parser.add_argument(
        "--size",
        default="auto",
        choices=sorted(SUPPORTED_SIZES),
        help=(
            "auto이면 카테고리에 따라 자동 결정. "
            "하의/원피스/긴 아우터는 1024x1536, 나머지는 1024x1024"
        ),
    )
    parser.add_argument(
        "--background",
        default="opaque",
        choices=["opaque", "transparent", "auto"],
    )
    parser.add_argument(
        "--output-subdir",
        default="generated",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
    )
    parser.add_argument(
        "--rewrite-json",
        action="store_true",
    )
    parser.add_argument(
        "--model",
        default=None,
    )
    parser.add_argument(
        "--only-category",
        default=None,
        help="예: 하의, 신발",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
    )

    return parser.parse_args()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def validate_modes(raw_mode: str) -> list[str]:
    modes = [
        mode.strip()
        for mode in raw_mode.split(",")
        if mode.strip()
    ]

    if not modes:
        raise ValueError("--mode에 하나 이상의 값을 넣어야 합니다.")

    invalid = [
        mode
        for mode in modes
        if mode not in SUPPORTED_MODES
    ]

    if invalid:
        raise ValueError(
            f"지원하지 않는 mode: {invalid}\n"
            f"사용 가능: {sorted(SUPPORTED_MODES)}"
        )

    return modes


def main() -> None:
    args = parse_args()

    if not ENV_PATH.exists():
        print(f"[warning] .env 파일이 없습니다: {ENV_PATH}")

    items_json_path = Path(args.items_json).expanduser().resolve()

    if not items_json_path.exists():
        raise FileNotFoundError(
            f"items.json을 찾을 수 없습니다: {items_json_path}"
        )

    modes = validate_modes(args.mode)
    data = load_json(items_json_path)
    original_items = data.get("items", [])

    selected_items = list(enumerate(original_items))

    if args.only_category:
        selected_items = [
            (index, item)
            for index, item in selected_items
            if item.get("category_large") == args.only_category
        ]

    if args.max_items is not None:
        selected_items = selected_items[:args.max_items]

    base_dir = items_json_path.parent
    generated_root = base_dir / args.output_subdir
    generated_root.mkdir(parents=True, exist_ok=True)

    generator = ProductImageGenerator(
        model=args.model,
        default_size=args.size,
    )

    success_count = 0
    failed_items: list[dict] = []

    for original_index, item in selected_items:
        image_file_name = item.get("_image_file")

        if not image_file_name:
            print(
                f"[skip] item[{original_index}]에 "
                "_image_file이 없습니다."
            )
            continue

        source_image_path = base_dir / image_file_name

        if not source_image_path.exists():
            print(f"[skip] 원본 crop 없음: {source_image_path}")
            continue

        item.setdefault("_generated_images", {})

        for mode in modes:
            mode_dir = generated_root / mode
            mode_dir.mkdir(parents=True, exist_ok=True)

            output_path = mode_dir / build_generated_filename(
                image_file_name=image_file_name,
                mode=mode,
            )

            try:
                print(
                    f"[generate] {source_image_path.name} "
                    f"-> {output_path.name} ({mode})"
                )

                generator.generate_image(
                    reference_image_path=source_image_path,
                    metadata=item,
                    output_path=output_path,
                    mode=mode,
                    size=args.size,
                    background=args.background,
                    overwrite=args.overwrite,
                )

                item["_generated_images"][mode] = os.path.relpath(
                    output_path,
                    base_dir,
                )
                success_count += 1

            except Exception as exc:
                failure = {
                    "item_index": original_index,
                    "image_file": image_file_name,
                    "mode": mode,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                failed_items.append(failure)

                print(
                    f"[failed] {image_file_name} ({mode}): "
                    f"{type(exc).__name__}: {exc}"
                )

    output_json_path = (
        items_json_path
        if args.rewrite_json
        else base_dir / "items_with_generated.json"
    )

    data["_generation_meta"] = {
        "modes": modes,
        "requested_size": args.size,
        "background": args.background,
        "generator": "OpenAI ProductImageGenerator",
        "image_model": generator.model,
        "success_count": success_count,
        "failed_count": len(failed_items),
        "failed_items": failed_items,
    }

    save_json(
        output_json_path,
        data,
    )

    print(f"[done] 성공: {success_count}")
    print(f"[done] 실패: {len(failed_items)}")
    print(f"[done] JSON: {output_json_path}")


if __name__ == "__main__":
    main()
