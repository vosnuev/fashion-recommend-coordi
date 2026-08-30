"""11번가 ProductSearch 상품 수집, 카테고리 동기화, LLM 태깅."""

from __future__ import annotations

import argparse
import math
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import db
import requests
from attribute_extractor import clean_title, extract_attributes
from category_mapping import map_eleven_category
from config import (
    BATCH_POLL_SECONDS,
    CATEGORY_API_URL,
    CATEGORY_INACTIVE_MISS_THRESHOLD,
    DAILY_MAX_ITEMS,
    ELEVEN_API_KEY,
    KST,
    MAX_ITEMS_PER_KEYWORD,
    MAX_RETRIES,
    PAGE_SIZE,
    PRODUCT_SEARCH_URL,
    REQUEST_INTERVAL_SECONDS,
    REQUEST_TIMEOUT,
    RETRY_DELAY_SECONDS,
    SCHEDULE_HOUR,
    SCHEDULE_MINUTE,
    SCHEDULER_POLL_SECONDS,
    SEARCH_SORT,
    TAGGING_MODE,
    TAGGING_PROVIDER,
    logger,
)
from keywords import KeywordEntry, iter_daily_keywords, iter_keywords
from util.product_indexer_trigger import trigger_product_indexer
from xml_parser import (
    decode_xml,
    extract_api_error,
    extract_category_disp_no,
    extract_category_path,
    get_value,
    parse_categories,
    parse_products,
)

try:
    from psycopg2.extras import Json
except ImportError:  # pragma: no cover
    Json = None


@dataclass(frozen=True)
class ApiResult:
    response_id: int
    body: str


def now_kst() -> datetime:
    return datetime.now(KST)


def _safe_params(params: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in params.items()
        if key.lower() not in {"key", "apikey", "api_key", "openapikey"}
    }


def _require_api_key() -> None:
    if not ELEVEN_API_KEY:
        raise ValueError("11ST_API_KEY가 없습니다. 루트 .env에 설정하세요.")


