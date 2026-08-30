from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.chat.models import ChatRun, ChatRunPersona, ChatSession
from apps.chat.services import identity as identity_service
from apps.chat.services import orchestrator, sessions
from apps.chat.services.persona_retry import (
    PersonaRetryNotFailed,
    prepare_failed_persona_retry,
)

User = get_user_model()


class PersonaRetryStateTests(TestCase):
    def setUp(self) -> None:
        user = User.objects.create_user(username="persona-retry-member")
        identity = identity_service.get_or_create_member_identity(user)
        session = sessions.create_session(
            identity=identity,
            mode=ChatSession.Mode.NEW_ITEM,
        )
        session.response_mode = ChatSession.ResponseMode.STYLIST
        session.selected_persona_ids = ["minimal", "practical"]
        session.save(update_fields=["response_mode", "selected_persona_ids"])
        _message, _created, self.run, _run_created = (
            orchestrator.submit_message_and_create_run(
                identity=identity,
                session_id=session.pk,
                content="출근 코디를 추천해줘",
                client_message_id="persona-retry-state",
            )
        )
        self.run.status = ChatRun.Status.SUCCEEDED
        self.run.completed_at = timezone.now()
        self.run.save(update_fields=["status", "completed_at", "updated_at"])
        self.failed = self.run.persona_executions.get(persona_id="minimal")
        self.failed.status = ChatRunPersona.Status.FAILED
        self.failed.error_code = "STYLIST_PERSONA_TIMEOUT"
        self.failed.error_message = "추천 처리 시간이 초과되었습니다."
        self.failed.latency_ms = 20000
        self.failed.completed_at = timezone.now()
        self.failed.save(
            update_fields=[
                "status",
                "error_code",
                "error_message",
                "latency_ms",
                "completed_at",
                "updated_at",
            ]
        )
        self.succeeded = self.run.persona_executions.get(persona_id="practical")
        self.succeeded.status = ChatRunPersona.Status.SUCCEEDED
        self.succeeded.save(update_fields=["status", "updated_at"])

    def test_only_failed_persona_is_prepared_and_error_is_preserved(self) -> None:
        prepared = prepare_failed_persona_retry(
            run_id=self.run.pk,
            persona_id="minimal",
        )

        self.run.refresh_from_db()
        self.failed.refresh_from_db()
        self.succeeded.refresh_from_db()
        self.assertEqual(prepared.retry_count, 1)
        self.assertEqual(self.run.status, ChatRun.Status.PENDING)
        self.assertEqual(self.failed.status, ChatRunPersona.Status.PENDING)
        self.assertEqual(self.failed.retry_count, 1)
        self.assertEqual(self.failed.error_code, "")
        self.assertEqual(len(self.failed.error_history), 1)
        self.assertEqual(
            self.failed.error_history[0]["error_code"],
            "STYLIST_PERSONA_TIMEOUT",
        )
        self.assertEqual(self.succeeded.status, ChatRunPersona.Status.SUCCEEDED)

    def test_succeeded_persona_cannot_use_failure_retry(self) -> None:
        with self.assertRaises(PersonaRetryNotFailed):
            prepare_failed_persona_retry(
                run_id=self.run.pk,
                persona_id="practical",
            )
