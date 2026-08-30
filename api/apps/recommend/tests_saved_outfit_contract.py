from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from apps.chat.models import ChatIdentity
from apps.recommend.models import (
    OutfitComposition,
    RecommendationResult,
    SavedOutfit,
)
from apps.recommend.serializers import SavedOutfitSerializer


class SavedOutfitContractTests(SimpleTestCase):
    @staticmethod
    def _saved_outfit(*, owner_id: int = 1, user_id: int = 1) -> SavedOutfit:
        identity = ChatIdentity(
            id=uuid.uuid4(),
            user_id=owner_id,
            identity_type=ChatIdentity.IdentityType.MEMBER,
        )
        result = RecommendationResult(id=uuid.uuid4(), identity=identity)
        composition = OutfitComposition(
            id=uuid.uuid4(),
            result=result,
            status=OutfitComposition.Status.VALIDATED,
        )
        return SavedOutfit(
            id=uuid.uuid4(),
            user_id=user_id,
            composition=composition,
            created_at=timezone.now(),
        )

    def test_member_owner_can_save_validated_outfit(self) -> None:
        self._saved_outfit().clean()

    def test_other_member_or_unvalidated_outfit_is_rejected(self) -> None:
        other_member = self._saved_outfit(owner_id=1, user_id=2)
        rejected = self._saved_outfit()
        rejected.composition.status = OutfitComposition.Status.REJECTED

        with self.assertRaises(ValidationError):
            other_member.clean()
        with self.assertRaises(ValidationError):
            rejected.clean()

    def test_serializer_exposes_stable_saved_contract(self) -> None:
        saved_outfit = self._saved_outfit()

        payload = SavedOutfitSerializer(saved_outfit).data

        self.assertEqual(payload["saved_outfit_id"], str(saved_outfit.id))
        self.assertEqual(payload["card_id"], str(saved_outfit.composition_id))
        self.assertTrue(payload["is_saved"])
        self.assertIsNotNone(payload["saved_at"])

    def test_save_endpoint_has_stable_route(self) -> None:
        result_id = uuid.uuid4()
        card_id = uuid.uuid4()

        path = reverse("recommend:recommendation-save", args=[result_id, card_id])

        self.assertEqual(
            path,
            f"/api/v1/recommendations/{result_id}/cards/{card_id}/save/",
        )
