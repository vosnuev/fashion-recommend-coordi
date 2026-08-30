"""채팅 사진 업로드와 메시지·첨부 메타데이터의 원자적 연결."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from io import BytesIO

from django.db import transaction

from apps.chat.models import ChatAttachment, ChatIdentity, ChatMessage, ChatSession
from apps.chat.services import attachment_storage, sessions

logger = logging.getLogger(__name__)


class ChatAttachmentError(RuntimeError):
    code = "CHAT_ATTACHMENT_INVALID"


class ChatAttachmentSessionNotFound(ChatAttachmentError):
    code = "CHAT_SESSION_NOT_FOUND"


class ChatAttachmentClientIdConflict(ChatAttachmentError):
    code = "CHAT_CLIENT_MESSAGE_ID_CONFLICT"


class ChatAttachmentStorageUnavailable(ChatAttachmentError):
    code = "CHAT_ATTACHMENT_STORAGE_UNAVAILABLE"


@dataclass(frozen=True)
class ChatAttachmentUploadResult:
    message: ChatMessage
    attachment: ChatAttachment
    created: bool


def _existing_upload(
    *,
    identity: ChatIdentity,
    session_id,
    client_message_id: str,
) -> ChatAttachmentUploadResult | None:
    message = (
        ChatMessage.objects.filter(
            session_id=session_id,
            session__identity=identity,
            session__deleted_at__isnull=True,
            client_message_id=client_message_id,
        )
        .prefetch_related("attachments")
        .first()
    )
    if message is None:
        return None
    attachment = message.attachments.first()
    if attachment is None:
        raise ChatAttachmentClientIdConflict(
            "이미 다른 메시지에서 사용한 client_message_id입니다."
        )
    return ChatAttachmentUploadResult(
        message=message,
        attachment=attachment,
        created=False,
    )


def _delete_unreferenced_object(key: str) -> None:
    try:
        attachment_storage.delete_object(key)
    except Exception:
        logger.warning("미참조 채팅 첨부 S3 객체 정리 실패: key=%s", key, exc_info=True)


def upload_photo(
    *,
    identity: ChatIdentity,
    session_id,
    image,
    client_message_id: str,
    content: str = "",
    metadata: dict | None = None,
) -> ChatAttachmentUploadResult:
    """사진을 S3에 저장하고 첨부 전용 사용자 메시지에 연결한다.

    이 단계에서는 무드 분석을 요청하지 않는다. 후속 분석 기능이 같은 첨부 행의
    ``analysis_status``를 전이할 수 있도록 초기값 ``NOT_REQUESTED``로 남긴다.
    """

    session_exists = ChatSession.objects.filter(
        pk=session_id,
        identity=identity,
        deleted_at__isnull=True,
    ).exists()
    if not session_exists:
        raise ChatAttachmentSessionNotFound("채팅 세션을 찾을 수 없습니다.")

    existing = _existing_upload(
        identity=identity,
        session_id=session_id,
        client_message_id=client_message_id,
    )
    if existing is not None:
        return existing

    if not attachment_storage.is_configured():
        raise ChatAttachmentStorageUnavailable(
            "채팅 이미지 저장소가 설정되지 않았습니다."
        )

    image.seek(0)
    image_bytes = image.read()
    mime_type = image.content_type
    attachment_id = uuid.uuid4()
    key = attachment_storage.attachment_key(identity.id, attachment_id, mime_type)

    try:
        attachment_storage.upload_fileobj(BytesIO(image_bytes), key, mime_type)
    except Exception as exc:
        logger.exception(
            "채팅 첨부 S3 업로드 실패: identity=%s session=%s",
            identity.pk,
            session_id,
        )
        raise ChatAttachmentStorageUnavailable(
            "사진 저장에 실패했습니다. 잠시 후 다시 시도해주세요."
        ) from exc

    try:
        with transaction.atomic():
            message, message_created = sessions.append_message(
                identity=identity,
                session_id=session_id,
                role=ChatMessage.Role.USER,
                content=content,
                status=ChatMessage.Status.COMPLETED,
                client_message_id=client_message_id,
                metadata={**(metadata or {}), "message_kind": "image"},
            )
            if not message_created:
                attachment = message.attachments.first()
                if attachment is None:
                    raise ChatAttachmentClientIdConflict(
                        "이미 다른 메시지에서 사용한 client_message_id입니다."
                    )
                result = ChatAttachmentUploadResult(
                    message=message,
                    attachment=attachment,
                    created=False,
                )
            else:
                attachment = ChatAttachment.objects.create(
                    id=attachment_id,
                    message=message,
                    s3_key=key,
                    mime_type=mime_type,
                    size=len(image_bytes),
                    sha256=hashlib.sha256(image_bytes).hexdigest(),
                    analysis_status=ChatAttachment.AnalysisStatus.NOT_REQUESTED,
                )
                result = ChatAttachmentUploadResult(
                    message=message,
                    attachment=attachment,
                    created=True,
                )
    except Exception:
        _delete_unreferenced_object(key)
        raise

    if not result.created:
        _delete_unreferenced_object(key)
    return result
