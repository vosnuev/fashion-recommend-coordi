from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.recommend.services.body_profile import ROUND, BodyProfile
from apps.recommend.services.item_retriever import ItemSource
from apps.recommend.services.outfit_types import (
    OutfitComposition,
    OutfitItem,
    OutfitSlot,
    RecommendationMode,
)
from apps.recommend.services.validator import (
    DjangoEligibilityGateway,
    OutfitValidator,
    ReferenceValidationContract,
    SourceEligibility,
    ValidationContext,
    ValidationSeverity,
)


def _item(
    slot_id: str,
    source_type: ItemSource,
    source_id: str,
    *,
    category_large: str = "상의",
    image_ref: str = "items/item.jpg",
    price: int | None = None,
    payload: dict | None = None,
) -> OutfitItem:
    collections = {
        ItemSource.WARDROBE: "wardrobe_items",
        ItemSource.GOLDENSET_ITEM: "goldenset_items",
        ItemSource.PRODUCT: "products_naver_v1",
    }
    item_payload = {
        "category_large": category_large,
        "image_s3_key": image_ref,
    }
    item_payload.update(payload or {})
    if price is not None:
        item_payload["price"] = price
    return OutfitItem(
        slot_id=slot_id,
        template_point_id=f"template-{slot_id}",
        category_large=category_large,
        layer_role=str(item_payload.get("layer_role") or ""),
        source_type=source_type,
        source_id=source_id,
        source_collection=collections[source_type],
        point_id=f"point-{source_id}",
        image_ref=image_ref,
        price=price,
        score=0.8,
        reasons=("후보 선택",),
        payload=item_payload,
    )


def _composition(
    *items: OutfitItem,
    missing: tuple[str, ...] = (),
    total_price: int | None = None,
    slots: tuple[OutfitSlot, ...] = (),
    mode: RecommendationMode | None = None,
) -> OutfitComposition:
    if total_price is None:
        total_price = sum(
            item.price or 0 for item in items if item.source_type is ItemSource.PRODUCT
        )
    return OutfitComposition(
        mode=(
            mode
            or (
                RecommendationMode.NEW_ITEM
                if any(item.source_type is ItemSource.PRODUCT for item in items)
                else RecommendationMode.WARDROBE_BASED
            )
        ),
        items=tuple(items),
        missing_slot_ids=missing,
        total_product_price=total_price,
        slots=slots,
    )


class FakeEligibilityGateway:
    def __init__(self, statuses=None) -> None:
        self.statuses = statuses or {}
        self.user_ids: list[int | None] = []

    def check(self, items, *, user_id):
        self.user_ids.append(user_id)
        return {
            item.identity: self.statuses.get(
                item.identity,
                SourceEligibility(
                    eligible=True,
                    current_price=(
                        item.price if item.source_type is ItemSource.PRODUCT else None
                    ),
                ),
            )
            for item in items
        }


def _codes(result, severity: ValidationSeverity | None = None) -> set[str]:
    return {
        issue.code
        for issue in result.issues
        if severity is None or issue.severity is severity
    }