def request_xml(
    conn,
    *,
    api_name: str,
    endpoint: str,
    params: dict[str, Any],
) -> ApiResult:
    """API 호출을 수행하고 성공/실패 응답을 모두 eleven_api_response에 남긴다."""
    _require_api_key()
    safe_params = _safe_params(params)
    headers = {"Accept": "application/xml"}
    if api_name == "category":
        headers["openapikey"] = ELEVEN_API_KEY

    for attempt in range(MAX_RETRIES + 1):
        fetched_at = now_kst()
        try:
            response = requests.get(
                endpoint,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            body = decode_xml(response.content)
            error = None if response.ok else f"HTTP {response.status_code}"
            response_id = db.insert_api_response(
                conn,
                api_name=api_name,
                endpoint=endpoint,
                request_params=safe_params,
                response_status=response.status_code,
                content_type=response.headers.get("Content-Type"),
                raw_body=body,
                error_message=error,
                fetched_at=fetched_at,
            )
            if response.ok:
                return ApiResult(response_id=response_id, body=body)
            if response.status_code not in {429, 500, 502, 503, 504}:
                raise RuntimeError(error)
        except requests.RequestException as exc:
            db.insert_api_response(
                conn,
                api_name=api_name,
                endpoint=endpoint,
                request_params=safe_params,
                response_status=None,
                content_type=None,
                raw_body="",
                error_message=str(exc),
                fetched_at=fetched_at,
            )
            if attempt >= MAX_RETRIES:
                raise

        if attempt < MAX_RETRIES:
            delay = RETRY_DELAY_SECONDS * (2**attempt)
            logger.warning(
                "%s API 재시도 (%s/%s, %s초 후)",
                api_name,
                attempt + 1,
                MAX_RETRIES,
                delay,
            )
            time.sleep(delay)

    raise RuntimeError(f"{api_name} API 호출이 재시도 후 실패했습니다.")


def sync_categories(conn, dry_run: bool = False) -> int:
    result = request_xml(
        conn,
        api_name="category",
        endpoint=CATEGORY_API_URL,
        params={},
    )
    try:
        api_error = extract_api_error(result.body)
        if api_error:
            raise ValueError(f"11번가 API 오류: {api_error}")
        categories = parse_categories(result.body)
        if not categories:
            raise ValueError("카테고리 노드를 찾지 못했습니다.")
    except Exception as exc:
        db.update_api_response_error(conn, result.response_id, f"XML parse: {exc}")
        raise

    if dry_run:
        logger.info("[dry-run] 카테고리 파싱 완료: %s건", len(categories))
        return len(categories)

    saved = db.upsert_categories(
        conn,
        categories,
        result.response_id,
        now_kst(),
        CATEGORY_INACTIVE_MISS_THRESHOLD,
    )
    logger.info("카테고리 동기화 완료: %s건", saved)
    return saved


def fetch_keyword_products(
    conn, keyword: str, limit: int
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_num = 1
    while len(items) < limit:
        page_size = min(PAGE_SIZE, limit - len(items))
        params: dict[str, Any] = {
            "key": ELEVEN_API_KEY,
            "apiCode": "ProductSearch",
            "keyword": keyword,
            "pageNum": page_num,
            "pageSize": page_size,
        }
        if SEARCH_SORT:
            params["sortCd"] = SEARCH_SORT

        result = request_xml(
            conn,
            api_name="product_search",
            endpoint=PRODUCT_SEARCH_URL,
            params=params,
        )
        try:
            api_error = extract_api_error(result.body)
            if api_error:
                raise ValueError(f"11번가 API 오류: {api_error}")
            page_items, total_count = parse_products(result.body)
            if not page_items and total_count > 0:
                raise ValueError(
                    f"TotalCount={total_count}이지만 Product 노드를 찾지 못했습니다."
                )
        except Exception as exc:
            db.update_api_response_error(conn, result.response_id, f"XML parse: {exc}")
            raise

        rank_offset = len(items)
        for page_rank, item in enumerate(page_items, start=1):
            item["_collector"] = {
                "api_response_id": result.response_id,
                "page_num": page_num,
                "search_rank": rank_offset + page_rank,
            }
            items.append(item)
            if len(items) >= limit:
                break

        if not page_items or len(page_items) < page_size or len(items) >= total_count:
            break
        page_num += 1
        time.sleep(REQUEST_INTERVAL_SECONDS)
    return items[:limit]


def to_int(value: Any, *, zero_is_none: bool = False) -> int | None:
    try:
        number = int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return None if zero_is_none and number == 0 else number


def to_decimal(value: Any) -> Decimal | None:
    try:
        text = str(value).replace(",", "").strip()
        return Decimal(text) if text else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"items": value}
    if value not in (None, ""):
        return {"value": value}
    return {}


def build_row(
    item: dict[str, Any],
    entry: KeywordEntry,
    category_paths: dict[str, list[str]],
    tags: dict[str, Any],
    tag_meta: dict[str, Any],
    collected_at: datetime,
) -> tuple[Any, ...]:
    collector_meta = item.get("_collector", {})
    product_id = str(get_value(item, "ProductCode") or "").strip()
    title_raw = str(get_value(item, "ProductName") or "").strip()
    title = clean_title(title_raw)
    disp_no = extract_category_disp_no(item)
    category_path = category_paths.get(disp_no or "") or extract_category_path(item)
    category_path = category_path[:4]
    padded_path = category_path + [None] * (4 - len(category_path))

    mapped_large, mapped_small, mapping_version = map_eleven_category(category_path)
    if mapped_large and mapped_small:
        category_large = mapped_large
        category_small = mapped_small
        category_source = "eleven_category"
    else:
        category_large = entry.category_large
        category_small = entry.category_small
        category_source = "keyword"

    raw_item = {key: value for key, value in item.items() if key != "_collector"}
    raw_data = {
        "api": "eleven_product_search",
        "item": raw_item,
        "keyword": entry.keyword,
        "page_num": collector_meta.get("page_num"),
        "search_rank": collector_meta.get("search_rank"),
    }
    status = tag_meta.get("tagging_status", "pending")

    return (
        product_id,
        title[:500],
        title_raw[:500],
        get_value(item, "ProductDetailUrl", "DetailPageUrl"),
        get_value(item, "ProductImage", "ProductImage300", "BasicImage"),
        to_int(get_value(item, "ProductPrice"), zero_is_none=True),
        to_int(get_value(item, "SalePrice"), zero_is_none=True),
        get_value(item, "Seller", "SellerNick"),
        to_decimal(get_value(item, "Rating")),
        to_int(get_value(item, "ReviewCount")),
        to_decimal(get_value(item, "BuySatisfy")),
        get_value(item, "Delivery"),
        Json(_json_object(get_value(item, "Benefit"))),
        *padded_path,
        disp_no,
        category_large,
        category_small,
        category_source,
        mapping_version,
        tags.get("season", []),
        tags.get("style", []),
        tags.get("color", []),
        tags.get("pattern", []),
        tags.get("fit"),
        tags.get("material", []),
        tags.get("sleeve"),
        tags.get("length"),
        tags.get("usage", []),
        tags.get("layer_role"),
        tags.get("layer_order"),
        Json(tag_meta.get("tag_source", {})),
        status,
        tag_meta.get("tagging_model"),
        tag_meta.get("tagging_used_image", False),
        now_kst() if status == "tagged" else None,
        entry.keyword,
        SEARCH_SORT or None,
        collector_meta.get("search_rank"),
        collector_meta.get("page_num"),
        Json(raw_data),
        collector_meta.get("api_response_id"),
        collected_at,
    )


def collect(
    conn,
    entries: Iterable[KeywordEntry],
    limit_per_keyword: int,
    skip_llm: bool = False,
    dry_run: bool = False,
    max_total_items: int | None = None,
) -> int:
    tagger = None
    if not skip_llm:
        from util.tagging import get_sync_tagger

        tagger = get_sync_tagger(TAGGING_PROVIDER)
        logger.info("11번가 태깅 provider: %s", TAGGING_PROVIDER)

    category_paths = db.load_category_paths(conn)
    seen_ids: set[str] = set()
    total_saved = 0
    total_new_candidates = 0
    skipped_existing = 0

    for entry in entries:
        counted_total = total_new_candidates if dry_run else total_saved
        if max_total_items is not None:
            remaining = max_total_items - counted_total
            if remaining <= 0:
                break
            keyword_limit = min(
                limit_per_keyword,
                MAX_ITEMS_PER_KEYWORD,
                remaining,
            )
        else:
            keyword_limit = min(limit_per_keyword, MAX_ITEMS_PER_KEYWORD)

        try:
            items = fetch_keyword_products(conn, entry.keyword, keyword_limit)
        except Exception:
            logger.exception("키워드 수집 실패, 다음 키워드로 진행: %s", entry.keyword)
            continue

        collected_at = now_kst()
        rows: list[tuple[Any, ...]] = []
        candidate_items: list[tuple[str, dict[str, Any]]] = []
        for item in items:
            product_id = str(get_value(item, "ProductCode") or "").strip()
            if not product_id or product_id in seen_ids:
                continue
            seen_ids.add(product_id)
            candidate_items.append((product_id, item))

        new_product_ids = set(
            db.find_new_product_ids(
                conn,
                [product_id for product_id, _ in candidate_items],
            )
        )
        skipped_existing += len(candidate_items) - len(new_product_ids)

        for product_id, item in candidate_items:
            if product_id not in new_product_ids:
                continue
            title = clean_title(str(get_value(item, "ProductName") or ""))
            rule_attrs = extract_attributes(title)
            disp_no = extract_category_disp_no(item)
            category_path = category_paths.get(disp_no or "") or extract_category_path(item)
            mapped_large, mapped_small, _ = map_eleven_category(category_path)
            category_large = mapped_large or entry.category_large
            category_small = mapped_small or entry.category_small

            if tagger is not None:
                tags, tag_meta = tagger.tag(
                    title=title,
                    category_large=category_large,
                    category_small=category_small,
                    naver_categories=category_path,
                    rule_attrs=rule_attrs,
                    image_url=get_value(
                        item, "ProductImage", "ProductImage300", "BasicImage"
                    ),
                )
            else:
                tags = {
                    **rule_attrs,
                    "season": [],
                    "style": [],
                    "usage": [],
                    "layer_role": None,
                    "layer_order": None,
                }
                tag_meta = {
                    "tagging_status": "pending",
                    "tag_source": {
                        key: "rule" for key, value in rule_attrs.items() if value
                    },
                }
            rows.append(
                build_row(
                    item,
                    entry,
                    category_paths,
                    tags,
                    tag_meta,
                    collected_at,
                )
            )
            total_new_candidates += 1
            if max_total_items is not None and len(rows) >= remaining:
                break

        if dry_run:
            logger.info("[dry-run] keyword=%s 파싱/태깅 %s건", entry.keyword, len(rows))
        else:
            saved = db.upsert_products(conn, rows)
            total_saved += saved
            logger.info(
                "저장 완료: keyword=%s [%s>%s] %s건",
                entry.keyword,
                entry.category_large,
                entry.category_small,
                saved,
            )

        counted_total = total_new_candidates if dry_run else total_saved
        if max_total_items is not None and counted_total >= max_total_items:
            logger.info("11번가 일일 수집 상한 %s건에 도달", max_total_items)
            break

    logger.info(
        "11번가 수집 종료: 신규 %s건 저장 "
        "(실행 내 확인 %s건, 기존 DB 중복 제외 %s건)",
        total_saved,
        len(seen_ids),
        skipped_existing,
    )
    return total_saved


def run_collect_job(
    conn,
    entries: Iterable[KeywordEntry],
    limit_per_keyword: int,
    skip_llm: bool,
    dry_run: bool = False,
    max_total_items: int | None = None,
) -> None:
    """설정된 태깅 모드에 따라 동기 태깅 또는 Batch 제출을 수행한다."""
    if TAGGING_MODE == "batch" and not skip_llm:
        collect(
            conn,
            entries,
            limit_per_keyword,
            skip_llm=True,
            dry_run=dry_run,
            max_total_items=max_total_items,
        )
        if not dry_run:
            import batch_tagger

            batch_tagger.submit_pending(conn)
    else:
        saved = collect(
            conn,
            entries,
            limit_per_keyword,
            skip_llm=skip_llm,
            dry_run=dry_run,
            max_total_items=max_total_items,
        )
        if not dry_run and not skip_llm:
            trigger_product_indexer(
                source="eleven",
                reason="sync_completed",
                tagged_count=saved,
            )


def retag(conn, limit: int) -> int:
    from util.tagging import get_sync_tagger

    tagger = get_sync_tagger(TAGGING_PROVIDER)
    logger.info("11번가 재태깅 provider: %s", TAGGING_PROVIDER)
    products = db.fetch_products_for_retag(conn, limit)
    done = 0
    for product in products:
        rule_attrs = extract_attributes(product["title"])
        category_path = [
            product.get(f"eleven_category{i}") or "" for i in range(1, 5)
        ]
        tags, meta = tagger.tag(
            title=product["title"],
            category_large=product["category_large"],
            category_small=product["category_small"],
            naver_categories=category_path,
            rule_attrs=rule_attrs,
            image_url=product.get("image_url"),
        )
        db.update_product_tags(conn, product["id"], tags, meta)
        if meta.get("tagging_status") == "tagged":
            done += 1
    logger.info("재태깅 완료: %s/%s건", done, len(products))
    if done:
        trigger_product_indexer(
            source="eleven",
            reason="retag_completed",
            tagged_count=done,
        )
    return done


def run_scheduler(limit_per_keyword: int, skip_llm: bool) -> None:
    logger.info(
        "scheduler 시작: 매일 %02d:%02d KST 카테고리 동기화 + "
        "상품 수집 최대 %d건 (tagging_mode=%s)",
        SCHEDULE_HOUR,
        SCHEDULE_MINUTE,
        DAILY_MAX_ITEMS,
        TAGGING_MODE,
    )
    last_run_date = None
    last_batch_poll = 0.0
    while True:
        current = now_kst()
        due = (
            current.hour == SCHEDULE_HOUR
            and current.minute >= SCHEDULE_MINUTE
            and last_run_date != current.date()
        )
        if due:
            last_run_date = current.date()
            conn = None
            try:
                conn = db.get_connection()
                db.ensure_schema(conn)
                try:
                    sync_categories(conn)
                except Exception:
                    logger.exception("카테고리 동기화 실패. 키워드 fallback으로 수집합니다.")
                run_collect_job(
                    conn,
                    iter_daily_keywords(
                        current.date(),
                        expected_keywords_per_day=math.ceil(
                            DAILY_MAX_ITEMS / MAX_ITEMS_PER_KEYWORD
                        ),
                    ),
                    limit_per_keyword,
                    skip_llm,
                    max_total_items=DAILY_MAX_ITEMS,
                )
            except Exception:
                logger.exception("11번가 스케줄 수집 실패")
            finally:
                if conn is not None:
                    conn.close()

        if (
            TAGGING_MODE == "batch"
            and not skip_llm
            and time.monotonic() - last_batch_poll >= BATCH_POLL_SECONDS
        ):
            last_batch_poll = time.monotonic()
            conn = None
            try:
                conn = db.get_connection()
                db.ensure_schema(conn)
                import batch_tagger

                batch_tagger.poll_batches(conn)
            except Exception:
                logger.exception("11번가 Batch 폴링 실패. 다음 주기에 재시도합니다.")
            finally:
                if conn is not None:
                    conn.close()
        time.sleep(SCHEDULER_POLL_SECONDS)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="11번가 ProductSearch collector")
    parser.add_argument(
        "--job",
        choices=[
            "collect",
            "sync-categories",
            "retag",
            "batch-submit",
            "batch-poll",
        ],
        help="1회 실행할 작업",
    )
    parser.add_argument("--scheduler", action="store_true", help="매일 자동 수집")
    parser.add_argument("--category-large", help="특정 대분류만 수집")
    parser.add_argument("--category-small", help="특정 소분류만 수집")
    parser.add_argument("--keyword", help="단일 키워드만 수집 (소량 테스트용)")
    parser.add_argument(
        "--limit",
        type=int,
        default=MAX_ITEMS_PER_KEYWORD,
        help="키워드당 최대 수집 건수 또는 재태깅 건수",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="LLM 태깅을 생략하고 pending으로 저장",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="상품/카테고리 upsert 생략 (API 응답 감사 로그는 저장)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.limit < 1:
        raise ValueError("--limit은 1 이상이어야 합니다.")

    if args.scheduler:
        run_scheduler(args.limit, args.skip_llm)
        return 0
    if not args.job:
        logger.info("실행할 작업이 없습니다. --help를 확인하세요.")
        return 0

    conn = db.get_connection()
    try:
        db.ensure_schema(conn)
        if args.job == "sync-categories":
            sync_categories(conn, args.dry_run)
        elif args.job == "collect":
            entries = iter_keywords(
                category_large=args.category_large,
                category_small=args.category_small,
                only_keyword=args.keyword,
            )
            run_collect_job(
                conn,
                entries,
                args.limit,
                skip_llm=args.skip_llm,
                dry_run=args.dry_run,
            )
        elif args.job == "retag":
            retag(conn, args.limit)
        elif args.job == "batch-submit":
            import batch_tagger

            batch_tagger.submit_pending(conn)
        elif args.job == "batch-poll":
            import batch_tagger

            batch_tagger.poll_batches(conn)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
