from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import MemberStylistSelection
from apps.chat.services import member_stylist_selections

User = get_user_model()


class StylistListApiTests(APITestCase):
    def setUp(self) -> None:
        self.url = reverse("chat:stylist-list")
        self.user = User.objects.create_user(username="stylist-list-member")

    def test_member_without_selection_gets_minimal_default_in_fixed_order(self) -> None:
        self.client.force_authenticate(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["schema_version"], "stylist-personas-v1")
        self.assertEqual(response.data["min_select"], 1)
        self.assertEqual(response.data["max_select"], 3)
        self.assertEqual(response.data["default_persona_ids"], ["minimal"])
        self.assertEqual(response.data["last_selected_persona_ids"], ["minimal"])
        self.assertEqual(
            [stylist["id"] for stylist in response.data["stylists"]],
            ["minimal", "experimental", "practical"],
        )
        self.assertEqual(
            [stylist["display_order"] for stylist in response.data["stylists"]],
            [1, 2, 3],
        )
        self.assertFalse(MemberStylistSelection.objects.exists())

    def test_member_last_selection_is_returned_without_internal_configuration(
        self,
    ) -> None:
        member_stylist_selections.save_member_last_persona_ids(
            self.user,
            ["minimal", "practical"],
        )
        self.client.force_authenticate(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["last_selected_persona_ids"],
            ["minimal", "practical"],
        )
        public_fields = {"id", "display_name", "description", "display_order"}
        for stylist in response.data["stylists"]:
            self.assertEqual(set(stylist), public_fields)
            self.assertNotIn("strategy_profile", stylist)
            self.assertNotIn("voice_profile", stylist)
            self.assertNotIn("prompt_version", stylist)

    def test_guest_cannot_read_stylist_list(self) -> None:
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
