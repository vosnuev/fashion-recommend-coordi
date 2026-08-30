"""코디 평가 작업 큐 (Redis reliable queue).

`image-processor/services/queue.py`와 같은 패턴이다:

    pending → (BLMOVE) → processing → 성공 시 제거(ack) / 실패 시 재시도 → dead

단순 리스트(LPUSH/RPOP)를 쓰지 않는 이유: 작업이 유실되면 사용자는 **영원히
"분석 중"에 갇힌다.** BLMOVE로 pending에서 processing으로 원자적으로 옮겨두면
워커가 처리 도중 죽어도 재시작 시 복구할 수 있다.

페이로드에 이미지를 넣지 않는다 — 사진은 S3에 있고 큐에는 참조만 넣는다
(Redis에 수 MB를 밀어넣으면 메모리도 복제도 감당이 안 된다).
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache

import redis
from dataclasses import dataclass

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
# requirepass 비밀번호 (Infisical: REDIS_PASSWORD). URL에 내장하지 않고 별도 주입한다.
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

PENDING_KEY = os.getenv("OUTFIT_QUEUE_PENDING", "outfit:analysis:pending")
PROCESSING_KEY = os.getenv("OUTFIT_QUEUE_PROCESSING", "outfit:analysis:processing")
DEAD_KEY = os.getenv("OUTFIT_QUEUE_DEAD", "outfit:analysis:dead")
RETRY_HASH = os.getenv("OUTFIT_QUEUE_RETRY", "outfit:analysis:retry")


@dataclass(frozen=True)
class QueueSpec:
    """큐 하나가 쓰는 Redis 키 묶음.

    같은 신뢰성 패턴을 쓰는 큐가 둘 이상이 되면서 키를 파라미터로 뺐다. 모듈
    수준 함수들은 기본 스펙(코디 평가)을 그대로 쓰므로 기존 호출부는 바뀌지 않는다.
    """

    pending: str
    processing: str
    dead: str
    retry: str


#: 코디 평가 (기존). 모듈 함수의 기본값이다.
OUTFIT_ANALYSIS = QueueSpec(
    pending=PENDING_KEY,
    processing=PROCESSING_KEY,
    dead=DEAD_KEY,
    retry=RETRY_HASH,
)

#: 오늘의 룩 생성. 평가와 큐를 나눈 이유는 소비 속도와 실패 성격이 다르기
#: 때문이다 — 평가는 사용자가 화면에서 기다리고, 오늘의 룩은 로그인 직후
#: 백그라운드로 돈다. 한 큐에 섞으면 룩 생성이 밀릴 때 평가까지 같이 밀린다.
DAILY_LOOK = QueueSpec(
    pending=os.getenv("DAILY_LOOK_QUEUE_PENDING", "daily:look:pending"),
    processing=os.getenv("DAILY_LOOK_QUEUE_PROCESSING", "daily:look:processing"),
    dead=os.getenv("DAILY_LOOK_QUEUE_DEAD", "daily:look:dead"),
    retry=os.getenv("DAILY_LOOK_QUEUE_RETRY", "daily:look:retry"),
)

BLOCK_SEC = int(os.getenv("OUTFIT_QUEUE_BLOCK_SEC", "5"))
MAX_RETRIES = int(os.getenv("OUTFIT_QUEUE_MAX_RETRIES", "3"))


@lru_cache(maxsize=1)
def get_client() -> redis.Redis:
    """프로세스당 1개 재사용. gunicorn 워커·평가 워커가 각자 생성한다."""
    # socket_timeout: redis-py 8.0부터 기본값이 None → 5초로 바뀌어, BLMOVE가
    # BLOCK_SEC 동안 블로킹하면 응답 전에 소켓 read가 먼저 TimeoutError로 끊긴다.
    # 블로킹 대기시간보다 넉넉히 크게 명시해 이를 막는다.
    kwargs: dict = {"decode_responses": True, "socket_timeout": BLOCK_SEC + 10}
    if REDIS_PASSWORD:
        # from_url은 URL에 내장된 비밀번호를 kwargs보다 우선하므로 둘 다 있으면 URL이 이긴다
        kwargs["password"] = REDIS_PASSWORD
    return redis.Redis.from_url(REDIS_URL, **kwargs)


def enqueue(analysis) -> None:
    """평가 작업을 큐에 적재한다. 실패 시 redis.RedisError를 그대로 올린다."""
    payload = {
        "analysis_id": str(analysis.pk),
        "s3_key": analysis.image_s3_key,
    }
    push(payload, spec=OUTFIT_ANALYSIS)


def push(payload: dict, *, spec: QueueSpec = OUTFIT_ANALYSIS) -> None:
    """임의 페이로드를 큐에 적재한다. 실패 시 redis.RedisError를 그대로 올린다."""
    get_client().lpush(spec.pending, json.dumps(payload, ensure_ascii=False))


def fetch(
    timeout: int = BLOCK_SEC, *, spec: QueueSpec = OUTFIT_ANALYSIS
) -> str | None:
    """pending 오른쪽 끝(가장 오래된 작업)을 processing으로 옮기며 가져온다."""
    return get_client().blmove(
        spec.pending, spec.processing, timeout, src="RIGHT", dest="LEFT"
    )


def ack(raw: str, analysis_id: str, *, spec: QueueSpec = OUTFIT_ANALYSIS) -> None:
    """처리 완료 — processing에서 제거하고 재시도 카운터를 정리한다."""
    client = get_client()
    client.lrem(spec.processing, 1, raw)
    client.hdel(spec.retry, analysis_id)


def retry_or_dead(
    raw: str, analysis_id: str, error: str, *, spec: QueueSpec = OUTFIT_ANALYSIS
) -> bool:
    """실패 처리.

    Returns: dead queue로 보냈으면 True (호출부가 행을 FAILED로 마킹해야 한다),
             재시도 예약이면 False.
    """
    client = get_client()
    retries = client.hincrby(spec.retry, analysis_id, 1)
    client.lrem(spec.processing, 1, raw)
    if retries >= MAX_RETRIES:
        client.lpush(
            spec.dead,
            json.dumps(
                {"payload": raw, "error": error[:2000], "retries": retries},
                ensure_ascii=False,
            ),
        )
        client.hdel(spec.retry, analysis_id)
        logger.error(
            "평가 %s → dead queue (retries=%s): %s", analysis_id, retries, error
        )
        return True
    # LPUSH(왼쪽) = 큐의 맨 뒤 → 다른 대기 작업을 먼저 처리한 뒤 자연스럽게 재시도
    client.lpush(spec.pending, raw)
    logger.warning(
        "평가 %s 재시도 예약 (%s/%s): %s", analysis_id, retries, MAX_RETRIES, error
    )
    return False


def recover_stale(*, spec: QueueSpec = OUTFIT_ANALYSIS) -> int:
    """워커 재시작 시 processing에 남은 작업을 pending으로 되돌린다.

    단일 워커 전제. 워커를 여러 대로 늘리면 다른 워커가 진행 중인 작업까지
    되돌리므로, 그때는 워커별 processing 키로 분리해야 한다.
    """
    client = get_client()
    moved = 0
    while client.lmove(spec.processing, spec.pending, src="RIGHT", dest="RIGHT"):
        moved += 1
    if moved:
        logger.info("재시작 복구: processing → pending %d건", moved)
    return moved
