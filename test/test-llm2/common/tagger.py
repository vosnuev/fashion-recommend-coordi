"""편집된 상품 이미지 → Confluence 태그 스키마 프로퍼티 (Gemini structured output).

test-sam/sam3/test_sam3_gemini.py의 GeminiTagger와 같은 방식이지만,
입력이 "세그 크롭"이 아니라 "편집 모델이 생성한 흰 배경 상품 이미지"이고,
스키마가 Confluence 문서(taxonomy.py) 전체 필드(pattern·layer_order 포함)를 따른다.

환경변수:
  GEMINI_API_KEY    (필수, 열거·편집 단계와 공유)
  GEMINI_TAG_MODEL  (기본 gemini-3.5-flash)
"""
from __future__ import annotations

import io
import json
import os

from . import taxonomy as tx

# Gemini structured output 스키마 (OpenAPI subset).
# enum으로 라벨을 강제해 taxonomy 밖의 값이 나오는 것을 막는다.
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
- 스커트는 하의/스커트. 홈웨어·파자마 세트는 usage에 홈웨어 또는 수면 추가.
- fit/sleeve/length는 의류(상의·하의·아우터·원피스/세트)에만, 아니면 null.
  sleeve는 하의에는 null.
- season 유도: 패딩·퍼·울→겨울, 코트→가을·겨울, 반팔·민소매·린넨→여름,
  니트·후드→가을·겨울·간절기, 얇은 아우터→봄·가을·간절기,
  판단이 어려우면 봄·가을·간절기.
- style은 대표 분위기 우선 최대 2개. 애매하면 캐주얼 또는 베이직.
- layer_role/order: 아우터→(아우터,3), 니트 베스트 등 레이어드용 상의→(레이어드 상의,2),
  일반 상의·원피스→(기본 상의,1), 그 외→null.
- usage: 사진만으로 특정 어려우면 ["데일리", "외출"].
- 참고용 검출 라벨 힌트: "{hint}" (참고만 하고 이미지 우선으로 판단)
"""


class ConfluenceTagger:
    def __init__(self) -> None:
        from google import genai  # 지연 import

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY 환경변수가 필요합니다.")
        self.client = genai.Client(api_key=api_key)
        self.model = os.getenv("GEMINI_TAG_MODEL", "gemini-3.5-flash")

    def tag(self, image_bytes: bytes, hint: str, mime: str = "image/png") -> dict:
        from google.genai import types

        resp = self.client.models.generate_content(
            model=self.model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime),
                TAGGING_PROMPT.format(hint=hint),
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=TAG_SCHEMA,
                temperature=0.1,
            ),
        )
        tags = json.loads(resp.text)
        return tx.fix_category_pair(tags)
