"""
OpenAI Batch API 태깅 오케스트레이션 (동기 대비 토큰 단가 50% 할인).

순수 태깅 로직(요청 구성/응답 파싱/병합)은 util.tagging에 있고,
이 모듈은 naver DB 상태 전환과 배치 이력 관리만 담당한다.

흐름:
  1. submit_pending(): tagging_status='pending' 상품을 JSONL로 만들어 배치 제출
     → 해당 상품은 'queued'로 전환 (중복 제출 방지)
  2. poll_batches(): 진행 중 배치 상태 확인
     → completed면 출력 파일을 파싱해 상품별 태그 반영 ('tagged'/'failed')
     → failed/expired/cancelled면 배치 기록 갱신, 상품은 pending으로 복귀
  3. 어떤 배치에도 속하지 않은 'queued' 잔여물은 pending으로 복구

배치 추적 테이블(naver_tagging_batch)의 스키마는 Django migration
(api/apps/catalog/0002)이 소유한다.

주의: Batch API는 OpenAI 전용이다. NAVER_TAGGING_PROVIDER=claude에서는 사용할 수 없다.
"""

from __future__ import annotations

from typing import Any, Dict, List

import db
from attribute_extractor import extract_attributes
from config import (
    BATCH_COMPLETION_WINDOW,
    BATCH_INCLUDE_IMAGE,
    BATCH_MAX_REQUESTS,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    TAGGING_PROVIDER,
    logger,
)
from util.product_indexer_trigger import trigger_product_indexer
from util.tagging import merge_tags
from util.tagging.openai_batch import build_request_line, parse_output_line

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

# OpenAI Batch 상태값
_OPEN_STATUSES = {"validating", "in_progress", "finalizing"}
_FAIL_STATUSES = {"failed", "expired", "cancelled", "cancelling"}


def _client(require_openai_provider: bool = True) -> "OpenAI":
    """
    require_openai_provider=True  : 신규 배치 제출 경로 (openai provider에서만 허용)
    require_openai_provider=False : 폴링/소진 경로 — provider를 claude로 전환한 뒤에도
                                    진행 중이던 기존 배치를 회수할 수 있어야 한다.
    """
    if require_openai_provider and TAGGING_PROVIDER != "openai":
        raise RuntimeError(
            f"Batch 제출은 OpenAI 전용입니다 (현재 NAVER_TAGGING_PROVIDER={TAGGING_PROVIDER}). "
            "claude provider는 sync 모드로 동작합니다. 기존 배치 회수는 --job batch-poll로 가능합니다."
        )
    if OpenAI is None:
        raise RuntimeError("openai 패키지가 없습니다. requirements.naver.txt를 설치하세요.")
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY가 없습니다. .env에 설정하세요.")
    return OpenAI(api_key=OPENAI_API_KEY)


# ------------------------------------------------------------
# 제출
# ------------------------------------------------------------


def _request_line(product: Dict[str, Any]) -> str:
    rule_attrs = extract_attributes(product["title"])
    return build_request_line(
        custom_id=str(product["id"]),
        title=product["title"],
        category_large=product["category_large"],
        category_small=product["category_small"],
        naver_categories=[product.get(f"naver_category{i}") or "" for i in range(1, 5)],
        rule_attrs=rule_attrs,
        model=OPENAI_MODEL,
        image_url=product.get("image_url") if BATCH_INCLUDE_IMAGE else None,
    )


def submit_pending(conn) -> int:
    """pending 상품 전체를 BATCH_MAX_REQUESTS 단위로 나눠 배치 제출. 제출 건수 반환."""
    client = _client()
    total_submitted = 0

    while True:
        products = db.fetch_pending_products(conn, BATCH_MAX_REQUESTS)
        if not products:
            break

        jsonl = "\n".join(_request_line(p) for p in products)
        product_ids = [p["id"] for p in products]

        input_file = client.files.create(
            file=("tagging_batch.jsonl", jsonl.encode("utf-8")),
            purpose="batch",
        )
        batch = client.batches.create(
            input_file_id=input_file.id,
            endpoint="/v1/chat/completions",
            completion_window=BATCH_COMPLETION_WINDOW,
        )

        db.insert_tagging_batch(
            conn, batch.id, OPENAI_MODEL, len(products), BATCH_INCLUDE_IMAGE, input_file.id
        )
        db.set_products_tagging_status(conn, product_ids, "queued")
        total_submitted += len(products)
        logger.info("배치 제출: batch_id=%s, %s건 (이미지 포함=%s)", batch.id, len(products), BATCH_INCLUDE_IMAGE)

    if total_submitted == 0:
        logger.info("배치 제출 대상(pending)이 없습니다.")
    return total_submitted


