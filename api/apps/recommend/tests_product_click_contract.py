from __future__ import annotations

import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from apps.chat.models import ChatIdentity
from apps.recommend.models import (
    OutfitComposition,
    OutfitCompositionItem,
    ProductClickEvent,
    RecommendationResult,
)
from apps.recommend.serializers import (
    ProductClickEngagementRequestSerializer,
    ProductClickEventSerializer,
)
from apps.recommend.services.recommendation_results import (
    PRODUCT_CLICK_DEDUPLICATION_WINDOW,
)


class ProductClickContractTests(SimpleTestCase):
    @staticmethod
    def _event(*, owner_id: int = 1, user_id: int = 1) -> ProductClickEvent:
        identity = ChatIdentity(
            id=uuid.uuid4(),
            user_id=owner_id,
            identity_type=ChatIdentity.IdentityType.MEMBER,
        )
        result = RecommendationResult(
            id=uuid.uuid4(),
            identity=identity,
            persona_id="minimal",
        )
        composition = OutfitComposition(
            id=uuid.uuid4(),
            result=result,
            status=OutfitComposition.Status.VALIDATED,
        )
        item = OutfitCompositionItem(
            id=uuid.uuid4(),
            composition=composition,
            source_type=OutfitCompositionItem.SourceType.PRODUCT,
            source_collection="products_naver_v1",
            source_id="naver-101",
        )
        return ProductClickEvent(
            id=uuid.uuid4(),
            user_id=user_id,
            item=item,
            result_id_snapshot=result.id,
            composition_id_snapshot=composition.id,
            persona_id=result.persona_id,
            source_collection=item.source_collection,
            source_id=item.source_id,
            created_at=timezone.now(),
        )

    def test_owned_product_click_passes_model_boundary(self) -> None:
        self._event().clean()

    def test_other_owner_wardrobe_and_mismatched_snapshot_are_rejected(self) -> None:
        other_owner = self._event(owner_id=1, user_id=2)
        wardrobe = self._event()
        wardrobe.item.source_type = OutfitCompositionItem.SourceType.WARDROBE
        mismatched = self._event()
        mismatched.result_id_snapshot = uuid.uuid4()

        for event in (other_owner, wardrobe, mismatched):
            with self.assertRaises(ValidationError):
                event.clean()

    def test_serializer_exposes_attribution_and_deduplication(self) -> None:
        event = self._event()
        event.deduplicated = True
        event.engagement_duration_ms = 42_000
        event.engagement_recorded_at = timezone.now()

        payload = ProductClickEventSerializer(event).data

        self.assertEqual(payload["product_click_id"], str(event.id))
        self.assertEqual(payload["result_id"], str(event.result_id_snapshot))
        self.assertEqual(payload["card_id"], str(event.composition_id_snapshot))
        self.assertEqual(payload["item_id"], str(event.item_id))
        self.assertEqual(payload["persona_id"], "minimal")
        self.assertTrue(payload["deduplicated"])
        self.assertEqual(payload["engagement_duration_ms"], 42_000)
        self.assertIsNotNone(payload["engagement_recorded_at"])

    def test_click_endpoint_and_deduplication_window_are_stable(self) -> None:
        result_id = uuid.uuid4()
        card_id = uuid.uuid4()
        item_id = uuid.uuid4()

        path = reverse(
            "recommend:recommendation-product-click",
            args=[result_id, card_id, item_id],
        )

        self.assertEqual(
            path,
            (
                f"/api/v1/recommendations/{result_id}/cards/{card_id}/"
                f"items/{item_id}/click/"
            ),
        )
        self.assertEqual(PRODUCT_CLICK_DEDUPLICATION_WINDOW, timedelta(minutes=5))

    def test_engagement_endpoint_and_duration_contract_are_stable(self) -> None:
        click_id = uuid.uuid4()
        path = reverse(
            "recommend:recommendation-product-click-engagement",
            args=[click_id],
        )
        self.assertEqual(
            path,
            f"/api/v1/recommendations/product-clicks/{click_id}/engagement/",
        )

        valid = ProductClickEngagementRequestSerializer(data={"duration_ms": 42_000})
        too_long = ProductClickEngagementRequestSerializer(
            data={"duration_ms": 86_400_001}
        )
        self.assertTrue(valid.is_valid())
        self.assertFalse(too_long.is_valid())
