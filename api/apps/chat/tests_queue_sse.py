from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

import redis
from django.conf import settings
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import ChatMessage, ChatRun, ChatSession
from apps.chat.services import identity as identity_service
from apps.chat.services import queue
from apps.chat.services import sessions as session_service
from apps.chat.services.events import ChatEvent, ChatEventStore, encode_sse


class ChatQueueTests(APITestCase):
    @patch("apps.chat.services.queue.get_client")
    def test_enqueue_keeps_only_run_reference_in_redis(self, get_client):
        client = get_client.return_value
        run = SimpleNamespace(pk="run-1")

        queue.enqueue(run)

        key, raw = client.lpush.call_args.args
        self.assertEqual(key, settings.CHAT_QUEUE_PENDING_KEY)
        self.assertEqual(json.loads(raw), {"run_id": "run-1"})

    @patch("apps.chat.services.queue.get_client")
    def test_retry_moves_delivery_back_then_dead_letters_at_limit(self, get_client):
        client = get_client.return_value
        client.hincrby.side_effect = [1, settings.CHAT_QUEUE_MAX_RETRIES]
        raw = '{"run_id":"run-1"}'

        first_dead = queue.retry_or_dead(raw, "run-1", "TEMPORARY")
        final_dead = queue.retry_or_dead(raw, "run-1", "TEMPORARY")

        self.assertFalse(first_dead)
        self.assertTrue(final_dead)
        client.lpush.assert_any_call(settings.CHAT_QUEUE_PENDING_KEY, raw)
        self.assertTrue(
            any(
                call.args[0] == settings.CHAT_QUEUE_DEAD_KEY
                for call in client.lpush.call_args_list
            )
        )


class ChatEventStoreTests(APITestCase):
    def test_publish_and_replay_preserve_stream_event_id(self):
        client = Mock()
        client.xadd.return_value = "1700000000000-0"
        client.xread.return_value = [
            (
                "stream",
                [
                    (
                        "1700000000000-0",
                        {"event": "running", "data": '{"status":"RUNNING"}'},
                    )
                ],
            )
        ]
        store = ChatEventStore(client=client)

        event_id = store.publish("run-1", "running", {"status": "RUNNING"})
        events = store.read("run-1", last_event_id="0-0", block_milliseconds=0)

        self.assertEqual(event_id, "1700000000000-0")
        self.assertEqual(events[0].id, event_id)
        self.assertEqual(events[0].event, "running")
        self.assertIn("event: running", encode_sse(events[0]))
        self.assertNotIn("block", client.xread.call_args.kwargs)
        client.expire.assert_called_once()


