from __future__ import annotations

from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.chat.models import (
    ChatIdentity,
    ChatMessage,
    ChatRun,
    ChatRunPersona,
    ChatSession,
)
from apps.chat.services.openai_adapter import (
    RecommendationConditions,
    TurnAnalysis,
)
from apps.chat.services.recommendation_pipeline import (
    ChatRecommendationPipeline,
    OutfitCompositionFailed,
    WARDROBE_OUTFIT_UNAVAILABLE_MESSAGE,
    WardrobeOutfitUnavailable,
)
from apps.recommend.models import (
    GoldenTemplateSnapshot,
    OutfitCompositionItem,
    RecommendationResult,
)
from apps.recommend.models import (
    OutfitComposition as OutfitCompositionModel,
)
from apps.recommend.services.item_retriever import ItemSource
from apps.recommend.services.outfit_types import (
    CompositionBatch,
    OutfitComposition,
    OutfitItem,
    RecommendationMode,
)
from apps.recommend.services.retriever import (
    OutfitCandidate,
    RetrievalResult,
)
from apps.recommend.services.validator import OutfitValidationResult

User = get_user_model()


class RecommendationCandidatePipelineTests(TestCase):
    def setUp(self) -> None:
        user = User.objects.create_user(username="candidate-pipeline")
        self.identity = ChatIdentity.objects.create(
            user=user,
            identity_type=ChatIdentity.IdentityType.MEMBER,
        )

    def _run(
        self,
        *,
        stylist: bool = False,
        mode: str = ChatSession.Mode.NEW_ITEM,
    ) -> tuple[ChatRun, ChatRunPersona | None]:
        persona_ids = ["minimal"] if stylist else []
        response_mode = (
            ChatSession.ResponseMode.STYLIST
            if stylist
            else ChatSession.ResponseMode.DEFAULT
        )
        session = ChatSession.objects.create(
            identity=self.identity,
            mode=mode,
            response_mode=response_mode,
            selected_persona_ids=persona_ids,
        )
        message = ChatMessage.objects.create(
            session=session,
            sequence=1,
            role=ChatMessage.Role.USER,
            content="가을 출근룩을 추천해줘",
        )
        run = ChatRun.objects.create(
            session=session,
            request_message=message,
            response_mode=response_mode,
            persona_ids=persona_ids,
            persona_versions={"minimal": 1} if stylist else {},
            persona_prompt_versions={"minimal": "minimal-v1"} if stylist else {},
            stylist_config_version="1.0" if stylist else "",
        )
        if not stylist:
            return run, None
        execution = ChatRunPersona.objects.create(
            run=run,
            persona_id="minimal",
            persona_version=1,
            prompt_version="minimal-v1",
            display_order=1,
            strategy_snapshot={"weights": {"simplicity": 1.0}},
        )
        return run, execution

    @staticmethod
    def _context() -> dict:
        return {
            "profile": {"pursuit": None},
            "weather": {"temperature": 20},
            "current_request": "가을 출근룩을 추천해줘",
        }

    @staticmethod
    def _analysis() -> TurnAnalysis:
        return TurnAnalysis(
            action="RECOMMEND",
            target_mode="CURRENT",
            search_query="가을 출근 미니멀 코디",
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
                budget=None,
            ),
            clarification_question="",
            response_text="",
        )

    @staticmethod
    def _composition(source_id: str, price: int) -> OutfitComposition:
        item = OutfitItem(
            slot_id="TOP",
            template_point_id="template-top",
            category_large="상의",
            layer_role="top",
            source_type=ItemSource.PRODUCT,
            source_id=source_id,
            source_collection="products_naver_v1",
            point_id=f"point-{source_id}",
            image_ref=f"https://example.com/{source_id}.jpg",
            price=price,
            score=0.9,
            reasons=("골든 아이템과 유사",),
            payload={"title": source_id, "price": price},
        )
        return OutfitComposition(
            mode=RecommendationMode.NEW_ITEM,
            items=(item,),
            missing_slot_ids=(),
            total_product_price=price,
        )

    def _pipeline(
        self,
        *,
        diversity_slots: tuple[str, ...] = ("TOP", "BOTTOM", "OUTER"),
    ) -> tuple[ChatRecommendationPipeline, Mock]:
        first = OutfitCandidate(
            point_id="outfit-1",
            golden_id="golden-1",
            score=0.95,
            similarity=0.95,
            payload={
                "item_point_ids": ["template-top"],
                "dataset_version": "goldenset-v1",
            },
        )
        second = OutfitCandidate(
            point_id="outfit-2",
            golden_id="golden-2",
            score=0.9,
            similarity=0.9,
            payload={
                "item_point_ids": ["template-top"],
                "dataset_version": "goldenset-v1",
            },
        )
        golden = Mock()
        golden.retrieve.return_value = RetrievalResult(
            candidates=(first, second),
            search_mode="text",
        )
        composer = Mock()
        composer.compose.side_effect = (
            CompositionBatch(
                mode=RecommendationMode.NEW_ITEM,
                compositions=(
                    self._composition("product-a", 40_000),
                    self._composition("product-b", 50_000),
                ),
            ),
            CompositionBatch(
                mode=RecommendationMode.NEW_ITEM,
                compositions=(self._composition("product-c", 60_000),),
            ),
        )
        validator = Mock()
        validator.validate.side_effect = lambda composition, **_: (
            OutfitValidationResult(
                issues=(),
                effective_total_product_price=composition.total_product_price,
            )
        )
        return (
            ChatRecommendationPipeline(
                golden_retriever=golden,
                item_retriever=Mock(retrieve=Mock(return_value=object())),
                wardrobe_composer=Mock(),
                new_item_composer=composer,
                validator=validator,
                diversity_slots=diversity_slots,
            ),
            composer,
        )

    def test_avoided_condition_reaches_item_candidate_search(self) -> None:
        """기피 조건을 검색에 안 넘기면 조합까지 만든 뒤 Validator가 버린다."""

        run, _ = self._run()
        pipeline, _ = self._pipeline()
        analysis = self._analysis().model_copy(
            update={
                "conditions": self._analysis().conditions.model_copy(
                    update={"avoided_styles": ["캐주얼"]}
                )
            }
        )

        pipeline._generate_candidates(
            run=run,
            context=self._context(),
            analysis=analysis,
        )

        requests = [
            call.args[0] for call in pipeline.item_retriever.retrieve.call_args_list
        ]
        self.assertTrue(requests)
        self.assertEqual(requests[0].avoided_tags, {"style": ("캐주얼",)})

    def test_no_avoided_condition_sends_empty_filter(self) -> None:
        run, _ = self._run()
        pipeline, _ = self._pipeline()

        pipeline._generate_candidates(
            run=run,
            context=self._context(),
            analysis=self._analysis(),
        )

        requests = [
            call.args[0] for call in pipeline.item_retriever.retrieve.call_args_list
        ]
        self.assertTrue(requests)
        self.assertEqual(requests[0].avoided_tags, {})

    def test_empty_wardrobe_returns_actionable_failure_before_retrieval(self) -> None:
        run, _ = self._run(mode=ChatSession.Mode.WARDROBE_BASED)
        pipeline, _ = self._pipeline()

        with self.assertRaises(WardrobeOutfitUnavailable) as raised:
            pipeline._generate_candidates(
                run=run,
                context=self._context(),
                analysis=self._analysis(),
            )

        self.assertEqual(raised.exception.code, "WARDROBE_OUTFIT_UNAVAILABLE")
        self.assertEqual(str(raised.exception), WARDROBE_OUTFIT_UNAVAILABLE_MESSAGE)
        pipeline.golden_retriever.retrieve.assert_not_called()

    @patch("apps.chat.services.recommendation_pipeline.render_jobs.schedule_result")
    def test_generation_does_not_write_and_persistence_saves_only_selection(
        self,
        mock_schedule: Mock,
    ) -> None:
        run, _ = self._run()
        pipeline, _ = self._pipeline()

        generated = pipeline.generate_candidates(
            run=run,
            context=self._context(),
            analysis=self._analysis(),
        )

        self.assertEqual([row.ordinal for row in generated.candidates], [1, 2, 3])
        self.assertEqual(
            [row.template_rank for row in generated.candidates],
            [1, 1, 2],
        )
        self.assertEqual(RecommendationResult.objects.count(), 0)
        self.assertEqual(GoldenTemplateSnapshot.objects.count(), 0)
        self.assertEqual(OutfitCompositionModel.objects.count(), 0)
        self.assertEqual(OutfitCompositionItem.objects.count(), 0)
        mock_schedule.assert_not_called()

        with self.captureOnCommitCallbacks(execute=True):
            output = pipeline.persist_candidates(
                run=run,
                generated=generated,
                selected=(generated.candidates[1],),
            )

        self.assertEqual(output.result.compositions.count(), 1)
        saved_item = output.result.compositions.get().items.get()
        self.assertEqual(saved_item.source_id, "product-b")
        self.assertEqual(output.result.golden_template.golden_id, "golden-1")
        mock_schedule.assert_called_once_with(output.result.pk)

    def test_candidates_from_different_templates_cannot_share_one_result(self) -> None:
        run, _ = self._run()
        pipeline, _ = self._pipeline()
        generated = pipeline.generate_candidates(
            run=run,
            context=self._context(),
            analysis=self._analysis(),
        )

        with self.assertRaises(OutfitCompositionFailed):
            pipeline.persist_candidates(
                run=run,
                generated=generated,
                selected=(generated.candidates[0], generated.candidates[2]),
            )

        self.assertFalse(RecommendationResult.objects.exists())

    def test_default_execute_preserves_first_template_and_up_to_three_results(
        self,
    ) -> None:
        run, _ = self._run()
        pipeline, composer = self._pipeline()

        output = pipeline.execute(
            run=run,
            context=self._context(),
            analysis=self._analysis(),
        )

        self.assertEqual(output.result.compositions.count(), 2)
        self.assertEqual(
            list(
                output.result.compositions.values_list(
                    "items__source_id",
                    flat=True,
                )
            ),
            ["product-a", "product-b"],
        )
        self.assertEqual(output.result.golden_template.golden_id, "golden-1")
        self.assertEqual(composer.compose.call_count, 1)

    def test_default_execute_uses_injected_diversity_slots_after_generation(
        self,
    ) -> None:
        run, _ = self._run()
        pipeline, composer = self._pipeline(diversity_slots=("OUTER",))

        output = pipeline.execute(
            run=run,
            context=self._context(),
            analysis=self._analysis(),
        )

        self.assertEqual(output.result.compositions.count(), 1)
        self.assertEqual(
            output.result.compositions.get().items.get().source_id,
            "product-a",
        )
        self.assertEqual(composer.compose.call_count, 1)

    @patch("apps.chat.services.recommendation_pipeline.render_jobs.schedule_result")
    def test_stylist_persistence_saves_one_candidate_without_auto_render(
        self,
        mock_schedule: Mock,
    ) -> None:
        run, execution = self._run(stylist=True)
        assert execution is not None
        pipeline, _ = self._pipeline()
        generated = pipeline.generate_candidates(
            run=run,
            context=self._context(),
            analysis=self._analysis(),
            max_validated_templates=1,
        )

        with self.assertRaises(OutfitCompositionFailed):
            pipeline.persist_candidates(
                run=run,
                generated=generated,
                selected=generated.candidates,
                persona_execution=execution,
            )
        with self.captureOnCommitCallbacks(execute=True):
            output = pipeline.persist_candidates(
                run=run,
                generated=generated,
                selected=(generated.candidates[0],),
                persona_execution=execution,
                persona_explanation="정돈된 실루엣을 우선한 추천입니다.",
                validated_reason_codes=("STYLE_MATCH", "STYLE_MATCH"),
            )

        self.assertEqual(output.result.response_mode, "STYLIST")
        self.assertEqual(output.result.persona_id, "minimal")
        self.assertEqual(output.result.validated_reason_codes, ["STYLE_MATCH"])
        self.assertEqual(output.result.compositions.count(), 1)
        self.assertEqual(
            output.result.strategy_snapshot,
            execution.strategy_snapshot,
        )
        mock_schedule.assert_not_called()
