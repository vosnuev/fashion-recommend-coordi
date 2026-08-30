"""채팅 세션 생성·파생과 메시지 순서·멱등 저장."""

from __future__ import annotations

from copy import deepcopy

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from apps.chat.models import ChatIdentity, ChatMessage, ChatSession
from apps.chat.services.identity import touch_identity


class ChatSessionError(RuntimeError):
    code = "CHAT_SESSION_INVALID"


class ChatSessionForbidden(ChatSessionError):
    code = "CHAT_SESSION_FORBIDDEN"


class ChatModeMismatch(ChatSessionError):
    code = "CHAT_MODE_MISMATCH"


DEFAULT_SESSION_TITLE = "새 대화"
AUTO_TITLE_MAX_CHARS = 20

_INITIAL_GREETINGS = {
    ChatSession.Mode.NEW_ITEM: (
        "추구미를 반영해 새 룩을 골라드릴게요.\n"
        "어떤 자리에 입을 옷이 필요하세요?"
    ),
    ChatSession.Mode.WARDROBE_BASED: (
        "옷장에 있는 옷으로 코디를 짜드릴게요.\n"
        "어떤 자리에 입을 옷이 필요하세요?"
    ),
}


def _normalized_title(title: str) -> str:
    return title.strip() or DEFAULT_SESSION_TITLE


def _create_initial_greeting(session: ChatSession) -> ChatMessage:
    """새 세션의 첫 메시지를 모드별 고정 인사로 한 번만 저장한다."""
    message = ChatMessage(
        session=session,
        sequence=1,
        role=ChatMessage.Role.ASSISTANT,
        content=_INITIAL_GREETINGS[session.mode],
        status=ChatMessage.Status.COMPLETED,
        metadata={"message_kind": "greeting", "mode": session.mode},
    )
    message.full_clean()
    message.save()
    session.last_message_at = message.created_at
    session.save(update_fields=["last_message_at", "updated_at"])
    return message


def _title_from_question(content: str) -> str:
    normalized = " ".join(content.split())
    if len(normalized) <= AUTO_TITLE_MAX_CHARS:
        return normalized
    return f"{normalized[:AUTO_TITLE_MAX_CHARS]}…"


@transaction.atomic
def create_session(
    *,
    identity: ChatIdentity,
    mode: str,
    title: str = "",
    persona_profile_id=None,
) -> ChatSession:
    session = ChatSession(
        identity=identity,
        mode=mode,
        title=_normalized_title(title),
        persona_profile_id=persona_profile_id,
    )
    session.full_clean()
    session.save()
    _create_initial_greeting(session)
    touch_identity(identity)
    return session


@transaction.atomic
def derive_session(
    *,
    identity: ChatIdentity,
    source_session_id,
    mode: str,
    title: str = "",
) -> ChatSession:
    source = (
        ChatSession.objects.select_for_update()
        .filter(pk=source_session_id, identity=identity, deleted_at__isnull=True)
        .first()
    )
    if source is None:
        raise ChatSessionForbidden("원본 채팅 세션에 접근할 수 없습니다.")
    if source.mode == mode:
        raise ChatModeMismatch("같은 모드로 파생 세션을 만들 수 없습니다.")

    derived = ChatSession(
        identity=identity,
        mode=mode,
        response_mode=source.response_mode,
        selected_persona_ids=deepcopy(source.selected_persona_ids),
        persona_selection_updated_at=source.persona_selection_updated_at,
        title=_normalized_title(title),
        persona_profile_id=source.persona_profile_id,
        parent_session=source,
        context_state=deepcopy(source.context_state),
        conversation_summary=source.conversation_summary,
        summary_through_sequence=source.summary_through_sequence,
    )
    derived.full_clean()
    derived.save()
    _create_initial_greeting(derived)
    touch_identity(identity)
    return derived


@transaction.atomic
def append_message(
    *,
    identity: ChatIdentity,
    session_id,
    role: str,
    content: str = "",
    status: str = ChatMessage.Status.COMPLETED,
    client_message_id: str | None = None,
    metadata: dict | None = None,
) -> tuple[ChatMessage, bool]:
    """세션 잠금으로 sequence를 배정하고 client_message_id 재전송을 멱등 처리한다."""
    session = (
        ChatSession.objects.select_for_update()
        .filter(pk=session_id, identity=identity, deleted_at__isnull=True)
        .first()
    )
    if session is None:
        raise ChatSessionForbidden("채팅 세션에 접근할 수 없습니다.")

    normalized_client_id = (client_message_id or "").strip() or None
    if normalized_client_id is not None:
        existing = ChatMessage.objects.filter(
            session=session,
            client_message_id=normalized_client_id,
        ).first()
        if existing is not None:
            return existing, False

    last_sequence = (
        ChatMessage.objects.filter(session=session).aggregate(value=Max("sequence"))[
            "value"
        ]
        or 0
    )
    should_set_auto_title = (
        role == ChatMessage.Role.USER
        and bool(content.strip())
        and session.title in {"", DEFAULT_SESSION_TITLE}
        and not ChatMessage.objects.filter(
            session=session,
            role=ChatMessage.Role.USER,
        )
        .exclude(content="")
        .exists()
    )
    message = ChatMessage(
        session=session,
        sequence=last_sequence + 1,
        role=role,
        content=content,
        status=status,
        client_message_id=normalized_client_id,
        metadata=metadata or {},
    )
    try:
        message.full_clean()
    except ValidationError as exc:
        raise ChatSessionError("채팅 메시지 값이 올바르지 않습니다.") from exc
    message.save()

    session.last_message_at = message.created_at
    update_fields = ["last_message_at", "updated_at"]
    if should_set_auto_title:
        session.title = _title_from_question(content)
        update_fields.append("title")
    session.save(update_fields=update_fields)
    touch_identity(identity)
    return message, True
