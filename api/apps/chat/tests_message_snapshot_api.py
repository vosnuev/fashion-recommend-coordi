from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import ChatRun, ChatRunPersona, ChatSession
from apps.chat.services import identity as identity_service
from apps.chat.services import response_modes
from apps.chat.services import sessions as session_service

User = get_user_model()


class ChatMessageSnapshotApiTests(APITestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="message-snapshot-member")
        self.identity = identity_service.get_or_create_member_identity(self.user)
        self.session = session_service.create_session(
            identity=self.identity,
            mode=ChatSession.Mode.NEW_ITEM,
        )
        self.url = reverse("chat:session-messages", args=[self.session.pk])
        self.client.force_authenticate(self.user)

    @patch("apps.chat.views.ChatEventStore.publish")
    @patch("apps.chat.views.chat_queue.enqueue")
    def test_message_api_persists_snapshot_and_persona_rows_before_enqueue(
        self,
        enqueue_mock,
        _publish_mock,
    ) -> None:
        response_modes.update_session_response_mode(
            user=self.user,
            identity=self.identity,
            session_id=self.session.pk,
            response_mode=ChatSession.ResponseMode.STYLIST,
            selected_persona_ids=["minimal", "practical"],
        )
        observed: dict[str, object] = {}

        def inspect_committed_snapshot(run: ChatRun) -> None:
            stored = ChatRun.objects.get(pk=run.pk)
            observed["response_mode"] = stored.response_mode
            observed["persona_ids"] = stored.persona_ids
            observed["personalization_snapshot"] = stored.personalization_snapshot
            observed["persona_rows"] = list(
                stored.persona_executions.values_list("persona_id", flat=True)
            )

        enqueue_mock.side_effect = inspect_committed_snapshot

        response = self.client.post(
            self.url,
            {
                "content": "출근 코디를 추천해줘",
                "client_message_id": "snapshot-api-message",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["run"]["response_mode"], "STYLIST")
        self.assertEqual(
            response.data["run"]["persona_ids"],
            ["minimal", "practical"],
        )
        self.assertEqual(observed["response_mode"], "STYLIST")
        self.assertEqual(observed["persona_ids"], ["minimal", "practical"])
        personalization = observed["personalization_snapshot"]
        self.assertEqual(personalization["schema_version"], "1.0")
        self.assertTrue(personalization["personalized"])
        self.assertIn("captured_at", personalization)
        self.assertEqual(
            set(personalization["sources"]),
            {"profile", "wardrobe", "behavior"},
        )
        self.assertEqual(observed["persona_rows"], ["minimal", "practical"])

    @patch("apps.chat.views.ChatEventStore.publish")
    @patch("apps.chat.views.chat_queue.enqueue")
    def test_duplicate_message_keeps_original_snapshot_and_persona_rows(
        self,
        _enqueue_mock,
        _publish_mock,
    ) -> None:
        response_modes.update_session_response_mode(
            user=self.user,
            identity=self.identity,
            session_id=self.session.pk,
            response_mode=ChatSession.ResponseMode.STYLIST,
            selected_persona_ids=["minimal"],
        )
        payload = {
            "content": "데이트 코디를 추천해줘",
            "client_message_id": "snapshot-api-duplicate",
        }
        first = self.client.post(self.url, payload, format="json")
        original_snapshot = ChatRun.objects.get(
            pk=first.data["run"]["id"]
        ).personalization_snapshot
        self.user.category_budgets = {"상의": 120_000}
        self.user.save(update_fields=["category_budgets"])

        response_modes.update_session_response_mode(
            user=self.user,
            identity=self.identity,
            session_id=self.session.pk,
            response_mode=ChatSession.ResponseMode.DEFAULT,
        )
        duplicate = self.client.post(self.url, payload, format="json")

        self.assertEqual(duplicate.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(duplicate.data["run"]["id"], first.data["run"]["id"])
        self.assertEqual(duplicate.data["run"]["response_mode"], "STYLIST")
        self.assertEqual(duplicate.data["run"]["persona_ids"], ["minimal"])
        run = ChatRun.objects.get(pk=first.data["run"]["id"])
        self.assertEqual(ChatRunPersona.objects.filter(run=run).count(), 1)
        self.assertEqual(run.personalization_snapshot, original_snapshot)

        next_response = self.client.post(
            self.url,
            {
                "content": "변경된 예산으로 다시 추천해줘",
                "client_message_id": "snapshot-api-after-budget-change",
            },
            format="json",
        )
        next_snapshot = ChatRun.objects.get(
            pk=next_response.data["run"]["id"]
        ).personalization_snapshot
        self.assertNotEqual(
            next_snapshot["sources"]["profile"]["category_budgets_fingerprint"],
            original_snapshot["sources"]["profile"][
                "category_budgets_fingerprint"
            ],
        )
