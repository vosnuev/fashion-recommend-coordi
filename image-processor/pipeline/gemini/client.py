"""Gemini 클라이언트 공유 (열거·편집·태깅이 같은 API 키를 쓴다)."""
from __future__ import annotations

from functools import lru_cache

import config


@lru_cache(maxsize=1)
def gemini_client():
    from google import genai  # 지연 import: 다른 파이프라인 구현 사용 시 불필요

    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY 환경변수가 필요합니다.")
    return genai.Client(api_key=config.GEMINI_API_KEY)
