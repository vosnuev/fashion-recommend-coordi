from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from apps.chat.models import ChatSession
from apps.chat.services.openai_adapter import (
    RecommendationConditions,
    TurnAnalysis,
)
from apps.chat.services.recommendation_pipeline import ChatRecommendationPipeline
from apps.recommend.services.item_retriever import (
    ItemCandidate,
    ItemRetrievalResult,
    ItemSource,
    TemplateItem,
)
from apps.recommend.services.new_item_composer import NewItemOutfitComposer
from apps.recommend.services.retriever import OutfitCandidate, RetrievalResult
from apps.recommend.services.shared_reference_anchor import PinnedReferenceAnchor
from apps.recommend.services.shared_reference_loader import (
    ReferenceSearchExclusions,
    SharedReferenceSearchBasis,
    SharedReferenceTags,
)
from apps.recommend.services.validator import OutfitValidationResult


def _id() -> str:
    return str(uuid.uuid4())


def _reference() -> SharedReferenceSearchBasis:
    source_id = _id()
    return SharedReferenceSearchBasis(
        schema_version="1.0",
        shared_item_id=_id(),
        room_id=_id(),
        source_wardrobe_item_id=source_id,
        collection_name="wardrobe_items",
        point_id=source_id,
        embedding_version="fashionsiglip-v1",
        image_s3_key="wardrobe/friend.webp",
        image_vector=(1.0, 0.0),
        text_vector=(0.0, 1.0),
        tags=SharedReferenceTags(
            item_name="친구 재킷",
            category_large="아우터",
            category_small="재킷",
            season=("가을",),
            style=("미니멀",),
            color="검정",
            pattern="무지",
            fit="오버핏",
            material="울",
            sleeve="긴소매",
            length="기본",
            usage=("출근",),
            layer_role="OUTER",
            layer_order=3,
        ),
        exclusions=ReferenceSearchExclusions(
            wardrobe_item_ids=(source_id,),
            qdrant_point_ids=(source_id,),
        ),
    )


def _product(
    source_id: str,
    *,
    category_large: str,
    layer_role: str,
    price: int,
    score: float,
) -> ItemCandidate:
    return ItemCandidate(
        point_id=f"point-{source_id}",
        source_type=ItemSource.PRODUCT,
        source_id=source_id,
        source_collection="products_naver_v1",
        score=score,
        reasons=("유사 상품",),
        payload={
            "source": "naver",
            "external_product_id": source_id,
            "title": source_id,
            "image_url": f"https://example.com/{source_id}.jpg",
            "price": price,
            "category_large": category_large,
            "layer_role": layer_role,
            "tagging_status": "tagged",
        },
    )