class ChatQueueApiTests(APITestCase):
    def _guest_session(self) -> ChatSession:
        response = self.client.post(reverse("chat:guest-identity"), {}, format="json")
        identity = identity_service.get_guest_identity(
            response.cookies[settings.CHAT_GUEST_COOKIE_NAME].value,
            touch=False,
        )
        return session_service.create_session(
            identity=identity,
            mode=ChatSession.Mode.NEW_ITEM,
        )

    @patch("apps.chat.views.ChatEventStore")
    @patch("apps.chat.views.chat_queue.enqueue")
    def test_message_submission_is_idempotent_and_enqueues_same_run(
        self, enqueue, event_store
    ):
        session = self._guest_session()
        url = reverse("chat:session-messages", kwargs={"session_id": session.pk})
        payload = {
            "content": "가을 출근룩 추천해줘",
            "client_message_id": "client-message-1",
        }

        first = self.client.post(url, payload, format="json")
        second = self.client.post(url, payload, format="json")

        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(second.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(first.data["run"]["id"], second.data["run"]["id"])
        self.assertEqual(
            ChatMessage.objects.filter(role=ChatMessage.Role.USER).count(), 1
        )
        session.refresh_from_db()
        self.assertEqual(session.title, "가을 출근룩 추천해줘")
        self.assertEqual(ChatRun.objects.count(), 1)
        self.assertIsNotNone(ChatRun.objects.get().enqueued_at)
        self.assertEqual(enqueue.call_count, 2)
        self.assertEqual(event_store.return_value.publish.call_count, 2)

    @patch("apps.chat.views.chat_queue.enqueue")
    def test_queue_failure_marks_run_and_message_failed(self, enqueue):
        enqueue.side_effect = redis.ConnectionError("redis unavailable")
        session = self._guest_session()

        response = self.client.post(
            reverse("chat:session-messages", kwargs={"session_id": session.pk}),
            {"content": "추천해줘", "client_message_id": "queue-fail-1"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        run = ChatRun.objects.get()
        run.request_message.refresh_from_db()
        self.assertEqual(run.status, ChatRun.Status.FAILED)
        self.assertEqual(run.error_code, "CHAT_QUEUE_UNAVAILABLE")
        self.assertEqual(run.request_message.status, ChatMessage.Status.FAILED)
        self.assertEqual(response.data["message"]["status"], ChatMessage.Status.FAILED)

    @patch("apps.chat.views.chat_queue.enqueue")
    def test_server_reserved_client_message_id_is_rejected(self, enqueue):
        session = self._guest_session()

        response = self.client.post(
            reverse("chat:session-messages", kwargs={"session_id": session.pk}),
            {"content": "추천해줘", "client_message_id": "run:forged:response"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        enqueue.assert_not_called()

    @patch("apps.chat.views.ChatEventStore")
    def test_terminal_sse_falls_back_to_database_when_stream_is_empty(
        self, event_store
    ):
        event_store.return_value.read.return_value = []
        session = self._guest_session()
        request_message, _ = session_service.append_message(
            identity=session.identity,
            session_id=session.pk,
            role=ChatMessage.Role.USER,
            content="추천해줘",
            status=ChatMessage.Status.COMPLETED,
            client_message_id="sse-1",
        )
        response_message, _ = session_service.append_message(
            identity=session.identity,
            session_id=session.pk,
            role=ChatMessage.Role.ASSISTANT,
            content="검증된 코디예요.",
        )
        run = ChatRun.objects.create(
            session=session,
            request_message=request_message,
            response_message=response_message,
            status=ChatRun.Status.SUCCEEDED,
        )

        response = self.client.get(
            reverse("chat:run-events", kwargs={"run_id": run.pk}),
            HTTP_ACCEPT="text/event-stream",
        )
        body = b"".join(response.streaming_content).decode()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/event-stream")
        self.assertEqual(response["X-Accel-Buffering"], "no")
        self.assertIn("event: completed", body)
        self.assertIn("검증된 코디예요.", body)

    @patch("apps.chat.views.ChatEventStore")
    def test_sse_replays_only_after_last_event_id(self, event_store):
        session = self._guest_session()
        request_message, _ = session_service.append_message(
            identity=session.identity,
            session_id=session.pk,
            role=ChatMessage.Role.USER,
            content="추천해줘",
            status=ChatMessage.Status.PENDING,
            client_message_id="sse-replay-1",
        )
        run = ChatRun.objects.create(session=session, request_message=request_message)
        event_store.return_value.read.return_value = [
            ChatEvent(
                id="1700000000001-0",
                event="failed",
                data={"run_id": str(run.pk), "status": "FAILED"},
            )
        ]

        response = self.client.get(
            reverse("chat:run-events", kwargs={"run_id": run.pk}),
            HTTP_ACCEPT="text/event-stream",
            HTTP_LAST_EVENT_ID="1700000000000-0",
        )
        body = b"".join(response.streaming_content).decode()

        first_read = event_store.return_value.read.call_args_list[0]
        self.assertEqual(first_read.kwargs["last_event_id"], "1700000000000-0")
        self.assertIn("id: 1700000000001-0", body)

    def test_other_identity_cannot_read_run_status(self):
        session = self._guest_session()
        request_message, _ = session_service.append_message(
            identity=session.identity,
            session_id=session.pk,
            role=ChatMessage.Role.USER,
            content="추천해줘",
            client_message_id="owned-run-1",
        )
        run = ChatRun.objects.create(session=session, request_message=request_message)
        other_client = type(self.client)()
        other_client.post(reverse("chat:guest-identity"), {}, format="json")

        response = other_client.get(
            reverse("chat:run-detail", kwargs={"run_id": run.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ChatWorkerTests(APITestCase):
    def _run(self, status_value=ChatRun.Status.PENDING) -> ChatRun:
        credential = identity_service.issue_guest_identity()
        session = session_service.create_session(
            identity=credential.identity,
            mode=ChatSession.Mode.NEW_ITEM,
        )
        message, _ = session_service.append_message(
            identity=credential.identity,
            session_id=session.pk,
            role=ChatMessage.Role.USER,
            content="추천해줘",
            status=(
                ChatMessage.Status.PROCESSING
                if status_value == ChatRun.Status.RUNNING
                else ChatMessage.Status.PENDING
            ),
        )
        return ChatRun.objects.create(
            session=session,
            request_message=message,
            status=status_value,
        )

    @patch("apps.chat.management.commands.run_chat_worker.ChatEventStore")
    @patch("apps.chat.management.commands.run_chat_worker.ChatOrchestrator")
    @patch("apps.chat.management.commands.run_chat_worker.queue")
    def test_worker_processes_and_acks_success(
        self, worker_queue, orchestrator, _events
    ):
        run = self._run()
        raw = json.dumps({"run_id": str(run.pk)})
        worker_queue.recover_processing.return_value = []
        worker_queue.fetch.return_value = raw
        run.status = ChatRun.Status.SUCCEEDED
        orchestrator.return_value.process.return_value = SimpleNamespace(run=run)

        call_command("run_chat_worker", "--once", stdout=StringIO())

        orchestrator.return_value.process.assert_called_once_with(str(run.pk))
        worker_queue.ack.assert_called_once_with(raw, str(run.pk))

    @patch("apps.chat.management.commands.run_chat_worker.ChatEventStore")
    @patch("apps.chat.management.commands.run_chat_worker.queue")
    def test_worker_recovery_resets_interrupted_run(self, worker_queue, _events):
        run = self._run(ChatRun.Status.RUNNING)
        raw = json.dumps({"run_id": str(run.pk)})
        worker_queue.recover_processing.return_value = [raw]
        worker_queue.fetch.return_value = None

        call_command("run_chat_worker", "--once", stdout=StringIO())

        run.refresh_from_db()
        run.request_message.refresh_from_db()
        self.assertEqual(run.status, ChatRun.Status.PENDING)
        self.assertEqual(run.request_message.status, ChatMessage.Status.PENDING)

    @patch("apps.chat.management.commands.run_chat_worker.ChatEventStore")
    @patch("apps.chat.management.commands.run_chat_worker.ChatOrchestrator")
    @patch("apps.chat.management.commands.run_chat_worker.queue")
    def test_transient_failure_is_reset_for_retry(
        self, worker_queue, orchestrator, _events
    ):
        run = self._run()
        raw = json.dumps({"run_id": str(run.pk)})
        worker_queue.recover_processing.return_value = []
        worker_queue.fetch.return_value = raw
        worker_queue.retry_or_dead.return_value = False

        def fail(run_id):
            ChatRun.objects.filter(pk=run_id).update(
                status=ChatRun.Status.FAILED,
                error_code="CHAT_LLM_UNAVAILABLE",
                error_message="일시 오류",
            )
            ChatMessage.objects.filter(pk=run.request_message_id).update(
                status=ChatMessage.Status.FAILED
            )
            raise RuntimeError("temporary")

        orchestrator.return_value.process.side_effect = fail

        call_command("run_chat_worker", "--once", stdout=StringIO())

        run.refresh_from_db()
        run.request_message.refresh_from_db()
        self.assertEqual(run.status, ChatRun.Status.PENDING)
        self.assertEqual(run.request_message.status, ChatMessage.Status.PENDING)
        worker_queue.retry_or_dead.assert_called_once_with(
            raw, str(run.pk), "CHAT_LLM_UNAVAILABLE"
        )
        worker_queue.ack.assert_not_called()

    @patch("apps.chat.management.commands.run_chat_worker.ChatEventStore")
    @patch("apps.chat.management.commands.run_chat_worker.queue")
    def test_worker_recovers_old_database_run_not_confirmed_in_redis(
        self, worker_queue, _events
    ):
        run = self._run()
        ChatRun.objects.filter(pk=run.pk).update(
            created_at=timezone.now()
            - timedelta(seconds=settings.CHAT_QUEUE_ORPHAN_AGE_SECONDS + 1)
        )
        worker_queue.recover_processing.return_value = []
        worker_queue.fetch.return_value = None

        call_command("run_chat_worker", "--once", stdout=StringIO())

        run.refresh_from_db()
        self.assertIsNotNone(run.enqueued_at)
        worker_queue.enqueue.assert_called_once()
