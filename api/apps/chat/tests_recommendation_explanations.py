from __future__ import annotations

from django.test import TestCase

from apps.chat.models import ChatIdentity, ChatMessage, ChatRun, ChatSession
from apps.chat.services.openai_adapter import (
    RecommendationExplanation,
    RecommendationExplanationItem,
    RecommendationExplanationOutfit,
)
from apps.chat.services.recommendation_explanations import (
    apply_recommendation_explanation,
)
from apps.recommend.models import (
    OutfitComposition,
    OutfitCompositionItem,
    RecommendationResult,
)


class RecommendationExplanationTests(TestCase):
    def setUp(self) -> None:
        identity = ChatIdentity.objects.create(
            identity_type=ChatIdentity.IdentityType.GUEST,
            guest_token_hash="e" * 64,
            expires_at="2099-01-01T00:00:00Z",
        )
        session = ChatSession.objects.create(
            identity=identity,
            mode=ChatSession.Mode.NEW_ITEM,
        )
        message = ChatMessage.objects.create(
            session=session,
            sequence=1,
            role=ChatMessage.Role.USER,
            content="출근용 미니멀 코디 추천해줘",
        )
        run = ChatRun.objects.create(session=session, request_message=message)
        self.result = RecommendationResult.objects.create(
            identity=identity,
            session=session,
            run=run,
            mode=RecommendationResult.Mode.NEW_ITEM,
            dataset_version="explanation-test-v1",
        )
        self.card = OutfitComposition.objects.create(
            result=self.result,
            rank=1,
            status=OutfitComposition.Status.VALIDATED,
            composition_fingerprint="a" * 64,
            total_product_price=49_900,
            validation_reasons=[{"code": "VALID"}],
            warnings=[],
        )
        self.item = OutfitCompositionItem.objects.create(
            composition=self.card,
            position=1,
            slot="TOP",
            source_type=OutfitCompositionItem.SourceType.PRODUCT,
            source_id="product-101",
            source_collection="products",
            source_point_id="product-point-101",
            template_item_point_id="golden-item-101",
            replacement_score=0.91,
            image_ref="products/101.jpg",
            price_snapshot=49_900,
            reasons=["대분류 일치"],
            item_snapshot={"product_name": "아이보리 니트", "color": "아이보리"},
        )
        self.conditions = {
            "occasion": "출근",
            "styles": ["미니멀"],
        }

    def test_valid_explanation_is_normalized_and_persisted(self) -> None:
        explanation = RecommendationExplanation(
            opening="### 추천 룩\n- **출근용으로 정리했어요.**",
            outfits=[
                RecommendationExplanationOutfit(
                    outfit_index=1,
                    rationale="**미니멀한 출근 분위기**에 맞는 룩이에요.",
                    items=[
                        RecommendationExplanationItem(
                            item_index=1,
                            note="단정한 인상을 만드는 상의로 골랐어요.",
                            attribute_claims=[],
                        )
                    ],
                )
            ],
        )

        applied = apply_recommendation_explanation(
            result=self.result,
            explanation=explanation,
            mode=RecommendationResult.Mode.NEW_ITEM,
            budget=60_000,
            conditions=self.conditions,
            weather={},
            recent_messages=[],
        )

        self.card.refresh_from_db()
        self.item.refresh_from_db()
        self.assertFalse(applied.fallback_used)
        self.assertEqual(applied.opening, "추천 룩\n출근용으로 정리했어요.")
        self.assertEqual(
            self.card.rationale,
            "미니멀한 출근 분위기에 맞는 룩이에요.",
        )
        self.assertEqual(
            self.item.note,
            "단정한 인상을 만드는 상의로 골랐어요.",
        )

    def test_mismatched_item_index_uses_rule_fallback_for_whole_result(self) -> None:
        explanation = RecommendationExplanation(
            opening="준비했어요.",
            outfits=[
                RecommendationExplanationOutfit(
                    outfit_index=1,
                    rationale="모델이 만든 설명",
                    items=[
                        RecommendationExplanationItem(
                            item_index=2,
                            note="모델이 만든 아이템 설명",
                            attribute_claims=[],
                        )
                    ],
                )
            ],
        )

        applied = apply_recommendation_explanation(
            result=self.result,
            explanation=explanation,
            mode=RecommendationResult.Mode.NEW_ITEM,
            budget=60_000,
            conditions=self.conditions,
            weather={},
            recent_messages=[],
        )

        self.card.refresh_from_db()
        self.item.refresh_from_db()
        self.assertTrue(applied.fallback_used)
        self.assertEqual(
            applied.fallback_reason,
            "RECOMMENDATION_EXPLANATION_CONTRACT_FAILED",
        )
        self.assertIn("미니멀", applied.opening)
        self.assertNotEqual(self.card.rationale, "모델이 만든 설명")
        self.assertIn("카테고리", self.item.note)

    def test_missing_llm_explanation_uses_rule_fallback(self) -> None:
        applied = apply_recommendation_explanation(
            result=self.result,
            explanation=None,
            mode=RecommendationResult.Mode.NEW_ITEM,
            budget=None,
            conditions=self.conditions,
            weather={},
            recent_messages=[],
            fallback_reason="CHAT_LLM_UNAVAILABLE",
        )

        self.card.refresh_from_db()
        self.item.refresh_from_db()
        self.assertTrue(applied.fallback_used)
        self.assertEqual(applied.fallback_reason, "CHAT_LLM_UNAVAILABLE")
        self.assertTrue(applied.opening.startswith("안녕하세요!"))
        self.assertTrue(self.card.rationale)
        self.assertTrue(self.item.note)

    def test_nonempty_attribute_claims_use_rule_fallback(self) -> None:
        explanation = RecommendationExplanation(
            opening="준비했어요.",
            outfits=[
                RecommendationExplanationOutfit(
                    outfit_index=1,
                    rationale="출근용 룩이에요.",
                    items=[
                        RecommendationExplanationItem(
                            item_index=1,
                            note="울 소재라서 골랐어요.",
                            attribute_claims=["울 소재"],
                        )
                    ],
                )
            ],
        )

        applied = apply_recommendation_explanation(
            result=self.result,
            explanation=explanation,
            mode=RecommendationResult.Mode.NEW_ITEM,
            budget=None,
            conditions=self.conditions,
            weather={},
            recent_messages=[],
        )

        self.item.refresh_from_db()
        self.assertTrue(applied.fallback_used)
        self.assertNotIn("울 소재", self.item.note)
