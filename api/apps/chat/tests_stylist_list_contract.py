from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.chat.services import stylist_catalog
from apps.chat.views import StylistListView


class StylistListContractTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.view = StylistListView.as_view()

    @patch(
        "apps.chat.services.stylist_catalog.member_stylist_selections."
        "get_member_last_persona_ids",
        return_value=("minimal", "practical"),
    )
    def test_catalog_service_uses_fixed_order_and_member_selection(
        self,
        selection_mock,
    ) -> None:
        user = SimpleNamespace(is_authenticated=True, pk=1)

        payload = stylist_catalog.get_member_stylist_catalog(user)

        self.assertEqual(payload["min_select"], 1)
        self.assertEqual(payload["max_select"], 3)
        self.assertEqual(payload["default_persona_ids"], ["minimal"])
        self.assertEqual(
            payload["last_selected_persona_ids"],
            ["minimal", "practical"],
        )
        self.assertEqual(
            [stylist["id"] for stylist in payload["stylists"]],
            ["minimal", "experimental", "practical"],
        )
        selection_mock.assert_called_once_with(user)

    @patch("apps.chat.views.stylist_catalog.get_member_stylist_catalog")
    def test_member_response_exposes_only_public_stylist_fields(self, catalog_mock) -> None:
        catalog_mock.return_value = {
            "schema_version": "stylist-personas-v1",
            "min_select": 1,
            "max_select": 3,
            "default_persona_ids": ["minimal"],
            "last_selected_persona_ids": ["minimal", "practical"],
            "stylists": [
                {
                    "id": "minimal",
                    "display_name": "미니멀",
                    "description": "정돈된 조합을 제안합니다.",
                    "display_order": 1,
                    "strategy_profile": {"secret": True},
                    "voice_profile": {"secret": True},
                    "prompt_version": "private-prompt-v1",
                }
            ],
        }
        request = self.factory.get("/api/v1/chat/stylists/")
        user = SimpleNamespace(is_authenticated=True)
        force_authenticate(request, user=user)

        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data),
            {
                "schema_version",
                "min_select",
                "max_select",
                "default_persona_ids",
                "last_selected_persona_ids",
                "stylists",
            },
        )
        self.assertEqual(
            set(response.data["stylists"][0]),
            {"id", "display_name", "description", "display_order"},
        )
        catalog_mock.assert_called_once_with(user)

    @patch("apps.chat.views.stylist_catalog.get_member_stylist_catalog")
    def test_guest_request_is_rejected_before_catalog_lookup(self, catalog_mock) -> None:
        request = self.factory.get("/api/v1/chat/stylists/")

        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        catalog_mock.assert_not_called()
