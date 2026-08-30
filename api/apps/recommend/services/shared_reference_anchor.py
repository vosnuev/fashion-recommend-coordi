"""공유 옷 검색 결과를 골든 템플릿의 고정 슬롯 아이템으로 변환한다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from apps.recommend.services.item_retriever import (
    ItemCandidate,
    ItemRetrievalResult,
    ItemSource,
)
from apps.recommend.services.outfit_types import OutfitComposition, RecommendationMode
from apps.recommend.services.shared_reference_loader import (
    SharedReferenceSearchBasis,
    SharedReferenceVectorLoader,
    StageTimingObserver,
    measure_reference_stage,
)
from apps.recommend.services.shared_reference_product_search import (
    SharedReferenceProductSearcher,
    SharedReferenceProductSearchRequest,
    SimilarProductCandidate,
)
from apps.recommend.services.shared_reference_style_fallback import (
    SharedReferenceStyleFallbackSearcher,
    StyleFallbackRequest,
    StyleSimilarCandidate,
)
from apps.recommend.services.shared_reference_visual_search import (
    SharedReferenceWardrobeVisualSearcher,
    WardrobeVisualCandidate,
    WardrobeVisualSearchRequest,
)


class SharedReferenceAnchorError(RuntimeError):
    """공유 레퍼런스를 최종 코디의 고정 아이템으로 만들 수 없는 경우."""

    code = "SHARED_REFERENCE_ANCHOR_FAILED"


class SharedReferenceAnchorInvalid(SharedReferenceAnchorError):
    code = "SHARED_REFERENCE_ANCHOR_INVALID"


class SharedReferenceAnchorNotFound(SharedReferenceAnchorError):
    code = "SHARED_REFERENCE_ANCHOR_NOT_FOUND"


class SharedReferenceTemplateSlotNotFound(SharedReferenceAnchorError):
    code = "SHARED_REFERENCE_TEMPLATE_SLOT_NOT_FOUND"


@dataclass(frozen=True)
class PinnedReferenceAnchor:
    reference: SharedReferenceSearchBasis
    candidate: ItemCandidate
    match_type: str

    @property
    def identity(self) -> tuple[str, str, str]:
        return (
            self.candidate.source_type.value,
            self.candidate.source_collection,
            self.candidate.source_id,
        )


def _payload_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _wardrobe_visual_candidate(
    reference: SharedReferenceSearchBasis,
    candidate: WardrobeVisualCandidate,
) -> ItemCandidate:
    return ItemCandidate(
        point_id=candidate.point_id,
        source_type=ItemSource.WARDROBE,
        source_id=candidate.wardrobe_item_id,
        source_collection=reference.collection_name,
        score=candidate.visual_score,
        reasons=(
            "공유 옷을 참고한 고정 아이템",
            f"공유 옷 이미지 유사도: {candidate.visual_score:.4f}",
        ),
        payload={
            "item_id": candidate.wardrobe_item_id,
            "s3_key": candidate.image_s3_key,
            "embedding_version": candidate.embedding_version,
            "category_large": candidate.category_large,
            "category_small": candidate.category_small,
            "layer_role": candidate.layer_role,
            "style": list(candidate.style),
            "color": candidate.color,
            "confirmed": True,
            "match_type": candidate.match_type,
            "selection_role": "PINNED_REFERENCE_ANCHOR",
        },
    )


def _wardrobe_style_candidate(
    reference: SharedReferenceSearchBasis,
    candidate: StyleSimilarCandidate,
) -> ItemCandidate:
    return ItemCandidate(
        point_id=candidate.point_id,
        source_type=ItemSource.WARDROBE,
        source_id=candidate.wardrobe_item_id,
        source_collection=reference.collection_name,
        score=candidate.style_score,
        reasons=(
            "공유 옷을 참고한 고정 아이템",
            *candidate.evidence,
        ),
        payload={
            "item_id": candidate.wardrobe_item_id,
            "s3_key": candidate.image_s3_key,
            "embedding_version": candidate.embedding_version,
            "category_large": candidate.category_large,
            "category_small": candidate.category_small,
            "layer_role": candidate.layer_role,
            "style": list(candidate.style),
            "color": candidate.color,
            "fit": candidate.fit,
            "material": candidate.material,
            "confirmed": True,
            "match_type": candidate.match_type,
            "selection_role": "PINNED_REFERENCE_ANCHOR",
        },
    )


def _product_candidate(candidate: SimilarProductCandidate) -> ItemCandidate:
    return ItemCandidate(
        point_id=candidate.point_id,
        source_type=ItemSource.PRODUCT,
        source_id=candidate.external_product_id,
        source_collection=candidate.source_collection,
        score=candidate.visual_score,
        reasons=(
            "공유 옷을 참고한 고정 신규 상품",
            f"공유 옷 이미지 유사도: {candidate.visual_score:.4f}",
        ),
        payload={
            "source": candidate.source,
            "external_product_id": candidate.external_product_id,
            "title": candidate.title,
            "brand": candidate.brand,
            "mall_name": candidate.mall_name,
            "link": candidate.link,
            "image_url": candidate.image_url,
            "image_s3_key": candidate.image_s3_key,
            "price": candidate.price,
            "category_large": candidate.category_large,
            "category_small": candidate.category_small,
            "layer_role": candidate.layer_role,
            "tagging_status": candidate.tagging_status,
            "sale_status": candidate.sale_status,
            "match_type": candidate.match_type,
            "selection_role": "PINNED_REFERENCE_ANCHOR",
        },
    )


class SharedReferenceAnchorResolver:
    """실행 스냅샷을 모드별 검색기로 보내 단 하나의 anchor를 선택한다."""

    def __init__(
        self,
        *,
        loader: SharedReferenceVectorLoader | None = None,
        visual_searcher: SharedReferenceWardrobeVisualSearcher | None = None,
        style_searcher: SharedReferenceStyleFallbackSearcher | None = None,
        product_searcher: SharedReferenceProductSearcher | None = None,
    ) -> None:
        self.loader = loader or SharedReferenceVectorLoader()
        self.visual_searcher = visual_searcher or (
            SharedReferenceWardrobeVisualSearcher()
        )
        self.style_searcher = style_searcher or SharedReferenceStyleFallbackSearcher()
        self.product_searcher = product_searcher or SharedReferenceProductSearcher()

    def resolve(
        self,
        *,
        snapshot: Mapping[str, Any],
        mode: RecommendationMode,
        user_id: int | None,
        total_budget: int | None,
        category_budgets: Mapping[str, int],
        stage_observer: StageTimingObserver | None = None,
    ) -> PinnedReferenceAnchor:
        reference = self.loader.load(snapshot, stage_observer=stage_observer)
        if mode is RecommendationMode.WARDROBE_BASED:
            return self._wardrobe_anchor(
                reference=reference,
                user_id=user_id,
                stage_observer=stage_observer,
            )
        if mode is RecommendationMode.NEW_ITEM:
            return self._product_anchor(
                reference=reference,
                total_budget=total_budget,
                category_budgets=category_budgets,
                stage_observer=stage_observer,
            )
        raise SharedReferenceAnchorInvalid("지원하지 않는 추천 모드입니다.")

    def _wardrobe_anchor(
        self,
        *,
        reference: SharedReferenceSearchBasis,
        user_id: int | None,
        stage_observer: StageTimingObserver | None,
    ) -> PinnedReferenceAnchor:
        if user_id is None:
            raise SharedReferenceAnchorInvalid(
                "내 옷 기반 공유 레퍼런스 추천에는 회원 user_id가 필요합니다."
            )
        with measure_reference_stage(stage_observer, "SIMILAR_SEARCH"):
            visual = self.visual_searcher.search(
                WardrobeVisualSearchRequest(reference=reference, user_id=user_id)
            )
            if visual.candidates:
                selected = visual.candidates[0]
                return PinnedReferenceAnchor(
                    reference=reference,
                    candidate=_wardrobe_visual_candidate(reference, selected),
                    match_type=selected.match_type,
                )

            fallback = self.style_searcher.search(
                StyleFallbackRequest(
                    reference=reference,
                    visual_result=visual,
                    user_id=user_id,
                )
            )
            if not fallback.candidates:
                raise SharedReferenceAnchorNotFound(
                    "공유 옷과 같은 슬롯에서 유사한 내 옷을 찾지 못했습니다."
                )
            selected = fallback.candidates[0]
            return PinnedReferenceAnchor(
                reference=reference,
                candidate=_wardrobe_style_candidate(reference, selected),
                match_type=selected.match_type,
            )

    def _product_anchor(
        self,
        *,
        reference: SharedReferenceSearchBasis,
        total_budget: int | None,
        category_budgets: Mapping[str, int],
        stage_observer: StageTimingObserver | None,
    ) -> PinnedReferenceAnchor:
        with measure_reference_stage(stage_observer, "SIMILAR_SEARCH"):
            result = self.product_searcher.search(
                SharedReferenceProductSearchRequest(
                    reference=reference,
                    total_budget=total_budget,
                    category_budgets=category_budgets,
                )
            )
            if result.selected_anchor is None:
                raise SharedReferenceAnchorNotFound(
                    "예산 안에서 판매 중인 유사 상품을 찾지 못했습니다."
                )
            return PinnedReferenceAnchor(
                reference=reference,
                candidate=_product_candidate(result.selected_anchor),
                match_type=result.selected_anchor.match_type,
            )


def pin_reference_anchor(
    anchor: PinnedReferenceAnchor,
    slot_results: tuple[ItemRetrievalResult, ...],
) -> tuple[ItemRetrievalResult, ...]:
    """레퍼런스와 가장 정확히 맞는 템플릿 슬롯 하나에 anchor를 고정한다."""

    reference = anchor.reference.tags
    matches = [
        (index, result)
        for index, result in enumerate(slot_results)
        if _payload_text(result.template.payload, "category_large")
        == reference.category_large
        and _payload_text(result.template.payload, "layer_role").casefold()
        == reference.layer_role.casefold()
    ]
    if not matches:
        raise SharedReferenceTemplateSlotNotFound(
            "골든 템플릿에 공유 옷과 같은 대분류·슬롯이 없습니다."
        )

    exact_small = [
        row
        for row in matches
        if reference.category_small
        and _payload_text(row[1].template.payload, "category_small")
        == reference.category_small
    ]
    selected_index = (exact_small or matches)[0][0]
    return tuple(
        replace(result, pinned_candidate=anchor.candidate)
        if index == selected_index
        else result
        for index, result in enumerate(slot_results)
    )


def composition_contains_anchor(
    composition: OutfitComposition,
    anchor: PinnedReferenceAnchor,
) -> bool:
    return any(item.identity == anchor.identity for item in composition.items)
