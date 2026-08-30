"""11번가 상품의 OpenAI Batch API 태깅 오케스트레이션.

요청 생성과 결과 파싱은 collector/util/tagging/openai_batch.py를 사용하고,
이 모듈은 eleven_product와 eleven_tagging_batch의 상태 전이만 담당한다.
"""

from __future__ import annotations

from typing import Any

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
from util.tagging.openai_batch import (
    FAIL_BATCH_STATUSES,
    OPEN_BATCH_STATUSES,
    build_request_line,
    parse_result_lines,
)

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None


def _client(require_openai_provider: bool = True) -> "OpenAI":
    """신규 제출은 OpenAI provider만 허용하고 기존 Batch 폴링은 항상 허용한다."""
    if require_openai_provider and TAGGING_PROVIDER != "openai":
        raise RuntimeError(
            "Batch 제출은 OpenAI 전용입니다 "
            f"(현재 ELEVEN_TAGGING_PROVIDER={TAGGING_PROVIDER}). "
            "기존 Batch 회수는 --job batch-poll로 가능합니다."
        )
    if OpenAI is None:
        raise RuntimeError(
            "openai 패키지가 없습니다. requirements.eleven.txt를 설치하세요."
        )
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY가 없습니다. .env에 설정하세요.")
    return OpenAI(api_key=OPENAI_API_KEY)


def _request_line(product: dict[str, Any]) -> str:
    rule_attrs = extract_attributes(product["title"])
    category_path = [
        product.get(f"eleven_category{i}") or "" for i in range(1, 5)
    ]
    return build_request_line(
        custom_id=str(product["id"]),
        title=product["title"],
        category_large=product["category_large"],
        category_small=product["category_small"],
        naver_categories=category_path,
        rule_attrs=rule_attrs,
        model=OPENAI_MODEL,
        image_url=(
            product.get("image_url") if BATCH_INCLUDE_IMAGE else None
        ),
    )


def submit_pending(conn) -> int:
    """pending 상품을 설정된 요청 수 단위로 나눠 OpenAI Batch에 제출한다."""
    client = _client()
    total_submitted = 0

    while True:
        products = db.fetch_pending_products(conn, BATCH_MAX_REQUESTS)
        if not products:
            break

        jsonl = "\n".join(_request_line(product) for product in products)
        product_ids = [product["id"] for product in products]
        input_file = client.files.create(
            file=("eleven_tagging_batch.jsonl", jsonl.encode("utf-8")),
            purpose="batch",
        )
        batch = client.batches.create(
            input_file_id=input_file.id,
            endpoint="/v1/chat/completions",
            completion_window=BATCH_COMPLETION_WINDOW,
        )
        db.insert_tagging_batch(
            conn,
            batch.id,
            OPENAI_MODEL,
            len(products),
            BATCH_INCLUDE_IMAGE,
            input_file.id,
        )
        db.set_products_tagging_status(conn, product_ids, "queued")
        total_submitted += len(products)
        logger.info(
            "11번가 배치 제출: batch_id=%s, %s건 (이미지 포함=%s)",
            batch.id,
            len(products),
            BATCH_INCLUDE_IMAGE,
        )

    if total_submitted == 0:
        logger.info("11번가 배치 제출 대상(pending)이 없습니다.")
    return total_submitted


def poll_batches(conn) -> int:
    """진행 중 Batch 상태를 조회하고 완료 결과를 DB에 반영한다."""
    open_batches = db.fetch_open_tagging_batches(conn)
    if not open_batches:
        restored = db.reset_orphan_queued_products(conn)
        if restored:
            logger.info("queued 잔여 상품 %s건을 pending으로 복구", restored)
        return 0

    client = _client(require_openai_provider=False)
    tagged_total = 0

    for record in open_batches:
        batch = client.batches.retrieve(record["batch_id"])

        if batch.status in OPEN_BATCH_STATUSES:
            db.update_tagging_batch(conn, batch.id, batch.status)
            logger.info("11번가 배치 진행 중: %s (%s)", batch.id, batch.status)
            continue

        if batch.status in FAIL_BATCH_STATUSES:
            error_text = str(
                getattr(batch, "errors", None) or batch.status
            )[:1000]
            db.update_tagging_batch(
                conn,
                batch.id,
                batch.status,
                error=error_text,
                completed=True,
            )
            logger.warning(
                "11번가 배치 실패: %s (%s). 상품은 pending으로 복귀합니다.",
                batch.id,
                batch.status,
            )
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
            logger.info(
                "11번가 배치 완료 반영: %s, tagged=%s건",
                batch.id,
                tagged,
            )

    restored = db.reset_orphan_queued_products(conn)
    if restored:
        logger.info("queued 잔여 상품 %s건을 pending으로 복구", restored)
    if tagged_total:
        trigger_product_indexer(
            source="eleven",
            reason="batch_completed",
            tagged_count=tagged_total,
        )
    return tagged_total


def _apply_completed_batch(conn, client, batch) -> int:
    output_lines: list[str] = []
    error_lines: list[str] = []
    if batch.output_file_id:
        output_lines = client.files.content(
            batch.output_file_id
        ).text.splitlines()
    if batch.error_file_id:
        error_lines = client.files.content(
            batch.error_file_id
        ).text.splitlines()

    results, failed_ids = parse_result_lines(output_lines, error_lines)
    products = db.fetch_products_by_ids(conn, list(results))
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
        logger.warning(
            "11번가 배치 내 실패 요청 %s건을 failed로 변경",
            len(failed_ids),
        )
    return tagged
