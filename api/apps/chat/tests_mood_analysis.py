from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

import redis
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import (
    ChatAttachment,
    ChatIdentity,
    ChatMessage,
    ChatRun,
    ChatSession,
)
from apps.chat.services import sessions as session_service
from apps.chat.services.mood_analysis import normalize_image
from apps.chat.services.openai_adapter import (
    LLMResult,
    LLMUsage,
    OpenAIChatAdapter,
    PhotoMoodAnalysis,
    RecommendationConditions,
    TurnAnalysis,
)
from apps.chat.services.orchestrator import ChatOrchestrator, create_run


def image_bytes(*, width: int = 16, height: int = 16, image_format: str = "JPEG"):
    buffer = BytesIO()
    Image.new("RGB", (width, height), color=(64, 72, 80)).save(
        buffer,
        format=image_format,
    )
    return buffer.getvalue()


def mood_result() -> PhotoMoodAnalysis:
    return PhotoMoodAnalysis(
        summary="차분한 무채색 미니멀 룩",
        tags=["미니멀", "톤다운", "오버핏"],
        styles=["미니멀", "존재하지 않는 스타일"],
        colors=["그레이", "블랙"],
        fits=["오버핏"],
    )


@override_settings(
    CHAT_OPENAI_MODEL="gpt-4o-mini",
    CHAT_PROMPT_VERSION="test-prompt-v1",
    CHAT_OPENAI_MAX_OUTPUT_TOKENS=500,
    CHAT_MOOD_IMAGE_DETAIL="low",
)
class PhotoMoodOpenAIAdapterTests(SimpleTestCase):
    def test_image_is_sent_as_non_stored_structured_responses_input(self):
        response = SimpleNamespace(
            id="resp-mood",
            output_parsed=mood_result(),
            usage=SimpleNamespace(
                input_tokens=30,
                output_tokens=12,
                input_tokens_details=SimpleNamespace(cached_tokens=0),
            ),
        )
        client = Mock()
        client.responses.parse.return_value = response

        result = OpenAIChatAdapter(client=client).analyze_photo_mood(
            identity_id="internal-id",
            image_bytes=b"jpeg-bytes",
            mime_type="image/jpeg",
        )

        kwargs = client.responses.parse.call_args.kwargs
        content = kwargs["input"][0]["content"]
        image_part = next(part for part in content if part["type"] == "input_image")
        self.assertTrue(image_part["image_url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(image_part["detail"], "low")
        self.assertIs(kwargs["text_format"], PhotoMoodAnalysis)
        self.assertFalse(kwargs["store"])
        self.assertNotEqual(kwargs["safety_identifier"], "internal-id")
        self.assertEqual(result.response_id, "resp-mood")

    @override_settings(CHAT_MOOD_IMAGE_MAX_EDGE_PX=128)
    def test_image_is_oriented_resized_and_converted_to_jpeg(self):
        normalized, mime_type = normalize_image(
            image_bytes(width=400, height=200, image_format="PNG"),
            "image/png",
        )

        with Image.open(BytesIO(normalized)) as image:
            self.assertLessEqual(max(image.size), 128)
            self.assertEqual(image.mode, "RGB")
            self.assertEqual(image.format, "JPEG")
        self.assertEqual(mime_type, "image/jpeg")


@override_settings(CHAT_ATTACHMENT_S3_BUCKET="chat-test-bucket")
class ChatMoodApiTests(APITestCase):
    def setUp(self):
        response = self.client.post(reverse("chat:guest-identity"), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.identity = ChatIdentity.objects.get(pk=response.data["identity_id"])
        self.session = session_service.create_session(
            identity=self.identity,
            mode=ChatSession.Mode.NEW_ITEM,
        )
        self.message = ChatMessage.objects.create(
            session=self.session,
            sequence=2,
            role=ChatMessage.Role.USER,
            content="이 사진 같은 느낌으로 추천해줘",
            status=ChatMessage.Status.COMPLETED,
            client_message_id="photo-mood-1",
            metadata={"message_kind": "image"},
        )
        self.attachment = ChatAttachment.objects.create(
            message=self.message,
            s3_key=f"chat/{self.identity.pk}/attachments/test.jpg",
            mime_type="image/jpeg",
            size=100,
            sha256="a" * 64,
        )
        self.analysis_url = reverse(
            "chat:attachment-mood-analysis",
            kwargs={
                "session_id": self.session.pk,
                "attachment_id": self.attachment.pk,
            },
        )
        self.decision_url = reverse(
            "chat:attachment-mood-decision",
            kwargs={
                "session_id": self.session.pk,
                "attachment_id": self.attachment.pk,
            },
        )

    @patch("apps.chat.views.ChatEventStore")
    @patch("apps.chat.views.chat_queue.enqueue")
    def test_analysis_request_is_idempotent_and_uses_existing_chat_queue(
        self,
        enqueue,
        event_store,
    ):
        first = self.client.post(self.analysis_url, {}, format="json")
        second = self.client.post(self.analysis_url, {}, format="json")

        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(second.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(first.data["run"]["id"], second.data["run"]["id"])
        self.assertEqual(ChatRun.objects.count(), 1)
        self.attachment.refresh_from_db()
        self.assertEqual(
            self.attachment.analysis_status,
            ChatAttachment.AnalysisStatus.QUEUED,
        )
        self.assertEqual(enqueue.call_count, 2)
        self.assertEqual(event_store.return_value.publish.call_count, 2)

    @patch("apps.chat.views.chat_queue.enqueue")
    def test_queue_failure_marks_run_message_and_attachment_failed(self, enqueue):
        enqueue.side_effect = redis.ConnectionError("redis unavailable")

        response = self.client.post(self.analysis_url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        run = ChatRun.objects.get()
        self.message.refresh_from_db()
        self.attachment.refresh_from_db()
        self.assertEqual(run.status, ChatRun.Status.FAILED)
        self.assertEqual(self.message.status, ChatMessage.Status.FAILED)
        self.assertEqual(
            self.attachment.analysis_status,
            ChatAttachment.AnalysisStatus.FAILED,
        )

    def test_other_identity_cannot_request_analysis(self):
        other_client = type(self.client)()
        other_client.post(reverse("chat:guest-identity"), {}, format="json")

        response = other_client.post(self.analysis_url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["code"], "CHAT_ATTACHMENT_NOT_FOUND")
        self.assertFalse(ChatRun.objects.exists())

    def _complete_analysis(self):
        self.attachment.analysis_status = ChatAttachment.AnalysisStatus.SUCCEEDED
        self.attachment.analysis_result = {
            "summary": "차분한 무채색 미니멀 룩",
            "tags": ["미니멀", "톤다운", "오버핏"],
            "styles": ["미니멀"],
            "colors": ["그레이", "블랙"],
            "fits": ["오버핏"],
        }
        self.attachment.save(update_fields=["analysis_status", "analysis_result"])

    def test_approve_applies_conditions_once_and_opposite_retry_conflicts(self):
        self._complete_analysis()

        first = self.client.post(
            self.decision_url,
            {"decision": "APPROVE"},
            format="json",
        )
        same_retry = self.client.post(
            self.decision_url,
            {"decision": "APPROVE"},
            format="json",
        )
        opposite = self.client.post(
            self.decision_url,
            {"decision": "REJECT"},
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertTrue(first.data["changed"])
        self.assertTrue(first.data["applied"])
        self.assertEqual(same_retry.status_code, status.HTTP_200_OK)
        self.assertFalse(same_retry.data["changed"])
        self.assertEqual(opposite.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(opposite.data["code"], "CHAT_MOOD_DECISION_FINALIZED")
        self.session.refresh_from_db()
        conditions = self.session.context_state["recommendation_conditions"]
        self.assertEqual(conditions["styles"], ["미니멀"])
        self.assertEqual(conditions["colors"], ["그레이", "블랙"])
        self.assertEqual(conditions["fits"], ["오버핏"])
        self.assertEqual(len(self.session.context_state["approved_photo_moods"]), 1)

    def test_reject_preserves_analysis_without_changing_context(self):
        self._complete_analysis()

        response = self.client.post(
            self.decision_url,
            {"decision": "REJECT"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["applied"])
        self.attachment.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(
            self.attachment.mood_decision,
            ChatAttachment.MoodDecision.REJECTED,
        )
        self.assertTrue(self.attachment.analysis_result)
        self.assertEqual(self.session.context_state, {})

    def test_decision_before_analysis_completion_is_rejected(self):
        response = self.client.post(
            self.decision_url,
            {"decision": "APPROVE"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "CHAT_MOOD_ANALYSIS_NOT_READY")


@override_settings(CHAT_SUMMARY_TRIGGER_MESSAGES=100)
class ChatMoodWorkerTests(APITestCase):
    def setUp(self):
        self.identity = ChatIdentity.objects.create(
            identity_type=ChatIdentity.IdentityType.GUEST,
            guest_token_hash="d" * 64,
            expires_at="2099-01-01T00:00:00Z",
        )
        self.session = ChatSession.objects.create(
            identity=self.identity,
            mode=ChatSession.Mode.NEW_ITEM,
        )
        self.message = ChatMessage.objects.create(
            session=self.session,
            sequence=1,
            role=ChatMessage.Role.USER,
            content="이 무드로 추천해줘",
            status=ChatMessage.Status.PENDING,
            metadata={"message_kind": "image"},
        )
        self.attachment = ChatAttachment.objects.create(
            message=self.message,
            s3_key="chat/identity/attachments/mood.jpg",
            mime_type="image/jpeg",
            size=100,
            sha256="b" * 64,
            analysis_status=ChatAttachment.AnalysisStatus.QUEUED,
        )
        self.run, _ = create_run(
            identity=self.identity,
            session_id=self.session.pk,
            request_message_id=self.message.pk,
        )

    @patch("apps.chat.services.mood_analysis.attachment_storage.download_bytes")
    def test_worker_persists_analysis_and_returns_mood_card_message(self, download):
        download.return_value = image_bytes()
        llm = Mock()
        llm.analyze_photo_mood.return_value = LLMResult(
            value=mood_result(),
            response_id="resp-photo",
            usage=LLMUsage(input_tokens=40, output_tokens=15),
        )
        context_service = Mock()
        pipeline = Mock()

        result = ChatOrchestrator(
            context_service=context_service,
            llm=llm,
            recommendation_pipeline=pipeline,
        ).process(self.run.pk)

        self.attachment.refresh_from_db()
        self.assertEqual(result.run.status, ChatRun.Status.SUCCEEDED)
        self.assertEqual(
            self.attachment.analysis_status,
            ChatAttachment.AnalysisStatus.SUCCEEDED,
        )
        self.assertEqual(self.attachment.analysis_result["styles"], ["미니멀"])
        self.assertEqual(result.response_message.metadata["message_kind"], "mood")
        self.assertEqual(
            result.response_message.metadata["attachment_id"],
            str(self.attachment.pk),
        )
        self.assertEqual(result.run.input_tokens, 40)
        context_service.build.assert_not_called()
        pipeline.execute.assert_not_called()

    def test_saved_session_conditions_fill_empty_next_turn_conditions(self):
        self.session.context_state = {
            "recommendation_conditions": {
                "styles": ["미니멀"],
                "colors": ["그레이"],
                "fits": ["오버핏"],
            }
        }
        self.session.save(update_fields=["context_state", "updated_at"])
        analysis = TurnAnalysis(
            action="RECOMMEND",
            target_mode="CURRENT",
            search_query="비슷한 룩",
            conditions=RecommendationConditions(
                occasion="",
                occasion_kind="UNKNOWN",
                season="",
                presentation_groups=[],
                styles=[],
                colors=[],
                fits=[],
                avoided_styles=[],
                avoided_colors=[],
                excluded_source_ids=[],
                budget=None,
            ),
            clarification_question="",
            response_text="",
        )

        effective = ChatOrchestrator._effective_analysis(self.session, analysis)

        self.assertEqual(effective.conditions.styles, ["미니멀"])
        self.assertEqual(effective.conditions.colors, ["그레이"])
        self.assertEqual(effective.conditions.fits, ["오버핏"])