class SharedReferenceRecommendationPipelineTests(SimpleTestCase):
    @staticmethod
    def _analysis() -> TurnAnalysis:
        return TurnAnalysis(
            action="RECOMMEND",
            target_mode="CURRENT",
            search_query="친구 재킷 같은 출근 코디",
            conditions=RecommendationConditions(
                occasion="출근",
                occasion_kind="FORMAL",
                season="가을",
                presentation_groups=[],
                styles=["미니멀"],
                colors=[],
                fits=[],
                avoided_styles=[],
                avoided_colors=[],
                excluded_source_ids=[],
                budget=100_000,
            ),
            clarification_question="",
            response_text="",
        )

    def _pipeline(self):
        reference = _reference()
        pinned_product = _product(
            "pinned-product",
            category_large="아우터",
            layer_role="OUTER",
            price=50_000,
            score=0.91,
        )
        pinned_product.payload["selection_role"] = "PINNED_REFERENCE_ANCHOR"
        pinned_product.payload["match_type"] = "VISUAL_SIMILAR"
        anchor = PinnedReferenceAnchor(
            reference=reference,
            candidate=pinned_product,
            match_type="VISUAL_SIMILAR",
        )
        resolver = Mock()
        resolver.resolve.return_value = anchor

        golden = Mock()
        golden.retrieve.return_value = RetrievalResult(
            candidates=(
                OutfitCandidate(
                    point_id="golden-outfit",
                    golden_id="golden-1",
                    score=0.9,
                    similarity=0.9,
                    payload={
                        "item_point_ids": ["template-outer", "template-bottom"],
                        "dataset_version": "goldenset-v1",
                    },
                ),
            ),
            search_mode="text",
        )

        outer_result = ItemRetrievalResult(
            template=TemplateItem(
                point_id="template-outer",
                payload={
                    "category_large": "아우터",
                    "category_small": "재킷",
                    "layer_role": "OUTER",
                },
            ),
            candidates=(
                _product(
                    "higher-score-product",
                    category_large="아우터",
                    layer_role="OUTER",
                    price=40_000,
                    score=0.99,
                ),
            ),
            vector_name="image",
        )
        bottom_result = ItemRetrievalResult(
            template=TemplateItem(
                point_id="template-bottom",
                payload={
                    "category_large": "하의",
                    "category_small": "슬랙스",
                    "layer_role": "BOTTOM",
                },
            ),
            candidates=(
                _product(
                    "bottom-a",
                    category_large="하의",
                    layer_role="BOTTOM",
                    price=20_000,
                    score=0.9,
                ),
                _product(
                    "bottom-b",
                    category_large="하의",
                    layer_role="BOTTOM",
                    price=25_000,
                    score=0.8,
                ),
            ),
            vector_name="image",
        )
        item_retriever = Mock()
        item_retriever.retrieve.side_effect = lambda request: {
            "template-outer": outer_result,
            "template-bottom": bottom_result,
        }[request.template_item_point_id]
        validator = Mock()
        validator.validate.side_effect = lambda composition, **_: (
            OutfitValidationResult(
                issues=(),
                effective_total_product_price=composition.total_product_price,
            )
        )
        pipeline = ChatRecommendationPipeline(
            golden_retriever=golden,
            item_retriever=item_retriever,
            wardrobe_composer=Mock(),
            new_item_composer=NewItemOutfitComposer(),
            validator=validator,
            reference_anchor_resolver=resolver,
        )
        return pipeline, golden, item_retriever, resolver

    def test_default_and_stylist_candidates_always_include_pinned_anchor(self) -> None:
        for response_mode in (
            ChatSession.ResponseMode.DEFAULT,
            ChatSession.ResponseMode.STYLIST,
        ):
            with self.subTest(response_mode=response_mode):
                pipeline, golden, item_retriever, resolver = self._pipeline()
                session = SimpleNamespace(
                    pk=_id(),
                    identity_id=_id(),
                    identity=SimpleNamespace(user_id=None),
                    mode=ChatSession.Mode.NEW_ITEM,
                )
                run = SimpleNamespace(
                    pk=_id(),
                    session=session,
                    # 최근 골든 템플릿 제외 조회가 세션 단위라 실제 ChatRun처럼
                    # session_id를 갖춰야 한다.
                    session_id=session.pk,
                    response_mode=response_mode,
                    reference_snapshot={"type": "SHARED_WARDROBE_ITEM"},
                )

                generated = pipeline.generate_candidates(
                    run=run,
                    context={
                        "profile": {
                            "pursuit": None,
                            "category_budgets": {"아우터": 60_000},
                        },
                        "weather": {"temperature": 18},
                        "current_request": "친구 재킷 같은 출근 코디",
                    },
                    analysis=self._analysis(),
                )

                self.assertTrue(generated.candidates)
                for row in generated.candidates:
                    source_ids = [item.source_id for item in row.composition.items]
                    self.assertIn("pinned-product", source_ids)
                    self.assertNotIn("higher-score-product", source_ids)
                    reference_match = pipeline._reference_match(row.composition)
                    self.assertEqual(
                        reference_match["selection_role"],
                        "PINNED_REFERENCE_ANCHOR",
                    )
                    self.assertEqual(
                        reference_match["source_id"],
                        "pinned-product",
                    )
                    self.assertTrue(reference_match["reasons"])
                validation_context = pipeline.validator.validate.call_args.kwargs[
                    "context"
                ]
                self.assertEqual(
                    validation_context.reference.anchor_identity,
                    (
                        "PRODUCT",
                        "products_naver_v1",
                        "pinned-product",
                    ),
                )
                self.assertNotIn(
                    "pinned-product",
                    validation_context.reference.original_wardrobe_item_ids,
                )
                retrieval_request = golden.retrieve.call_args.args[0]
                self.assertEqual(
                    retrieval_request.required_item_categories,
                    ("아우터",),
                )
                self.assertEqual(
                    retrieval_request.required_item_layer_roles,
                    ("OUTER",),
                )
                self.assertEqual(item_retriever.retrieve.call_count, 2)
                resolver.resolve.assert_called_once()

    def test_pipeline_rejects_composer_output_that_drops_anchor(self) -> None:
        pipeline, _, _, _ = self._pipeline()
        pipeline.new_item_composer = Mock()
        pipeline.new_item_composer.compose.return_value = SimpleNamespace(
            compositions=(
                SimpleNamespace(
                    items=(),
                    total_product_price=0,
                ),
            )
        )
        session = SimpleNamespace(
            pk=_id(),
            identity_id=_id(),
            identity=SimpleNamespace(user_id=None),
            mode=ChatSession.Mode.NEW_ITEM,
        )
        run = SimpleNamespace(
            pk=_id(),
            session=session,
            session_id=session.pk,
            response_mode=ChatSession.ResponseMode.DEFAULT,
            reference_snapshot={"type": "SHARED_WARDROBE_ITEM"},
        )

        with self.assertRaisesRegex(RuntimeError, "검증 가능한 최종 조합"):
            pipeline.generate_candidates(
                run=run,
                context={
                    "profile": {"pursuit": None, "category_budgets": {}},
                    "weather": {},
                    "current_request": "친구 재킷 같은 코디",
                },
                analysis=self._analysis(),
            )
