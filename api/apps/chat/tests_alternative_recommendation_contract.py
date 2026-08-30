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
from apps.chat.views import ChatRunPersonaAlternativeView


class AlternativeRecommendationApiContractTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.view = ChatRunPersonaAlternativeView.as_view()
        self.run_id = uuid.uuid4()
        self.user = SimpleNamespace(is_authenticated=True, pk=1)

    def _request(self):
        request = self.factory.post(
            f"/api/v1/chat/runs/{self.run_id}/personas/minimal/alternative/",
            {},
            format="json",
        )
        force_authenticate(request, user=self.user)
        return request

    @patch("apps.chat.views.ChatEventStore")
    @patch("apps.chat.views.ChatRun.objects.filter")
    @patch("apps.chat.views.ChatRunSerializer")
    @patch("apps.chat.views.chat_queue.enqueue_persona_alternative")
    @patch("apps.chat.views.prepare_alternative_recommendation")
    @patch("apps.chat.views._owned_run")
    def test_member_enqueues_only_requested_stylist(
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
            persona_id="minimal",
            source_result_id="result-1",
            generation=2,
        )
        serializer_mock.return_value = SimpleNamespace(data={"status": "PENDING"})
        filter_mock.return_value.update.return_value = 1
        event_store_mock.return_value.publish = Mock()

        response = self.view(
            self._request(),
            run_id=self.run_id,
            persona_id="minimal",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        enqueue_mock.assert_called_once_with(
            run_id=str(self.run_id),
            persona_id="minimal",
            source_result_id="result-1",
            generation=2,
        )

    @patch("apps.chat.views.prepare_alternative_recommendation")
    def test_guest_is_rejected(self, prepare_mock) -> None:
        request = self.factory.post(
            f"/api/v1/chat/runs/{self.run_id}/personas/minimal/alternative/",
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


class AlternativeRecommendationQueueContractTests(SimpleTestCase):
    @patch("apps.chat.services.queue.get_client")
    def test_payload_preserves_source_result_and_generation(self, get_client) -> None:
        queue.enqueue_persona_alternative(
            run_id="run-1",
            persona_id="experimental",
            source_result_id="result-1",
            generation=4,
        )

        _key, raw = get_client.return_value.lpush.call_args.args
        payload = json.loads(raw)
        self.assertEqual(payload["task"], queue.PERSONA_ALTERNATIVE_TASK)
        self.assertEqual(payload["source_result_id"], "result-1")
        self.assertEqual(payload["generation"], 4)
        self.assertEqual(
            queue.delivery_key(payload),
            "run-1:experimental:PERSONA_ALTERNATIVE:4",
        )

    @patch(
        "apps.chat.management.commands.run_chat_worker.finalize_persisted_alternative",
        return_value=False,
    )
    @patch("apps.chat.management.commands.run_chat_worker.ChatRun.objects.filter")
    @patch("apps.chat.management.commands.run_chat_worker.ChatOrchestrator")
    @patch("apps.chat.management.commands.run_chat_worker.queue.ack")
    def test_worker_dispatches_alternative_without_whole_run(
        self,
        ack_mock,
        orchestrator_mock,
        run_filter_mock,
        _finalize_mock,
    ) -> None:
        payload = {
            "task": queue.PERSONA_ALTERNATIVE_TASK,
            "run_id": "run-1",
            "persona_id": "practical",
            "source_result_id": "result-2",
            "generation": 3,
        }
        raw = json.dumps(payload)
        run_filter_mock.return_value.exists.return_value = True
        completed_run = SimpleNamespace(
            pk="run-1",
            status="SUCCEEDED",
            response_message_id=None,
        )
        orchestrator_mock.return_value.process_persona_alternative.return_value = (
            SimpleNamespace(run=completed_run)
        )
        command = Command()

        with (
            patch.object(command, "_publish"),
            patch.object(command, "_publish_terminal") as terminal_mock,
        ):
            command._handle_persona_alternative(raw, payload)

        orchestrator_mock.return_value.process_persona_alternative.assert_called_once_with(
            run_id="run-1",
            persona_id="practical",
            source_result_id="result-2",
            generation=3,
        )
        orchestrator_mock.return_value.process.assert_not_called()
        terminal_mock.assert_called_once_with(completed_run)
        ack_mock.assert_called_once_with(
            raw,
            "run-1:practical:PERSONA_ALTERNATIVE:3",
        )

    def test_worker_recovers_unexpected_alternative_failure_before_ack(self) -> None:
        payload = {
            "task": queue.PERSONA_ALTERNATIVE_TASK,
            "run_id": "run-1",
            "persona_id": "minimal",
            "source_result_id": "result-1",
            "generation": 2,
        }
        raw = json.dumps(payload)
        current = SimpleNamespace(
            pk="run-1",
            status="SUCCEEDED",
            response_message_id=None,
        )
        command = Command()

        with (
            patch(
                "apps.chat.management.commands.run_chat_worker."
                "finalize_persisted_alternative",
                return_value=False,
            ),
            patch(
                "apps.chat.management.commands.run_chat_worker.ChatRun.objects.filter"
            ) as run_filter_mock,
            patch(
                "apps.chat.management.commands.run_chat_worker.ChatRun.objects."
                "select_related"
            ) as run_select_mock,
            patch(
                "apps.chat.management.commands.run_chat_worker.ChatOrchestrator"
            ) as orchestrator_mock,
            patch(
                "apps.chat.management.commands.run_chat_worker."
                "mark_alternative_processing_failed"
            ) as recover_mock,
            patch(
                "apps.chat.management.commands.run_chat_worker.queue.ack"
            ) as ack_mock,
            patch.object(command, "_publish"),
            patch.object(command, "_publish_terminal") as terminal_mock,
        ):
            run_filter_mock.return_value.exists.return_value = True
            run_select_mock.return_value.get.return_value = current
            orchestrator_mock.return_value.process_persona_alternative.side_effect = (
                RuntimeError("database failure")
            )

            command._handle_persona_alternative(raw, payload)

        recover_mock.assert_called_once_with(
            run_id="run-1",
            persona_id="minimal",
        )
        terminal_mock.assert_called_once_with(current)
        ack_mock.assert_called_once_with(raw, "run-1:minimal:PERSONA_ALTERNATIVE:2")
