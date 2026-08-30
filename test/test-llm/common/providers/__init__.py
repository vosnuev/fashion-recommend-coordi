"""프로바이더 레지스트리.

모델 추가 시 이 목록에만 등록하면 run_all.py가 자동으로 포함한다.
"""
from __future__ import annotations

from .base import ImageEditProvider
from .gemini_image import GeminiFlashImageProvider, GeminiProImageProvider
from .openai_image import GptImageProvider
from .qwen import QwenImageEditProvider
from .seedream import SeedreamProvider

# 순차 실행 순서 그대로의 기본 목록
PROVIDERS: list[type[ImageEditProvider]] = [
    GptImageProvider,
    GeminiProImageProvider,
    GeminiFlashImageProvider,
    SeedreamProvider,
    QwenImageEditProvider,
]

REGISTRY: dict[str, type[ImageEditProvider]] = {p.key: p for p in PROVIDERS}


def resolve(keys: list[str] | None) -> list[type[ImageEditProvider]]:
    """--models 인자(키 목록)를 프로바이더 클래스 목록으로 변환. None이면 전체."""
    if not keys:
        return list(PROVIDERS)
    unknown = [k for k in keys if k not in REGISTRY]
    if unknown:
        raise SystemExit(
            f"알 수 없는 모델 키: {unknown}\n사용 가능: {list(REGISTRY)}"
        )
    return [REGISTRY[k] for k in keys]
