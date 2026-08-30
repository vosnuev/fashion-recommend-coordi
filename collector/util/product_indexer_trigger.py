"""원격 GPU product-indexer drain 실행 트리거.

collector는 상품 데이터나 이미지를 전송하지 않는다. PostgreSQL에 태깅 결과와
임베딩 작업을 저장한 뒤 GPU 서버에 작업 확인 신호만 보낸다. 호출 실패가 상품
수집 트랜잭션을 되돌리지 않도록 이 모듈은 예외를 외부로 전파하지 않는다.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger("product_indexer_trigger")


def _int_env(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        logger.error("%s 값이 정수가 아니어서 기본값 %s를 사용합니다.", name, default)
        return default


def trigger_product_indexer(
    *,
    source: str,
    reason: str,
    tagged_count: int | None = None,
) -> bool:
    """GPU 서버에 비동기 drain 시작을 요청한다.

    URL이 없으면 기능을 비활성 상태로 간주한다. URL을 설정했는데 token이 없으면
    인증되지 않은 외부 호출을 피하기 위해 요청하지 않는다.
    """

    url = os.getenv("PRODUCT_INDEXER_TRIGGER_URL", "").strip()
    if not url:
        logger.debug("PRODUCT_INDEXER_TRIGGER_URL이 없어 원격 트리거를 생략합니다.")
        return False

    token = os.getenv("PRODUCT_INDEXER_TRIGGER_TOKEN", "").strip()
    if not token:
        logger.error(
            "PRODUCT_INDEXER_TRIGGER_URL은 설정됐지만 "
            "PRODUCT_INDEXER_TRIGGER_TOKEN이 없어 원격 트리거를 생략합니다."
        )
        return False

    timeout = _int_env("PRODUCT_INDEXER_TRIGGER_TIMEOUT_SECONDS", 10, 1)
    max_retries = _int_env("PRODUCT_INDEXER_TRIGGER_MAX_RETRIES", 2)
    retry_base = _int_env("PRODUCT_INDEXER_TRIGGER_RETRY_BASE_SECONDS", 2, 1)
    payload: dict[str, Any] = {
        "source": source,
        "reason": reason,
    }
    if tagged_count is not None:
        payload["tagged_count"] = max(0, tagged_count)

    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout,
            )
            if 200 <= response.status_code < 300:
                logger.info(
                    "GPU product-indexer 트리거 접수: source=%s, reason=%s, status=%s",
                    source,
                    reason,
                    response.status_code,
                )
                return True

            retryable = response.status_code == 429 or response.status_code >= 500
            logger.warning(
                "GPU product-indexer 트리거 실패: source=%s, status=%s, attempt=%s/%s",
                source,
                response.status_code,
                attempt + 1,
                max_retries + 1,
            )
            if not retryable:
                return False
        except requests.RequestException as exc:
            logger.warning(
                "GPU product-indexer 트리거 통신 실패: "
                "source=%s, attempt=%s/%s, error=%s",
                source,
                attempt + 1,
                max_retries + 1,
                type(exc).__name__,
            )

        if attempt < max_retries:
            time.sleep(retry_base * (2**attempt))

    logger.error(
        "GPU product-indexer 트리거 최종 실패. "
        "상품과 임베딩 작업은 DB에 유지되며 다음 트리거에서 다시 처리됩니다."
    )
    return False
