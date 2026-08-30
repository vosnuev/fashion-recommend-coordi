from __future__ import annotations

from django.test import SimpleTestCase

from apps.recommend.services.item_retriever import (
    ItemCandidate,
    ItemRetrievalResult,
    ItemSource,
    TemplateItem,
)
from apps.recommend.services.outfit_types import RecommendationMode
from apps.recommend.services.wardrobe_composer import (
    WardrobeCompositionRequest,
    WardrobeOutfitComposer,
)


def _candidate(
    source: ItemSource,
    source_id: str,
    *,
    score: float,
    image: str = "items/item.jpg",
) -> ItemCandidate:
    collections = {
        ItemSource.WARDROBE: "wardrobe_items",
        ItemSource.GOLDENSET_ITEM: "goldenset_items",
        ItemSource.PRODUCT: "products_naver_v1",
    }
    return ItemCandidate(
        point_id=f"point-{source_id}",
        source_type=source,
        source_id=source_id,
        source_collection=collections[source],
        score=score,
        reasons=("유사 아이템",),
        payload={"image_s3_key": image} if image else {},
    )


def _slot(
    point_id: str,
    layer_role: str,
    *candidates: ItemCandidate,
    golden_image: str = "golden/item.jpg",
) -> ItemRetrievalResult:
    return ItemRetrievalResult(
        template=TemplateItem(
            point_id=point_id,
            payload={
                "category_large": "상의" if layer_role == "TOP" else "하의",
                "layer_role": layer_role,
                "image_s3_key": golden_image,
            },
        ),
        candidates=tuple(candidates),
        vector_name="image",
    )


class WardrobeOutfitComposerTests(SimpleTestCase):
    def test_only_wardrobe_source_is_used(self) -> None:
        slot = _slot(
            "golden-top",
            "TOP",
            _candidate(ItemSource.PRODUCT, "product-top", score=0.99),
            _candidate(ItemSource.WARDROBE, "owned-top", score=0.5),
        )

        batch = WardrobeOutfitComposer().compose(
            WardrobeCompositionRequest(slot_results=(slot,), composition_count=2)
        )

        self.assertEqual(batch.mode, RecommendationMode.WARDROBE_BASED)
        self.assertEqual(batch.compositions[0].items[0].source_id, "owned-top")
        self.assertTrue(
            all(
                item.source_type is ItemSource.WARDROBE
                for composition in batch.compositions
                for item in composition.items
            )
        )

    def test_missing_wardrobe_slot_is_not_filled_from_another_source(self) -> None:
        top = _slot(
            "golden-top",
            "TOP",
            _candidate(ItemSource.WARDROBE, "owned-top", score=0.8),
        )
        bottom = _slot("golden-bottom", "BOTTOM")

        batch = WardrobeOutfitComposer().compose(
            WardrobeCompositionRequest(slot_results=(top, bottom))
        )

        first = batch.compositions[0]
        self.assertFalse(first.complete)
        self.assertEqual([item.source_id for item in first.items], ["owned-top"])
        self.assertEqual(first.missing_slot_ids, ("BOTTOM:golden-bottom",))

    def test_returns_up_to_three_distinct_wardrobe_compositions(self) -> None:
        top = _slot(
            "golden-top",
            "TOP",
            _candidate(ItemSource.WARDROBE, "top-1", score=0.9),
            _candidate(ItemSource.WARDROBE, "top-2", score=0.8),
        )
        bottom = _slot(
            "golden-bottom",
            "BOTTOM",
            _candidate(ItemSource.WARDROBE, "bottom-1", score=0.9),
            _candidate(ItemSource.WARDROBE, "bottom-2", score=0.8),
        )

        batch = WardrobeOutfitComposer().compose(
            WardrobeCompositionRequest(
                slot_results=(top, bottom),
                composition_count=3,
            )
        )

        self.assertEqual(len(batch.compositions), 3)
        fingerprints = {
            tuple(item.identity for item in composition.items)
            for composition in batch.compositions
        }
        self.assertEqual(len(fingerprints), 3)
        self.assertTrue(all(composition.complete for composition in batch.compositions))

    def test_unusable_slot_is_reported_instead_of_using_product(self) -> None:
        slot = _slot(
            "golden-top",
            "TOP",
            _candidate(ItemSource.PRODUCT, "product-top", score=0.99),
            golden_image="",
        )

        batch = WardrobeOutfitComposer().compose(
            WardrobeCompositionRequest(slot_results=(slot,))
        )

        self.assertFalse(batch.compositions[0].complete)
        self.assertEqual(
            batch.compositions[0].missing_slot_ids,
            ("TOP:golden-top",),
        )
