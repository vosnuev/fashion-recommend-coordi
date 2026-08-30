from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import ChatSession, MemberStylistSelection
from apps.chat.services import identity as identity_service
from apps.chat.services import member_stylist_selections
from apps.chat.services import sessions as session_service

User = get_user_model()


class ChatSessionResponseModeApiTests(APITestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="response-mode-member")
        self.identity = identity_service.get_or_create_member_identity(self.user)
        self.session = session_service.create_session(
            identity=self.identity,
            mode=ChatSession.Mode.NEW_ITEM,
            title="응답 모드 테스트",
        )
        self.url = reverse(
            "chat:session-response-mode",
            args=[self.session.pk],
        )
        self.client.force_authenticate(self.user)

    def test_stylist_mode_saves_session_and_member_selection(self) -> None:
        response = self.client.patch(
            self.url,
            {
                "response_mode": "STYLIST",
                "selected_persona_ids": ["minimal", "practical"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.session.refresh_from_db()
        self.assertEqual(self.session.response_mode, ChatSession.ResponseMode.STYLIST)
        self.assertEqual(
            self.session.selected_persona_ids,
            ["minimal", "practical"],
        )
        self.assertIsNotNone(self.session.persona_selection_updated_at)
        self.assertEqual(self.session.mode, ChatSession.Mode.NEW_ITEM)
        self.assertEqual(
            MemberStylistSelection.objects.get(user=self.user).last_selected_persona_ids,
            ["minimal", "practical"],
        )

    def test_stylist_mode_normalizes_click_order_before_saving(self) -> None:
        response = self.client.patch(
            self.url,
            {
                "response_mode": "STYLIST",
                "selected_persona_ids": ["practical", "minimal", "experimental"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected = ["minimal", "experimental", "practical"]
        self.assertEqual(response.data["selected_persona_ids"], expected)
        self.session.refresh_from_db()
        self.assertEqual(self.session.selected_persona_ids, expected)
        self.assertEqual(
            MemberStylistSelection.objects.get(user=self.user).last_selected_persona_ids,
            expected,
        )

    def test_first_stylist_activation_without_ids_uses_minimal(self) -> None:
        response = self.client.patch(
            self.url,
            {"response_mode": "STYLIST"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["selected_persona_ids"], ["minimal"])
        self.assertEqual(
            MemberStylistSelection.objects.get(user=self.user).last_selected_persona_ids,
            ["minimal"],
        )

    def test_current_session_selection_has_priority_over_member_last_selection(
        self,
    ) -> None:
        member_stylist_selections.save_member_last_persona_ids(
            self.user,
            ["experimental"],
        )
        self.session.selected_persona_ids = ["minimal", "practical"]
        self.session.save(update_fields=["selected_persona_ids", "updated_at"])

        response = self.client.patch(
            self.url,
            {"response_mode": "STYLIST"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["selected_persona_ids"],
            ["minimal", "practical"],
        )
        self.assertEqual(
            MemberStylistSelection.objects.get(user=self.user).last_selected_persona_ids,
            ["minimal", "practical"],
        )

    def test_other_session_uses_member_last_selection(self) -> None:
        member_stylist_selections.save_member_last_persona_ids(
            self.user,
            ["experimental", "practical"],
        )
        other_session = session_service.create_session(
            identity=self.identity,
            mode=ChatSession.Mode.WARDROBE_BASED,
        )
        url = reverse("chat:session-response-mode", args=[other_session.pk])

        response = self.client.patch(
            url,
            {"response_mode": "STYLIST"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["selected_persona_ids"],
            ["experimental", "practical"],
        )
        self.assertEqual(response.data["mode"], ChatSession.Mode.WARDROBE_BASED)

    def test_default_mode_preserves_selection_and_recommendation_mode(self) -> None:
        self.client.patch(
            self.url,
            {
                "response_mode": "STYLIST",
                "selected_persona_ids": ["minimal", "experimental"],
            },
            format="json",
        )

        response = self.client.patch(
            self.url,
            {"response_mode": "DEFAULT"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["response_mode"], "DEFAULT")
        self.assertEqual(
            response.data["selected_persona_ids"],
            ["minimal", "experimental"],
        )
        self.assertEqual(response.data["mode"], ChatSession.Mode.NEW_ITEM)
        self.assertEqual(
            MemberStylistSelection.objects.get(user=self.user).last_selected_persona_ids,
            ["minimal", "experimental"],
        )

    def test_invalid_stylist_selections_are_rejected(self) -> None:
        invalid_values = (
            [],
            ["unknown"],
            ["minimal", "minimal"],
            ["minimal", "experimental", "practical", "unknown"],
        )

        for persona_ids in invalid_values:
            with self.subTest(persona_ids=persona_ids):
                response = self.client.patch(
                    self.url,
                    {
                        "response_mode": "STYLIST",
                        "selected_persona_ids": persona_ids,
                    },
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.session.refresh_from_db()
        self.assertEqual(self.session.response_mode, ChatSession.ResponseMode.DEFAULT)
        self.assertEqual(self.session.selected_persona_ids, [])

    def test_default_mode_rejects_a_selection_payload(self) -> None:
        response = self.client.patch(
            self.url,
            {
                "response_mode": "DEFAULT",
                "selected_persona_ids": ["minimal"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_member_cannot_change_another_members_session(self) -> None:
        other_user = User.objects.create_user(username="response-mode-other")
        other_identity = identity_service.get_or_create_member_identity(other_user)
        other_session = session_service.create_session(
            identity=other_identity,
            mode=ChatSession.Mode.NEW_ITEM,
        )
        url = reverse("chat:session-response-mode", args=[other_session.pk])

        response = self.client.patch(
            url,
            {
                "response_mode": "STYLIST",
                "selected_persona_ids": ["minimal"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_guest_cannot_change_response_mode(self) -> None:
        self.client.force_authenticate(user=None)

        response = self.client.patch(
            self.url,
            {
                "response_mode": "STYLIST",
                "selected_persona_ids": ["minimal"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
