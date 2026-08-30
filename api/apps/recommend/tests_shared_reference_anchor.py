from __future__ import annotations

import uuid
from unittest.mock import Mock

from django.test import SimpleTestCase

from apps.recommend.services.item_retriever import (
    ItemCandidate,
    ItemRetrievalResult,
    ItemSource,
    TemplateItem,
)
from apps.recommend.services.outfit_types import RecommendationMode
from apps.recommend.services.qdrant import collection_spec
from apps.recommend.services.shared_reference_anchor import (
    PinnedReferenceAnchor,
    SharedReferenceAnchorResolver,
    SharedReferenceTemplateSlotNotFound,
    pin_reference_anchor,
)
from apps.recommend.services.shared_reference_loader import (
    ReferenceSearchExclusions,
    SharedReferenceSearchBasis,
    SharedReferenceTags,
)
from apps.recommend.services.shared_reference_product_search import (
    SharedReferenceProductSearchResult,
    SimilarProductCandidate,
)
from apps.recommend.services.shared_reference_style_fallback import (
    StyleFallbackResult,
)
from apps.recommend.services.shared_reference_visual_search import (
    WardrobeVisualCandidate,
    WardrobeVisualSearchResult,
)


def _id() -> str:
    return str(uuid.uuid4())


def _reference() -> SharedReferenceSearchBasis:
    source_id = _id()
    return SharedReferenceSearchBasis(
        schema_version="1.0",
        shared_item_id=_id(),
        room_id=_id(),
        source_wardrobe_item_id=source_id,
        collection_name=collection_spec("wardrobe").name,
        point_id=source_id,
        embedding_version="fashionsiglip-v1",
        image_s3_key="wardrobe/friend.webp",
        image_vector=(1.0, 0.0, 0.0),
        text_vector=(0.0, 1.0, 0.0),
        tags=SharedReferenceTags(
            item_name="친구 재킷",
            category_large="아우터",
            category_small="재킷",
            season=("봄",),
            style=("미니멀",),
            color="검정",
            pattern="무지",
            fit="오버핏",
            material="울",
            sleeve="긴소매",
            length="기본",
            usage=("데이트",),
            layer_role="OUTER",
            layer_order=3,
        ),
        exclusions=ReferenceSearchExclusions(
            wardrobe_item_ids=(source_id,),
            qdrant_point_ids=(source_id,),
        ),
    )


def _slot(point_id: str, category_small: str) -> ItemRetrievalResult:
    return ItemRetrievalResult(
        template=TemplateItem(
            point_id=point_id,
            payload={
                "category_large": "아우터",
                "category_small": category_small,
                "layer_role": "OUTER",
                "image_s3_key": f"golden/{point_id}.webp",
            },
        ),
        candidates=(),
        vector_name="image",
    )


