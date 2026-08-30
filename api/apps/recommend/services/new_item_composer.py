"""기존 옷장 아이템에 새 상품을 끌워 넣어 코디하는 Composer."""

from __future__ import annotations

from dataclasses import dataclass

from apps.recommend.services.composer import (
    CompositionEngine,
    CompositionError,
    CompositionPolicy,
)
from apps.recommend.services.item_retriever import ItemRetrievalResult, ItemSource
from apps.recommend.services.outfit_types import CompositionBatch, RecommendationMode


class NewItemCandidateNotFound(CompositionError):
    """예산·이미지 조건을 만족하는 신규 상품 조합이 없는 경우."""


@dataclass(frozen=True)
class NewItemCompositionRequest:
    slot_results: tuple[ItemRetrievalResult, ...]
    composition_count: int = 3
    total_budget: int | None = None
    category_budgets: dict[str, int] | None = None
    require_image: bool = True


class NewItemOutfitComposer:
    """옷장을 우선 활용하되 각 코디에 상품을 최소 1개 포함한다."""

    SOURCE_PRIORITY = (
        ItemSource.WARDROBE,
        ItemSource.PRODUCT,
    )

    def __init__(self, *, engine: CompositionEngine | None = None) -> None:
        self.engine = engine or CompositionEngine()

    def compose(self, request: NewItemCompositionRequest) -> CompositionBatch:
        compositions = self.engine.compose(
            request.slot_results,
            policy=CompositionPolicy(
                mode=RecommendationMode.NEW_ITEM,
                source_priority=self.SOURCE_PRIORITY,
                composition_count=request.composition_count,
                total_budget=request.total_budget,
                category_budgets=request.category_budgets,
                require_image=request.require_image,
                minimum_source_counts=((ItemSource.PRODUCT, 1),),
            ),
        )
        if not compositions:
            raise NewItemCandidateNotFound(
                "새 상품을 포함한 코디를 구성할 수 없습니다."
            )
        return CompositionBatch(
            mode=RecommendationMode.NEW_ITEM,
            compositions=compositions,
        )
