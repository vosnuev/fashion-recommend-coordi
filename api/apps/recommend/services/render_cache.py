"""코디 렌더 결과 메타데이터용 장애 허용 Redis 캐시."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from functools import lru_cache

import redis
from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RenderCacheEntry:
    render_fingerprint: str
    output_s3_bucket: str
    output_s3_key: str
    output_media_type: str
    output_bytes: int
    provider: str
    model: str
    prompt_version: str
    reference_count: int
    usage: dict


@lru_cache(maxsize=1)
def get_client() -> redis.Redis:
    kwargs: dict = {
        "decode_responses": True,
        "socket_connect_timeout": settings.OUTFIT_RENDER_QUEUE_CONNECT_TIMEOUT_SECONDS,
        "socket_timeout": settings.OUTFIT_RENDER_QUEUE_CONNECT_TIMEOUT_SECONDS,
    }
    if settings.REDIS_PASSWORD:
        kwargs["password"] = settings.REDIS_PASSWORD
    return redis.Redis.from_url(settings.REDIS_URL, **kwargs)


class RenderResultCache:
    def __init__(self, *, client: redis.Redis | None = None) -> None:
        self.client = client or get_client()

    @staticmethod
    def key(render_fingerprint: str) -> str:
        return f"{settings.OUTFIT_RENDER_CACHE_PREFIX}:{render_fingerprint}"

    def get(self, render_fingerprint: str) -> RenderCacheEntry | None:
        try:
            raw = self.client.get(self.key(render_fingerprint))
        except redis.RedisError:
            logger.warning("코디 이미지 캐시 조회 실패", exc_info=True)
            return None
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            entry = RenderCacheEntry(**payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            logger.warning("올바르지 않은 코디 이미지 캐시를 무시합니다")
            return None
        if entry.render_fingerprint != render_fingerprint:
            return None
        return entry

    def set(self, entry: RenderCacheEntry) -> None:
        try:
            self.client.setex(
                self.key(entry.render_fingerprint),
                settings.OUTFIT_RENDER_CACHE_TTL_SECONDS,
                json.dumps(
                    asdict(entry), ensure_ascii=False, separators=(",", ":")
                ),
            )
        except redis.RedisError:
            logger.warning("코디 이미지 캐시 저장 실패", exc_info=True)
