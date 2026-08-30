from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import (
    ChatAttachment,
    ChatIdentity,
    ChatMessage,
    ChatRun,
    ChatSession,
)
from apps.chat.services import identity as identity_service
from apps.chat.services import sessions as session_service
from apps.recommend.models import RecommendationResult
from apps.users.services.oauth import SocialProfile

User = get_user_model()


class GuestIdentityServiceTests(APITestCase):
    def test_guest_token_is_returned_once_and_only_hash_is_stored(self):
        credential = identity_service.issue_guest_identity()

        self.assertNotEqual(credential.token, credential.identity.guest_token_hash)
        self.assertEqual(len(credential.identity.guest_token_hash), 64)
        self.assertEqual(
            credential.identity.guest_token_hash,
            identity_service.token_hash(credential.token),
        )
        self.assertTrue(credential.identity.is_guest_active)

    def test_expired_guest_token_is_rejected(self):
        credential = identity_service.issue_guest_identity()
        ChatIdentity.objects.filter(pk=credential.identity.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        with self.assertRaises(identity_service.GuestTokenExpired):
            identity_service.get_guest_identity(credential.token)

    def test_claim_moves_sessions_messages_attachments_and_recommendations_once(self):
        credential = identity_service.issue_guest_identity()
        session = session_service.create_session(
            identity=credential.identity,
            mode=ChatSession.Mode.NEW_ITEM,
        )
        message, _ = session_service.append_message(
            identity=credential.identity,
            session_id=session.id,
            role=ChatMessage.Role.USER,
            content="출근용 새 바지를 추천해줘",
            client_message_id="client-1",
        )
        ChatAttachment.objects.create(
            message=message,
            s3_key="chat/guest/reference.jpg",
            mime_type="image/jpeg",
            size=1234,
            sha256="a" * 64,
        )
        run = ChatRun.objects.create(
            session=session,
            request_message=message,
            status=ChatRun.Status.SUCCEEDED,
        )
        recommendation = RecommendationResult.objects.create(
            identity=credential.identity,
            session=session,
            run=run,
            mode=RecommendationResult.Mode.NEW_ITEM,
            dataset_version="goldenset-v1",
        )
        user = User.objects.create_user(username="member-claim")

        summary = identity_service.claim_guest_identity(user, credential.token)

        member = ChatIdentity.objects.get(user=user)
        session.refresh_from_db()
        recommendation.refresh_from_db()
        credential.identity.refresh_from_db()
        self.assertEqual(session.identity, member)
        self.assertEqual(recommendation.identity, member)
        self.assertEqual(summary.session_count, 1)
        self.assertEqual(summary.message_count, 2)
        self.assertEqual(summary.attachment_count, 1)
        self.assertEqual(summary.recommendation_count, 1)
        self.assertEqual(credential.identity.claimed_by, member)

        with self.assertRaises(identity_service.GuestAlreadyClaimed):
            identity_service.claim_guest_identity(user, credential.token)


class ChatSessionServiceTests(APITestCase):
    def setUp(self):
        self.credential = identity_service.issue_guest_identity()

    def test_mode_is_immutable_and_change_creates_derived_session(self):
        source = session_service.create_session(
            identity=self.credential.identity,
            mode=ChatSession.Mode.WARDROBE_BASED,
            title="내 옷장",
        )
        source.context_state = {"occasion": "출근", "season": "가을"}
        source.conversation_summary = "검은색은 피하고 출근복을 찾는 중"
        source.save(
            update_fields=["context_state", "conversation_summary", "updated_at"]
        )

        source.mode = ChatSession.Mode.NEW_ITEM
        with self.assertRaises(ValidationError):
            source.save()

        derived = session_service.derive_session(
            identity=self.credential.identity,
            source_session_id=source.id,
            mode=ChatSession.Mode.NEW_ITEM,
            title="새 상품 포함",
        )
        self.assertEqual(derived.parent_session, source)
        self.assertEqual(derived.context_state, source.context_state)
        self.assertEqual(derived.conversation_summary, source.conversation_summary)
        self.assertEqual(derived.messages.count(), 1)
        self.assertEqual(
            derived.messages.get().metadata,
            {"message_kind": "greeting", "mode": ChatSession.Mode.NEW_ITEM},
        )

    def test_create_session_persists_mode_greeting_and_default_title(self):
        session = session_service.create_session(
            identity=self.credential.identity,
            mode=ChatSession.Mode.WARDROBE_BASED,
        )

        greeting = session.messages.get()
        session.refresh_from_db()
        self.assertEqual(session.title, "새 대화")
        self.assertEqual(greeting.sequence, 1)
        self.assertEqual(greeting.role, ChatMessage.Role.ASSISTANT)
        self.assertEqual(
            greeting.content,
            "옷장에 있는 옷으로 코디를 짜드릴게요.\n"
            "어떤 자리에 입을 옷이 필요하세요?",
        )
        self.assertEqual(greeting.metadata["message_kind"], "greeting")
        self.assertEqual(session.last_message_at, greeting.created_at)

    def test_first_question_sets_normalized_truncated_title_only_once(self):
        session = session_service.create_session(
            identity=self.credential.identity,
            mode=ChatSession.Mode.NEW_ITEM,
        )
        first, _ = session_service.append_message(
            identity=self.credential.identity,
            session_id=session.id,
            role=ChatMessage.Role.USER,
            content="  다음 주   금요일 면접에 입을 차분한 옷을 추천해 주세요  ",
            client_message_id="first-question",
        )
        duplicate, duplicate_created = session_service.append_message(
            identity=self.credential.identity,
            session_id=session.id,
            role=ChatMessage.Role.USER,
            content="재전송 본문",
            client_message_id="first-question",
        )
        session_service.append_message(
            identity=self.credential.identity,
            session_id=session.id,
            role=ChatMessage.Role.USER,
            content="두 번째 질문으로 제목을 바꾸지 마",
            client_message_id="second-question",
        )

        session.refresh_from_db()
        self.assertEqual(first.id, duplicate.id)
        self.assertFalse(duplicate_created)
        self.assertEqual(session.title, "다음 주 금요일 면접에 입을 차분한 …")

    def test_explicit_session_title_is_not_overwritten_by_first_question(self):
        session = session_service.create_session(
            identity=self.credential.identity,
            mode=ChatSession.Mode.NEW_ITEM,
            title="내 면접 코디",
        )

        session_service.append_message(
            identity=self.credential.identity,
            session_id=session.id,
            role=ChatMessage.Role.USER,
            content="다음 주 면접룩 추천해줘",
        )

        session.refresh_from_db()
        self.assertEqual(session.title, "내 면접 코디")

    def test_append_message_assigns_sequence_and_deduplicates_client_id(self):
        session = session_service.create_session(
            identity=self.credential.identity,
            mode=ChatSession.Mode.WARDROBE_BASED,
        )

        first, first_created = session_service.append_message(
            identity=self.credential.identity,
            session_id=session.id,
            role=ChatMessage.Role.USER,
            content="내일 뭐 입지?",
            client_message_id="message-1",
        )
        duplicate, duplicate_created = session_service.append_message(
            identity=self.credential.identity,
            session_id=session.id,
            role=ChatMessage.Role.USER,
            content="재전송된 본문",
            client_message_id="message-1",
        )
        second, _ = session_service.append_message(
            identity=self.credential.identity,
            session_id=session.id,
            role=ChatMessage.Role.ASSISTANT,
            content="날씨에 맞춰 찾아볼게요.",
        )

        self.assertTrue(first_created)
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate.id, first.id)
        self.assertEqual((first.sequence, second.sequence), (2, 3))
        self.assertEqual(session.messages.count(), 3)

    def test_other_identity_cannot_append_to_session(self):
        session = session_service.create_session(
            identity=self.credential.identity,
            mode=ChatSession.Mode.WARDROBE_BASED,
        )
        other = identity_service.issue_guest_identity().identity

        with self.assertRaises(session_service.ChatSessionForbidden):
            session_service.append_message(
                identity=other,
                session_id=session.id,
                role=ChatMessage.Role.USER,
                content="접근 시도",
            )


