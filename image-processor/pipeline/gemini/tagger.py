"""태깅 단계 — 상품 이미지를 Gemini structured output으로 분석 (test-llm2 로직).

입력은 편집(생성) 결과인 흰 배경 상품 이미지. taxonomy enum을 스키마로 강제하고
대분류-소분류 짝은 사후 보정한다.
"""
from __future__ import annotations

import json

import config

from .. import taxonomy as tx
from ..base import ItemTagger
from .client import gemini_client

TAG_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "item_name": {"type": "STRING",
                      "description": "색·핏·소매·소재가 드러나는 자연스러운 한국어 상품명"},
        "category_large": {"type": "STRING", "enum": tx.CATEGORY_LARGE},
        "category_small": {"type": "STRING", "enum": tx.ALL_SMALL},
        "season": {"type": "ARRAY", "items": {"type": "STRING", "enum": tx.SEASONS}},
        "style": {"type": "ARRAY", "items": {"type": "STRING", "enum": tx.STYLES},
                  "description": "최대 2개, 대표 분위기 우선"},
        "color": {"type": "STRING", "enum": tx.COLORS,
                  "description": "대표 색상 1개, 2색 이상 배색이면 멀티"},
        "pattern": {"type": "STRING", "enum": tx.PATTERNS},
        "fit": {"type": "STRING", "enum": tx.FITS, "nullable": True},
        "material": {"type": "STRING", "enum": tx.MATERIALS, "nullable": True},
        "sleeve": {"type": "STRING", "enum": tx.SLEEVES, "nullable": True},
        "length": {"type": "STRING", "enum": tx.LENGTHS, "nullable": True},
        "usage": {"type": "ARRAY", "items": {"type": "STRING"}},
        "layer_role": {"type": "STRING", "enum": tx.LAYER_ROLES, "nullable": True},
        "layer_order": {"type": "INTEGER", "nullable": True},
    },
    "required": ["item_name", "category_large", "category_small", "season",
                 "style", "color", "pattern", "usage"],
}

TAGGING_PROMPT = """\
흰 배경에 놓인 패션 아이템 상품 사진이다. 스키마에 맞춰 태깅하라.

규칙:
- category_small은 반드시 category_large에 속한 소분류만 고른다.
- 니트 베스트(민소매 니트)는 상의/니트/스웨터, 패딩·퍼 베스트는 아우터/베스트.
- fit/sleeve/length는 의류(상의·하의·아우터·원피스/세트)에만, 아니면 null.
  sleeve는 하의에는 null.
- season 유도: 패딩·퍼·울→겨울, 코트→가을·겨울, 반팔·민소매·린넨→여름,
  니트·후드→가을·겨울·간절기, 판단이 어려우면 봄·가을·간절기.
- layer_role/order: 아우터→(아우터,3), 민소매 니트 등 레이어드용 상의→(레이어드 상의,2),
  일반 상의·원피스→(기본 상의,1), 그 외→null.
- usage: 사진만으로 특정 어려우면 ["데일리", "외출"].
"""


class GeminiTagger(ItemTagger):
    def __init__(self, model: str | None = None) -> None:
        self.model = model or config.GEMINI_TAG_MODEL

    def tag(self, product_png: bytes) -> dict:
        from google.genai import types

        resp = gemini_client().models.generate_content(
            model=self.model,
            contents=[types.Part.from_bytes(data=product_png, mime_type="image/png"),
                      TAGGING_PROMPT],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=TAG_SCHEMA,
                temperature=0.1,
            ),
        )
        tags = json.loads(resp.text)
        # null 허용 필드를 콜백 계약(빈 문자열)에 맞게 정규화
        for f in ("fit", "material", "sleeve", "length", "layer_role"):
            if tags.get(f) is None:
                tags[f] = ""
        return tx.fix_category_pair(tags)
