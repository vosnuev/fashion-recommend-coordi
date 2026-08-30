from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import ChatIdentity, ChatMessage, ChatSession
from apps.chat.services import sessions as session_service


class ChatHistoryApiTests(APITestCase):
    def setUp(self):
        response = self.client.post(reverse("chat:guest-identity"), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.identity = ChatIdentity.objects.get(pk=response.data["identity_id"])

    def _session(self, *, title="", mode=ChatSession.Mode.NEW_ITEM):
        return session_service.create_session(
            identity=self.identity,
            mode=mode,
            title=title,
        )

    def _message(self, session, content, client_id):
        return session_service.append_message(
            identity=self.identity,
            session_id=session.pk,
            role=ChatMessage.Role.USER,
            content=content,
            client_message_id=client_id,
        )[0]

    def test_searches_title_and_message_content_with_match_preview(self):
        title_match = self._session(title="금융권 면접 코디")
        body_match = self._session(title="업무용 코디")
        body_message = self._message(
            body_match,
            "다음 주 금융권 면접에 입을 옷이 필요해요",
            "body-match",
        )
        unrelated = self._session(title="주말 산책")
        self._message(unrelated, "편한 운동화 코디", "unrelated")

        response = self.client.get(
            reverse("chat:session-search"),
            {"query": "  금융권   면접 ", "limit": 20},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["query"], "금융권 면접")
        self.assertEqual(response.data["total_count"], 2)
        found = {str(item["id"]): item for item in response.data["items"]}
        self.assertIn(str(title_match.pk), found)
        self.assertIn(str(body_match.pk), found)
        self.assertNotIn(str(unrelated.pk), found)
        match = found[str(body_match.pk)]["search_match"]
        self.assertEqual(match["message_id"], str(body_message.pk))
        self.assertEqual(match["sequence"], body_message.sequence)
        self.assertIn("금융권 면접", match["preview"])

    def test_search_hides_deleted_and_other_identity_sessions(self):
        deleted = self._session(title="면접 삭제 대화")
        deleted.deleted_at = timezone.now()
        deleted.save(update_fields=["deleted_at", "updated_at"])

        other = ChatIdentity.objects.create(
            identity_type=ChatIdentity.IdentityType.GUEST,
            guest_token_hash="c" * 64,
            expires_at=timezone.now() + timedelta(days=1),
        )
        session_service.create_session(
            identity=other,
            mode=ChatSession.Mode.NEW_ITEM,
            title="면접 다른 사용자",
        )

        response = self.client.get(
            reverse("chat:session-search"),
            {"query": "면접"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total_count"], 0)
        self.assertEqual(response.data["items"], [])

    def test_search_cursor_paginates_without_duplicates_and_binds_query(self):
        sessions = [self._session(title=f"출근 코디 {index}") for index in range(3)]
        base = timezone.now()
        for index, session in enumerate(sessions):
            ChatSession.objects.filter(pk=session.pk).update(
                updated_at=base - timedelta(minutes=index)
            )

        first = self.client.get(
            reverse("chat:session-search"),
            {"query": "출근", "limit": 2},
        )
        second = self.client.get(
            reverse("chat:session-search"),
            {"query": "출근", "limit": 2, "cursor": first.data["next_cursor"]},
        )
        wrong_query = self.client.get(
            reverse("chat:session-search"),
            {"query": "주말", "limit": 2, "cursor": first.data["next_cursor"]},
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertTrue(first.data["has_more"])
        self.assertEqual(len(first.data["items"]), 2)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertFalse(second.data["has_more"])
        self.assertEqual(len(second.data["items"]), 1)
        first_ids = {item["id"] for item in first.data["items"]}
        second_ids = {item["id"] for item in second.data["items"]}
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(wrong_query.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(wrong_query.data["code"], "CHAT_PAGE_CURSOR_INVALID")

    def test_message_cursor_returns_latest_then_older_pages_in_time_order(self):
        session = self._session()
        for index in range(1, 6):
            self._message(session, f"질문 {index}", f"question-{index}")
        url = reverse("chat:session-message-page", kwargs={"session_id": session.pk})

        first = self.client.get(url, {"limit": 2})
        second = self.client.get(
            url,
            {"limit": 2, "cursor": first.data["next_cursor"]},
        )
        third = self.client.get(
            url,
            {"limit": 2, "cursor": second.data["next_cursor"]},
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["total_count"], 6)
        self.assertEqual(
            [item["sequence"] for item in first.data["items"]],
            [5, 6],
        )
        self.assertEqual(
            [item["sequence"] for item in second.data["items"]],
            [3, 4],
        )
        self.assertEqual(
            [item["sequence"] for item in third.data["items"]],
            [1, 2],
        )
        self.assertFalse(third.data["has_more"])
        self.assertIsNone(third.data["next_cursor"])

    def test_message_cursor_rejects_tampering_and_other_session(self):
        session = self._session(title="첫 대화")
        other_session = self._session(title="둘째 대화")
        self._message(session, "첫 질문", "first")
        first_url = reverse(
            "chat:session-message-page",
            kwargs={"session_id": session.pk},
        )
        other_url = reverse(
            "chat:session-message-page",
            kwargs={"session_id": other_session.pk},
        )
        first = self.client.get(first_url, {"limit": 1})

        tampered = self.client.get(first_url, {"cursor": "modified-cursor"})
        wrong_session = self.client.get(
            other_url,
            {"cursor": first.data["next_cursor"]},
        )

        self.assertEqual(tampered.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(tampered.data["code"], "CHAT_PAGE_CURSOR_INVALID")
        self.assertEqual(wrong_session.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(wrong_session.data["code"], "CHAT_PAGE_CURSOR_INVALID")

    def test_other_identity_cannot_page_session_messages(self):
        session = self._session(title="비공개 대화")
        other_client = type(self.client)()
        guest = other_client.post(reverse("chat:guest-identity"), {}, format="json")
        self.assertIn(settings.CHAT_GUEST_COOKIE_NAME, guest.cookies)

        response = other_client.get(
            reverse(
                "chat:session-message-page",
                kwargs={"session_id": session.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["code"], "CHAT_SESSION_NOT_FOUND")

    def test_invalid_query_and_limit_are_rejected(self):
        search = self.client.get(reverse("chat:session-search"), {"query": "   "})
        session = self._session()
        page = self.client.get(
            reverse("chat:session-message-page", kwargs={"session_id": session.pk}),
            {"limit": 101},
        )

        self.assertEqual(search.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(page.status_code, status.HTTP_400_BAD_REQUEST)
