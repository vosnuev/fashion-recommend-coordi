"""분리된 의류 crop을 상품 원형 형태로 복원하는 OpenAI 이미지 편집 모듈.

프로젝트 구조:
project/
├─ .env
├─ common/
│  └─ product_image_generator.py
└─ test/
   └─ generate_product_images.py

.env 예시:
OPENAI_API_KEY=sk-...
OPENAI_IMAGE_MODEL=gpt-image-1

지원 모드:
- ecommerce
- ghost_mannequin
- hanger
- showroom
"""
from __future__ import annotations

import base64
import io
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from PIL import Image

try:
    from openai import OpenAI
except ImportError as exc:
    raise ImportError(
        "openai 패키지가 없습니다. "
        "`python -m pip install openai python-dotenv pillow`를 실행하세요."
    ) from exc


def find_project_root() -> Path:
    """common 폴더의 상위 경로를 프로젝트 루트로 사용."""
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = find_project_root()
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=False)


MODE_GUIDE: dict[str, str] = {
    "ecommerce": (
        "Generate a clean e-commerce catalog product photo. "
        "Use a plain white background and a standard front-facing retail presentation."
    ),
    "ghost_mannequin": (
        "Generate a clean ghost-mannequin product photo. "
        "The garment should have natural volume without showing a person or mannequin."
    ),
    "hanger": (
        "Generate a clean retail hanger presentation. "
        "Use a simple hanger and an uncluttered neutral background."
    ),
    "showroom": (
        "Generate a premium showroom product display with restrained soft lighting. "
        "Keep the product fully visible and visually dominant."
    ),
}

SUPPORTED_MODES = set(MODE_GUIDE)

# GPT Image에서 사용할 표준 크기
SUPPORTED_SIZES = {
    "auto",
    "1024x1024",
    "1536x1024",
    "1024x1536",
}


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item is not None)
    return str(value)


def select_output_size(
    category_large: str,
    category_small: str,
    requested_size: str | None,
) -> str:
    """카테고리에 맞춰 출력 비율을 자동 선택.

    - 하의, 원피스/세트: 세로형
    - 코트처럼 긴 아우터: 세로형
    - 상의, 일반 아우터, 신발, 가방, 액세서리: 정사각형
    """
    if requested_size and requested_size != "auto":
        if requested_size not in SUPPORTED_SIZES:
            raise ValueError(
                f"지원하지 않는 이미지 크기입니다: {requested_size}\n"
                f"사용 가능: {sorted(SUPPORTED_SIZES)}"
            )
        return requested_size

    if category_large in {"하의", "원피스/세트"}:
        return "1024x1536"

    if category_large == "아우터" and category_small in {
        "코트",
        "패딩",
    }:
        return "1024x1536"

    return "1024x1024"


