from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from apps.chat.services.recommendation_pipeline import (
    ChatRecommendationPipeline,
    OutfitCompositionFailed,
)
from apps.recommend.models import OutfitComposition as OutfitCompositionModel
from apps.recommend.serializers import RecommendationCardSerializer
from apps.recommend.services.item_retriever import ItemSource
from apps.recommend.services.outfit_types import (
    OutfitComposition,
    OutfitItem,
    RecommendationMode,
)
from apps.recommend.services.validator import OutfitValidationResult


def _anchor_item() -> OutfitItem:
    return OutfitItem(
        slot_id="OUTER:template-outer",
        template_point_id="template-outer",
        category_large="아우터",
        layer_role="OUTER",
        source_type=ItemSource.PRODUCT,
        source_id="naver-anchor",
        source_collection="products_naver_v1",
        point_id="product-point",
        image_ref="products/naver-anchor.webp",
        price=50_000,
        score=0.91,
        reasons=(
            "신규 아이템 추천: 구매 가능한 상품 추가",
            "공유 옷을 참고한 고정 신규 상품",
            "공유 옷 이미지 유사도: 0.9100",
        ),
        payload={
            "title": "유사 재킷",
            "price": 50_000,
            "match_type": "VISUAL_SIMILAR",
            "selection_role": "PINNED_REFERENCE_ANCHOR",
        },
    )


def _composition() -> OutfitComposition:
    return OutfitComposition(
        mode=RecommendationMode.NEW_ITEM,
        items=(_anchor_item(),),
        missing_slot_ids=(),
        total_product_price=50_000,
    )


class ReferenceMatchContractTests(SimpleTestCase):
    def test_model_and_card_serializer_expose_reference_match(self) -> None:
        field = OutfitCompositionModel._meta.get_field("reference_match")

        self.assertEqual(field.get_default(), {})
        self.assertIn("match_type", field.db_comment)
        self.assertIn("reference_match", RecommendationCardSerializer.Meta.fields)

    def test_model_rejects_non_anchor_reference_match(self) -> None:
        row = OutfitCompositionModel(
            rank=1,
            reference_match={
                "schema_version": "1.0",
                "match_type": "VISUAL_SIMILAR",
                "selection_role": "NOT_PINNED",
                "source_type": "PRODUCT",
                "source_id": "naver-anchor",
                "source_collection": "products_naver_v1",
                "source_point_id": "product-point",
                "template_item_point_id": "template-outer",
                "score": 0.91,
                "reasons": ["이미지 유사"],
            },
        )

        with self.assertRaises(ValidationError):
            row.clean()

    @patch(
        "apps.chat.services.recommendation_pipeline.OutfitCompositionItem.objects.create"
    )
    @patch(
        "apps.chat.services.recommendation_pipeline.OutfitCompositionModel.objects.create"
    )
    def test_persistence_writes_matching_reasons(
        self,
        create_composition: Mock,
        create_item: Mock,
    ) -> None:
        create_composition.return_value = SimpleNamespace(pk="composition-id")
        composition = _composition()
        candidate = SimpleNamespace(
            composition=composition,
            validation=OutfitValidationResult(
                issues=(),
                effective_total_product_price=50_000,
            ),
        )
        pipeline = object.__new__(ChatRecommendationPipeline)

        pipeline._persist_composition(
            result=SimpleNamespace(
                pk="result-id",
                mode="NEW_ITEM",
                run=SimpleNamespace(reference_snapshot={}),
            ),
            rank=1,
            candidate=candidate,
        )

        reference_match = create_composition.call_args.kwargs["reference_match"]
        self.assertEqual(reference_match["source_id"], "naver-anchor")
        self.assertEqual(reference_match["match_type"], "VISUAL_SIMILAR")
        self.assertIn("공유 옷 이미지 유사도: 0.9100", reference_match["reasons"])
        create_item.assert_called_once()

    def test_approved_payload_passes_reference_match_to_explanation(self) -> None:
        reference_match = ChatRecommendationPipeline._reference_match(_composition())

        class Related:
            """_approved_payload가 쓰는 쿼리셋 흉내.

            filter/order_by까지 받는 이유는 설명 계약이 검증부와 **같은 필터·
            같은 정렬**을 보도록 바뀌었기 때문이다. 여기서는 스텁이라 조건을
            무시하고 자기 자신을 돌려준다.
            """

            def __init__(self, values):
                self.values = values

            def prefetch_related(self, *_args):
                return self

            def filter(self, **_kwargs):
                return self

            def order_by(self, *_args):
                return self

            def __iter__(self):
                return iter(self.values)

            def all(self):
                return self.values

        item = SimpleNamespace(
            slot="OUTER:template-outer",
            source_type="PRODUCT",
            item_snapshot={"title": "유사 재킷"},
            price_snapshot=50_000,
            reasons=["공유 옷 이미지 유사도: 0.9100"],
        )
        composition = SimpleNamespace(
            rank=1,
            total_product_price=50_000,
            reference_match=reference_match,
            warnings=[],
            validation_reasons=[],
            items=Related([item]),
        )
        result = SimpleNamespace(
            id="result-id",
            mode="NEW_ITEM",
            compositions=Related([composition]),
        )

        payload = ChatRecommendationPipeline._approved_payload(result)

        self.assertEqual(
            payload["compositions"][0]["reference_match"],
            reference_match,
        )
        self.assertIn(
            "공유 옷 이미지 유사도: 0.9100",
            payload["compositions"][0]["reference_match"]["reasons"],
        )

    def test_persistence_boundary_rejects_friend_original(self) -> None:
        friend = _anchor_item()
        leaked = OutfitItem(
            **{
                **friend.__dict__,
                "source_type": ItemSource.WARDROBE,
                "source_id": "friend-original",
                "source_collection": "wardrobe_items",
                "point_id": "friend-point",
                "price": None,
                "payload": {
                    **friend.payload,
                    "item_id": "friend-original",
                },
            }
        )
        composition = OutfitComposition(
            mode=RecommendationMode.WARDROBE_BASED,
            items=(leaked,),
            missing_slot_ids=(),
            total_product_price=0,
        )
        result = SimpleNamespace(
            mode="WARDROBE_BASED",
            run=SimpleNamespace(
                reference_snapshot={
                    "wardrobe_item_id": "friend-original",
                    "qdrant_point_id": "friend-point",
                }
            ),
        )

        with self.assertRaises(OutfitCompositionFailed):
            ChatRecommendationPipeline._validate_composition_for_persistence(
                result=result,
                composition=composition,
            )
