"""Seedream 5.0 Pro Edit (ByteDance, BytePlus ModelArk).

OpenAI 호환 형태의 REST API. 프롬프트는 600단어 미만 권장.

환경변수:
  ARK_API_KEY       (필수)
  SEEDREAM_MODEL    (기본 seedream-5-0-pro)
  SEEDREAM_BASE_URL (기본 https://ark.ap-southeast.bytepluses.com/api/v3)
  SEEDREAM_SIZE     (기본 2K)
"""
from __future__ import annotations

import base64
import os

import requests

from .base import ImageEditProvider

TIMEOUT_SEC = 300


class SeedreamProvider(ImageEditProvider):
    key = "seedream-5-0-pro"
    required_env = "ARK_API_KEY"

    def __init__(self) -> None:
        self.api_key = os.environ["ARK_API_KEY"]
        self.model = os.getenv("SEEDREAM_MODEL", "seedream-5-0-pro")
        self.base_url = os.getenv(
            "SEEDREAM_BASE_URL", "https://ark.ap-southeast.bytepluses.com/api/v3"
        ).rstrip("/")
        self.size = os.getenv("SEEDREAM_SIZE", "2K")

    def edit(self, image_bytes: bytes, mime: str, prompt: str,
             item: dict | None = None) -> bytes:
        b64 = base64.b64encode(image_bytes).decode()
        resp = requests.post(
            f"{self.base_url}/images/generations",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "prompt": prompt,
                "image": f"data:{mime};base64,{b64}",
                "size": self.size,
                "response_format": "b64_json",
                "watermark": False,
            },
            timeout=TIMEOUT_SEC,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Seedream API {resp.status_code}: {resp.text[:500]}"
            )
        body = resp.json()
        data = body.get("data") or []
        if not data:
            raise RuntimeError(f"Seedream 응답에 data가 없습니다: {body}")
        first = data[0]
        if first.get("b64_json"):
            return base64.b64decode(first["b64_json"])
        if first.get("url"):  # response_format 미지원 시 url 폴백
            img = requests.get(first["url"], timeout=TIMEOUT_SEC)
            img.raise_for_status()
            return img.content
        raise RuntimeError(f"Seedream 응답에서 이미지를 찾지 못했습니다: {first}")
