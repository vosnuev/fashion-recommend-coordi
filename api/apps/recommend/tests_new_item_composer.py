from __future__ import annotations

from django.test import SimpleTestCase

from apps.recommend.services.item_retriever import (
    ItemCandidate,
    ItemRetrievalResult,
    ItemSource,
    TemplateItem,
)
from apps.recommend.services.new_item_composer import (
    NewItemCandidateNotFound,
    NewItemCompositionRequest,
    NewItemOutfitComposer,
)
from apps.recommend.services.outfit_types import RecommendationMode


def _candidate(
    source: ItemSource,
    source_id: str,
    *,
    score: float,
    price: int | None = None,
    image: str = "items/item.jpg",
) -> ItemCandidate:
    collections = {
        ItemSource.WARDROBE: "wardrobe_items",
        ItemSource.GOLDENSET_ITEM: "goldenset_items",
        ItemSource.PRODUCT: "products_naver_v1",
    }
    payload = {"image_s3_key": image} if image else {}
    if price is not None:
        payload["price"] = price
    return ItemCandidate(
        point_id=f"point-{source_id}",
        source_type=source,
        source_id=source_id,
        source_collection=collections[source],
        score=score,
        reasons=("골든 아이템 유사 후보",),
        payload=payload,
    )


def _slot(
    point_id: str,
    layer_role: str,
    *candidates: ItemCandidate,
) -> ItemRetrievalResult:
    return ItemRetrievalResult(
        template=TemplateItem(
            point_id=point_id,
            payload={
                "category_large": "상의" if layer_role == "TOP" else "하의",
                "layer_role": layer_role,
                "image_s3_key": "golden/item.jpg",
            },
        ),
        candidates=tuple(candidates),
        vector_name="image",
    )


class NewItemOutfitComposerTests(SimpleTestCase):
    def test_each_composition_contains_product_and_uses_wardrobe_for_other_slots(
        self,
    ) -> None:
        top = _slot(
            "golden-top",
            "TOP",
            _candidate(ItemSource.WARDROBE, "owned-top", score=0.95),
            _candidate(ItemSource.PRODUCT, "product-top", score=0.8, price=30_000),
        )
        bottom = _slot(
            "golden-bottom",
            "BOTTOM",
            _candidate(ItemSource.WARDROBE, "owned-bottom", score=0.9),
        )

        batch = NewItemOutfitComposer().compose(
            NewItemCompositionRequest(slot_results=(top, bottom))
        )

        first = batch.compositions[0]
        self.assertEqual(batch.mode, RecommendationMode.NEW_ITEM)
        self.assertEqual(first.purchasable_count, 1)
        self.assertEqual(first.owned_count, 1)
        self.assertEqual(
            [item.source_id for item in first.items],
            ["product-top", "owned-bottom"],
        )

    def test_goldenset_item_is_never_used_as_final_item(self) -> None:
        slot = _slot(
            "golden-top",
            "TOP",
            _candidate(ItemSource.GOLDENSET_ITEM, "golden-alternative", score=0.99),
            _candidate(ItemSource.PRODUCT, "product-top", score=0.5, price=20_000),
        )

        batch = NewItemOutfitComposer().compose(
            NewItemCompositionRequest(slot_results=(slot,))
        )

        self.assertTrue(
            all(
                item.source_type is not ItemSource.GOLDENSET_ITEM
                for composition in batch.compositions
                for item in composition.items
            )
        )

    def test_no_eligible_product_fails_instead_of_returning_wardrobe_only(self) -> None:
        slot = _slot(
            "golden-top",
            "TOP",
            _candidate(ItemSource.WARDROBE, "owned-top", score=0.9),
        )

        with self.assertRaises(NewItemCandidateNotFound):
            NewItemOutfitComposer().compose(
                NewItemCompositionRequest(slot_results=(slot,))
            )

    def test_product_over_budget_does_not_create_new_item_composition(self) -> None:
        slot = _slot(
            "golden-top",
            "TOP",
            _candidate(ItemSource.WARDROBE, "owned-top", score=0.9),
            _candidate(ItemSource.PRODUCT, "product-top", score=0.8, price=60_000),
        )

        with self.assertRaises(NewItemCandidateNotFound):
            NewItemOutfitComposer().compose(
                NewItemCompositionRequest(
                    slot_results=(slot,),
                    total_budget=50_000,
                )
            )

    def test_returns_up_to_three_distinct_product_mixed_compositions(self) -> None:
        top = _slot(
            "golden-top",
            "TOP",
            _candidate(ItemSource.WARDROBE, "owned-top", score=0.95),
            _candidate(ItemSource.PRODUCT, "product-top-1", score=0.9, price=30_000),
            _candidate(ItemSource.PRODUCT, "product-top-2", score=0.8, price=25_000),
        )
        bottom = _slot(
            "golden-bottom",
            "BOTTOM",
            _candidate(ItemSource.WARDROBE, "owned-bottom", score=0.95),
            _candidate(
                ItemSource.PRODUCT,
                "product-bottom",
                score=0.85,
                price=35_000,
            ),
        )

        batch = NewItemOutfitComposer().compose(
            NewItemCompositionRequest(
                slot_results=(top, bottom),
                composition_count=3,
            )
        )

        self.assertEqual(len(batch.compositions), 3)
        self.assertTrue(
            all(
                composition.purchasable_count >= 1 for composition in batch.compositions
            )
        )
        fingerprints = {
            tuple(item.identity for item in composition.items)
            for composition in batch.compositions
        }
        self.assertEqual(len(fingerprints), 3)