class ProductImageGenerator:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        default_size: str = "auto",
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")

        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY를 찾을 수 없습니다.\n"
                f"프로젝트 루트에 .env 파일을 만드세요: {ENV_PATH}\n\n"
                "OPENAI_API_KEY=sk-...\n"
                "OPENAI_IMAGE_MODEL=gpt-image-1"
            )

        self.model = model or os.getenv(
            "OPENAI_IMAGE_MODEL",
            "gpt-image-1",
        )
        self.default_size = default_size
        self.client = OpenAI(api_key=self.api_key)

    def build_prompt(
        self,
        metadata: dict,
        mode: str = "ecommerce",
    ) -> str:
        if mode not in SUPPORTED_MODES:
            raise ValueError(
                f"지원하지 않는 mode입니다: {mode}\n"
                f"사용 가능: {sorted(SUPPORTED_MODES)}"
            )

        category_large = safe_text(metadata.get("category_large"))
        category_small = safe_text(metadata.get("category_small"))
        color = safe_text(metadata.get("color"))
        material = safe_text(metadata.get("material"))
        fit = safe_text(metadata.get("fit"))
        sleeve = safe_text(metadata.get("sleeve"))
        length = safe_text(metadata.get("length"))
        pattern = safe_text(metadata.get("pattern"))
        style = safe_text(metadata.get("style"))
        item_name = safe_text(metadata.get("item_name"))

        attributes: list[str] = []

        if item_name:
            attributes.append(f"item identity: {item_name}")
        if category_small:
            attributes.append(f"product type: {category_small}")
        elif category_large:
            attributes.append(f"product category: {category_large}")
        if color:
            attributes.append(f"color: {color}")
        if material:
            attributes.append(f"material impression: {material}")
        if fit:
            attributes.append(f"fit or silhouette: {fit}")
        if sleeve:
            attributes.append(f"sleeve: {sleeve}")
        if length:
            attributes.append(f"length: {length}")
        if pattern:
            attributes.append(f"pattern: {pattern}")
        if style:
            attributes.append(f"style: {style}")

        attribute_text = "; ".join(attributes) or "visible product details"

        category_guide = self._category_specific_guide(
            category_large=category_large,
            category_small=category_small,
            mode=mode,
        )

        return f"""
Use the provided isolated garment image as the visual reference.

Presentation goal:
{MODE_GUIDE[mode]}

Reconstruction goal:
Transform the worn or posed garment into a normal standalone retail product shape.
Do not preserve the person's pose, crossed limbs, bent joints, body tension, or wearing distortion.

Attributes to preserve:
{attribute_text}

Category-specific instructions:
{category_guide}

Mandatory composition rules:
- The complete product must fit entirely inside the image canvas.
- Show every outer edge of the product.
- Leave generous clean margin above, below, left, and right.
- Do not crop, cut off, zoom into, or place any portion outside the frame.
- Use a centered front-facing catalog composition unless the category instructions say otherwise.
- Keep the item visually separate from the canvas edges.
- Use one product only, except footwear may be shown as one matching pair.
- Do not create duplicate sleeves, legs, shoes, pockets, handles, straps, or accessories.

Mandatory reconstruction rules:
- Preserve the original product identity, category, color, visible construction, seams, pockets, closures, and important design details.
- Remove human pose cues and reconstruct hidden areas conservatively.
- Do not add invented logos, text, graphics, decorations, pockets, or hardware.
- Do not change the item into a different product type.
- The result must look like a real original product photograph, not a person-wearing photo with the person erased.
""".strip()

    def _category_specific_guide(
        self,
        category_large: str,
        category_small: str,
        mode: str,
    ) -> str:
        if category_large in {"상의", "아우터"}:
            return (
                "Reconstruct the garment into a clean unworn front-facing shape. "
                "Both sleeves must be fully visible, naturally extended downward or slightly outward, "
                "and separated from the torso. Do not preserve crossed arms, folded-arm posture, "
                "bent elbows, overlapping sleeves, or compressed body-worn folds. "
                "Show the complete collar or neckline, shoulders, sleeves, cuffs, front closure, "
                "pockets, side seams, and hem. Leave visible white margin around the full garment."
            )

        if category_large == "하의":
            return (
                "Create a full-length front-facing product image of the complete bottom garment. "
                "The full waistband, belt loops, front closure, rise, hip area, pockets, "
                "both complete legs, and both complete hems must be visible. "
                "Both legs must be fully extended, naturally aligned, and approximately symmetrical. "
                "Do not preserve a standing pose, crossed legs, bent knees, or body-worn tension. "
                "Leave generous white space above the waistband and below both hems. "
                "Never crop the waistband or either hem."
            )

        if category_large == "원피스/세트":
            return (
                "Create a full-length front-facing catalog image. "
                "Show the complete neckline, shoulders, sleeves if present, waistline, "
                "full skirt or trouser section, and complete hem. "
                "Keep the entire product inside the portrait canvas with generous margin. "
                "Do not crop the top, sides, or bottom."
            )

        if category_large == "신발":
            return (
                "Present one matching pair of footwear only. "
                "Do not create a third shoe or duplicate either shoe. "
                "Use a clean standard retail arrangement, with both shoes fully visible and separated. "
                "Preserve the toe shape, sole, upper construction, laces or straps, stitching, "
                "and visible design details. Remove distortion caused by being worn."
            )

        if category_large == "가방":
            return (
                "Present one complete bag only. "
                "Show the entire body, all handles or straps, pockets, hardware, and closures. "
                "Do not crop or duplicate straps or handles. Leave generous margin around the bag."
            )

        if category_large == "액세서리":
            return (
                "Present one complete accessory item only. "
                "Remove body-worn pose cues and keep every part of the item fully visible."
            )

        return (
            "Reconstruct the item into a clean standalone retail product form. "
            "Keep the complete item inside the frame with generous margin."
        )

    def generate_image(
        self,
        reference_image_path: str | Path,
        metadata: dict,
        output_path: str | Path,
        mode: str = "ecommerce",
        size: str | None = None,
        background: str = "opaque",
        overwrite: bool = False,
    ) -> Path:
        reference_image_path = Path(reference_image_path)
        output_path = Path(output_path)

        if not reference_image_path.exists():
            raise FileNotFoundError(
                f"참조 의류 이미지를 찾을 수 없습니다: {reference_image_path}"
            )

        if background not in {"opaque", "transparent", "auto"}:
            raise ValueError(
                "background는 opaque, transparent, auto 중 하나여야 합니다."
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)

        if output_path.exists() and not overwrite:
            print(f"[skip] 이미 존재함: {output_path}")
            return output_path

        category_large = safe_text(metadata.get("category_large"))
        category_small = safe_text(metadata.get("category_small"))

        output_size = select_output_size(
            category_large=category_large,
            category_small=category_small,
            requested_size=size or self.default_size,
        )

        prompt = self.build_prompt(
            metadata=metadata,
            mode=mode,
        )

        print(
            f"[image-api] category={category_large}/{category_small}, "
            f"size={output_size}, background={background}"
        )

        with reference_image_path.open("rb") as image_file:
            result = self.client.images.edit(
                model=self.model,
                image=image_file,
                prompt=prompt,
                size=output_size,
                background=background,
            )

        image_bytes = self._extract_image_bytes(result)
        self._save_image_bytes(
            image_bytes=image_bytes,
            output_path=output_path,
        )
        return output_path

    @staticmethod
    def _extract_image_bytes(result: Any) -> bytes:
        if not hasattr(result, "data") or not result.data:
            raise RuntimeError("OpenAI 이미지 응답에 data가 없습니다.")

        item = result.data[0]

        if getattr(item, "b64_json", None):
            return base64.b64decode(item.b64_json)

        if getattr(item, "b64", None):
            return base64.b64decode(item.b64)

        if getattr(item, "url", None):
            raise RuntimeError(
                "이미지 응답이 URL 형태로 반환되었습니다. "
                "현재 코드는 GPT Image의 base64 응답을 기대합니다."
            )

        raise RuntimeError("생성 이미지 데이터를 응답에서 찾지 못했습니다.")

    @staticmethod
    def _save_image_bytes(
        image_bytes: bytes,
        output_path: Path,
    ) -> None:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.save(output_path)


def build_generated_filename(
    image_file_name: str,
    mode: str,
    extension: str = ".png",
) -> str:
    stem = Path(image_file_name).stem
    return f"{stem}_{mode}{extension}"