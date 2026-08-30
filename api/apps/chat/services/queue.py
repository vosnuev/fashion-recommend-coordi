"""채팅 오케스트레이터 실행을 위한 Redis reliable queue."""

from __future__ import annotations

import json
import logging
from functools import lru_cache

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

PERSONA_RETRY_TASK = "PERSONA_RETRY"
PERSONA_ALTERNATIVE_TASK = "PERSONA_ALTERNATIVE"


@lru_cache(maxsize=1)
def get_client() -> redis.Redis:
    """API와 채팅 워커가 프로세스별 연결 풀을 재사용한다."""
    kwargs: dict = {
        "decode_responses": True,
        "socket_connect_timeout": settings.CHAT_QUEUE_CONNECT_TIMEOUT_SECONDS,
        "socket_timeout": settings.CHAT_QUEUE_BLOCK_SECONDS + 10,
    }
    if settings.REDIS_PASSWORD:
        kwargs["password"] = settings.REDIS_PASSWORD
    return redis.Redis.from_url(settings.REDIS_URL, **kwargs)


def enqueue(run) -> None:
    """큐에는 ChatRun 참조만 넣고 대화·추천 본문은 PostgreSQL에 둔다."""
    payload = json.dumps({"run_id": str(run.pk)}, separators=(",", ":"))
    get_client().lpush(settings.CHAT_QUEUE_PENDING_KEY, payload)


def enqueue_persona_retry(*, run_id, persona_id: str, retry_count: int) -> None:
    """실패 카드 한 장의 재실행임을 명시해 일반 run 작업과 구분한다."""

    payload = json.dumps(
        {
            "task": PERSONA_RETRY_TASK,
            "run_id": str(run_id),
            "persona_id": persona_id,
            "retry_count": retry_count,
        },
        separators=(",", ":"),
    )
    get_client().lpush(settings.CHAT_QUEUE_PENDING_KEY, payload)


def enqueue_persona_alternative(
    *,
    run_id,
    persona_id: str,
    source_result_id: str,
    generation: int,
) -> None:
    payload = json.dumps(
        {
            "task": PERSONA_ALTERNATIVE_TASK,
            "run_id": str(run_id),
            "persona_id": persona_id,
            "source_result_id": source_result_id,
            "generation": generation,
        },
        separators=(",", ":"),
    )
    get_client().lpush(settings.CHAT_QUEUE_PENDING_KEY, payload)


def delivery_key(payload: dict[str, object]) -> str:
    """같은 run의 일반 실행과 개별 재실행 재배달 횟수를 분리한다."""

    run_id = str(payload.get("run_id", "?"))
    task = payload.get("task")
    if task not in {PERSONA_RETRY_TASK, PERSONA_ALTERNATIVE_TASK}:
        return run_id
    sequence = (
        payload.get("retry_count")
        if task == PERSONA_RETRY_TASK
        else payload.get("generation")
    )
    return ":".join(
        (
            run_id,
            str(payload.get("persona_id", "?")),
            str(task),
            str(sequence or "?"),
        )
    )


def fetch(timeout: int | None = None) -> str | None:
    """가장 오래된 pending 작업을 processing으로 원자 이동하며 가져온다."""
    block_seconds = (
        settings.CHAT_QUEUE_BLOCK_SECONDS if timeout is None else max(timeout, 0)
    )
    return get_client().blmove(
        settings.CHAT_QUEUE_PENDING_KEY,
        settings.CHAT_QUEUE_PROCESSING_KEY,
        block_seconds,
        src="RIGHT",
        dest="LEFT",
    )


def ack(raw: str, run_id: str) -> None:
    """처리된 배달을 제거하고 실행별 재시도 횟수를 정리한다."""
    client = get_client()
    client.lrem(settings.CHAT_QUEUE_PROCESSING_KEY, 1, raw)
    client.hdel(settings.CHAT_QUEUE_RETRY_KEY, run_id)


def retry_or_dead(raw: str, run_id: str, error_code: str) -> bool:
    """실패 작업을 재적재하고 한도 초과 시 dead queue로 격리한다."""
    client = get_client()
    retries = client.hincrby(settings.CHAT_QUEUE_RETRY_KEY, run_id, 1)
    client.lrem(settings.CHAT_QUEUE_PROCESSING_KEY, 1, raw)
    if retries >= settings.CHAT_QUEUE_MAX_RETRIES:
        dead_payload = {
            "payload": raw,
            "error_code": error_code[:64],
            "retries": retries,
        }
        client.lpush(
            settings.CHAT_QUEUE_DEAD_KEY,
            json.dumps(dead_payload, ensure_ascii=False, separators=(",", ":")),
        )
        client.hdel(settings.CHAT_QUEUE_RETRY_KEY, run_id)
        logger.error(
            "채팅 실행 %s dead queue 이동 (retries=%s, code=%s)",
            run_id,
            retries,
            error_code,
        )
        return True

    client.lpush(settings.CHAT_QUEUE_PENDING_KEY, raw)
    logger.warning(
        "채팅 실행 %s 재시도 예약 (%s/%s, code=%s)",
        run_id,
        retries,
        settings.CHAT_QUEUE_MAX_RETRIES,
        error_code,
    )
    return False


def dead_letter(raw: str, run_id: str, error_code: str) -> None:
    """재시도로 회복되지 않는 요청을 즉시 dead queue로 격리한다."""
    client = get_client()
    client.lrem(settings.CHAT_QUEUE_PROCESSING_KEY, 1, raw)
    client.lpush(
        settings.CHAT_QUEUE_DEAD_KEY,
        json.dumps(
            {
                "payload": raw,
                "error_code": error_code[:64],
                "retries": 0,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    client.hdel(settings.CHAT_QUEUE_RETRY_KEY, run_id)
    logger.error("채팅 실행 %s 즉시 dead queue 이동 (code=%s)", run_id, error_code)


def recover_processing() -> list[str]:
    """단일 워커 재시작 시 처리 중이던 배달을 pending으로 되돌린다."""
    client = get_client()
    recovered: list[str] = []
    while True:
        raw = client.lmove(
            settings.CHAT_QUEUE_PROCESSING_KEY,
            settings.CHAT_QUEUE_PENDING_KEY,
            src="RIGHT",
            dest="RIGHT",
        )
        if raw is None:
            break
        recovered.append(raw)
    if recovered:
        logger.info("채팅 워커 재시작 복구: processing → pending %d건", len(recovered))
    return recovered
