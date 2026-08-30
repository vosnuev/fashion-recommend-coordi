from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.chat.models import ChatSession
from apps.chat.services import response_modes
from apps.chat.views import ChatSessionResponseModeView


class ChatSessionResponseModeContractTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.view = ChatSessionResponseModeView.as_view()
        self.session_id = uuid.uuid4()
        self.identity_id = uuid.uuid4()
        self.user = SimpleNamespace(is_authenticated=True, pk=1)

    def _request(self, payload: dict):
        request = self.factory.patch(
            f"/api/v1/chat/sessions/{self.session_id}/response-mode/",
            payload,
            format="json",
        )
        force_authenticate(request, user=self.user)
        return request

    @patch("apps.chat.views._identity")
    @patch("apps.chat.views.response_modes.update_session_response_mode")
    def test_member_request_is_forwarded_and_serialized(
        self,
        update_mock,
        identity_mock,
    ) -> None:
        identity = SimpleNamespace(id=self.identity_id)
        identity_mock.return_value = identity
        update_mock.return_value = ChatSession(
            id=self.session_id,
            identity_id=self.identity_id,
            mode=ChatSession.Mode.NEW_ITEM,
            response_mode=ChatSession.ResponseMode.STYLIST,
            selected_persona_ids=["minimal", "practical"],
            title="테스트",
        )
        request = self._request(
            {
                "response_mode": "STYLIST",
                "selected_persona_ids": ["minimal", "practical"],
            }
        )

        response = self.view(request, session_id=self.session_id)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["mode"], "NEW_ITEM")
        self.assertEqual(response.data["response_mode"], "STYLIST")
        self.assertEqual(
            response.data["selected_persona_ids"],
            ["minimal", "practical"],
        )
        update_mock.assert_called_once_with(
            user=self.user,
            identity=identity,
            session_id=self.session_id,
            response_mode="STYLIST",
            selected_persona_ids=["minimal", "practical"],
        )

    @patch("apps.chat.views._identity", return_value=SimpleNamespace(id="member"))
    @patch("apps.chat.views.response_modes.update_session_response_mode")
    def test_service_validation_error_becomes_400(
        self,
        update_mock,
        _identity_mock,
    ) -> None:
        update_mock.side_effect = response_modes.ChatResponseModeError(
            "스타일리스트 ID는 중복될 수 없습니다."
        )

        response = self.view(
            self._request(
                {
                    "response_mode": "STYLIST",
                    "selected_persona_ids": ["minimal", "minimal"],
                }
            ),
            session_id=self.session_id,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "CHAT_RESPONSE_MODE_INVALID")

    @patch("apps.chat.views._identity", return_value=SimpleNamespace(id="member"))
    @patch("apps.chat.views.response_modes.update_session_response_mode")
    def test_missing_or_unowned_session_becomes_404(
        self,
        update_mock,
        _identity_mock,
    ) -> None:
        update_mock.side_effect = response_modes.ChatResponseModeSessionNotFound(
            "채팅 세션이 없거나 현재 회원이 소유하지 않습니다."
        )

        response = self.view(
            self._request({"response_mode": "DEFAULT"}),
            session_id=self.session_id,
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["code"], "CHAT_SESSION_NOT_FOUND")

    @patch("apps.chat.views.response_modes.update_session_response_mode")
    def test_guest_is_rejected_before_service_call(self, update_mock) -> None:
        request = self.factory.patch(
            f"/api/v1/chat/sessions/{self.session_id}/response-mode/",
            {"response_mode": "STYLIST"},
            format="json",
        )

        response = self.view(request, session_id=self.session_id)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        update_mock.assert_not_called()
