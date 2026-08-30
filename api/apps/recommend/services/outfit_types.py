"""Retriever·Composer·Validator·RenderService가 공유하는 코디 도메인 모델."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from apps.recommend.services.item_retriever import ItemSource


class RecommendationMode(StrEnum):
    """서로 다른 후보 선택 정책을 사용하는 추천 모드."""

    WARDROBE_BASED = "WARDROBE_BASED"
    NEW_ITEM = "NEW_ITEM"


@dataclass(frozen=True)
class OutfitSlot:
    """골든 코디에서 추출한 하나의 필수 또는 선택 슬롯."""

    slot_id: str
    template_point_id: str
    category_large: str
    category_small: str = ""
    layer_role: str = ""
    required: bool = True


@dataclass(frozen=True)
class OutfitItem:
    """슬롯에 최종 선택된 실제 아이템."""

    slot_id: str
    template_point_id: str
    category_large: str
    layer_role: str
    source_type: ItemSource
    source_id: str
    source_collection: str
    point_id: str
    image_ref: str
    price: int | None
    score: float | None
    reasons: tuple[str, ...]
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def is_owned(self) -> bool:
        return self.source_type is ItemSource.WARDROBE

    @property
    def is_purchasable(self) -> bool:
        return self.source_type is ItemSource.PRODUCT

    @property
    def identity(self) -> tuple[str, str, str]:
        return (
            self.source_type.value,
            self.source_collection,
            self.source_id,
        )


@dataclass(frozen=True)
class OutfitComposition:
    """이미지 생성 전에 Validator가 검사할 코디 조합."""

    mode: RecommendationMode
    items: tuple[OutfitItem, ...]
    missing_slot_ids: tuple[str, ...]
    total_product_price: int
    warnings: tuple[str, ...] = ()
    slots: tuple[OutfitSlot, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.missing_slot_ids

    @property
    def owned_count(self) -> int:
        return sum(item.is_owned for item in self.items)

    @property
    def purchasable_count(self) -> int:
        return sum(item.is_purchasable for item in self.items)

    @property
    def goldenset_count(self) -> int:
        return sum(item.source_type is ItemSource.GOLDENSET_ITEM for item in self.items)


@dataclass(frozen=True)
class CompositionBatch:
    """하나의 골든 템플릿에서 파생된 순위별 코디 1~3개."""

    mode: RecommendationMode
    compositions: tuple[OutfitComposition, ...]

    @property
    def complete_compositions(self) -> tuple[OutfitComposition, ...]:
        return tuple(
            composition for composition in self.compositions if composition.complete
        )
