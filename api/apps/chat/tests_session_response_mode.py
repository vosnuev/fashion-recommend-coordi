from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.chat.models import ChatSession
from apps.chat.serializers import ChatSessionSerializer
from apps.chat.services import identity as identity_service
from apps.chat.services import sessions as session_service

User = get_user_model()


class ChatSessionResponseModeTests(TestCase):
    def setUp(self) -> None:
        user = User.objects.create_user(username="response-mode-member")
        self.identity = identity_service.get_or_create_member_identity(user)

    def _create_session(self) -> ChatSession:
        return session_service.create_session(
            identity=self.identity,
            mode=ChatSession.Mode.WARDROBE_BASED,
        )

    def test_new_session_uses_safe_default_response_state(self) -> None:
        session = self._create_session()

        self.assertEqual(session.response_mode, ChatSession.ResponseMode.DEFAULT)
        self.assertEqual(session.selected_persona_ids, [])
        self.assertIsNone(session.persona_selection_updated_at)

        serialized = ChatSessionSerializer(session).data
        self.assertEqual(serialized["response_mode"], "DEFAULT")
        self.assertEqual(serialized["selected_persona_ids"], [])
        self.assertIsNone(serialized["persona_selection_updated_at"])

    def test_stylist_mode_persists_selection_and_change_time(self) -> None:
        session = self._create_session()
        session.response_mode = ChatSession.ResponseMode.STYLIST
        session.selected_persona_ids = ["minimal", "practical"]

        session.full_clean()
        session.save(update_fields=["response_mode", "selected_persona_ids"])
        session.refresh_from_db()

        self.assertEqual(session.response_mode, ChatSession.ResponseMode.STYLIST)
        self.assertEqual(session.selected_persona_ids, ["minimal", "practical"])
        self.assertIsNotNone(session.persona_selection_updated_at)

    def test_default_mode_can_retain_previous_valid_selection(self) -> None:
        session = self._create_session()
        session.selected_persona_ids = ["minimal"]

        session.full_clean()

        self.assertEqual(session.response_mode, ChatSession.ResponseMode.DEFAULT)

    def test_stylist_mode_requires_at_least_one_persona(self) -> None:
        session = self._create_session()
        session.response_mode = ChatSession.ResponseMode.STYLIST

        with self.assertRaisesMessage(ValidationError, "1명 이상"):
            session.full_clean()

    def test_selection_rejects_invalid_shape_ids_duplicates_and_order(self) -> None:
        invalid_values = (
            ({"minimal": True}, "JSON 배열"),
            (["unknown"], "지원하지 않는"),
            (["minimal", "minimal"], "중복"),
            (
                ["minimal", "experimental", "practical", "minimal"],
                "최대 3명",
            ),
            (["practical", "minimal"], "고정 순서"),
        )

        for value, message in invalid_values:
            with self.subTest(value=value):
                session = self._create_session()
                session.selected_persona_ids = value
                with self.assertRaisesMessage(ValidationError, message):
                    session.full_clean()

    def test_derived_session_copies_current_response_state(self) -> None:
        source = self._create_session()
        source.response_mode = ChatSession.ResponseMode.STYLIST
        source.selected_persona_ids = ["minimal", "experimental"]
        source.full_clean()
        source.save(update_fields=["response_mode", "selected_persona_ids"])
        source.refresh_from_db()

        derived = session_service.derive_session(
            identity=self.identity,
            source_session_id=source.id,
            mode=ChatSession.Mode.NEW_ITEM,
        )

        self.assertEqual(derived.response_mode, source.response_mode)
        self.assertEqual(derived.selected_persona_ids, source.selected_persona_ids)
        self.assertEqual(
            derived.persona_selection_updated_at,
            source.persona_selection_updated_at,
        )
