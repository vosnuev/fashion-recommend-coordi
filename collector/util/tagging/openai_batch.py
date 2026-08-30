"""OpenAI Batch API 요청/응답 헬퍼 (DB 의존 없음).

각 collector가 DB 오케스트레이션(제출 이력, 상태 전환)을 담당하고,
여기서는 JSONL 요청 생성과 출력/오류 결과 집계를 공용으로 제공한다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, Dict, Optional, Tuple

from util.tagging.base import (
    TEMPERATURE,
    build_openai_messages,
    logger,
    openai_response_format,
)

OPEN_BATCH_STATUSES = frozenset({"validating", "in_progress", "finalizing"})
FAIL_BATCH_STATUSES = frozenset(
    {"failed", "expired", "cancelled", "cancelling"}
)


def build_request_line(
    custom_id: str,
    title: str,
    category_large: str,
    category_small: str,
    naver_categories: list,
    rule_attrs: Dict[str, Any],
    model: str,
    image_url: Optional[str] = None,
) -> str:
    """상품 1건 → Batch JSONL 요청 라인."""
    messages = build_openai_messages(
        title, category_large, category_small, naver_categories, rule_attrs, image_url
    )
    request = {
        "custom_id": custom_id,
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": model,
            "temperature": TEMPERATURE,
            "messages": messages,
            "response_format": openai_response_format(),
        },
    }
    return json.dumps(request, ensure_ascii=False)


def parse_output_line(
    line: str,
) -> Tuple[Optional[int], Optional[Dict[str, Any]], bool]:
    """
    Batch 출력 파일 라인 파싱.

    반환: (custom_id, llm_tags, is_error)
    - 파싱 자체가 불가능하면 (None, None, True)
    - 요청 실패 라인이면 (custom_id, None, True)
    - 성공이면 (custom_id, tags, False)
    """
    line = line.strip()
    if not line:
        return None, None, True
    try:
        obj = json.loads(line)
        custom_id = int(obj["custom_id"])
    except (KeyError, ValueError, TypeError):
        logger.warning("배치 출력 라인 파싱 실패: %s", line[:200])
        return None, None, True

    try:
        response = obj.get("response") or {}
        if obj.get("error") or response.get("status_code") != 200:
            return custom_id, None, True
        content = response["body"]["choices"][0]["message"]["content"]
        tags = json.loads(content)
        if not isinstance(tags, dict):
            return custom_id, None, True
        return custom_id, tags, False
    except (KeyError, ValueError, TypeError):
        logger.warning("배치 응답 본문 파싱 실패: custom_id=%s", custom_id)
        return custom_id, None, True


def parse_result_lines(
    output_lines: Iterable[str],
    error_lines: Iterable[str] = (),
) -> tuple[dict[int, Dict[str, Any]], list[int]]:
    """Batch 출력/오류 라인을 상품 ID별 성공 태그와 실패 ID로 집계한다."""
    results: dict[int, Dict[str, Any]] = {}
    failed_ids: set[int] = set()

    for line in output_lines:
        custom_id, tags, is_error = parse_output_line(line)
        if custom_id is None:
            continue
        if is_error or tags is None:
            failed_ids.add(custom_id)
        else:
            results[custom_id] = tags

    for line in error_lines:
        try:
            custom_id = int(json.loads(line)["custom_id"])
        except (KeyError, ValueError, TypeError):
            logger.warning("배치 오류 라인 파싱 실패: %s", line[:200])
            continue
        failed_ids.add(custom_id)

    for product_id in failed_ids:
        results.pop(product_id, None)
    return results, sorted(failed_ids)
