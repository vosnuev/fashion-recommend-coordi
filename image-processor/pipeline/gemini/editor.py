"""상품 이미지 생성 단계 — Gemini 이미지 편집 (test-llm2 로직 이식).

원본 전체 사진 + 아이템별 프롬프트 → 흰 배경 정면 상품 이미지를 '생성'한다.
크롭이 아니라 재구성이므로 가림 복구·정면화가 가능하지만, 프롬프트에서
로고·디테일 날조 금지를 강제해 hallucination을 억제한다.

교체 지점: SAM3 크롭 방식으로 바꾸려면 ProductImageGenerator를 구현한
별도 클래스를 만들어 factory(pipeline/__init__.py)에 등록하면 된다.
"""
from __future__ import annotations

import config

from ..base import EnumeratedItem, ProductImageGenerator
from .client import gemini_client

# 촬영 각도별 정면화 지시 (test-llm2/common/prompts.py)
_VIEW_NOTE = {
    "front": "The photo already shows the front of the item.",
    "side": "The photo shows the item from the side. Reconstruct the front view.",
    "back": "The photo shows the item from the back. Reconstruct the front view.",
    "three-quarter": (
        "The photo shows the item at a three-quarter angle. "
        "Reconstruct the straight-on front view."
    ),
}


def build_edit_prompt(item: EnumeratedItem) -> str:
    """아이템 1개를 분리·복구·정면화하는 편집 프롬프트.

    주의: "remove the person" 같은 표현은 이미지 API 안전 필터 오탐을
    유발하므로 사람 언급 없이 '상품만 보여달라'로 쓴다 (test-llm2 지식).
    """
    occluded = item.occluded_by
    occlusion_note = (
        "In the source photo, some areas of the item are covered by: "
        + ", ".join(occluded)
        + ". Reconstruct those covered areas conservatively, continuing the "
        "visible color, pattern, material and construction. "
        if occluded
        else "If any area of the item is covered in the source photo, "
        "reconstruct it conservatively from the visible evidence. "
    )
    pair_note = (
        "Footwear must be shown as one matching pair, both shoes fully visible."
        if item.category_large == "신발"
        else "Show exactly one item. Do not duplicate sleeves, legs, straps or parts."
    )
    return (
        "From the provided photo, extract exactly one fashion item: "
        f"{item.descriptor_en}.\n"
        "Create a clean e-commerce catalog product photo of that single item.\n"
        "Rules:\n"
        "- Pure white background. Item centered, entirely inside the frame, "
        "with generous margin on all sides. Never crop any edge of the item.\n"
        f"- Front-facing standard retail presentation. "
        f"{_VIEW_NOTE.get(item.view_angle, _VIEW_NOTE['front'])}\n"
        f"- {occlusion_note}\n"
        "- The output must contain only the product itself on the white "
        "background: no other garments, no accessories that are not the "
        "target item, and nothing else from the source photo.\n"
        "- Do not preserve the on-body drape or posing distortion of the "
        "source photo. Natural unworn retail product shape "
        "(ghost-mannequin style volume is acceptable for clothing).\n"
        "- Preserve the true colors, fabric texture, seams, closures, pockets, "
        "and any real logos or printed graphics exactly as they appear. "
        "Do not invent logos, text, patterns or design details that are not "
        "visible in the photo.\n"
        f"- {pair_note}\n"
        "Output only the edited product image."
    )


class GeminiImageEditor(ProductImageGenerator):
    """gemini-3.1-flash-image (Flash 티어). 모델명은 환경변수로 교체 가능."""

    key = "gemini-image-edit"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or config.GEMINI_IMAGE_MODEL

    def generate(self, image_bytes: bytes, mime: str, item: EnumeratedItem) -> bytes:
        from google.genai import types

        resp = gemini_client().models.generate_content(
            model=self.model,
            contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime),
                      build_edit_prompt(item)],
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )
        for cand in getattr(resp, "candidates", None) or []:
            for part in cand.content.parts:
                inline = getattr(part, "inline_data", None)
                if inline is not None and getattr(inline, "data", None):
                    return inline.data
        raise RuntimeError(
            f"{self.model} 응답에 이미지 파트가 없습니다 "
            f"(text={str(getattr(resp, 'text', None))[:200]!r})"
        )
