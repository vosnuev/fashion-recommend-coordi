"""소유권이 적용된 채팅 검색과 안정적인 커서 페이지 조회."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.core import signing
from django.db.models import Exists, OuterRef, Q, Subquery
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.chat.models import ChatIdentity, ChatMessage, ChatSession

_CURSOR_SALT = "apps.chat.history.cursor.v1"
_SEARCH_PREVIEW_MAX_CHARS = 160


class ChatHistoryError(RuntimeError):
    code = "CHAT_HISTORY_INVALID"


class ChatHistoryCursorInvalid(ChatHistoryError):
    code = "CHAT_PAGE_CURSOR_INVALID"


class ChatHistorySessionNotFound(ChatHistoryError):
    code = "CHAT_SESSION_NOT_FOUND"


@dataclass(frozen=True)
class SessionSearchPage:
    items: list[ChatSession]
    total_count: int
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True)
class MessagePage:
    items: list[ChatMessage]
    total_count: int
    next_cursor: str | None
    has_more: bool


def _encode_cursor(payload: dict) -> str:
    return signing.dumps(payload, salt=_CURSOR_SALT, compress=True)


def _decode_cursor(cursor: str, *, kind: str) -> dict:
    try:
        payload = signing.loads(cursor, salt=_CURSOR_SALT)
    except signing.BadSignature as exc:
        raise ChatHistoryCursorInvalid("유효하지 않은 페이지 커서입니다.") from exc
    if not isinstance(payload, dict) or payload.get("kind") != kind:
        raise ChatHistoryCursorInvalid("요청 종류와 페이지 커서가 일치하지 않습니다.")
    return payload


def _normalize_query(query: str) -> str:
    return " ".join(query.split())


def _preview(content: str) -> str:
    normalized = " ".join(content.split())
    if len(normalized) <= _SEARCH_PREVIEW_MAX_CHARS:
        return normalized
    return f"{normalized[:_SEARCH_PREVIEW_MAX_CHARS]}…"


def search_sessions(
    *,
    identity: ChatIdentity,
    query: str,
    limit: int,
    cursor: str = "",
) -> SessionSearchPage:
    """제목 또는 메시지 본문이 부분 일치하는 세션을 최근순으로 찾는다."""
    normalized_query = _normalize_query(query)
    message_matches = ChatMessage.objects.filter(
        session_id=OuterRef("pk"),
        content__icontains=normalized_query,
    ).order_by("sequence")
    queryset = (
        ChatSession.objects.filter(
            identity=identity,
            deleted_at__isnull=True,
        )
        .annotate(
            search_message_exists=Exists(message_matches),
            search_message_id=Subquery(message_matches.values("id")[:1]),
            search_message_sequence=Subquery(message_matches.values("sequence")[:1]),
            search_message_role=Subquery(message_matches.values("role")[:1]),
            search_message_content=Subquery(message_matches.values("content")[:1]),
        )
        .filter(Q(title__icontains=normalized_query) | Q(search_message_exists=True))
    )
    total_count = queryset.count()

    if cursor:
        payload = _decode_cursor(cursor, kind="session_search")
        if payload.get("query") != normalized_query:
            raise ChatHistoryCursorInvalid(
                "검색어가 달라졌습니다. 첫 페이지부터 다시 조회해 주세요."
            )
        cursor_datetime = parse_datetime(str(payload.get("updated_at") or ""))
        try:
            cursor_id = uuid.UUID(str(payload.get("id") or ""))
        except (ValueError, AttributeError) as exc:
            raise ChatHistoryCursorInvalid(
                "세션 검색 페이지 커서가 올바르지 않습니다."
            ) from exc
        if cursor_datetime is None or timezone.is_naive(cursor_datetime):
            raise ChatHistoryCursorInvalid(
                "세션 검색 페이지 커서가 올바르지 않습니다."
            )
        queryset = queryset.filter(
            Q(updated_at__lt=cursor_datetime)
            | Q(updated_at=cursor_datetime, id__lt=cursor_id)
        )

    fetched = list(queryset.order_by("-updated_at", "-id")[: limit + 1])
    has_more = len(fetched) > limit
    items = fetched[:limit]
    for session in items:
        content = getattr(session, "search_message_content", None)
        session.search_match = (
            {
                "message_id": str(session.search_message_id),
                "sequence": session.search_message_sequence,
                "role": session.search_message_role,
                "preview": _preview(content),
            }
            if content is not None
            else None
        )
    next_cursor = None
    if has_more and items:
        boundary = items[-1]
        next_cursor = _encode_cursor(
            {
                "kind": "session_search",
                "query": normalized_query,
                "updated_at": boundary.updated_at.isoformat(),
                "id": str(boundary.pk),
            }
        )
    return SessionSearchPage(
        items=items,
        total_count=total_count,
        next_cursor=next_cursor,
        has_more=has_more,
    )


def page_messages(
    *,
    identity: ChatIdentity,
    session_id,
    limit: int,
    cursor: str = "",
) -> MessagePage:
    """최신 구간부터 읽고 커서로 더 오래된 메시지를 조회한다."""
    session = ChatSession.objects.filter(
        pk=session_id,
        identity=identity,
        deleted_at__isnull=True,
    ).first()
    if session is None:
        raise ChatHistorySessionNotFound("채팅 세션을 찾을 수 없습니다.")

    queryset = (
        ChatMessage.objects.filter(session=session)
        .select_related("run")
        .prefetch_related("attachments")
    )
    total_count = queryset.count()
    if cursor:
        payload = _decode_cursor(cursor, kind="messages")
        if payload.get("session_id") != str(session.pk):
            raise ChatHistoryCursorInvalid(
                "다른 세션의 페이지 커서는 사용할 수 없습니다."
            )
        try:
            before_sequence = int(payload["before_sequence"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ChatHistoryCursorInvalid(
                "메시지 페이지 커서가 올바르지 않습니다."
            ) from exc
        if before_sequence < 1:
            raise ChatHistoryCursorInvalid(
                "메시지 페이지 커서가 올바르지 않습니다."
            )
        queryset = queryset.filter(sequence__lt=before_sequence)

    fetched = list(queryset.order_by("-sequence")[: limit + 1])
    has_more = len(fetched) > limit
    newest_first = fetched[:limit]
    items = list(reversed(newest_first))
    next_cursor = None
    if has_more and items:
        next_cursor = _encode_cursor(
            {
                "kind": "messages",
                "session_id": str(session.pk),
                "before_sequence": items[0].sequence,
            }
        )
    return MessagePage(
        items=items,
        total_count=total_count,
        next_cursor=next_cursor,
        has_more=has_more,
    )
