from __future__ import annotations

from unittest.mock import Mock, patch

from django.test import TestCase, override_settings

from apps.chat.models import ChatIdentity, ChatMessage, ChatRun, ChatSession
from apps.chat.services.openai_adapter import RecommendationConditions, TurnAnalysis
from apps.chat.services.recommendation_pipeline import ChatRecommendationPipeline
from apps.recommend.models import OutfitRenderJob, RecommendationResult
from apps.recommend.services import render_execution, render_jobs
from apps.recommend.services.item_retriever import ItemSource
from apps.recommend.services.mixed_outfit_render import RenderedOutfit
from apps.recommend.services.outfit_types import (
    CompositionBatch,
    OutfitComposition,
    OutfitItem,
    RecommendationMode,
)
from apps.recommend.services.retriever import OutfitCandidate, RetrievalResult
from apps.recommend.services.validator import OutfitValidationResult

PNG = b"\x89PNG\r\n\x1a\n" + b"integration-image"


@override_settings(
    CHAT_GOLDENSET_DATASET_VERSION="goldenset-v1",
    CHAT_GOLDENSET_DATASET_STATUSES=("ACTIVE",),
    OUTFIT_RENDER_RESULT_BUCKET="render-bucket",
    OUTFIT_RENDER_RESULT_PREFIX="integration-renders",
)
class ChatRecommendationRenderIntegrationTests(TestCase):
    def setUp(self) -> None:
        identity = ChatIdentity.objects.create(
            identity_type=ChatIdentity.IdentityType.GUEST,
            guest_token_hash="a" * 64,
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
            content="가을 출근 코디 추천해줘",
        )
        self.run = ChatRun.objects.create(session=session, request_message=message)

    @patch("apps.recommend.services.render_events.RenderEventStore")
    @patch("apps.recommend.services.render_queue.enqueue")
    @patch(
        "apps.recommend.services.render_artifacts.storage.metadata_for",
        return_value=None,
    )
    @patch("apps.recommend.services.render_artifacts.storage.put_bytes_for")
    def test_chat_pipeline_persists_card_and_completes_shared_render(
        self,
        put_bytes,
        _metadata,
        enqueue,
        _events,
    ) -> None:
        golden = Mock()
        golden.retrieve.return_value = RetrievalResult(
            candidates=(
                OutfitCandidate(
                    point_id="outfit-point-1",
                    golden_id="golden-1",
                    score=0.94,
                    similarity=0.91,
                    payload={
                        "item_point_ids": ["template-top"],
                        "dataset_version": "goldenset-v1",
                    },
                ),
            ),
            search_mode="text",
        )
        item_retriever = Mock()
        item_retriever.retrieve.return_value = object()
        item = OutfitItem(
            slot_id="TOP",
            template_point_id="template-top",
            category_large="상의",
            layer_role="top",
            source_type=ItemSource.PRODUCT,
            source_id="naver-1",
            source_collection="products_naver_v1",
            point_id="product-point-1",
            image_ref="https://cdn.example.com/top.png",
            price=59_000,
            score=0.93,
            reasons=("골든 코디의 상의와 유사",),
            payload={"title": "미니멀 셔츠", "price": 59_000},
        )
        composer = Mock()
        composer.compose.return_value = CompositionBatch(
            mode=RecommendationMode.NEW_ITEM,
            compositions=(
                OutfitComposition(
                    mode=RecommendationMode.NEW_ITEM,
                    items=(item,),
                    missing_slot_ids=(),
                    total_product_price=59_000,
                ),
            ),
        )
        validator = Mock()
        validator.validate.return_value = OutfitValidationResult(
            issues=(),
            effective_total_product_price=59_000,
        )
        pipeline = ChatRecommendationPipeline(
            golden_retriever=golden,
            item_retriever=item_retriever,
            wardrobe_composer=Mock(),
            new_item_composer=composer,
            validator=validator,
        )
        analysis = TurnAnalysis(
            action="RECOMMEND",
            target_mode="CURRENT",
            search_query="가을 출근 미니멀",
            conditions=RecommendationConditions(
                occasion="출근",
                occasion_kind="FORMAL",
                season="가을",
                presentation_groups=["man"],
                styles=["미니멀"],
                colors=[],
                fits=[],
                avoided_styles=[],
                avoided_colors=[],
                excluded_source_ids=[],
                budget=None,
            ),
            clarification_question="",
            response_text="",
        )

        with self.captureOnCommitCallbacks(execute=True):
            output = pipeline.execute(
                run=self.run,
                context={
                    "profile": {"pursuit": None, "body": {"gender": "male"}},
                    "weather": {"temperature": 18},
                    "current_request": self.run.request_message.content,
                },
                analysis=analysis,
            )

        result = RecommendationResult.objects.prefetch_related(
            "compositions__items"
        ).get(pk=output.result.pk)
        card = result.compositions.get()
        job = OutfitRenderJob.objects.get(composition=card)
        enqueue.assert_called_once()
        self.assertEqual(job.status, OutfitRenderJob.Status.QUEUED)
        self.assertEqual(card.items.get().source_type, "PRODUCT")

        processing = render_jobs.start(job.pk)
        renderer = Mock()
        renderer.render_request.return_value = RenderedOutfit(
            content=PNG,
            media_type="image/png",
            provider="openrouter",
            model="qwen/qwen-image-3-pro",
            prompt_version="mixed-outfit-render-v2",
            composition_fingerprint=card.composition_fingerprint,
            reference_count=1,
            usage={"cost": 0.01},
        )
        cache = Mock()
        cache.get.return_value = None

        completed = render_execution.execute(
            processing,
            renderer=renderer,
            cache=cache,
        )

        self.assertEqual(completed.status, OutfitRenderJob.Status.SUCCEEDED)
        self.assertEqual(completed.output_s3_bucket, "render-bucket")
        put_bytes.assert_called_once_with(
            "render-bucket",
            completed.output_s3_key,
            PNG,
            "image/png",
        )
