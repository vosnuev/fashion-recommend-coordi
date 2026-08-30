"""Redis reliable queue (설계서 4장 수용).

pending → (BLMOVE) → processing → 성공 시 제거 / 실패 시 재시도 → dead

- 작업을 가져갈 때 pending에서 processing으로 원자적으로 이동시켜,
  Worker가 처리 중 죽어도 작업이 유실되지 않는다.
- S3 저장과 콜백까지 성공한 후에만 processing에서 제거한다(ack).
- 재시도 횟수는 job_id 기준 해시로 추적하고, 초과 시 dead로 이동한다.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache

import redis

import config

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _redis() -> redis.Redis:
    # REDIS_PASSWORD가 있을 때만 password 인자를 넘긴다.
    # (from_url은 URL에 내장된 비밀번호를 kwargs보다 우선하므로 둘 다 있으면 URL이 이긴다)
    #
    # socket_timeout: redis-py 8.0부터 기본값이 None → 5초로 바뀌어, BLMOVE가
    # QUEUE_BLOCK_SEC(기본 5초) 동안 블로킹하면 응답 전에 소켓 read가 먼저
    # TimeoutError로 끊긴다. 블로킹 대기시간보다 넉넉히 크게 명시해 이를 막는다.
    kwargs: dict = {
        "decode_responses": True,
        "socket_timeout": config.QUEUE_BLOCK_SEC + 10,
    }
    if config.REDIS_PASSWORD:
        kwargs["password"] = config.REDIS_PASSWORD
    return redis.Redis.from_url(config.REDIS_URL, **kwargs)


def fetch(timeout: int = config.QUEUE_BLOCK_SEC) -> str | None:
    """pending 오른쪽 끝(가장 오래된 작업)을 processing으로 이동시키며 가져온다."""
    return _redis().blmove(
        config.PENDING_KEY, config.PROCESSING_KEY, timeout, src="RIGHT", dest="LEFT"
    )


def ack(raw: str, job_id: str) -> None:
    """처리 완료 — processing에서 제거하고 재시도 카운터를 정리한다."""
    r = _redis()
    r.lrem(config.PROCESSING_KEY, 1, raw)
    r.hdel(config.RETRY_HASH, job_id)


def retry_or_dead(raw: str, job_id: str, error: str) -> bool:
    """실패 처리. 재시도 한도 내면 pending으로 되돌리고 False,
    초과면 dead로 이동하고 True를 반환한다."""
    r = _redis()
    retries = r.hincrby(config.RETRY_HASH, job_id, 1)
    r.lrem(config.PROCESSING_KEY, 1, raw)
    if retries >= config.MAX_RETRIES:
        r.lpush(config.DEAD_KEY, json.dumps(
            {"payload": raw, "error": error[:2000], "retries": retries},
            ensure_ascii=False,
        ))
        r.hdel(config.RETRY_HASH, job_id)
        logger.error("job %s → dead queue (retries=%s): %s", job_id, retries, error)
        return True
    # LPUSH(왼쪽) = 큐의 맨 뒤 → 다른 대기 작업 먼저 처리 후 자연스러운 재시도
    r.lpush(config.PENDING_KEY, raw)
    logger.warning("job %s 재시도 예약 (%s/%s): %s", job_id, retries, config.MAX_RETRIES, error)
    return False


def recover_stale() -> int:
    """Worker 재시작 시 processing에 남은 작업을 pending으로 복구한다.

    단일 워커 전제. 워커를 여러 대로 늘리면 이 방식은 다른 워커의
    진행 중 작업까지 되돌리므로, 그때는 워커별 processing 키로 분리할 것.
    """
    r = _redis()
    moved = 0
    while r.lmove(config.PROCESSING_KEY, config.PENDING_KEY, src="RIGHT", dest="RIGHT"):
        moved += 1
    if moved:
        logger.info("재시작 복구: processing → pending %d건", moved)
    return moved
