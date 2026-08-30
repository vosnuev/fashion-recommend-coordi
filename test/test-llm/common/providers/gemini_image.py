"""Gemini 이미지 생성 모델 (Nano Banana 계열).

- gemini-3-pro-image      : Pro 티어 (품질 우선, 2026-05-28 GA)
- gemini-3.1-flash-image  : Flash 티어 (속도·비용 우선, 2026-05-28 GA)

주의: 태깅에 쓰는 추론 라인(Gemini 3.x Pro/Flash 텍스트 모델)과 별개 라인이며
"gemini-3.1-pro-image"는 존재하지 않는다.

환경변수:
  GEMINI_API_KEY (필수, 열거 단계와 공유)
"""
from __future__ import annotations

import os

from .base import ImageEditProvider


class _GeminiImageBase(ImageEditProvider):
    required_env = "GEMINI_API_KEY"
    model = ""  # 서브클래스에서 지정

    def __init__(self) -> None:
        from google import genai  # 지연 import

        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    def edit(self, image_bytes: bytes, mime: str, prompt: str,
             item: dict | None = None) -> bytes:
        from google.genai import types

        resp = self.client.models.generate_content(
            model=self.model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )
        candidates = getattr(resp, "candidates", None) or []
        for cand in candidates:
            for part in cand.content.parts:
                inline = getattr(part, "inline_data", None)
                if inline is not None and getattr(inline, "data", None):
                    return inline.data
        raise RuntimeError(
            f"{self.model} 응답에 이미지 파트가 없습니다 "
            f"(text={getattr(resp, 'text', None)!r:.200})"
        )


class GeminiProImageProvider(_GeminiImageBase):
    key = "gemini-3-pro-image"
    model = os.getenv("GEMINI_PRO_IMAGE_MODEL", "gemini-3-pro-image")


class GeminiFlashImageProvider(_GeminiImageBase):
    key = "gemini-3.1-flash-image"
    model = os.getenv("GEMINI_FLASH_IMAGE_MODEL", "gemini-3.1-flash-image")
