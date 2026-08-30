"""코디 이미지 생성 진행 상태를 재생하는 Redis Stream."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import redis
from django.conf import settings

TERMINAL_EVENT_TYPES = {"completed", "failed"}


@dataclass(frozen=True)
class RenderEvent:
    id: str
    event: str
    data: dict[str, Any]

    @property
    def terminal(self) -> bool:
        return self.event in TERMINAL_EVENT_TYPES


@lru_cache(maxsize=1)
def get_client() -> redis.Redis:
    kwargs: dict = {
        "decode_responses": True,
        "socket_connect_timeout": settings.OUTFIT_RENDER_QUEUE_CONNECT_TIMEOUT_SECONDS,
        "socket_timeout": (settings.OUTFIT_RENDER_SSE_BLOCK_MILLISECONDS / 1000) + 10,
    }
    if settings.REDIS_PASSWORD:
        kwargs["password"] = settings.REDIS_PASSWORD
    return redis.Redis.from_url(settings.REDIS_URL, **kwargs)


class RenderEventStore:
    def __init__(self, *, client: redis.Redis | None = None) -> None:
        self.client = client or get_client()

    @staticmethod
    def key(job_id) -> str:
        return f"{settings.OUTFIT_RENDER_EVENT_STREAM_PREFIX}:{job_id}"

    def publish(self, job_id, event: str, data: dict[str, Any]) -> str:
        key = self.key(job_id)
        event_id = self.client.xadd(
            key,
            {
                "event": event,
                "data": json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            },
            maxlen=settings.OUTFIT_RENDER_EVENT_STREAM_MAX_LENGTH,
            approximate=True,
        )
        self.client.expire(key, settings.OUTFIT_RENDER_EVENT_STREAM_TTL_SECONDS)
        return str(event_id)

    def read(
        self,
        job_id,
        *,
        last_event_id: str = "0-0",
        block_milliseconds: int | None = None,
    ) -> list[RenderEvent]:
        if block_milliseconds is None:
            block: int | None = settings.OUTFIT_RENDER_SSE_BLOCK_MILLISECONDS
        elif block_milliseconds <= 0:
            block = None
        else:
            block = block_milliseconds
        kwargs: dict[str, Any] = {"count": settings.OUTFIT_RENDER_SSE_READ_COUNT}
        if block is not None:
            kwargs["block"] = block
        rows = self.client.xread({self.key(job_id): last_event_id}, **kwargs)
        events: list[RenderEvent] = []
        for _stream, messages in rows:
            for event_id, fields in messages:
                try:
                    data = json.loads(fields.get("data") or "{}")
                except (TypeError, ValueError):
                    data = {}
                if not isinstance(data, dict):
                    data = {"value": data}
                events.append(
                    RenderEvent(
                        id=str(event_id),
                        event=str(fields.get("event") or "message"),
                        data=data,
                    )
                )
        return events


def encode_sse(event: RenderEvent) -> str:
    payload = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
    id_line = f"id: {event.id}\n" if event.id else ""
    return f"{id_line}event: {event.event}\ndata: {payload}\n\n"


def heartbeat() -> str:
    return ": keep-alive\n\n"
