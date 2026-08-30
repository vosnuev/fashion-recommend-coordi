from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.chat.models import ChatRun, ChatRunPersona, ChatSession
from apps.chat.services import identity as identity_service
from apps.chat.services import orchestrator, sessions
from apps.chat.services.alternative_recommendations import (
    AlternativeRecommendationNotReady,
    mark_alternative_processing_failed,
    prepare_alternative_recommendation,
)
from apps.recommend.models import RecommendationResult

User = get_user_model()


class AlternativeRecommendationStateTests(TestCase):
    def setUp(self) -> None:
        user = User.objects.create_user(username="alternative-member")
        self.identity = identity_service.get_or_create_member_identity(user)
        self.session = sessions.create_session(
            identity=self.identity,
            mode=ChatSession.Mode.NEW_ITEM,
        )
        self.session.response_mode = ChatSession.ResponseMode.STYLIST
        self.session.selected_persona_ids = ["minimal", "practical"]
        self.session.save(update_fields=["response_mode", "selected_persona_ids"])
        _message, _created, self.run, _run_created = (
            orchestrator.submit_message_and_create_run(
                identity=self.identity,
                session_id=self.session.pk,
                content="출근 코디를 추천해줘",
                client_message_id="alternative-state",
            )
        )
        self.run.status = ChatRun.Status.SUCCEEDED
        self.run.completed_at = timezone.now()
        self.run.save(update_fields=["status", "completed_at", "updated_at"])
        self.minimal = self.run.persona_executions.get(persona_id="minimal")
        self.minimal.status = ChatRunPersona.Status.SUCCEEDED
        self.minimal.save(update_fields=["status", "updated_at"])
        self.practical = self.run.persona_executions.get(persona_id="practical")
        self.practical.status = ChatRunPersona.Status.SUCCEEDED
        self.practical.save(update_fields=["status", "updated_at"])
        self.current = RecommendationResult.objects.create(
            identity=self.identity,
            session=self.session,
            run=self.run,
            persona_execution=self.minimal,
            response_mode=RecommendationResult.ResponseMode.STYLIST,
            persona_id="minimal",
            persona_version=self.minimal.persona_version,
            mode=RecommendationResult.Mode.NEW_ITEM,
            dataset_version="test-v1",
        )

    def test_only_target_is_prepared_and_current_result_is_preserved(self) -> None:
        prepared = prepare_alternative_recommendation(
            run_id=self.run.pk,
            persona_id="minimal",
        )

        self.run.refresh_from_db()
        self.minimal.refresh_from_db()
        self.practical.refresh_from_db()
        self.current.refresh_from_db()
        self.assertEqual(prepared.source_result_id, str(self.current.pk))
        self.assertEqual(prepared.generation, 2)
        self.assertEqual(self.run.status, ChatRun.Status.PENDING)
        self.assertEqual(self.minimal.status, ChatRunPersona.Status.PENDING)
        self.assertEqual(
            self.minimal.alternative_status,
            ChatRunPersona.AlternativeStatus.PENDING,
        )
        self.assertEqual(self.minimal.alternative_count, 1)
        self.assertEqual(self.practical.status, ChatRunPersona.Status.SUCCEEDED)
        self.assertTrue(self.current.is_current)

    def test_persona_without_result_cannot_request_alternative(self) -> None:
        with self.assertRaises(AlternativeRecommendationNotReady):
            prepare_alternative_recommendation(
                run_id=self.run.pk,
                persona_id="practical",
            )

    def test_start_locks_run_without_nullable_outer_join(self) -> None:
        prepared = prepare_alternative_recommendation(
            run_id=self.run.pk,
            persona_id="minimal",
        )

        started_run, started_execution = (
            orchestrator.ChatOrchestrator._start_persona_alternative(
                run_id=prepared.run_id,
                persona_id=prepared.persona_id,
                source_result_id=prepared.source_result_id,
                generation=prepared.generation,
            )
        )

        self.assertEqual(started_run.status, ChatRun.Status.RUNNING)
        self.assertEqual(
            started_execution.alternative_status,
            ChatRunPersona.AlternativeStatus.RUNNING,
        )

    def test_worker_failure_restores_current_card_and_terminal_state(self) -> None:
        prepare_alternative_recommendation(
            run_id=self.run.pk,
            persona_id="minimal",
        )

        recovered = mark_alternative_processing_failed(
            run_id=self.run.pk,
            persona_id="minimal",
        )

        self.run.refresh_from_db()
        self.minimal.refresh_from_db()
        self.current.refresh_from_db()
        self.assertTrue(recovered)
        self.assertEqual(self.run.status, ChatRun.Status.SUCCEEDED)
        self.assertEqual(self.minimal.status, ChatRunPersona.Status.SUCCEEDED)
        self.assertEqual(
            self.minimal.alternative_status,
            ChatRunPersona.AlternativeStatus.FAILED,
        )
        self.assertEqual(
            self.minimal.alternative_error_code,
            "STYLIST_ALTERNATIVE_FAILED",
        )
        self.assertTrue(self.current.is_current)

    def test_result_generations_keep_previous_result(self) -> None:
        self.current.is_current = False
        self.current.save(update_fields=["is_current", "updated_at"])
        alternative = RecommendationResult.objects.create(
            identity=self.identity,
            session=self.session,
            run=self.run,
            persona_execution=self.minimal,
            response_mode=RecommendationResult.ResponseMode.STYLIST,
            persona_id="minimal",
            persona_version=self.minimal.persona_version,
            result_type=RecommendationResult.ResultType.ALTERNATIVE,
            generation=2,
            replaces=self.current,
            mode=RecommendationResult.Mode.NEW_ITEM,
            dataset_version="test-v1",
        )

        self.assertEqual(self.minimal.recommendation_result.pk, alternative.pk)
        self.assertEqual(
            list(
                self.minimal.recommendation_results.order_by("generation").values_list(
                    "pk", flat=True
                )
            ),
            [self.current.pk, alternative.pk],
        )