class OutfitValidatorTests(SimpleTestCase):
    def test_valid_composition_can_proceed_to_render(self) -> None:
        top = _item(
            "top",
            ItemSource.WARDROBE,
            "owned-top",
            payload={
                "season": ["간절기"],
                "usage": ["출근"],
                "layer_role": "기본 상의",
                "layer_order": 1,
            },
        )
        bottom = _item(
            "bottom",
            ItemSource.WARDROBE,
            "owned-bottom",
            category_large="하의",
            payload={"season": ["봄", "가을"], "usage": ["출근"]},
        )
        slots = (
            OutfitSlot("top", "template-top", "상의"),
            OutfitSlot("bottom", "template-bottom", "하의"),
        )
        gateway = FakeEligibilityGateway()

        result = OutfitValidator(eligibility_gateway=gateway).validate(
            _composition(top, bottom, slots=slots),
            context=ValidationContext(
                user_id=7,
                weather={"temperature": 18},
                occasion="출근",
            ),
        )

        self.assertTrue(result.valid)
        self.assertTrue(result.can_render)
        self.assertEqual(result.errors, ())
        self.assertEqual(gateway.user_ids, [7])

    def test_missing_slot_and_image_are_hard_errors(self) -> None:
        item = _item(
            "top",
            ItemSource.WARDROBE,
            "owned-top",
            image_ref="",
        )
        slots = (
            OutfitSlot("top", "template-top", "상의"),
            OutfitSlot("bottom", "template-bottom", "하의"),
        )

        result = OutfitValidator(eligibility_gateway=FakeEligibilityGateway()).validate(
            _composition(item, missing=("bottom",), slots=slots)
        )

        self.assertFalse(result.valid)
        self.assertEqual(
            {"REQUIRED_SLOT_MISSING", "ITEM_IMAGE_MISSING"},
            _codes(result, ValidationSeverity.ERROR),
        )

    def test_duplicate_item_category_and_layer_conflicts_are_rejected(self) -> None:
        duplicated_inner = _item(
            "top-inner",
            ItemSource.WARDROBE,
            "same",
            payload={"layer_role": "기본 상의", "layer_order": 2},
        )
        duplicated_outer = OutfitItem(
            **{
                **duplicated_inner.__dict__,
                "slot_id": "top-outer",
                "template_point_id": "template-top-outer",
                "layer_role": "아우터",
                "payload": {
                    **duplicated_inner.payload,
                    "layer_role": "아우터",
                    "layer_order": 1,
                },
            }
        )
        bottom_a = _item(
            "bottom-a",
            ItemSource.WARDROBE,
            "bottom-a",
            category_large="하의",
        )
        bottom_b = _item(
            "bottom-b",
            ItemSource.WARDROBE,
            "bottom-b",
            category_large="하의",
        )

        result = OutfitValidator(eligibility_gateway=FakeEligibilityGateway()).validate(
            _composition(duplicated_inner, duplicated_outer, bottom_a, bottom_b)
        )

        self.assertTrue(
            {
                "DUPLICATE_ITEM",
                "CATEGORY_CONFLICT",
                "LAYER_ORDER_CONFLICT",
            }.issubset(_codes(result, ValidationSeverity.ERROR))
        )

    def test_explicit_avoidance_is_error_but_context_mismatch_is_warning(self) -> None:
        item = _item(
            "top",
            ItemSource.WARDROBE,
            "owned-top",
            payload={
                "color": ["블랙"],
                "season": ["겨울"],
                "usage": ["데일리"],
                "material": ["울"],
            },
        )

        result = OutfitValidator(eligibility_gateway=FakeEligibilityGateway()).validate(
            _composition(item),
            context=ValidationContext(
                season="여름",
                occasion="출근",
                avoided_tags={"color": ("블랙",)},
                contextual_avoided_tags={"material": ("울",)},
            ),
        )

        self.assertIn("EXPLICIT_TAG_EXCLUDED", _codes(result, ValidationSeverity.ERROR))
        self.assertTrue(
            {"SEASON_MISMATCH", "OCCASION_MISMATCH", "CONTEXT_RULE_MISMATCH"}.issubset(
                _codes(result, ValidationSeverity.WARNING)
            )
        )

    def test_live_source_failure_is_returned_with_slot_context(self) -> None:
        item = _item("top", ItemSource.WARDROBE, "owned-top")
        gateway = FakeEligibilityGateway(
            {
                item.identity: SourceEligibility(
                    eligible=False,
                    code="WARDROBE_ITEM_FORBIDDEN",
                    message="다른 사용자의 아이템",
                )
            }
        )

        result = OutfitValidator(eligibility_gateway=gateway).validate(
            _composition(item),
            context=ValidationContext(user_id=7),
        )

        issue = next(
            issue for issue in result.errors if issue.code == "WARDROBE_ITEM_FORBIDDEN"
        )
        self.assertEqual(issue.slot_id, "top")
        self.assertEqual(issue.source_id, "owned-top")

    def test_hard_body_rule_rejects_final_composition(self) -> None:
        item = _item(
            "top",
            ItemSource.WARDROBE,
            "owned-crop",
            payload={"fit": "레귤러핏", "length": "크롭"},
        )

        result = OutfitValidator(eligibility_gateway=FakeEligibilityGateway()).validate(
            _composition(item),
            context=ValidationContext(body=BodyProfile(silhouette=ROUND)),
        )

        self.assertFalse(result.valid)
        self.assertIn(
            "BODY_RULE_HARD_EXCLUDED",
            _codes(result, ValidationSeverity.ERROR),
        )

    def test_soft_body_and_weather_rules_are_warnings(self) -> None:
        item = _item(
            "top",
            ItemSource.WARDROBE,
            "owned-knit",
            payload={"fit": "슬림핏", "material": "니트"},
        )

        result = OutfitValidator(eligibility_gateway=FakeEligibilityGateway()).validate(
            _composition(item),
            context=ValidationContext(
                body=BodyProfile(silhouette=ROUND),
                weather={"temperature": 28},
            ),
        )

        self.assertTrue(result.valid)
        self.assertTrue(
            {"BODY_FIT_WARNING", "WEATHER_RULE_WARNING"}.issubset(
                _codes(result, ValidationSeverity.WARNING)
            )
        )

    def test_current_catalog_price_is_used_for_total_budget(self) -> None:
        item = _item(
            "top",
            ItemSource.PRODUCT,
            "naver-1",
            price=40_000,
            payload={"source": "naver"},
        )
        gateway = FakeEligibilityGateway(
            {
                item.identity: SourceEligibility(
                    eligible=True,
                    current_price=55_000,
                )
            }
        )

        result = OutfitValidator(eligibility_gateway=gateway).validate(
            _composition(item),
            context=ValidationContext(total_budget=50_000),
        )

        self.assertEqual(result.effective_total_product_price, 55_000)
        self.assertIn("TOTAL_BUDGET_EXCEEDED", _codes(result, ValidationSeverity.ERROR))
        self.assertTrue(
            {"PRODUCT_PRICE_CHANGED", "COMPOSITION_PRICE_STALE"}.issubset(
                _codes(result, ValidationSeverity.WARNING)
            )
        )

    def test_current_catalog_price_is_used_for_category_budget(self) -> None:
        item = _item("top", ItemSource.PRODUCT, "naver-1", price=90_000)
        gateway = FakeEligibilityGateway(
            {
                item.identity: SourceEligibility(
                    eligible=True,
                    current_price=110_000,
                )
            }
        )

        result = OutfitValidator(eligibility_gateway=gateway).validate(
            _composition(item),
            context=ValidationContext(category_budgets={"상의": 100_000}),
        )

        self.assertIn(
            "CATEGORY_BUDGET_EXCEEDED",
            _codes(result, ValidationSeverity.ERROR),
        )

    def test_shared_friend_original_is_rejected_by_source_and_point_id(self) -> None:
        friend_id = "friend-original"
        leaked = _item(
            "outer",
            ItemSource.WARDROBE,
            friend_id,
            category_large="아우터",
        )
        contract = ReferenceValidationContract(
            original_wardrobe_item_ids=(friend_id,),
            original_qdrant_point_ids=(leaked.point_id,),
            anchor_identity=leaked.identity,
        )

        result = OutfitValidator(eligibility_gateway=FakeEligibilityGateway()).validate(
            _composition(leaked),
            context=ValidationContext(user_id=7, reference=contract),
        )

        self.assertIn(
            "SHARED_REFERENCE_SOURCE_LEAKED",
            _codes(result, ValidationSeverity.ERROR),
        )

    def test_reference_anchor_must_be_present(self) -> None:
        selected = _item("top", ItemSource.WARDROBE, "another-owned-item")
        contract = ReferenceValidationContract(
            original_wardrobe_item_ids=("friend-original",),
            original_qdrant_point_ids=("friend-point",),
            anchor_identity=("WARDROBE", "wardrobe_items", "expected-anchor"),
        )

        result = OutfitValidator(eligibility_gateway=FakeEligibilityGateway()).validate(
            _composition(selected),
            context=ValidationContext(user_id=7, reference=contract),
        )

        self.assertIn(
            "REFERENCE_ANCHOR_MISSING",
            _codes(result, ValidationSeverity.ERROR),
        )

    def test_reference_validation_requires_original_exclusion_ids(self) -> None:
        item = _item("top", ItemSource.WARDROBE, "owned-top")
        invalid_contract = ReferenceValidationContract(
            original_wardrobe_item_ids=(),
            original_qdrant_point_ids=(),
            anchor_identity=item.identity,
        )

        with self.assertRaises(ValueError):
            OutfitValidator(eligibility_gateway=FakeEligibilityGateway()).validate(
                _composition(item),
                context=ValidationContext(reference=invalid_contract),
            )

    def test_mode_rejects_wrong_final_sources(self) -> None:
        product = _item(
            "top",
            ItemSource.PRODUCT,
            "naver-1",
            price=30_000,
        )
        wardrobe_only = _item("top", ItemSource.WARDROBE, "owned-top")
        golden = _item("top", ItemSource.GOLDENSET_ITEM, "golden-top")
        validator = OutfitValidator(eligibility_gateway=FakeEligibilityGateway())

        wardrobe_result = validator.validate(
            _composition(
                product,
                mode=RecommendationMode.WARDROBE_BASED,
            )
        )
        new_item_result = validator.validate(
            _composition(
                wardrobe_only,
                mode=RecommendationMode.NEW_ITEM,
            )
        )
        golden_result = validator.validate(_composition(golden))

        self.assertIn(
            "MODE_SOURCE_INVALID",
            _codes(wardrobe_result, ValidationSeverity.ERROR),
        )
        self.assertIn(
            "NEW_ITEM_PRODUCT_REQUIRED",
            _codes(new_item_result, ValidationSeverity.ERROR),
        )
        self.assertIn(
            "MODE_SOURCE_INVALID",
            _codes(golden_result, ValidationSeverity.ERROR),
        )


