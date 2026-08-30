"""열거 단계 — Gemini 비전 structured output (test-llm2 로직 이식).

세그멘테이션 모델 없이 "사진에 어떤 아이템이 있는가"를 LLM이 판단한다.
descriptor_en은 편집 프롬프트에서 대상 아이템을 특정하는 핵심 정보이므로,
다른 아이템과 구분되는 서술을 강제한다.
"""
from __future__ import annotations

import json

import config

from ..base import EnumeratedItem, ItemEnumerator
from ..taxonomy import CATEGORY_LARGE
from .client import gemini_client

ENUM_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "descriptor_en": {
                        "type": "STRING",
                        "description": (
                            "Unambiguous English description of this one item: "
                            "color + garment type + how/where it appears "
                            "(e.g. 'the navy denim jacket worn open over the "
                            "white t-shirt'). Must distinguish it from every "
                            "other item in the photo."
                        ),
                    },
                    "label_ko": {"type": "STRING",
                                 "description": "짧은 한국어 라벨 (예: 네이비 데님 자켓)"},
                    "category_large": {"type": "STRING", "enum": CATEGORY_LARGE},
                    "occluded_by": {
                        "type": "ARRAY", "items": {"type": "STRING"},
                        "description": ("이 아이템을 가리는 요소 목록 "
                                        "(예: hair, arm, bag, outer jacket). 없으면 빈 배열"),
                    },
                    "view_angle": {
                        "type": "STRING",
                        "enum": ["front", "side", "back", "three-quarter"],
                    },
                    "bbox": {
                        "type": "ARRAY", "items": {"type": "INTEGER"},
                        "description": "[ymin, xmin, ymax, xmax], 0~1000 정규화 좌표",
                    },
                },
                "required": ["descriptor_en", "label_ko",
                             "category_large", "view_angle"],
            },
        },
    },
    "required": ["items"],
}

ENUM_PROMPT = """\
사진 속 패션 아이템을 빠짐없이 열거하라.

규칙:
- 착용 중이든 놓여 있든 사진에 보이는 모든 의류·신발·가방·액세서리를 포함한다.
- 같은 종류가 2개 이상이면 각각 별도 아이템으로 나누고 descriptor_en으로 구분한다.
- 신발 한 켤레는 아이템 1개로 센다.
- 배경 소품, 사람 신체, 일부만 보여 종류를 특정할 수 없는 물체는 제외한다.
"""


class GeminiEnumerator(ItemEnumerator):
    def __init__(self, model: str | None = None) -> None:
        self.model = model or config.GEMINI_ENUM_MODEL

    def enumerate(self, image_bytes: bytes, mime: str) -> list[EnumeratedItem]:
        from google.genai import types

        resp = gemini_client().models.generate_content(
            model=self.model,
            contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime),
                      ENUM_PROMPT],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ENUM_SCHEMA,
                temperature=0.1,
            ),
        )
        data = json.loads(resp.text)
        return [
            EnumeratedItem(
                descriptor_en=it["descriptor_en"],
                label_ko=it["label_ko"],
                category_large=it["category_large"],
                occluded_by=it.get("occluded_by") or [],
                view_angle=it.get("view_angle", "front"),
                bbox=it.get("bbox"),
            )
            for it in data.get("items", [])
        ]
