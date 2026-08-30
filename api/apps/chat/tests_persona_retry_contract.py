from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.chat.management.commands.run_chat_worker import Command
from apps.chat.services import queue
from apps.chat.views import ChatRunPersonaRetryView


class PersonaRetryApiContractTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.view = ChatRunPersonaRetryView.as_view()
        self.run_id = uuid.uuid4()
        self.user = SimpleNamespace(is_authenticated=True, pk=1)

    def _request(self):
        request = self.factory.post(
            f"/api/v1/chat/runs/{self.run_id}/personas/experimental/retry/",
            {},
            format="json",
        )
        force_authenticate(request, user=self.user)
        return request

    @patch("apps.chat.views.ChatEventStore")
    @patch("apps.chat.views.ChatRun.objects.filter")
    @patch("apps.chat.views.ChatRunSerializer")
    @patch("apps.chat.views.chat_queue.enqueue_persona_retry")
    @patch("apps.chat.views.prepare_failed_persona_retry")
    @patch("apps.chat.views._owned_run")
    def test_member_can_enqueue_only_target_persona(
        self,
        owned_run_mock,
        prepare_mock,
        enqueue_mock,
        serializer_mock,
        filter_mock,
        event_store_mock,
    ) -> None:
        owned_run_mock.return_value = SimpleNamespace(pk=self.run_id)
        prepare_mock.return_value = SimpleNamespace(
            run_id=str(self.run_id),
            persona_id="experimental",
            retry_count=2,
        )
        serializer_mock.return_value = SimpleNamespace(data={"status": "PENDING"})
        filter_mock.return_value.update.return_value = 1
        event_store_mock.return_value.publish = Mock()

        response = self.view(
            self._request(),
            run_id=self.run_id,
            persona_id="experimental",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        enqueue_mock.assert_called_once_with(
            run_id=str(self.run_id),
            persona_id="experimental",
            retry_count=2,
        )
        self.assertEqual(response.data["run"], {"status": "PENDING"})

    @patch("apps.chat.views.prepare_failed_persona_retry")
    def test_guest_is_rejected_before_retry_service(self, prepare_mock) -> None:
        request = self.factory.post(
            f"/api/v1/chat/runs/{self.run_id}/personas/minimal/retry/",
            {},
            format="json",
        )

        response = self.view(
            request,
            run_id=self.run_id,
            persona_id="minimal",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        prepare_mock.assert_not_called()


class PersonaRetryQueueContractTests(SimpleTestCase):
    @patch("apps.chat.services.queue.get_client")
    def test_retry_payload_is_distinct_from_whole_run_delivery(
        self, get_client
    ) -> None:
        queue.enqueue_persona_retry(
            run_id="run-1",
            persona_id="practical",
            retry_count=3,
        )

        _key, raw = get_client.return_value.lpush.call_args.args
        payload = json.loads(raw)
        self.assertEqual(payload["task"], queue.PERSONA_RETRY_TASK)
        self.assertEqual(payload["persona_id"], "practical")
        self.assertEqual(payload["retry_count"], 3)
        self.assertEqual(
            queue.delivery_key(payload),
            "run-1:practical:PERSONA_RETRY:3",
        )

    @patch("apps.chat.management.commands.run_chat_worker.ChatRun.objects.filter")
    @patch("apps.chat.management.commands.run_chat_worker.ChatOrchestrator")
    @patch("apps.chat.management.commands.run_chat_worker.queue.ack")
    def test_worker_dispatches_persona_retry_without_processing_whole_run(
        self,
        ack_mock,
        orchestrator_mock,
        run_filter_mock,
    ) -> None:
        payload = {
            "task": queue.PERSONA_RETRY_TASK,
            "run_id": "run-1",
            "persona_id": "minimal",
            "retry_count": 1,
        }
        raw = json.dumps(payload)
        run_filter_mock.return_value.exists.return_value = True
        completed_run = SimpleNamespace(
            pk="run-1",
            status="SUCCEEDED",
            response_message_id=None,
        )
        orchestrator_mock.return_value.process_persona_retry.return_value = (
            SimpleNamespace(run=completed_run)
        )
        command = Command()

        with (
            patch.object(command, "_publish"),
            patch.object(command, "_publish_terminal") as terminal_mock,
        ):
            command._handle_persona_retry(raw, payload)

        orchestrator_mock.return_value.process_persona_retry.assert_called_once_with(
            run_id="run-1",
            persona_id="minimal",
            retry_count=1,
        )
        orchestrator_mock.return_value.process.assert_not_called()
        terminal_mock.assert_called_once_with(completed_run)
        ack_mock.assert_called_once_with(raw, "run-1:minimal:PERSONA_RETRY:1")
