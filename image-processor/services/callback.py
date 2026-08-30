"""wardrobe-api 콜백 호출.

계약: POST {callback_url} + X-Internal-Token 헤더 (api/apps/wardrobe 구현 기준)
- job_id 멱등이므로 재시도가 안전하다 (이미 처리된 job이면 200 "이미 처리됨")
- 2xx면 성공으로 간주한다
"""
from __future__ import annotations

import logging
import time

import requests

import config

logger = logging.getLogger(__name__)


class CallbackError(RuntimeError):
    pass


def post(callback_url: str, payload: dict) -> None:
    url = callback_url or config.CALLBACK_FALLBACK_URL
    if not url:
        raise CallbackError("callback_url이 페이로드에도 환경변수에도 없습니다.")

    last: Exception | None = None
    for attempt in range(1, config.CALLBACK_RETRIES + 1):
        try:
            resp = requests.post(
                url, json=payload,
                headers={"X-Internal-Token": config.INTERNAL_TOKEN},
                timeout=config.CALLBACK_TIMEOUT,
            )
            if resp.ok:
                return
            # 4xx는 페이로드/인증 문제 — 재시도해도 같으므로 즉시 실패
            if 400 <= resp.status_code < 500:
                raise CallbackError(
                    f"콜백 거부 {resp.status_code}: {resp.text[:500]}"
                )
            last = CallbackError(f"콜백 실패 {resp.status_code}: {resp.text[:200]}")
        except CallbackError:
            raise
        except requests.RequestException as e:
            last = e
        sleep = 2 ** attempt
        logger.warning("콜백 재시도 %d/%d (%s초 후): %s",
                       attempt, config.CALLBACK_RETRIES, sleep, last)
        time.sleep(sleep)
    raise CallbackError(f"콜백 최종 실패: {last}")
