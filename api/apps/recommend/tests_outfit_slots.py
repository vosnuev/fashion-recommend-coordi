from django.test import SimpleTestCase

from apps.recommend.services.outfit_slots import (
    ACCESSORY,
    DRESS,
    FOOTWEAR,
    canonical_slot,
    is_required_outfit_slot,
)


class OutfitSlotContractTests(SimpleTestCase):
    def test_real_catalog_categories_share_one_canonical_contract(self) -> None:
        self.assertEqual(canonical_slot("원피스/세트"), DRESS)
        self.assertEqual(canonical_slot("SHOES"), FOOTWEAR)
        self.assertEqual(canonical_slot("가방"), ACCESSORY)
        self.assertEqual(canonical_slot("액세서리"), ACCESSORY)
        self.assertEqual(canonical_slot("TOP:template-id"), "TOP")

    def test_body_covering_and_finishing_requirements_are_consistent(self) -> None:
        for value in ("상의", "하의", "원피스/세트"):
            with self.subTest(value=value):
                self.assertTrue(is_required_outfit_slot(value))
        for value in ("아우터", "SHOES", "가방", "액세서리"):
            with self.subTest(value=value):
                self.assertFalse(is_required_outfit_slot(value))
        self.assertTrue(is_required_outfit_slot("아직없는분류"))
