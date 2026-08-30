from __future__ import annotations

import hashlib
from io import BytesIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import ChatAttachment, ChatIdentity, ChatMessage, ChatSession
from apps.chat.services import sessions as session_service


def make_image_file(
    name: str = "reference.jpg",
    *,
    content_type: str = "image/jpeg",
) -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", (4, 4), color=(32, 64, 96)).save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=content_type)


@override_settings(CHAT_ATTACHMENT_S3_BUCKET="chat-test-bucket")
class ChatAttachmentUploadApiTests(APITestCase):
    def setUp(self) -> None:
        guest_response = self.client.post(
            reverse("chat:guest-identity"),
            {},
            format="json",
        )
        self.assertEqual(guest_response.status_code, status.HTTP_201_CREATED)
        self.identity = ChatIdentity.objects.get(
            pk=guest_response.data["identity_id"]
        )
        self.session = session_service.create_session(
            identity=self.identity,
            mode=ChatSession.Mode.NEW_ITEM,
        )
        self.url = reverse(
            "chat:session-attachment-upload",
            kwargs={"session_id": self.session.pk},
        )

    @patch(
        "apps.chat.services.attachment_storage.presigned_get",
        return_value="https://signed.example/reference.jpg",
    )
    @patch("apps.chat.services.attachment_storage.upload_fileobj")
    def test_upload_creates_private_attachment_message(
        self,
        upload_fileobj,
        _presigned_get,
    ) -> None:
        image = make_image_file()
        expected_bytes = image.read()
        image.seek(0)

        response = self.client.post(
            self.url,
            {
                "image": image,
                "client_message_id": "photo-1",
                "content": "이 사진 같은 분위기로 추천해줘",
                "metadata": '{"source_screen":"chat-room"}',
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["created"])
        self.assertEqual(response.data["message"]["role"], ChatMessage.Role.USER)
        self.assertEqual(
            response.data["message"]["metadata"],
            {"source_screen": "chat-room", "message_kind": "image"},
        )
        self.assertEqual(
            response.data["attachment"]["analysis_status"],
            ChatAttachment.AnalysisStatus.NOT_REQUESTED,
        )
        self.assertEqual(
            response.data["attachment"]["image_url"],
            "https://signed.example/reference.jpg",
        )
        self.assertNotIn("s3_key", response.data["attachment"])
        self.session.refresh_from_db()
        self.assertEqual(self.session.title, "이 사진 같은 분위기로 추천해줘")

        attachment = ChatAttachment.objects.select_related("message").get()
        self.assertEqual(attachment.message.client_message_id, "photo-1")
        self.assertEqual(attachment.size, len(expected_bytes))
        self.assertEqual(attachment.sha256, hashlib.sha256(expected_bytes).hexdigest())
        self.assertTrue(
            attachment.s3_key.startswith(
                f"chat/{self.identity.pk}/attachments/{attachment.pk}"
            )
        )
        uploaded_file, uploaded_key, uploaded_type = upload_fileobj.call_args.args
        self.assertEqual(uploaded_file.read(), expected_bytes)
        self.assertEqual(uploaded_key, attachment.s3_key)
        self.assertEqual(uploaded_type, "image/jpeg")

    @patch(
        "apps.chat.services.attachment_storage.presigned_get",
        return_value="https://signed.example/reference.jpg",
    )
    @patch("apps.chat.services.attachment_storage.upload_fileobj")
    def test_same_client_message_id_returns_existing_upload_without_s3_duplicate(
        self,
        upload_fileobj,
        _presigned_get,
    ) -> None:
        first = self.client.post(
            self.url,
            {"image": make_image_file(), "client_message_id": "photo-retry"},
            format="multipart",
        )
        second = self.client.post(
            self.url,
            {"image": make_image_file(), "client_message_id": "photo-retry"},
            format="multipart",
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertFalse(second.data["created"])
        self.assertEqual(
            first.data["attachment"]["id"], second.data["attachment"]["id"]
        )
        self.assertEqual(ChatMessage.objects.count(), 2)
        self.assertEqual(ChatAttachment.objects.count(), 1)
        upload_fileobj.assert_called_once()

    @patch("apps.chat.services.attachment_storage.upload_fileobj")
    def test_other_identity_session_is_hidden(self, upload_fileobj) -> None:
        other_client = type(self.client)()
        guest_response = other_client.post(
            reverse("chat:guest-identity"),
            {},
            format="json",
        )
        self.assertEqual(guest_response.status_code, status.HTTP_201_CREATED)

        response = other_client.post(
            self.url,
            {"image": make_image_file(), "client_message_id": "photo-other"},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["code"], "CHAT_SESSION_NOT_FOUND")
        self.assertFalse(ChatAttachment.objects.exists())
        upload_fileobj.assert_not_called()

    @patch("apps.chat.services.attachment_storage.upload_fileobj")
    def test_existing_text_message_client_id_returns_conflict(self, upload_fileobj) -> None:
        session_service.append_message(
            identity=self.identity,
            session_id=self.session.pk,
            role=ChatMessage.Role.USER,
            content="기존 텍스트",
            client_message_id="already-text",
        )

        response = self.client.post(
            self.url,
            {"image": make_image_file(), "client_message_id": "already-text"},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "CHAT_CLIENT_MESSAGE_ID_CONFLICT")
        self.assertFalse(ChatAttachment.objects.exists())
        upload_fileobj.assert_not_called()

    @patch(
        "apps.chat.services.attachment_storage.upload_fileobj",
        side_effect=RuntimeError("S3 unavailable"),
    )
    def test_storage_failure_returns_503_without_database_rows(
        self,
        _upload_fileobj,
    ) -> None:
        response = self.client.post(
            self.url,
            {"image": make_image_file(), "client_message_id": "photo-fail"},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(
            response.data["code"], "CHAT_ATTACHMENT_STORAGE_UNAVAILABLE"
        )
        self.assertEqual(ChatMessage.objects.count(), 1)
        self.assertEqual(
            ChatMessage.objects.get().metadata.get("message_kind"),
            "greeting",
        )
        self.assertFalse(ChatAttachment.objects.exists())

    @override_settings(CHAT_ATTACHMENT_MAX_BYTES=8)
    @patch("apps.chat.services.attachment_storage.upload_fileobj")
    def test_oversized_image_is_rejected_before_storage(self, upload_fileobj) -> None:
        response = self.client.post(
            self.url,
            {"image": make_image_file(), "client_message_id": "photo-large"},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("image", response.data)
        self.assertFalse(ChatAttachment.objects.exists())
        upload_fileobj.assert_not_called()

    @patch("apps.chat.services.attachment_storage.upload_fileobj")
    def test_unsupported_image_type_is_rejected_before_storage(
        self,
        upload_fileobj,
    ) -> None:
        gif = BytesIO()
        Image.new("RGB", (4, 4)).save(gif, format="GIF")
        response = self.client.post(
            self.url,
            {
                "image": SimpleUploadedFile(
                    "reference.gif",
                    gif.getvalue(),
                    content_type="image/gif",
                ),
                "client_message_id": "photo-gif",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("image", response.data)
        self.assertFalse(ChatAttachment.objects.exists())
        upload_fileobj.assert_not_called()
