from __future__ import annotations

from dataclasses import dataclass

from django.test import SimpleTestCase

from apps.chat.services.recommendation_diversity import select_diverse_candidates
from apps.recommend.services.item_retriever import ItemSource
from apps.recommend.services.outfit_types import (
    OutfitComposition,
    OutfitItem,
    RecommendationMode,
)


@dataclass(frozen=True)
class Candidate:
    name: str
    composition: OutfitComposition


def item(
    source_id: str,
    *,
    layer_role: str,
    category_large: str,
) -> OutfitItem:
    return OutfitItem(
        slot_id=f"{layer_role}:template-{source_id}",
        template_point_id=f"template-{source_id}",
        category_large=category_large,
        layer_role=layer_role,
        source_type=ItemSource.PRODUCT,
        source_id=source_id,
        source_collection="products",
        point_id=f"point-{source_id}",
        image_ref=f"https://example.com/{source_id}.jpg",
        price=10_000,
        score=0.9,
        reasons=(),
    )


def candidate(name: str, *items: OutfitItem) -> Candidate:
    return Candidate(
        name=name,
        composition=OutfitComposition(
            mode=RecommendationMode.NEW_ITEM,
            items=tuple(items),
            missing_slot_ids=(),
            total_product_price=sum(value.price or 0 for value in items),
        ),
    )


class RecommendationDiversityTests(SimpleTestCase):
    def test_accessory_only_variation_does_not_count_as_another_outfit(
        self,
    ) -> None:
        first = candidate(
            "first",
            item("top-1", layer_role="TOP", category_large="상의"),
            item("bottom-1", layer_role="BOTTOM", category_large="하의"),
            item("shoe-1", layer_role="SHOES", category_large="신발"),
        )
        accessory_variant = candidate(
            "accessory-variant",
            item("top-1", layer_role="TOP", category_large="상의"),
            item("bottom-1", layer_role="BOTTOM", category_large="하의"),
            item("shoe-2", layer_role="SHOES", category_large="신발"),
        )

        selected = select_diverse_candidates((first, accessory_variant))

        self.assertEqual([value.name for value in selected], ["first"])

    def test_core_slot_variations_preserve_rank_and_stop_at_limit(self) -> None:
        first = candidate(
            "first",
            item("top-1", layer_role="TOP", category_large="상의"),
            item("bottom-1", layer_role="BOTTOM", category_large="하의"),
        )
        duplicate = candidate(
            "duplicate",
            item("top-1", layer_role="TOP", category_large="상의"),
            item("bottom-1", layer_role="BOTTOM", category_large="하의"),
        )
        changed_top = candidate(
            "changed-top",
            item("top-2", layer_role="TOP", category_large="상의"),
            item("bottom-1", layer_role="BOTTOM", category_large="하의"),
        )
        changed_bottom = candidate(
            "changed-bottom",
            item("top-1", layer_role="TOP", category_large="상의"),
            item("bottom-2", layer_role="BOTTOM", category_large="하의"),
        )
        fourth_unique = candidate(
            "fourth-unique",
            item("top-3", layer_role="TOP", category_large="상의"),
            item("bottom-3", layer_role="BOTTOM", category_large="하의"),
        )

        selected = select_diverse_candidates(
            (first, duplicate, changed_top, changed_bottom, fourth_unique),
            limit=3,
        )

        self.assertEqual(
            [value.name for value in selected],
            ["first", "changed-top", "changed-bottom"],
        )

    def test_first_candidate_is_kept_when_no_core_slot_can_be_classified(
        self,
    ) -> None:
        first = candidate(
            "first",
            item("shoe-1", layer_role="SHOES", category_large="신발"),
        )
        second = candidate(
            "second",
            item("shoe-2", layer_role="SHOES", category_large="신발"),
        )

        selected = select_diverse_candidates((first, second))

        self.assertEqual([value.name for value in selected], ["first"])

    def test_dress_variations_are_preserved_as_distinct_outfits(self) -> None:
        candidates = tuple(
            candidate(
                f"dress-{index}",
                item(
                    f"dress-{index}",
                    layer_role="",
                    category_large="원피스/세트",
                ),
                item(
                    f"shoe-{index}",
                    layer_role="SHOES",
                    category_large="신발",
                ),
            )
            for index in range(1, 4)
        )

        selected = select_diverse_candidates(candidates)

        self.assertEqual(
            [value.name for value in selected],
            ["dress-1", "dress-2", "dress-3"],
        )

    def test_injected_outer_slot_ignores_top_changes(self) -> None:
        first = candidate(
            "first",
            item("top-1", layer_role="TOP", category_large="상의"),
            item("outer-1", layer_role="아우터", category_large="아우터"),
        )
        changed_top = candidate(
            "changed-top",
            item("top-2", layer_role="TOP", category_large="상의"),
            item("outer-1", layer_role="OUTER", category_large="아우터"),
        )
        changed_outer = candidate(
            "changed-outer",
            item("top-1", layer_role="TOP", category_large="상의"),
            item("outer-2", layer_role="OUTER", category_large="아우터"),
        )

        selected = select_diverse_candidates(
            (first, changed_top, changed_outer),
            diversity_slots={"아우터"},
        )

        self.assertEqual(
            [value.name for value in selected],
            ["first", "changed-outer"],
        )

    def test_injected_accessory_slot_can_count_accessory_variations(self) -> None:
        first = candidate(
            "first",
            item("top-1", layer_role="TOP", category_large="상의"),
            item("bag-1", layer_role="가방", category_large="잡화"),
        )
        changed_top = candidate(
            "changed-top",
            item("top-2", layer_role="TOP", category_large="상의"),
            item("bag-1", layer_role="가방", category_large="잡화"),
        )
        changed_bag = candidate(
            "changed-bag",
            item("top-1", layer_role="TOP", category_large="상의"),
            item("bag-2", layer_role="가방", category_large="잡화"),
        )

        selected = select_diverse_candidates(
            (first, changed_top, changed_bag),
            diversity_slots={"액세서리"},
        )

        self.assertEqual(
            [value.name for value in selected],
            ["first", "changed-bag"],
        )

    def test_invalid_limit_is_rejected(self) -> None:
        first = candidate(
            "first",
            item("top-1", layer_role="TOP", category_large="상의"),
        )

        for invalid in (0, -1, True):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    select_diverse_candidates((first,), limit=invalid)
