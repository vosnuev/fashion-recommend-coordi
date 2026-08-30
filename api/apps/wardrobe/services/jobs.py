"""이미지 처리 job 큐잉.

설계 결정: 메인 API → 이미지 프로세서는 직접 HTTP가 아니라 큐를 경유한다.
GPU 서버가 내려가 있어도 job이 유실되지 않고, 서버리스 GPU 전환도 쉬워진다.

큐 구현은 Redis 리스트(LPUSH → 프로세서가 BRPOP)로 시작한다.
이미지 프로세서는 아래 페이로드를 소비한다고 가정한다:
{
  "job_id": "...", "user_id": 1,
  "source": {"bucket": "...", "key": "wardrobe/1/<job>/original.jpg"},
  "output": {"bucket": "...", "prefix": "wardrobe/1/<job>/"},
  "output_prefix": "wardrobe/1/<job>/",
  "exclude_categories": ["상의"],
  "callback_url": "https://api.../api/v1/internal/wardrobe/callback/"
}

원본은 옷장 업로드가 아닌 곳(코디 평가 사진)일 수도 있어 source와 output 버킷을
따로 싣는다. 그래서 job.source_s3_key가 항상 옷장 버킷의 키인 것은 아니다.
"""
from __future__ import annotations

import json
import os
from collections.abc import Sequence
from functools import lru_cache

import redis

from . import storage

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
# requirepass 비밀번호 (Infisical: REDIS_PASSWORD). URL에 내장하지 않고 별도 주입한다.
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
QUEUE_KEY = os.getenv("WARDROBE_JOB_QUEUE", "wardrobe:jobs")
ITEM_QUEUE_KEY = os.getenv("WARDROBE_ITEM_JOB_QUEUE", "wardrobe:item-jobs")
CALLBACK_URL = os.getenv("WARDROBE_CALLBACK_URL", "")


@lru_cache(maxsize=1)
def _redis():
    # REDIS_PASSWORD가 있을 때만 password 인자를 넘긴다.
    # (from_url은 URL에 내장된 비밀번호를 kwargs보다 우선하므로 둘 다 있으면 URL이 이긴다)
    kwargs = {"decode_responses": True}
    if REDIS_PASSWORD:
        kwargs["password"] = REDIS_PASSWORD
    return redis.Redis.from_url(REDIS_URL, **kwargs)


def enqueue(
    job,
    *,
    source_bucket: str | None = None,
    exclude_categories: Sequence[str] | None = None,
) -> None:
    """job을 처리 큐에 적재한다. 실패 시 redis.RedisError를 그대로 올린다.

    source_bucket: 원본이 옷장 버킷이 아닌 곳에 있을 때 지정한다. 코디 평가
        (apps/recommend)가 이미 업로드해 둔 사진을 그대로 재사용하는 경로에서 쓴다
        — 같은 사진을 두 번 올리면 S3 비용만 두 배가 된다.
        결과물(아이템 크롭·manifest)은 항상 옷장 버킷에 쌓는다.

    exclude_categories: 사진에서 등록하지 않을 옷장 대분류(상의/하의 등).
        룩북·캘린더가 '입은 옷'으로 이미 지정한 부위를 넘긴다. 프로세서는 열거 직후
        이 목록을 걸러 내므로 생성·태깅·임베딩 비용 자체가 발생하지 않는다.
        빈 목록이면 키를 싣지 않아 기존 페이로드와 동일하게 유지한다.
    """
    output_prefix = storage.output_prefix(job.user_id, job.id)
    payload = {
        "job_id": str(job.id),
        "user_id": job.user_id,
        "source": {"bucket": source_bucket or storage.BUCKET, "key": job.source_s3_key},
        # 원본과 출력 버킷이 다를 수 있으므로 출력을 명시한다. 프로세서는 output.bucket을
        # 먼저 보고 없으면 source.bucket으로 떨어진다(image-processor/worker.py).
        "output": {"bucket": storage.BUCKET, "prefix": output_prefix},
        "output_prefix": output_prefix,
        "callback_url": CALLBACK_URL,
    }
    if exclude_categories:
        payload["exclude_categories"] = list(exclude_categories)
    _redis().lpush(QUEUE_KEY, json.dumps(payload, ensure_ascii=False))


def enqueue_item(job) -> None:
    payload = {
        "job_id": str(job.id),
        "batch_id": str(job.batch_id),
        "user_id": job.user_id,
        "source": {"bucket": storage.BUCKET, "key": job.source_s3_key},
        "output_prefix": storage.output_prefix(job.user_id, job.id),
        "input_mode": "photo",
        "callback_url": CALLBACK_URL,
    }
    _redis().lpush(ITEM_QUEUE_KEY, json.dumps(payload, ensure_ascii=False))


def cancel_pending(job) -> bool:
    """아직 소비되지 않은 job을 해당 Redis 대기열에서 제거한다."""
    queue_key = ITEM_QUEUE_KEY if job.pipeline == "qwen-tag" else QUEUE_KEY
    redis_client = _redis()
    for raw in redis_client.lrange(queue_key, 0, -1):
        try:
            queued_job_id = json.loads(raw).get("job_id")
        except (json.JSONDecodeError, AttributeError):
            continue
        if queued_job_id == str(job.pk):
            return bool(redis_client.lrem(queue_key, 1, raw))
    return False
