"""파이프라인 컴포넌트 인터페이스 (전략 패턴).

코어 흐름: 열거 → 상품 이미지 생성 → 태깅 → 임베딩.
각 단계는 ABC로 추상화되어 있어 구현 교체가 자유롭다:
- 상품 이미지 생성을 SAM3 크롭 방식으로 → ProductImageGenerator 구현체 교체
- 태깅 LLM을 다른 API로 → ItemTagger 구현체 교체
- 열거를 검출 모델(bbox) 기반으로 → ItemEnumerator 구현체 교체
조립은 pipeline/__init__.py 의 build_pipeline()이 담당한다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class EnumeratedItem:
    """열거 단계가 찾아낸 사진 속 아이템 1개."""

    descriptor_en: str            # 다른 아이템과 구분되는 영어 서술 (편집 프롬프트용)
    label_ko: str                 # 짧은 한국어 라벨
    category_large: str
    occluded_by: list[str] = field(default_factory=list)
    view_angle: str = "front"     # front | side | back | three-quarter
    bbox: list[int] | None = None  # [ymin, xmin, ymax, xmax] 0~1000 정규화

    def meta(self) -> dict:
        return {
            "descriptor_en": self.descriptor_en,
            "label_ko": self.label_ko,
            "category_large": self.category_large,
            "occluded_by": self.occluded_by,
            "view_angle": self.view_angle,
            "bbox": self.bbox,
        }


@dataclass
class ProcessedItem:
    """파이프라인 최종 산출물 — 아이템 1개의 상품 이미지 + 태그 + 벡터."""

    index: int
    enum: EnumeratedItem
    image_png: bytes | None = None            # 흰 배경 상품 이미지
    tags: dict | None = None                  # taxonomy 스키마 태그
    image_vector: list[float] = field(default_factory=list)
    text_vector: list[float] = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    error: str | None = None                  # 단계 실패 시 기록 (None이면 성공)

    @property
    def ok(self) -> bool:
        return self.error is None and self.image_png is not None and self.tags is not None


class RetryablePipelineError(RuntimeError):
    pass


class ItemEnumerator(ABC):
    """원본 사진 → 아이템 목록."""

    @abstractmethod
    def enumerate(self, image_bytes: bytes, mime: str) -> list[EnumeratedItem]: ...


class ProductImageGenerator(ABC):
    """원본 사진 + 아이템 정보 → 흰 배경 상품 이미지(PNG bytes).

    생성형(Gemini 이미지 편집)이든 크롭형(SAM3 세그+합성)이든
    '아이템 1개짜리 흰 배경 이미지 bytes'라는 계약만 지키면 된다.
    """

    #: manifest에 기록할 구현 식별자
    key: str = ""

    @abstractmethod
    def generate(self, image_bytes: bytes, mime: str, item: EnumeratedItem) -> bytes: ...


class ItemTagger(ABC):
    """상품 이미지 → taxonomy 스키마 태그 dict."""

    @abstractmethod
    def tag(self, product_png: bytes) -> dict: ...


class Embedder(ABC):
    """상품 이미지·캡션 → 검색용 벡터. 비활성화 구현(NullEmbedder)도 허용."""

    version: str = ""

    @abstractmethod
    def embed_image(self, product_png: bytes) -> list[float]: ...

    @abstractmethod
    def embed_text(self, caption: str) -> list[float]: ...