# ------------------------------------------------------------
# 폴링 / 결과 반영
# ------------------------------------------------------------


def poll_batches(conn) -> int:
    """진행 중 배치 상태 확인 및 완료분 반영. 이번 폴링에서 태깅 반영된 건수 반환."""
    open_batches = db.fetch_open_tagging_batches(conn)
    if not open_batches:
        return 0

    # provider 전환 후에도 기존 배치는 회수해야 하므로 provider 가드를 걸지 않는다.
    client = _client(require_openai_provider=False)
    tagged_total = 0

    for record in open_batches:
        batch = client.batches.retrieve(record["batch_id"])

        if batch.status in _OPEN_STATUSES:
            db.update_tagging_batch(conn, batch.id, batch.status)
            logger.info("배치 진행 중: %s (%s)", batch.id, batch.status)
            continue

        if batch.status in _FAIL_STATUSES:
            error_text = str(getattr(batch, "errors", None) or batch.status)[:1000]
            db.update_tagging_batch(
                conn, batch.id, batch.status, error=error_text, completed=True
            )
            logger.warning("배치 실패: %s (%s) — 상품은 pending으로 복귀합니다.", batch.id, batch.status)
            continue

        if batch.status == "completed":
            tagged = _apply_completed_batch(conn, client, batch)
            tagged_total += tagged
            db.update_tagging_batch(
                conn,
                batch.id,
                "completed",
                output_file_id=batch.output_file_id,
                error_file_id=batch.error_file_id,
                completed=True,
            )
            logger.info("배치 완료 반영: %s, tagged=%s건", batch.id, tagged)

    # 실패/누락으로 queued에 남은 상품 복구 (진행 중 배치가 없을 때만)
    restored = db.reset_orphan_queued_products(conn)
    if restored:
        logger.info("queued 잔여 상품 %s건을 pending으로 복구", restored)

    if tagged_total:
        trigger_product_indexer(
            source="naver",
            reason="batch_completed",
            tagged_count=tagged_total,
        )

    return tagged_total


def _apply_completed_batch(conn, client, batch) -> int:
    """완료 배치의 출력 파일을 파싱해 상품 태그를 반영한다."""
    lines: List[str] = []
    if batch.output_file_id:
        lines.extend(client.files.content(batch.output_file_id).text.splitlines())

    results: Dict[int, Dict[str, Any]] = {}
    failed_ids: List[int] = []
    for line in lines:
        custom_id, tags, is_error = parse_output_line(line)
        if custom_id is None:
            continue
        if is_error or tags is None:
            failed_ids.append(custom_id)
        else:
            results[custom_id] = tags

    # 에러 파일에 있는 요청도 실패 처리
    if batch.error_file_id:
        import json as _json

        for line in client.files.content(batch.error_file_id).text.splitlines():
            try:
                failed_ids.append(int(_json.loads(line)["custom_id"]))
            except (KeyError, ValueError, TypeError):
                continue

    # 규칙 추출 재계산 후 병합·반영
    products = db.fetch_products_by_ids(conn, list(results.keys()))
    tagged = 0
    for product_id, llm_tags in results.items():
        product = products.get(product_id)
        if product is None:
            continue
        rule_attrs = extract_attributes(product["title"])
        merged, tag_source = merge_tags(rule_attrs, llm_tags)
        db.update_product_tags(
            conn,
            product_id,
            merged,
            {
                "tagging_status": "tagged",
                "tagging_model": OPENAI_MODEL,
                "tagging_used_image": BATCH_INCLUDE_IMAGE,
                "tag_source": tag_source,
            },
        )
        tagged += 1

    if failed_ids:
        db.set_products_tagging_status(conn, failed_ids, "failed")
        logger.warning("배치 내 실패 요청 %s건 → tagging_status=failed", len(failed_ids))

    return tagged
