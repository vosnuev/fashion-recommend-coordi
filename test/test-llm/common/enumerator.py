"""아이템 열거(enumeration) 단계 — Gemini 멀티모달 structured output.

end-to-end 테스트에서 SAM3를 쓰지 않으므로, "사진에 어떤 아이템이 있는가"는
LLM(비전)이 판단한다. 편집 모델 5종이 동일한 아이템 목록을 받아야 공정 비교가
되므로, 열거는 이미지당 1회만 수행하고 output/_enumeration/ 에 캐시한다.

환경변수:
  GEMINI_API_KEY   (필수)
  GEMINI_ENUM_MODEL (기본 gemini-3.5-flash — 기존 태깅 파이프라인과 동일 계열)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CATEGORY_LARGE = ["상의", "하의", "아우터", "원피스/세트",
                  "신발", "가방", "액세서리", "언더웨어/이너웨어"]

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
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": ("이 아이템을 가리는 요소 목록 "
                                        "(예: hair, arm, bag, outer jacket). 없으면 빈 배열"),
                    },
                    "view_angle": {
                        "type": "STRING",
                        "enum": ["front", "side", "back", "three-quarter"],
                    },
                    "bbox": {
                        "type": "ARRAY",
                        "items": {"type": "INTEGER"},
                        "description": ("이 아이템의 bounding box "
                                        "[ymin, xmin, ymax, xmax], 0~1000 정규화 좌표"),
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
사진에 보이는 패션 아이템을 빠짐없이 나열하라.

규칙:
- 착용 중이든 놓여 있든, 식별 가능한 모든 의류·신발·가방·액세서리를 포함한다.
- 신발 한 켤레는 1개 아이템으로 센다.
- 같은 종류가 2개 이상이면 각각 별도 아이템으로, descriptor_en으로 구분 가능하게 쓴다.
- 배경 사물(가구, 소품 등)은 제외한다.
- occluded_by: 머리카락·팔·다른 옷·가방 등에 가려진 부분이 있으면 그 원인을 적는다.
"""


class GeminiEnumerator:
    def __init__(self) -> None:
        from google import genai  # 지연 import

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY 환경변수가 필요합니다.")
        self.client = genai.Client(api_key=api_key)
        self.model = os.getenv("GEMINI_ENUM_MODEL", "gemini-3.5-flash")

    def enumerate(self, image_bytes: bytes, mime: str) -> list[dict]:
        from google.genai import types

        resp = self.client.models.generate_content(
            model=self.model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime),
                ENUM_PROMPT,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ENUM_SCHEMA,
                temperature=0.1,
            ),
        )
        items = json.loads(resp.text)["items"]
        for i, it in enumerate(items):
            it["id"] = i
            it.setdefault("occluded_by", [])
        return items


def enumerate_with_cache(
    enumerator: GeminiEnumerator | None,
    image_path: Path,
    image_bytes: bytes,
    mime: str,
    cache_dir: Path,
) -> list[dict]:
    """캐시가 있으면 재사용 (모델 5종이 같은 목록을 공유 + 재실행 시 비용 절약)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{image_path.stem}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))["items"]

    if enumerator is None:
        raise RuntimeError("열거 캐시가 없고 GeminiEnumerator도 없습니다.")
    items = enumerator.enumerate(image_bytes, mime)
    cache_path.write_text(
        json.dumps({"source_image": str(image_path), "model": enumerator.model,
                    "items": items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return items
