"""Redis Stream 기반 채팅 SSE 이벤트 저장·재생."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import redis
from django.conf import settings

TERMINAL_EVENT_TYPES = {"completed", "needs_clarification", "failed"}


@dataclass(frozen=True)
class ChatEvent:
    id: str
    event: str
    data: dict[str, Any]

    @property
    def terminal(self) -> bool:
        return self.event in TERMINAL_EVENT_TYPES


@lru_cache(maxsize=1)
def get_client() -> redis.Redis:
    """XREAD 블로킹 시간보다 긴 socket timeout으로 전용 연결 풀을 만든다."""
    kwargs: dict = {
        "decode_responses": True,
        "socket_connect_timeout": settings.CHAT_QUEUE_CONNECT_TIMEOUT_SECONDS,
        "socket_timeout": (settings.CHAT_SSE_BLOCK_MILLISECONDS / 1000) + 10,
    }
    if settings.REDIS_PASSWORD:
        kwargs["password"] = settings.REDIS_PASSWORD
    return redis.Redis.from_url(settings.REDIS_URL, **kwargs)


class ChatEventStore:
    def __init__(self, *, client: redis.Redis | None = None) -> None:
        self.client = client or get_client()

    @staticmethod
    def key(run_id) -> str:
        return f"{settings.CHAT_EVENT_STREAM_PREFIX}:{run_id}"

    def publish(self, run_id, event: str, data: dict[str, Any]) -> str:
        """이벤트를 제한 길이 Stream에 추가하고 재연결 보존 TTL을 갱신한다."""
        key = self.key(run_id)
        event_id = self.client.xadd(
            key,
            {
                "event": event,
                "data": json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            },
            maxlen=settings.CHAT_EVENT_STREAM_MAX_LENGTH,
            approximate=True,
        )
        self.client.expire(key, settings.CHAT_EVENT_STREAM_TTL_SECONDS)
        return str(event_id)

    def read(
        self,
        run_id,
        *,
        last_event_id: str = "0-0",
        block_milliseconds: int | None = None,
    ) -> list[ChatEvent]:
        if block_milliseconds is None:
            block: int | None = settings.CHAT_SSE_BLOCK_MILLISECONDS
        elif block_milliseconds <= 0:
            # Redis의 BLOCK 0은 non-blocking이 아니라 무기한 대기다.
            block = None
        else:
            block = block_milliseconds
        kwargs: dict[str, Any] = {"count": settings.CHAT_SSE_READ_COUNT}
        if block is not None:
            kwargs["block"] = block
        rows = self.client.xread({self.key(run_id): last_event_id}, **kwargs)
        events: list[ChatEvent] = []
        for _stream, messages in rows:
            for event_id, fields in messages:
                try:
                    data = json.loads(fields.get("data") or "{}")
                except (TypeError, ValueError):
                    data = {}
                if not isinstance(data, dict):
                    data = {"value": data}
                events.append(
                    ChatEvent(
                        id=str(event_id),
                        event=str(fields.get("event") or "message"),
                        data=data,
                    )
                )
        return events


def encode_sse(event: ChatEvent) -> str:
    """한 이벤트를 줄바꿈 안전한 SSE frame으로 직렬화한다."""
    payload = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
    id_line = f"id: {event.id}\n" if event.id else ""
    return f"{id_line}event: {event.event}\ndata: {payload}\n\n"


def heartbeat() -> str:
    return ": keep-alive\n\n"
