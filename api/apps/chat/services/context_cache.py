"""채팅 컨텍스트 JSON을 보관하는 장애 허용 Redis 캐시."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, Protocol

import redis
from django.conf import settings

logger = logging.getLogger(__name__)


class JsonCache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...

    def set(self, key: str, value: dict[str, Any], ttl_seconds: int) -> bool: ...


@lru_cache(maxsize=1)
def get_redis_client() -> redis.Redis:
    kwargs: dict[str, Any] = {
        "decode_responses": True,
        "socket_connect_timeout": settings.CHAT_CONTEXT_CACHE_CONNECT_TIMEOUT_SECONDS,
        "socket_timeout": settings.CHAT_CONTEXT_CACHE_TIMEOUT_SECONDS,
    }
    if settings.REDIS_PASSWORD:
        kwargs["password"] = settings.REDIS_PASSWORD
    return redis.Redis.from_url(settings.REDIS_URL, **kwargs)


class RedisJsonCache:
    """Redis 장애가 채팅 전체 실패로 번지지 않는 cache-aside 어댑터."""

    def __init__(self, *, client: redis.Redis | None = None) -> None:
        self.client = client or get_redis_client()

    def get(self, key: str) -> dict[str, Any] | None:
        try:
            raw = self.client.get(key)
        except redis.RedisError as exc:
            logger.warning("채팅 컨텍스트 캐시 조회 실패: %s", type(exc).__name__)
            return None
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            logger.warning("채팅 컨텍스트 캐시 값이 올바른 JSON이 아닙니다.")
            return None
        return value if isinstance(value, dict) else None

    def set(self, key: str, value: dict[str, Any], ttl_seconds: int) -> bool:
        try:
            self.client.setex(
                key,
                ttl_seconds,
                json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            )
        except (TypeError, ValueError):
            logger.exception("직렬화할 수 없는 채팅 컨텍스트입니다.")
            return False
        except redis.RedisError as exc:
            logger.warning("채팅 컨텍스트 캐시 저장 실패: %s", type(exc).__name__)
            return False
        return True
