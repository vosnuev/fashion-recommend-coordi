from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.chat.models import MemberStylistSelection
from apps.chat.services import member_stylist_selections

User = get_user_model()


class MemberStylistSelectionTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="stylist-selection-member")

    def test_member_without_row_uses_minimal_without_creating_row(self) -> None:
        persona_ids = member_stylist_selections.get_member_last_persona_ids(
            self.user
        )

        self.assertEqual(persona_ids, ("minimal",))
        self.assertFalse(MemberStylistSelection.objects.exists())

    def test_save_creates_and_then_updates_single_member_row(self) -> None:
        selection, created = (
            member_stylist_selections.save_member_last_persona_ids(
                self.user,
                ["minimal", "experimental"],
            )
        )

        self.assertTrue(created)
        self.assertEqual(
            selection.last_selected_persona_ids,
            ["minimal", "experimental"],
        )

        updated, created_again = (
            member_stylist_selections.save_member_last_persona_ids(
                self.user,
                ["practical"],
            )
        )

        self.assertFalse(created_again)
        self.assertEqual(updated.pk, selection.pk)
        self.assertEqual(updated.last_selected_persona_ids, ["practical"])
        self.assertEqual(MemberStylistSelection.objects.count(), 1)
        self.assertEqual(
            member_stylist_selections.get_member_last_persona_ids(self.user),
            ("practical",),
        )

    def test_save_rejects_empty_invalid_duplicate_and_unordered_ids(self) -> None:
        invalid_values = (
            ([], "최소 1명"),
            (["unknown"], "지원하지 않는"),
            (["minimal", "minimal"], "중복"),
            (["practical", "minimal"], "고정 순서"),
        )

        for value, message in invalid_values:
            with self.subTest(value=value), self.assertRaisesMessage(
                member_stylist_selections.MemberStylistSelectionError,
                message,
            ):
                member_stylist_selections.save_member_last_persona_ids(
                    self.user,
                    value,
                )

        self.assertFalse(MemberStylistSelection.objects.exists())

    def test_model_validation_rejects_empty_last_selection(self) -> None:
        selection = MemberStylistSelection(
            user=self.user,
            last_selected_persona_ids=[],
        )

        with self.assertRaisesMessage(ValidationError, "최소 1명"):
            selection.full_clean()

    def test_anonymous_user_cannot_read_or_save_selection(self) -> None:
        anonymous = AnonymousUser()

        with self.assertRaises(
            member_stylist_selections.MemberAuthenticationRequired
        ):
            member_stylist_selections.get_member_last_persona_ids(anonymous)
        with self.assertRaises(
            member_stylist_selections.MemberAuthenticationRequired
        ):
            member_stylist_selections.save_member_last_persona_ids(
                anonymous,
                ["minimal"],
            )
