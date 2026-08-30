"""추천 코디 이미지 생성을 위한 Redis reliable queue."""

from __future__ import annotations

import json
import logging
from functools import lru_cache

import redis
from django.conf import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_client() -> redis.Redis:
    kwargs: dict = {
        "decode_responses": True,
        "socket_connect_timeout": settings.OUTFIT_RENDER_QUEUE_CONNECT_TIMEOUT_SECONDS,
        "socket_timeout": settings.OUTFIT_RENDER_QUEUE_BLOCK_SECONDS + 10,
    }
    if settings.REDIS_PASSWORD:
        kwargs["password"] = settings.REDIS_PASSWORD
    return redis.Redis.from_url(settings.REDIS_URL, **kwargs)


#: 큐 페이로드의 작업 종류. 없으면 코디 이미지 생성이다(하위호환 — 배포 중에
#: 옛 워커가 집어도 예전과 같이 동작한다).
KIND_OUTFIT_RENDER = "outfit_render"
KIND_VIRTUAL_TRY_ON = "virtual_try_on"


def enqueue(job) -> None:
    """이미지나 프롬프트 대신 PostgreSQL 작업 UUID만 적재한다."""
    payload = json.dumps({"job_id": str(job.pk)}, separators=(",", ":"))
    get_client().lpush(settings.OUTFIT_RENDER_QUEUE_PENDING_KEY, payload)


def enqueue_virtual_try_on(job) -> None:
    """가상 피팅 작업을 **같은 큐**에 넣는다.

    큐를 새로 만들지 않는 이유: 둘 다 같은 이미지 모델을 부르는 긴 작업이라
    한 워커가 순서대로 처리하면 되고, 큐를 나누면 워커 컨테이너도 나뉜다.
    종류는 payload 의 kind 로 가른다.
    """
    payload = json.dumps(
        {"job_id": str(job.pk), "kind": KIND_VIRTUAL_TRY_ON}, separators=(",", ":")
    )
    get_client().lpush(settings.OUTFIT_RENDER_QUEUE_PENDING_KEY, payload)


def kind_of(raw: str) -> str:
    """페이로드의 작업 종류. 없으면 코디 이미지 생성(옛 페이로드)."""
    try:
        return str(json.loads(raw).get("kind") or KIND_OUTFIT_RENDER)
    except (ValueError, TypeError):
        return KIND_OUTFIT_RENDER


def fetch(timeout: int | None = None) -> str | None:
    block_seconds = (
        settings.OUTFIT_RENDER_QUEUE_BLOCK_SECONDS
        if timeout is None
        else max(timeout, 0)
    )
    return get_client().blmove(
        settings.OUTFIT_RENDER_QUEUE_PENDING_KEY,
        settings.OUTFIT_RENDER_QUEUE_PROCESSING_KEY,
        block_seconds,
        src="RIGHT",
        dest="LEFT",
    )


def ack(raw: str, job_id: str) -> None:
    client = get_client()
    client.lrem(settings.OUTFIT_RENDER_QUEUE_PROCESSING_KEY, 1, raw)
    client.hdel(settings.OUTFIT_RENDER_QUEUE_RETRY_KEY, job_id)


def retry_or_dead(raw: str, job_id: str, error_code: str) -> bool:
    """실패 배달을 재적재하고 한도에 도달하면 dead queue로 옮긴다."""
    client = get_client()
    retries = client.hincrby(settings.OUTFIT_RENDER_QUEUE_RETRY_KEY, job_id, 1)
    client.lrem(settings.OUTFIT_RENDER_QUEUE_PROCESSING_KEY, 1, raw)
    if retries >= settings.OUTFIT_RENDER_QUEUE_MAX_RETRIES:
        client.lpush(
            settings.OUTFIT_RENDER_QUEUE_DEAD_KEY,
            json.dumps(
                {
                    "payload": raw,
                    "error_code": error_code[:64],
                    "retries": retries,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        client.hdel(settings.OUTFIT_RENDER_QUEUE_RETRY_KEY, job_id)
        logger.error(
            "코디 이미지 작업 %s dead queue 이동 (retries=%s, code=%s)",
            job_id,
            retries,
            error_code,
        )
        return True

    client.lpush(settings.OUTFIT_RENDER_QUEUE_PENDING_KEY, raw)
    logger.warning(
        "코디 이미지 작업 %s 재시도 예약 (%s/%s, code=%s)",
        job_id,
        retries,
        settings.OUTFIT_RENDER_QUEUE_MAX_RETRIES,
        error_code,
    )
    return False


def dead_letter(raw: str, job_id: str, error_code: str) -> None:
    client = get_client()
    client.lrem(settings.OUTFIT_RENDER_QUEUE_PROCESSING_KEY, 1, raw)
    client.lpush(
        settings.OUTFIT_RENDER_QUEUE_DEAD_KEY,
        json.dumps(
            {"payload": raw, "error_code": error_code[:64], "retries": 0},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    client.hdel(settings.OUTFIT_RENDER_QUEUE_RETRY_KEY, job_id)


def recover_processing() -> list[str]:
    """단일 워커 재시작 시 processing 배달을 pending으로 되돌린다."""
    client = get_client()
    recovered: list[str] = []
    while True:
        raw = client.lmove(
            settings.OUTFIT_RENDER_QUEUE_PROCESSING_KEY,
            settings.OUTFIT_RENDER_QUEUE_PENDING_KEY,
            src="RIGHT",
            dest="RIGHT",
        )
        if raw is None:
            break
        recovered.append(raw)
    return recovered
