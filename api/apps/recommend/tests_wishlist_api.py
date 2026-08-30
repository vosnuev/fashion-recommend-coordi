"""찜(판매 상품) API 계약 — 담기·중복·소유권·빼기.

상품을 이름이 아니라 카탈로그 식별자(source_collection·source_id)로 묶는다는 것이
이 기능의 핵심이라, 같은 상품을 두 번 담아도 목록이 하나로 유지되는지를 먼저 본다.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalog.models import NaverProduct
from apps.chat.models import ChatMessage, ChatRun, ChatSession
from apps.chat.services import identity as identity_service
from apps.recommend.models import (
    OutfitComposition,
    OutfitCompositionItem,
    RecommendationResult,
    WishlistItem,
)


class WishlistApiTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="wishlist-owner")
        self.other_user = user_model.objects.create_user(username="wishlist-other")
        self.identity = identity_service.get_or_create_member_identity(self.user)
        self.other_identity = identity_service.get_or_create_member_identity(
            self.other_user
        )

    def _card(self, identity) -> tuple[RecommendationResult, OutfitComposition]:
        session = ChatSession.objects.create(
            identity=identity,
            mode=RecommendationResult.Mode.NEW_ITEM,
        )
        message = ChatMessage.objects.create(
            session=session,
            sequence=1,
            role=ChatMessage.Role.USER,
            content="주말에 입을 옷 추천해줘",
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
            mode=RecommendationResult.Mode.NEW_ITEM,
            dataset_version="goldenset-2026-08-11",
        )
        card = OutfitComposition.objects.create(
            result=result,
            rank=1,
            status=OutfitComposition.Status.VALIDATED,
            composition_fingerprint=uuid.uuid4().hex * 2,
            total_product_price=49_900,
            validation_reasons=[{"code": "VALID"}],
            warnings=[],
        )
        return result, card

    @staticmethod
    def _product_item(card: OutfitComposition, **overrides) -> OutfitCompositionItem:
        fields = {
            "composition": card,
            "position": 1,
            "slot": "TOP",
            "source_type": OutfitCompositionItem.SourceType.PRODUCT,
            "source_id": "naver-101",
            # 운영에서 실제로 적히는 이름을 쓴다 — 가짜 이름으로 테스트하면
            # 카탈로그 연결이 끊긴 것을 못 잡는다.
            "source_collection": settings.PRODUCT_NAVER_QDRANT_COLLECTION,
            "source_point_id": "naver-point-101",
            "template_item_point_id": "golden-item-101",
            "image_ref": "products/naver-101.jpg",
            "price_snapshot": 49_900,
            "reasons": ["스타일과 계절이 일치함"],
            "item_snapshot": {
                "product_name": "아이보리 니트",
                "product_url": "https://shop.example/items/101",
            },
        }
        fields.update(overrides)
        return OutfitCompositionItem.objects.create(**fields)

    def _add_url(self, result, card, item) -> str:
        return reverse(
            "recommend:wishlist-add",
            kwargs={"result_id": result.id, "card_id": card.id, "item_id": item.id},
        )

    def test_login_required(self) -> None:
        self.assertEqual(self.client.get(reverse("recommend:wishlist")).status_code, 401)

    def test_add_snapshots_item_and_fills_brand_from_catalog(self) -> None:
        """브랜드·판매처는 추천 응답에 없다 — 담는 순간 카탈로그에서 채워야 한다."""

        result, card = self._card(self.identity)
        item = self._product_item(card)
        NaverProduct.objects.create(
            naver_product_id="naver-101",
            title="아이보리 케이블 니트",
            title_raw="아이보리 케이블 니트",
            brand="COS",
            link="https://shopping.naver.com/goods/101",
            image_url="https://cdn.example/naver-101.jpg",
            lprice=49_900,
            collected_at=timezone.now(),
        )
        self.client.force_authenticate(self.user)

        response = self.client.post(self._add_url(result, card, item))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["brand"], "COS")
        self.assertEqual(
            response.data["purchase_url"], "https://shopping.naver.com/goods/101"
        )
        self.assertEqual(response.data["source_id"], "naver-101")
        self.assertEqual(response.data["slot"], "TOP")
        # 이름은 추천이 보여준 것을 유지한다 — 화면에서 본 이름과 달라지면 안 된다.
        self.assertEqual(response.data["display_name"], "아이보리 니트")
        self.assertEqual(response.data["price_snapshot"], 49_900)

    def test_same_product_from_another_card_does_not_duplicate(self) -> None:
        result, card = self._card(self.identity)
        first = self._product_item(card)
        second_card = OutfitComposition.objects.create(
            result=result,
            rank=2,
            status=OutfitComposition.Status.VALIDATED,
            composition_fingerprint=uuid.uuid4().hex * 2,
            validation_reasons=[],
            warnings=[],
        )
        # 같은 상품이 다른 카드에 다른 이름으로 실려 온 상황
        second = self._product_item(
            second_card,
            item_snapshot={"product_name": "아이보리 니트 (리스타일)"},
        )
        self.client.force_authenticate(self.user)

        created = self.client.post(self._add_url(result, card, first))
        again = self.client.post(self._add_url(result, second_card, second))

        self.assertEqual(created.status_code, 201)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.data["wish_id"], created.data["wish_id"])
        self.assertEqual(WishlistItem.objects.filter(user=self.user).count(), 1)

    def test_wardrobe_item_and_other_users_product_are_not_found(self) -> None:
        result, card = self._card(self.identity)
        wardrobe = self._product_item(
            card,
            position=2,
            slot="BOTTOM",
            source_type=OutfitCompositionItem.SourceType.WARDROBE,
            source_id="wardrobe-202",
            source_collection="wardrobe_items",
        )
        other_result, other_card = self._card(self.other_identity)
        other_item = self._product_item(other_card, source_id="naver-909")
        self.client.force_authenticate(self.user)

        self.assertEqual(
            self.client.post(self._add_url(result, card, wardrobe)).status_code, 404
        )
        self.assertEqual(
            self.client.post(
                self._add_url(other_result, other_card, other_item)
            ).status_code,
            404,
        )
        self.assertEqual(WishlistItem.objects.count(), 0)

    def test_list_and_remove(self) -> None:
        result, card = self._card(self.identity)
        item = self._product_item(card)
        self.client.force_authenticate(self.user)
        wish_id = self.client.post(self._add_url(result, card, item)).data["wish_id"]

        listed = self.client.get(reverse("recommend:wishlist"))
        removed = self.client.delete(
            reverse("recommend:wishlist-item", kwargs={"wish_id": wish_id})
        )
        emptied = self.client.get(reverse("recommend:wishlist"))

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.data), 1)
        self.assertEqual(removed.status_code, 204)
        self.assertEqual(len(emptied.data), 0)

    def test_other_users_wish_cannot_be_removed(self) -> None:
        result, card = self._card(self.other_identity)
        item = self._product_item(card)
        self.client.force_authenticate(self.other_user)
        wish_id = self.client.post(self._add_url(result, card, item)).data["wish_id"]

        self.client.force_authenticate(self.user)
        response = self.client.delete(
            reverse("recommend:wishlist-item", kwargs={"wish_id": wish_id})
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(WishlistItem.objects.count(), 1)
