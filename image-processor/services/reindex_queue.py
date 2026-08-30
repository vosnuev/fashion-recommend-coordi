"""옷장 재인덱싱 전용 Redis reliable queue."""

from __future__ import annotations

import json
import logging
from functools import lru_cache

import redis

import config

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _redis() -> redis.Redis:
    kwargs: dict = {
        "decode_responses": True,
        "socket_timeout": config.QUEUE_BLOCK_SEC + 10,
    }
    if config.REDIS_PASSWORD:
        kwargs["password"] = config.REDIS_PASSWORD
    return redis.Redis.from_url(config.REDIS_URL, **kwargs)


def fetch(timeout: int = config.QUEUE_BLOCK_SEC) -> str | None:
    return _redis().blmove(
        config.REINDEX_PENDING_KEY,
        config.REINDEX_PROCESSING_KEY,
        timeout,
        src="RIGHT",
        dest="LEFT",
    )


def ack(raw: str, item_id: str) -> None:
    client = _redis()
    client.lrem(config.REINDEX_PROCESSING_KEY, 1, raw)
    client.hdel(config.REINDEX_RETRY_HASH, item_id)
    client.srem(config.REINDEX_DEDUP_KEY, item_id)


def retry_or_dead(raw: str, item_id: str, error: str) -> bool:
    client = _redis()
    retries = client.hincrby(config.REINDEX_RETRY_HASH, item_id, 1)
    client.lrem(config.REINDEX_PROCESSING_KEY, 1, raw)
    if retries >= config.MAX_RETRIES:
        client.lpush(
            config.REINDEX_DEAD_KEY,
            json.dumps(
                {"payload": raw, "error": error[:2000], "retries": retries},
                ensure_ascii=False,
            ),
        )
        client.hdel(config.REINDEX_RETRY_HASH, item_id)
        client.srem(config.REINDEX_DEDUP_KEY, item_id)
        logger.error(
            "재인덱싱 item %s → dead queue (retries=%s): %s",
            item_id,
            retries,
            error,
        )
        return True
    client.lpush(config.REINDEX_PENDING_KEY, raw)
    logger.warning(
        "재인덱싱 item %s 재시도 예약 (%s/%s): %s",
        item_id,
        retries,
        config.MAX_RETRIES,
        error,
    )
    return False


def recover_stale() -> int:
    client = _redis()
    moved = 0
    while client.lmove(
        config.REINDEX_PROCESSING_KEY,
        config.REINDEX_PENDING_KEY,
        src="RIGHT",
        dest="RIGHT",
    ):
        moved += 1
    if moved:
        logger.info("재인덱싱 재시작 복구: processing → pending %d건", moved)
    return moved