class SharedReferenceAnchorTests(SimpleTestCase):
    def test_wardrobe_mode_uses_first_visual_match_without_style_fallback(self) -> None:
        reference = _reference()
        selected_id = _id()
        loader = Mock(load=Mock(return_value=reference))
        visual_searcher = Mock()
        visual_searcher.search.return_value = WardrobeVisualSearchResult(
            reference_point_id=reference.point_id,
            min_similarity=0.75,
            candidates=(
                WardrobeVisualCandidate(
                    match_type="VISUAL_SIMILAR",
                    point_id=selected_id,
                    wardrobe_item_id=selected_id,
                    visual_score=0.92,
                    image_s3_key="wardrobe/mine.webp",
                    embedding_version="fashionsiglip-v1",
                    category_large="아우터",
                    category_small="재킷",
                    layer_role="OUTER",
                    style=("미니멀",),
                    color="검정",
                ),
            ),
        )
        style_searcher = Mock()
        resolver = SharedReferenceAnchorResolver(
            loader=loader,
            visual_searcher=visual_searcher,
            style_searcher=style_searcher,
            product_searcher=Mock(),
        )
        observed: list[tuple[str, float]] = []

        anchor = resolver.resolve(
            snapshot={"type": "SHARED_WARDROBE_ITEM"},
            mode=RecommendationMode.WARDROBE_BASED,
            user_id=7,
            total_budget=None,
            category_budgets={},
            stage_observer=lambda stage, duration_ms: observed.append(
                (stage, duration_ms)
            ),
        )

        self.assertEqual(anchor.candidate.source_type, ItemSource.WARDROBE)
        self.assertEqual(anchor.candidate.source_id, selected_id)
        self.assertEqual(anchor.match_type, "VISUAL_SIMILAR")
        style_searcher.search.assert_not_called()
        self.assertEqual([stage for stage, _ in observed], ["SIMILAR_SEARCH"])
        self.assertGreaterEqual(observed[0][1], 0)

    def test_new_item_mode_selects_product_search_anchor(self) -> None:
        reference = _reference()
        product = SimilarProductCandidate(
            match_type="VISUAL_SIMILAR",
            selection_role="NEW_ITEM_ANCHOR",
            point_id=_id(),
            source="naver",
            external_product_id="naver-1",
            source_collection=collection_spec("products_naver").name,
            visual_score=0.9,
            price=55_000,
            title="유사 재킷",
            brand="브랜드",
            mall_name="쇼핑몰",
            link="https://example.com/product",
            image_url="https://example.com/product.jpg",
            image_s3_key="products/naver-1.webp",
            category_large="아우터",
            category_small="재킷",
            layer_role="OUTER",
            tagging_status="tagged",
            sale_status="ON_SALE",
        )
        product_searcher = Mock()
        product_searcher.search.return_value = SharedReferenceProductSearchResult(
            reference_point_id=reference.point_id,
            category_large="아우터",
            layer_role="OUTER",
            category_budget=60_000,
            remaining_total_budget=80_000,
            effective_max_price=60_000,
            min_similarity=0.75,
            candidates=(product,),
            selected_anchor=product,
        )
        resolver = SharedReferenceAnchorResolver(
            loader=Mock(load=Mock(return_value=reference)),
            visual_searcher=Mock(),
            style_searcher=Mock(),
            product_searcher=product_searcher,
        )

        anchor = resolver.resolve(
            snapshot={"type": "SHARED_WARDROBE_ITEM"},
            mode=RecommendationMode.NEW_ITEM,
            user_id=7,
            total_budget=80_000,
            category_budgets={"아우터": 60_000},
        )

        self.assertEqual(anchor.candidate.source_type, ItemSource.PRODUCT)
        self.assertEqual(anchor.candidate.source_id, "naver-1")
        self.assertEqual(anchor.candidate.price, 55_000)
        self.assertEqual(
            anchor.candidate.payload["selection_role"],
            "PINNED_REFERENCE_ANCHOR",
        )

    def test_pin_uses_exact_small_category_and_leaves_other_slots_unchanged(
        self,
    ) -> None:
        reference = _reference()
        candidate = ItemCandidate(
            point_id="product-point",
            source_type=ItemSource.PRODUCT,
            source_id="product-1",
            source_collection="products_naver_v1",
            score=0.9,
            reasons=("공유 옷 고정",),
            payload={"image_url": "https://example.com/product.jpg", "price": 10},
        )
        anchor = PinnedReferenceAnchor(
            reference=reference,
            candidate=candidate,
            match_type="VISUAL_SIMILAR",
        )
        coat = _slot("coat-slot", "코트")
        jacket = _slot("jacket-slot", "재킷")

        pinned = pin_reference_anchor(anchor, (coat, jacket))

        self.assertIsNone(pinned[0].pinned_candidate)
        self.assertEqual(pinned[1].pinned_candidate, candidate)

    def test_pin_fails_when_golden_template_has_no_matching_slot(self) -> None:
        reference = _reference()
        anchor = PinnedReferenceAnchor(
            reference=reference,
            candidate=Mock(),
            match_type="VISUAL_SIMILAR",
        )
        top = ItemRetrievalResult(
            template=TemplateItem(
                point_id="top-slot",
                payload={"category_large": "상의", "layer_role": "TOP"},
            ),
            candidates=(),
            vector_name="image",
        )

        with self.assertRaises(SharedReferenceTemplateSlotNotFound):
            pin_reference_anchor(anchor, (top,))

    def test_visual_miss_invokes_style_fallback(self) -> None:
        reference = _reference()
        visual = WardrobeVisualSearchResult(
            reference_point_id=reference.point_id,
            min_similarity=0.75,
            candidates=(),
        )
        visual_searcher = Mock()
        visual_searcher.search.return_value = visual
        style_searcher = Mock()
        style_searcher.search.return_value = StyleFallbackResult(
            fallback_used=True,
            decision="VISUAL_THRESHOLD_NOT_MET",
            search_mode="text",
            visual_threshold=0.75,
            min_style_score=0.3,
            candidates=(),
        )
        resolver = SharedReferenceAnchorResolver(
            loader=Mock(load=Mock(return_value=reference)),
            visual_searcher=visual_searcher,
            style_searcher=style_searcher,
            product_searcher=Mock(),
        )

        with self.assertRaisesRegex(RuntimeError, "유사한 내 옷"):
            resolver.resolve(
                snapshot={"type": "SHARED_WARDROBE_ITEM"},
                mode=RecommendationMode.WARDROBE_BASED,
                user_id=7,
                total_budget=None,
                category_budgets={},
            )

        style_searcher.search.assert_called_once()
