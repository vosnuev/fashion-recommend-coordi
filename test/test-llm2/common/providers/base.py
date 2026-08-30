"""이미지 편집 프로바이더 공통 인터페이스."""
from __future__ import annotations

from abc import ABC, abstractmethod


class ImageEditProvider(ABC):
    """전체 사진 + 프롬프트 → 편집된 상품 이미지(bytes, PNG/JPEG)."""

    #: output 폴더명 겸 리포트 표기용 키
    key: str = ""
    #: 필요한 API 키 환경변수 이름 (가용성 체크용)
    required_env: str = ""

    @abstractmethod
    def edit(self, image_bytes: bytes, mime: str, prompt: str,
             item: dict | None = None) -> bytes:
        """편집 이미지를 bytes로 반환. 실패 시 예외를 던진다.

        item: enumerator가 만든 아이템 dict (bbox 등).
              프로바이더별 폴백 전략에 선택적으로 사용한다.
        """

    @classmethod
    def available(cls) -> bool:
        import os

        return bool(os.environ.get(cls.required_env))
