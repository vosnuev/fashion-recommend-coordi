"""사용자가 실제 보유한 옷만 사용하는 옷장 기반 Composer."""

from __future__ import annotations

from dataclasses import dataclass

from apps.recommend.services.composer import CompositionEngine, CompositionPolicy
from apps.recommend.services.item_retriever import ItemRetrievalResult, ItemSource
from apps.recommend.services.outfit_types import CompositionBatch, RecommendationMode


@dataclass(frozen=True)
class WardrobeCompositionRequest:
    slot_results: tuple[ItemRetrievalResult, ...]
    composition_count: int = 3
    require_image: bool = True


class WardrobeOutfitComposer:
    """옷장 후보만 조합하고 부족한 슬롯은 누락으로 반환한다."""

    SOURCE_PRIORITY = (ItemSource.WARDROBE,)

    def __init__(self, *, engine: CompositionEngine | None = None) -> None:
        self.engine = engine or CompositionEngine()

    def compose(self, request: WardrobeCompositionRequest) -> CompositionBatch:
        compositions = self.engine.compose(
            request.slot_results,
            policy=CompositionPolicy(
                mode=RecommendationMode.WARDROBE_BASED,
                source_priority=self.SOURCE_PRIORITY,
                composition_count=request.composition_count,
                require_image=request.require_image,
            ),
        )
        return CompositionBatch(
            mode=RecommendationMode.WARDROBE_BASED,
            compositions=compositions,
        )