class ChatSessionApiTests(APITestCase):
    def _bootstrap_guest(self) -> ChatIdentity:
        response = self.client.post(reverse("chat:guest-identity"), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        cookie = response.cookies[settings.CHAT_GUEST_COOKIE_NAME]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], settings.CHAT_GUEST_COOKIE_SAMESITE)
        return ChatIdentity.objects.get(pk=response.data["identity_id"])

    def test_guest_can_create_list_derive_and_soft_delete_sessions(self):
        identity = self._bootstrap_guest()
        create_response = self.client.post(
            reverse("chat:session-list"),
            {"mode": "WARDROBE_BASED", "title": "내 옷 코디"},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        session_id = create_response.data["id"]

        messages_response = self.client.get(
            reverse("chat:session-messages", kwargs={"session_id": session_id})
        )
        self.assertEqual(messages_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(messages_response.data), 1)
        self.assertEqual(messages_response.data[0]["sequence"], 1)
        self.assertEqual(
            messages_response.data[0]["metadata"]["message_kind"],
            "greeting",
        )

        derive_response = self.client.post(
            reverse("chat:session-derive", kwargs={"session_id": session_id}),
            {"mode": "NEW_ITEM", "title": "신규 상품 추가"},
            format="json",
        )
        self.assertEqual(derive_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(str(derive_response.data["parent_session_id"]), session_id)

        list_response = self.client.get(reverse("chat:session-list"))
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.data), 2)
        self.assertIn(settings.CHAT_GUEST_COOKIE_NAME, list_response.cookies)
        self.assertTrue(
            all(item.identity_id == identity.id for item in identity.sessions.all())
        )

        delete_response = self.client.delete(
            reverse("chat:session-detail", kwargs={"session_id": session_id})
        )
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(len(self.client.get(reverse("chat:session-list")).data), 1)

    def test_explicit_claim_requires_authenticated_confirmation(self):
        guest = self._bootstrap_guest()
        session_service.create_session(
            identity=guest,
            mode=ChatSession.Mode.WARDROBE_BASED,
        )
        user = User.objects.create_user(username="existing-member")
        self.client.force_authenticate(user)

        response = self.client.post(
            reverse("chat:guest-claim"),
            {"confirm": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["session_count"], 1)
        self.assertEqual(ChatSession.objects.get().identity.user, user)


class SocialSignupGuestClaimTests(APITestCase):
    @patch("apps.users.views.oauth.authenticate")
    def test_first_social_login_automatically_claims_same_browser_guest_chat(
        self, mock_auth
    ):
        guest_response = self.client.post(
            reverse("chat:guest-identity"), {}, format="json"
        )
        guest = ChatIdentity.objects.get(pk=guest_response.data["identity_id"])
        session_service.create_session(
            identity=guest,
            mode=ChatSession.Mode.NEW_ITEM,
        )
        mock_auth.return_value = SocialProfile(
            provider="kakao",
            provider_user_id="new-chat-user",
            email="chat@example.com",
            nickname="채팅회원",
            profile_image="",
            raw={},
        )

        response = self.client.post(
            reverse("users:social-login", kwargs={"provider": "kakao"}),
            {
                "code": "authorization-code",
                "redirect_uri": "http://localhost:3000/oauth/callback",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["is_new_user"])
        self.assertEqual(response.data["guest_chat_claim"]["session_count"], 1)
        session = ChatSession.objects.get()
        self.assertEqual(session.identity.user_id, response.data["user"]["id"])
        guest.refresh_from_db()
        self.assertIsNotNone(guest.claimed_at)


class ExpiredGuestCleanupCommandTests(APITestCase):
    def test_dry_run_keeps_rows_and_real_run_cascades_chat_data(self):
        credential = identity_service.issue_guest_identity()
        session = session_service.create_session(
            identity=credential.identity,
            mode=ChatSession.Mode.WARDROBE_BASED,
        )
        session_service.append_message(
            identity=credential.identity,
            session_id=session.id,
            role=ChatMessage.Role.USER,
            content="만료될 대화",
        )
        ChatIdentity.objects.filter(pk=credential.identity.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        dry_output = StringIO()
        call_command("purge_expired_guest_chats", "--dry-run", stdout=dry_output)
        self.assertIn("1개", dry_output.getvalue())
        self.assertTrue(ChatIdentity.objects.filter(pk=credential.identity.pk).exists())

        call_command("purge_expired_guest_chats", stdout=StringIO())
        self.assertFalse(
            ChatIdentity.objects.filter(pk=credential.identity.pk).exists()
        )
        self.assertFalse(ChatSession.objects.exists())
        self.assertFalse(ChatMessage.objects.exists())
