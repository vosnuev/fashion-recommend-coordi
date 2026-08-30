from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.chat.models import ChatRun, ChatRunPersona, ChatSession
from apps.chat.services import identity as identity_service
from apps.chat.services import orchestrator, sessions
from apps.chat.services.context import ChatContext
from apps.chat.services.openai_adapter import (
    LLMResult,
    LLMUsage,
    RecommendationConditions,
    TurnAnalysis,
)
from apps.chat.services.persona_narration import PersonaNarrationResult
from apps.recommend.models import (
    OutfitComposition,
    OutfitCompositionItem,
    RecommendationResult,
)

User = get_user_model()


class StylistOrchestratorResponseTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="stylist-orchestrator-member")
        self.identity = identity_service.get_or_create_member_identity(self.user)
        self.session = sessions.create_session(
            identity=self.identity,
            mode=ChatSession.Mode.NEW_ITEM,
        )
        self.session.response_mode = ChatSession.ResponseMode.STYLIST
        self.session.selected_persona_ids = ["minimal", "practical"]
        self.session.save(
            update_fields=["response_mode", "selected_persona_ids", "updated_at"]
        )
        self.message, _created, self.run, _run_created = (
            orchestrator.submit_message_and_create_run(
                identity=self.identity,
                session_id=self.session.pk,
                content="출근 코디를 추천해줘",
                client_message_id="stylist-orchestrator-response",
            )
        )
        self.context_service = Mock()
        self.context_service.build.return_value = ChatContext(
            payload={
                "session": {"mode": self.session.mode, "conditions": {}},
                "persona": {},
                "profile": {},
                "weather": {},
                "recent_messages": [],
                "current_request": self.message.content,
            },
            fingerprint="a" * 64,
            base_fingerprint="b" * 64,
            cache_hit=False,
        )
        self.llm = Mock()
        self.llm.analyze_turn.return_value = LLMResult(
            value=TurnAnalysis(
                action="RECOMMEND",
                target_mode="CURRENT",
                search_query="출근 코디",
                conditions=RecommendationConditions(
                    occasion="출근",
                    occasion_kind="FORMAL",
                    season="가을",
                    presentation_groups=[],
                    styles=[],
                    colors=[],
                    fits=[],
                    avoided_styles=[],
                    avoided_colors=[],
                    excluded_source_ids=[],
                    budget=None,
                ),
                clarification_question="",
                response_text="",
            ),
            response_id="analysis-response",
            usage=LLMUsage(input_tokens=20, output_tokens=5),
        )
        self.pipeline = Mock()
        self.coordinator = Mock()
        self.narration = Mock()

    def test_stylist_run_saves_explanations_and_message_result_snapshot(self) -> None:
        persisted: dict[str, object] = {}

        def execute_stylists(*, run, persona_executions, **_kwargs):
            minimal, practical = persona_executions
            now = timezone.now()
            ChatRunPersona.objects.filter(pk=minimal.pk).update(
                status=ChatRunPersona.Status.SUCCEEDED,
                started_at=now,
                completed_at=now,
            )
            ChatRunPersona.objects.filter(pk=practical.pk).update(
                status=ChatRunPersona.Status.FAILED,
                error_code="PRACTICAL_FAILED",
                error_message="실용형 추천을 만들지 못했습니다.",
                started_at=now,
                completed_at=now,
            )
            result = RecommendationResult.objects.create(
                identity=self.identity,
                session=self.session,
                run=run,
                persona_execution=minimal,
                response_mode=RecommendationResult.ResponseMode.STYLIST,
                persona_id="minimal",
                persona_version=minimal.persona_version,
                validated_reason_codes=["MINIMAL_COLOR_COHESION"],
                strategy_snapshot=minimal.strategy_snapshot,
                mode=self.session.mode,
                dataset_version="goldenset-v1",
            )
            card = OutfitComposition.objects.create(
                result=result,
                rank=1,
                status=OutfitComposition.Status.VALIDATED,
                composition_fingerprint="a" * 64,
                total_product_price=0,
                validation_reasons=[],
                warnings=[],
            )
            OutfitCompositionItem.objects.create(
                composition=card,
                position=1,
                slot="TOP",
                source_type=OutfitCompositionItem.SourceType.WARDROBE,
                source_id="wardrobe-1",
                source_collection="wardrobe-v1",
                source_point_id="point-1",
                template_item_point_id="template-1",
                replacement_score=0.9,
                image_ref="wardrobe/item-1.jpg",
                reasons=["검증된 상의"],
                item_snapshot={"name": "네이비 니트"},
            )
            persisted["result"] = result
            persisted["card"] = card
            return SimpleNamespace(
                successes=(
                    SimpleNamespace(
                        persona_id="minimal",
                        persisted=SimpleNamespace(result=result),
                    ),
                ),
                failures=(SimpleNamespace(persona_id="practical"),),
                partial_failure=True,
                recommendation_result_ids=(str(result.pk),),
            )

        self.coordinator.execute.side_effect = execute_stylists
        self.narration.generate.return_value = PersonaNarrationResult(
            message="상의 네이비 니트 조합을 차분하게 정리했어요.",
            provider="template",
            requested_provider="openai",
            model="",
            fallback_used=True,
            fallback_reason="PERSONA_NARRATION_PROVIDER_FAILED",
            usage=LLMUsage(input_tokens=3, output_tokens=2),
        )
        service = orchestrator.ChatOrchestrator(
            context_service=self.context_service,
            llm=self.llm,
            recommendation_pipeline=self.pipeline,
            stylist_coordinator=self.coordinator,
            persona_narration_service=self.narration,
        )

        output = service.process(self.run.pk)

        result = RecommendationResult.objects.get(pk=persisted["result"].pk)
        self.assertEqual(output.run.status, ChatRun.Status.SUCCEEDED)
        self.assertEqual(output.recommendation_result_id, None)
        self.assertEqual(output.recommendation_result_ids, (str(result.pk),))
        self.assertEqual(
            result.persona_explanation,
            "상의 네이비 니트 조합을 차분하게 정리했어요.",
        )
        self.assertEqual(
            output.response_message.metadata["recommendation_result_ids"],
            [str(result.pk)],
        )
        snapshots = output.response_message.metadata["stylist_results"]
        self.assertEqual(
            [(row["persona_id"], row["status"]) for row in snapshots],
            [("minimal", "SUCCEEDED"), ("practical", "FAILED")],
        )
        self.assertEqual(snapshots[0]["result_id"], str(result.pk))
        self.assertEqual(snapshots[1]["error"]["code"], "PRACTICAL_FAILED")
        self.assertIn("일부 추천", output.response_message.content)
        narration_request = self.narration.generate.call_args.args[0]
        self.assertEqual(narration_request.persona_id, "minimal")
        self.assertEqual(str(narration_request.outfit_id), str(persisted["card"].pk))
        self.assertEqual(narration_request.items[0].name, "네이비 니트")
        self.assertEqual(output.run.input_tokens, 23)
        self.assertEqual(output.run.output_tokens, 7)
        self.pipeline.execute.assert_not_called()
        self.llm.explain_recommendation.assert_not_called()