class DjangoEligibilityGatewayRuleTests(SimpleTestCase):
    def test_naver_discontinued_product_is_not_eligible(self) -> None:
        product = SimpleNamespace(product_type=3)

        status = DjangoEligibilityGateway._naver_status(product)

        self.assertFalse(status.eligible)
        self.assertEqual(status.code, "PRODUCT_NOT_ON_SALE")

    def test_catalog_product_requires_tag_link_and_price(self) -> None:
        status = DjangoEligibilityGateway._catalog_status(
            tagging_status="tagged",
            link=None,
            price=10_000,
        )

        self.assertFalse(status.eligible)
        self.assertEqual(status.code, "PRODUCT_LINK_MISSING")


class OptionalSlotMissingTests(SimpleTestCase):
    """선택 슬롯이 비는 건 경고지 실패가 아니다."""

    def setUp(self) -> None:
        self.validator = OutfitValidator(eligibility_gateway=FakeEligibilityGateway())
        self.top = OutfitSlot(
            slot_id="TOP",
            template_point_id="template-TOP",
            category_large="상의",
            required=True,
        )
        self.accessory = OutfitSlot(
            slot_id="ACCESSORY",
            template_point_id="template-ACCESSORY",
            category_large="액세서리",
            required=False,
        )

    def _errors(self, result) -> list[str]:
        return [
            issue.code
            for issue in result.issues
            if issue.severity is ValidationSeverity.ERROR
        ]

    def test_missing_optional_slot_does_not_fail_validation(self) -> None:
        composition = _composition(
            _item("TOP", ItemSource.PRODUCT, "top-1", price=10000),
            missing=("ACCESSORY",),
            slots=(self.top, self.accessory),
        )

        result = self.validator.validate(composition, context=ValidationContext())

        self.assertNotIn("REQUIRED_SLOT_MISSING", self._errors(result))

    def test_missing_required_slot_still_fails(self) -> None:
        composition = _composition(
            _item("ACCESSORY", ItemSource.PRODUCT, "acc-1", category_large="액세서리", price=1000),
            missing=("TOP",),
            slots=(self.top, self.accessory),
        )

        result = self.validator.validate(composition, context=ValidationContext())

        self.assertIn("REQUIRED_SLOT_MISSING", self._errors(result))

    def test_context_required_slot_overrides_optional_flag(self) -> None:
        """공유 옷 고정처럼 호출부가 필수로 지정한 슬롯은 선택으로 내려가지 않는다."""

        composition = _composition(
            _item("TOP", ItemSource.PRODUCT, "top-1", price=10000),
            missing=("ACCESSORY",),
            slots=(self.top, self.accessory),
        )

        result = self.validator.validate(
            composition,
            context=ValidationContext(required_slot_ids=("ACCESSORY",)),
        )

        self.assertIn("REQUIRED_SLOT_MISSING", self._errors(result))

    def test_composition_without_slot_metadata_keeps_old_behaviour(self) -> None:
        """slots가 비면 판단 근거가 없다 — 예전처럼 누락을 전부 오류로 본다."""

        composition = _composition(
            _item("TOP", ItemSource.PRODUCT, "top-1", price=10000),
            missing=("ACCESSORY",),
        )

        result = self.validator.validate(composition, context=ValidationContext())

        self.assertIn("REQUIRED_SLOT_MISSING", self._errors(result))
