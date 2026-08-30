from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.chat.models import ChatMessage, ChatRun, ChatSession
from apps.chat.services import identity as identity_service
from apps.lookbook.contracts import recommendation_card_lookbook_id
from apps.lookbook.models import LookbookPost
from apps.recommend.models import (
    OutfitComposition,
    OutfitCompositionItem,
    ProductClickEvent,
    RecommendationFeedback,
    RecommendationResult,
    SavedOutfit,
)


class RecommendationApiTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="recommendation-owner")
        self.other_user = user_model.objects.create_user(username="recommendation-other")
        self.identity = identity_service.get_or_create_member_identity(self.user)
        self.other_identity = identity_service.get_or_create_member_identity(
            self.other_user
        )

    def _result(
        self,
        identity,
        *,
        mode: str = RecommendationResult.Mode.NEW_ITEM,
    ) -> tuple[RecommendationResult, OutfitComposition, OutfitComposition]:
        session = ChatSession.objects.create(identity=identity, mode=mode)
        message = ChatMessage.objects.create(
            session=session,
            sequence=1,
            role=ChatMessage.Role.USER,
            content="내일 입을 옷을 추천해줘",
        )
        run = ChatRun.objects.create(
            session=session,
            request_message=message,
            status=ChatRun.Status.SUCCEEDED,
        )
        result = RecommendationResult.objects.create(
            identity=identity,
            session=session,
            run=run,
            mode=mode,
            dataset_version="goldenset-2026-08-11",
        )
        validated = OutfitComposition.objects.create(
            result=result,
            rank=1,
            status=OutfitComposition.Status.VALIDATED,
            composition_fingerprint=uuid.uuid4().hex * 2,
            total_product_price=49_900,
            validation_reasons=[{"code": "VALID"}],
            warnings=[],
            rationale="출근 상황에 맞춘 단정한 룩이에요.",
        )
        rejected = OutfitComposition.objects.create(
            result=result,
            rank=2,
            status=OutfitComposition.Status.REJECTED,
            validation_reasons=[{"code": "MISSING_REQUIRED_SLOT"}],
            warnings=[],
        )
        OutfitCompositionItem.objects.create(
            composition=validated,
            position=1,
            slot="TOP",
            source_type=OutfitCompositionItem.SourceType.PRODUCT,
            source_id="naver-101",
            source_collection="naver_products",
            source_point_id="naver-point-101",
            template_item_point_id="golden-item-101",
            replacement_score=0.91,
            image_ref="products/naver-101.jpg",
            price_snapshot=49_900,
            reasons=["스타일과 계절이 일치함"],
            note="단정한 인상을 만드는 상의로 골랐어요.",
            item_snapshot={
                "product_name": "아이보리 니트",
                "category_small": "니트",
                "color": "아이보리",
                "product_url": "https://shop.example/items/101",
            },
        )
        return result, validated, rejected

    def test_member_history_returns_only_owned_results_and_validated_cards(self):
        result, validated, _ = self._result(self.identity)
        self._result(self.other_identity)
        self.client.force_authenticate(self.user)

        response = self.client.get(reverse("recommend:recommendation-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        item = response.data["results"][0]
        self.assertEqual(item["result_id"], str(result.id))
        self.assertEqual(item["card_count"], 1)
        self.assertEqual(item["top_card"]["card_id"], str(validated.id))
        self.assertEqual(
            item["top_card"]["items"][0]["display_name"], "아이보리 니트"
        )

    def test_history_validates_mode_and_pagination(self):
        self.client.force_authenticate(self.user)

        invalid_mode = self.client.get(
            reverse("recommend:recommendation-list"), {"mode": "PURSUIT_BASED"}
        )
        invalid_limit = self.client.get(
            reverse("recommend:recommendation-list"), {"limit": 101}
        )

        self.assertEqual(invalid_mode.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid_limit.status_code, status.HTTP_400_BAD_REQUEST)

    def test_guest_cookie_can_read_own_result(self):
        credential = identity_service.issue_guest_identity()
        result, validated, _ = self._result(credential.identity)
        self.client.cookies[settings.CHAT_GUEST_COOKIE_NAME] = credential.token

        detail = self.client.get(
            reverse("recommend:recommendation-detail", args=[result.id])
        )
        card = self.client.get(
            reverse(
                "recommend:recommendation-card-detail",
                args=[result.id, validated.id],
            )
        )

        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["cards"][0]["card_id"], str(validated.id))
        self.assertEqual(
            detail.data["cards"][0]["rationale"],
            "출근 상황에 맞춘 단정한 룩이에요.",
        )
        self.assertEqual(card.status_code, status.HTTP_200_OK)
        self.assertEqual(
            card.data["items"][0]["note"],
            "단정한 인상을 만드는 상의로 골랐어요.",
        )

    def test_card_item_labels_do_not_stringify_empty_arrays(self):
        result, validated, _ = self._result(self.identity)
        item = validated.items.get()
        item.slot = f"기본 상의:{uuid.uuid4()}"
        item.item_snapshot = {
            "product_name": "검정 티셔츠",
            "category_small": "티셔츠",
            "color": [],
        }
        item.save(update_fields=["slot", "item_snapshot"])
        self.client.force_authenticate(self.user)

        response = self.client.get(
            reverse(
                "recommend:recommendation-card-detail",
                args=[result.id, validated.id],
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.data["items"][0]
        self.assertEqual(payload["slot"], item.slot)
        self.assertEqual(payload["category"], "티셔츠")
        self.assertIsNone(payload["color"])

        item.item_snapshot = {**item.item_snapshot, "color": ["검정", "회색"]}
        item.save(update_fields=["item_snapshot"])
        response = self.client.get(
            reverse(
                "recommend:recommendation-card-detail",
                args=[result.id, validated.id],
            )
        )
        self.assertEqual(response.data["items"][0]["color"], "검정, 회색")

    def test_missing_identity_is_401_and_other_owner_is_404(self):
        result, _, _ = self._result(self.identity)

        missing_identity = self.client.get(
            reverse("recommend:recommendation-detail", args=[result.id])
        )
        self.client.force_authenticate(self.other_user)
        other_owner = self.client.get(
            reverse("recommend:recommendation-detail", args=[result.id])
        )

        self.assertEqual(missing_identity.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(other_owner.status_code, status.HTTP_404_NOT_FOUND)

    def test_feedback_put_is_idempotent_and_card_returns_latest_feedback(self):
        result, card, _ = self._result(self.identity)
        self.client.force_authenticate(self.user)
        url = reverse("recommend:recommendation-feedback", args=[result.id, card.id])

        created = self.client.put(
            url,
            {
                "reaction": RecommendationFeedback.Reaction.LIKE,
                "reason_codes": ["STYLE_MATCH", "PRICE_GOOD"],
                "comment": "바로 입어보고 싶어요.",
            },
            format="json",
        )
        updated = self.client.put(
            url,
            {
                "reaction": RecommendationFeedback.Reaction.DISLIKE,
                "reason_codes": ["NOT_MY_STYLE"],
                "comment": "색상이 취향과 달라요.",
            },
            format="json",
        )
        card_response = self.client.get(
            reverse("recommend:recommendation-card-detail", args=[result.id, card.id])
        )

        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertEqual(updated.status_code, status.HTTP_200_OK)
        self.assertEqual(created.data["feedback_id"], updated.data["feedback_id"])
        self.assertEqual(RecommendationFeedback.objects.count(), 1)
        self.assertEqual(card_response.data["feedback"]["reaction"], "DISLIKE")

    def test_feedback_rejects_duplicate_or_malformed_reason_codes(self):
        result, card, _ = self._result(self.identity)
        self.client.force_authenticate(self.user)
        url = reverse("recommend:recommendation-feedback", args=[result.id, card.id])

        duplicate = self.client.put(
            url,
            {"reaction": "LIKE", "reason_codes": ["STYLE_MATCH", "STYLE_MATCH"]},
            format="json",
        )
        malformed = self.client.put(
            url,
            {"reaction": "LIKE", "reason_codes": ["free form"]},
            format="json",
        )

        self.assertEqual(duplicate.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(malformed.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(RecommendationFeedback.objects.exists())

    def test_feedback_cannot_target_rejected_or_other_owners_card(self):
        result, _, rejected = self._result(self.identity)
        other_result, other_card, _ = self._result(self.other_identity)
        self.client.force_authenticate(self.user)
        payload = {"reaction": "LIKE", "reason_codes": []}

        rejected_response = self.client.put(
            reverse(
                "recommend:recommendation-feedback", args=[result.id, rejected.id]
            ),
            payload,
            format="json",
        )
        other_response = self.client.put(
            reverse(
                "recommend:recommendation-feedback",
                args=[other_result.id, other_card.id],
            ),
            payload,
            format="json",
        )

        self.assertEqual(rejected_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(other_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_feedback_delete_is_idempotent_for_an_owned_card(self):
        result, card, _ = self._result(self.identity)
        RecommendationFeedback.objects.create(
            composition=card,
            reaction=RecommendationFeedback.Reaction.LIKE,
        )
        self.client.force_authenticate(self.user)
        url = reverse("recommend:recommendation-feedback", args=[result.id, card.id])

        first = self.client.delete(url)
        second = self.client.delete(url)

        self.assertEqual(first.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(second.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(RecommendationFeedback.objects.exists())

    def test_guest_feedback_survives_member_claim(self):
        credential = identity_service.issue_guest_identity()
        result, card, _ = self._result(credential.identity)
        feedback = RecommendationFeedback.objects.create(
            composition=card,
            reaction=RecommendationFeedback.Reaction.LIKE,
            reason_codes=["STYLE_MATCH"],
        )

        identity_service.claim_guest_identity(self.user, credential.token)
        self.client.force_authenticate(self.user)
        response = self.client.get(
            reverse("recommend:recommendation-detail", args=[result.id])
        )

        feedback.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["cards"][0]["feedback"]["feedback_id"], str(feedback.id)
        )

    def test_member_saves_outfit_idempotently_and_card_reports_state(self):
        result, card, _ = self._result(self.identity)
        self.client.force_authenticate(self.user)
        url = reverse("recommend:recommendation-save", args=[result.id, card.id])

        created = self.client.put(url, {}, format="json")
        repeated = self.client.put(url, {}, format="json")
        card_response = self.client.get(
            reverse("recommend:recommendation-card-detail", args=[result.id, card.id])
        )
        history_response = self.client.get(reverse("recommend:recommendation-list"))

        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertEqual(repeated.status_code, status.HTTP_200_OK)
        self.assertEqual(
            created.data["saved_outfit_id"],
            repeated.data["saved_outfit_id"],
        )
        self.assertEqual(created.data["saved_at"], repeated.data["saved_at"])
        self.assertTrue(created.data["is_saved"])
        self.assertEqual(created.data["card_id"], str(card.id))
        self.assertEqual(SavedOutfit.objects.count(), 1)
        self.assertEqual(LookbookPost.objects.count(), 1)
        lookbook = LookbookPost.objects.get()
        self.assertEqual(
            lookbook.golden_id,
            recommendation_card_lookbook_id(card.id),
        )
        self.assertEqual(lookbook.schedule, card.rationale)
        self.assertEqual(lookbook.wardrobe_links.count(), 1)
        self.assertEqual(
            lookbook.wardrobe_links.get().snapshot["item_name"],
            "아이보리 니트",
        )
        self.assertTrue(card_response.data["is_saved"])
        self.assertTrue(history_response.data["results"][0]["top_card"]["is_saved"])

    def test_saved_outfit_delete_is_idempotent_and_updates_card_state(self):
        result, card, _ = self._result(self.identity)
        self.client.force_authenticate(self.user)
        url = reverse("recommend:recommendation-save", args=[result.id, card.id])
        self.client.put(url, {}, format="json")

        first = self.client.delete(url)
        second = self.client.delete(url)
        card_response = self.client.get(
            reverse("recommend:recommendation-card-detail", args=[result.id, card.id])
        )

        self.assertEqual(first.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(second.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(SavedOutfit.objects.exists())
        self.assertFalse(LookbookPost.objects.exists())
        self.assertFalse(card_response.data["is_saved"])

    def test_deleting_recommendation_lookbook_clears_saved_card_state(self):
        result, card, _ = self._result(self.identity)
        self.client.force_authenticate(self.user)
        save_url = reverse("recommend:recommendation-save", args=[result.id, card.id])
        self.client.put(save_url, {}, format="json")
        lookbook = LookbookPost.objects.get()

        deleted = self.client.delete(
            reverse("lookbook:lookbook-detail", args=[lookbook.id])
        )
        card_response = self.client.get(
            reverse("recommend:recommendation-card-detail", args=[result.id, card.id])
        )

        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(SavedOutfit.objects.exists())
        self.assertFalse(card_response.data["is_saved"])

    def test_guest_cannot_save_outfit(self):
        credential = identity_service.issue_guest_identity()
        result, card, _ = self._result(credential.identity)
        self.client.cookies[settings.CHAT_GUEST_COOKIE_NAME] = credential.token

        response = self.client.put(
            reverse("recommend:recommendation-save", args=[result.id, card.id]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(SavedOutfit.objects.exists())

    def test_save_rejects_other_owner_and_unvalidated_card(self):
        result, _, rejected = self._result(self.identity)
        other_result, other_card, _ = self._result(self.other_identity)
        self.client.force_authenticate(self.user)

        rejected_response = self.client.put(
            reverse(
                "recommend:recommendation-save",
                args=[result.id, rejected.id],
            ),
            {},
            format="json",
        )
        other_response = self.client.put(
            reverse(
                "recommend:recommendation-save",
                args=[other_result.id, other_card.id],
            ),
            {},
            format="json",
        )

        self.assertEqual(rejected_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(other_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(SavedOutfit.objects.exists())

    def test_product_click_is_deduplicated_within_five_minutes(self):
        result, card, _ = self._result(self.identity)
        item = card.items.get()
        self.client.force_authenticate(self.user)
        url = reverse(
            "recommend:recommendation-product-click",
            args=[result.id, card.id, item.id],
        )

        created = self.client.post(url, {}, format="json")
        repeated = self.client.post(url, {}, format="json")

        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertEqual(repeated.status_code, status.HTTP_200_OK)
        self.assertEqual(
            created.data["product_click_id"],
            repeated.data["product_click_id"],
        )
        self.assertFalse(created.data["deduplicated"])
        self.assertTrue(repeated.data["deduplicated"])
        self.assertEqual(created.data["result_id"], str(result.id))
        self.assertEqual(created.data["card_id"], str(card.id))
        self.assertEqual(created.data["item_id"], str(item.id))
        self.assertIsNone(created.data["persona_id"])
        self.assertEqual(created.data["source_collection"], item.source_collection)
        self.assertEqual(created.data["source_id"], item.source_id)
        self.assertEqual(ProductClickEvent.objects.count(), 1)

    def test_product_click_after_deduplication_window_creates_new_event(self):
        result, card, _ = self._result(self.identity)
        item = card.items.get()
        old_event = ProductClickEvent.objects.create(
            user=self.user,
            item=item,
            result_id_snapshot=result.id,
            composition_id_snapshot=card.id,
            persona_id=result.persona_id,
            source_collection=item.source_collection,
            source_id=item.source_id,
        )
        ProductClickEvent.objects.filter(pk=old_event.pk).update(
            created_at=timezone.now() - timedelta(minutes=6)
        )
        self.client.force_authenticate(self.user)

        response = self.client.post(
            reverse(
                "recommend:recommendation-product-click",
                args=[result.id, card.id, item.id],
            ),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(response.data["deduplicated"])
        self.assertEqual(ProductClickEvent.objects.count(), 2)

    def test_product_click_engagement_keeps_largest_duration_for_owner(self):
        result, card, _ = self._result(self.identity)
        item = card.items.get()
        event = ProductClickEvent.objects.create(
            user=self.user,
            item=item,
            result_id_snapshot=result.id,
            composition_id_snapshot=card.id,
            persona_id=result.persona_id,
            source_collection=item.source_collection,
            source_id=item.source_id,
        )
        self.client.force_authenticate(self.user)
        url = reverse(
            "recommend:recommendation-product-click-engagement",
            args=[event.id],
        )

        updated = self.client.patch(url, {"duration_ms": 42_000}, format="json")
        retried = self.client.patch(url, {"duration_ms": 20_000}, format="json")

        self.assertEqual(updated.status_code, status.HTTP_200_OK)
        self.assertEqual(retried.status_code, status.HTTP_200_OK)
        self.assertEqual(updated.data["engagement_duration_ms"], 42_000)
        self.assertEqual(retried.data["engagement_duration_ms"], 42_000)
        self.assertIsNotNone(updated.data["engagement_recorded_at"])

        other_client = APIClient()
        other_client.force_authenticate(self.other_user)
        rejected = other_client.patch(url, {"duration_ms": 50_000}, format="json")
        self.assertEqual(rejected.status_code, status.HTTP_404_NOT_FOUND)

    def test_product_click_requires_member(self):
        credential = identity_service.issue_guest_identity()
        result, card, _ = self._result(credential.identity)
        item = card.items.get()
        self.client.cookies[settings.CHAT_GUEST_COOKIE_NAME] = credential.token

        response = self.client.post(
            reverse(
                "recommend:recommendation-product-click",
                args=[result.id, card.id, item.id],
            ),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(ProductClickEvent.objects.exists())

    def test_product_click_rejects_other_owner_and_wardrobe_item(self):
        result, card, _ = self._result(self.identity)
        other_result, other_card, _ = self._result(self.other_identity)
        other_item = other_card.items.get()
        wardrobe_item = OutfitCompositionItem.objects.create(
            composition=card,
            position=2,
            slot="BOTTOM",
            source_type=OutfitCompositionItem.SourceType.WARDROBE,
            source_id="wardrobe-202",
            source_collection="wardrobe_items",
            source_point_id="wardrobe-point-202",
            template_item_point_id="golden-item-202",
            replacement_score=0.88,
            image_ref="wardrobe/202.jpg",
            reasons=["옷장 아이템"],
            item_snapshot={"item_name": "보유 중인 바지"},
        )
        self.client.force_authenticate(self.user)

        other_response = self.client.post(
            reverse(
                "recommend:recommendation-product-click",
                args=[other_result.id, other_card.id, other_item.id],
            ),
            {},
            format="json",
        )
        wardrobe_response = self.client.post(
            reverse(
                "recommend:recommendation-product-click",
                args=[result.id, card.id, wardrobe_item.id],
            ),
            {},
            format="json",
        )

        self.assertEqual(other_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(wardrobe_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(ProductClickEvent.objects.exists())
