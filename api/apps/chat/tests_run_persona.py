from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.chat.models import ChatRunPersona, ChatSession
from apps.chat.services import identity as identity_service
from apps.chat.services import orchestrator, sessions

User = get_user_model()


class ChatRunPersonaTests(TestCase):
    def setUp(self) -> None:
        user = User.objects.create_user(username="run-persona-member")
        self.identity = identity_service.get_or_create_member_identity(user)
        self.session = sessions.create_session(
            identity=self.identity,
            mode=ChatSession.Mode.WARDROBE_BASED,
        )

    def _submit(self, client_message_id: str = "persona-execution-message"):
        return orchestrator.submit_message_and_create_run(
            identity=self.identity,
            session_id=self.session.id,
            content="출근 코디를 추천해줘",
            client_message_id=client_message_id,
        )

    def _activate_stylist_mode(self) -> None:
        self.session.response_mode = ChatSession.ResponseMode.STYLIST
        self.session.selected_persona_ids = ["minimal", "practical"]
        self.session.full_clean()
        self.session.save(
            update_fields=["response_mode", "selected_persona_ids"]
        )

    def test_stylist_run_creates_pending_rows_in_fixed_order(self) -> None:
        self._activate_stylist_mode()

        _message, _message_created, run, _run_created = self._submit()
        executions = list(run.persona_executions.all())

        self.assertEqual(
            [execution.persona_id for execution in executions],
            ["minimal", "practical"],
        )
        self.assertEqual(
            [execution.display_order for execution in executions],
            [1, 3],
        )
        self.assertTrue(
            all(
                execution.status == ChatRunPersona.Status.PENDING
                for execution in executions
            )
        )
        self.assertEqual(executions[0].persona_version, 1)
        self.assertEqual(executions[0].prompt_version, "stylist-minimal-v1")
        self.assertEqual(
            executions[0].strategy_snapshot["hypothesis_count"],
            0,
        )

    def test_default_run_does_not_create_persona_execution_rows(self) -> None:
        self.session.selected_persona_ids = ["minimal"]
        self.session.full_clean()
        self.session.save(update_fields=["selected_persona_ids"])

        _message, _message_created, run, _run_created = self._submit()

        self.assertEqual(run.response_mode, ChatSession.ResponseMode.DEFAULT)
        self.assertEqual(run.persona_ids, ["minimal"])
        self.assertFalse(run.persona_executions.exists())

    def test_duplicate_submission_does_not_duplicate_execution_rows(self) -> None:
        self._activate_stylist_mode()
        _message, _message_created, run, _run_created = self._submit()

        _duplicate, message_created, duplicate_run, run_created = self._submit()

        self.assertFalse(message_created)
        self.assertFalse(run_created)
        self.assertEqual(duplicate_run.id, run.id)
        self.assertEqual(ChatRunPersona.objects.filter(run=run).count(), 2)

    def test_persona_statuses_can_change_independently(self) -> None:
        self._activate_stylist_mode()
        _message, _message_created, run, _run_created = self._submit()
        minimal = run.persona_executions.get(persona_id="minimal")
        practical = run.persona_executions.get(persona_id="practical")

        minimal.status = ChatRunPersona.Status.FAILED
        minimal.latency_ms = 245
        minimal.error_code = "PERSONA_TIMEOUT"
        minimal.error_message = "추천 처리 시간이 초과되었습니다."
        minimal.completed_at = timezone.now()
        minimal.save(
            update_fields=[
                "status",
                "latency_ms",
                "error_code",
                "error_message",
                "completed_at",
                "updated_at",
            ]
        )
        practical.refresh_from_db()

        self.assertEqual(minimal.status, ChatRunPersona.Status.FAILED)
        self.assertEqual(practical.status, ChatRunPersona.Status.PENDING)
        self.assertEqual(practical.error_code, "")

    def test_execution_validation_matches_parent_run_snapshot(self) -> None:
        self._activate_stylist_mode()
        _message, _message_created, run, _run_created = self._submit()
        execution = run.persona_executions.get(persona_id="minimal")
        execution.display_order = 2

        with self.assertRaisesMessage(ValidationError, "고정 표시 순서"):
            execution.full_clean()

    def test_execution_validation_rejects_unknown_persona_id_cleanly(self) -> None:
        self._activate_stylist_mode()
        _message, _message_created, run, _run_created = self._submit()
        execution = run.persona_executions.get(persona_id="minimal")
        execution.persona_id = "unknown"

        with self.assertRaisesMessage(ValidationError, "지원하지 않는"):
            execution.full_clean()
