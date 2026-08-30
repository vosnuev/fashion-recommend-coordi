"""
네이버 쇼핑 검색 API 의류 상품 collector.

파이프라인 (insert 전에 태깅 완료가 원칙):
  1. keywords.iter_keywords()로 검색 키워드 생성 (컨플루언스 문서 소분류 기반 자동 매핑)
  2. 네이버 쇼핑 검색 API 호출 + 페이징 (display=100, start<=1000)
  3. 노이즈 필터: category1이 패션의류/패션잡화가 아니면 제외, run 내 productId 중복 제거
  4. category_mapping으로 네이버 category1~4 → 문서 분류 매핑 (실패 시 키워드 분류 사용)
  5. attribute_extractor로 title 기반 color/fit/sleeve/pattern/material/length 규칙 추출
  6. llm_tagger로 season/style/usage/layer_* 및 누락 속성 태깅 (이미지 auto fallback)
  7. naver_product upsert

스키마는 Django migration(api/apps/catalog)이 관리한다. 실행 전 migrate 필요.

태깅 provider (NAVER_TAGGING_PROVIDER):
  openai(기본) - OpenAI API. sync/batch 모두 지원
  claude       - Claude Agent SDK (구독제 OAuth 토큰). sync 전용, 이미지 미지원

태깅 모드 (NAVER_TAGGING_MODE):
  batch(기본) - 수집은 pending 저장 → OpenAI Batch API 제출(50% 할인) → 폴링 후 반영
  sync        - 수집 중 상품별 실시간 태깅 (claude provider는 항상 sync)

사용 예:
  python naver_collector_db.py --job collect                       # 수집 (+batch 모드면 배치 제출까지)
  python naver_collector_db.py --job collect --category-large 상의
  python naver_collector_db.py --job collect --keyword "린넨 셔츠" --limit 50 --skip-llm
  python naver_collector_db.py --job batch-submit                  # pending 상품 배치 제출
  python naver_collector_db.py --job batch-poll                    # 배치 상태 확인·완료분 반영
  python naver_collector_db.py --job retag                         # 태깅 실패분 동기 재태깅
  python naver_collector_db.py --scheduler                         # 매일 자동 수집 + 배치 폴링
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

import requests

import db
from attribute_extractor import clean_title, extract_attributes
from category_mapping import is_fashion_item, map_naver_category
from config import (
    BATCH_POLL_SECONDS,
    KST,
    MAX_ITEMS_PER_KEYWORD,
    MAX_RETRIES,
    NAVER_CLIENT_ID,
    NAVER_CLIENT_SECRET,
    NAVER_DISPLAY,
    NAVER_EXCLUDE,
    NAVER_MAX_START,
    NAVER_SHOP_API_URL,
    NAVER_SORT,
    REQUEST_INTERVAL_SECONDS,
    REQUEST_TIMEOUT,
    RETRY_DELAY_SECONDS,
    SCHEDULE_HOUR,
    SCHEDULE_MINUTE,
    SCHEDULER_POLL_SECONDS,
    TAGGING_MODE,
    logger,
)
from keywords import KeywordEntry, iter_keywords
from util.product_indexer_trigger import trigger_product_indexer

try:
    from psycopg2.extras import Json
except ImportError:  # pragma: no cover
    Json = None


def now_kst() -> datetime:
    return datetime.now(KST)


# ============================================================
# 네이버 쇼핑 검색 API 클라이언트
# ============================================================


def _headers() -> Dict[str, str]:
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        raise ValueError("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET이 없습니다. .env에 설정하세요.")
    return {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }


def search_shop(query: str, start: int = 1, display: int = NAVER_DISPLAY) -> Dict[str, Any]:
    """쇼핑 검색 API 1회 호출. 429/5xx는 재시도한다."""
    params = {
        "query": query,
        "display": display,
        "start": start,
        "sort": NAVER_SORT,
    }
    if NAVER_EXCLUDE:
        params["exclude"] = NAVER_EXCLUDE

    last_error: Optional[BaseException] = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.get(
                NAVER_SHOP_API_URL, headers=_headers(), params=params, timeout=REQUEST_TIMEOUT
            )
        except requests.RequestException as exc:  # 타임아웃/커넥션 오류도 재시도
            last_error = exc
            logger.warning("API 네트워크 오류: query=%s, start=%s, error=%s", query, start, exc)
        else:
            if response.status_code == 200:
                return response.json()
            logger.warning(
                "API 오류: status=%s, query=%s, start=%s, body=%s",
                response.status_code, query, start, response.text[:300],
            )
            # 429(호출 한도)·5xx는 재시도, 그 외 4xx는 즉시 중단
            if response.status_code != 429 and response.status_code < 500:
                response.raise_for_status()
            last_error = requests.HTTPError(f"status={response.status_code}")
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))
    raise RuntimeError(f"쇼핑 검색 API 최종 실패: query={query}, start={start}") from last_error


def fetch_keyword_items(query: str, limit: int) -> List[Dict[str, Any]]:
    """키워드 1개에 대해 페이징하며 최대 limit건 수집."""
    items: List[Dict[str, Any]] = []
    start = 1
    while start <= NAVER_MAX_START and len(items) < limit:
        data = search_shop(query, start=start)
        batch = data.get("items", [])
        if not batch:
            break
        items.extend(batch)
        total = int(data.get("total", 0))
        start += NAVER_DISPLAY
        if start > total:
            break
        time.sleep(REQUEST_INTERVAL_SECONDS)
    return items[:limit]


# ============================================================
# 변환 파이프라인
# ============================================================


def to_int(value: Any) -> Optional[int]:
    try:
        text = str(value).strip()
        return int(text) if text else None
    except (TypeError, ValueError):
        return None


def build_row(
    item: Dict[str, Any],
    entry: KeywordEntry,
    tags: Dict[str, Any],
    tag_meta: Dict[str, Any],
    collected_at: datetime,
) -> tuple:
    """API item + 태깅 결과 → db.PRODUCT_COLUMNS 순서 튜플."""
    title = clean_title(item.get("title", ""))
    cat1, cat2 = item.get("category1"), item.get("category2")
    cat3, cat4 = item.get("category3"), item.get("category4")

    mapped_large, mapped_small = map_naver_category(cat1, cat2, cat3, cat4)
    if mapped_large and mapped_small:
        category_large, category_small, category_source = mapped_large, mapped_small, "naver_category"
    else:
        category_large, category_small, category_source = (
            entry.category_large, entry.category_small, "keyword",
        )

    raw = {"api": "naver_shop_search", "item": item, "keyword": entry.keyword}

    return (
        str(item.get("productId", "")),
        to_int(item.get("productType")),
        title[:500],
        (item.get("title") or "")[:500],
        item.get("link"),
        item.get("image"),
        to_int(item.get("lprice")),
        to_int(item.get("hprice")),
        (item.get("mallName") or None),
        (item.get("brand") or None),
        (item.get("maker") or None),
        cat1 or None, cat2 or None, cat3 or None, cat4 or None,
        category_large, category_small, category_source,
        tags.get("season", []), tags.get("style", []),
        tags.get("color", []), tags.get("pattern", []),
        tags.get("fit"), tags.get("material", []),
        tags.get("sleeve"), tags.get("length"),
        tags.get("usage", []), tags.get("layer_role"), tags.get("layer_order"),
        Json(tag_meta.get("tag_source", {})),
        tag_meta.get("tagging_status", "pending"),
        tag_meta.get("tagging_model"),
        tag_meta.get("tagging_used_image", False),
        now_kst() if tag_meta.get("tagging_status") == "tagged" else None,
        entry.keyword,
        Json(raw),
        collected_at,
    )


def collect(
    conn,
    entries: Iterable[KeywordEntry],
    limit_per_keyword: int = MAX_ITEMS_PER_KEYWORD,
    skip_llm: bool = False,
    dry_run: bool = False,
) -> int:
    """키워드 목록 순회 수집 → 태깅 → upsert."""
    tagger = None
    if not skip_llm:
        # API 키 없이 --skip-llm 실행 가능하도록 지연 임포트.
        # provider(openai|claude)는 NAVER_TAGGING_PROVIDER로 선택된다.
        from util.tagging import get_sync_tagger

        tagger = get_sync_tagger()

    seen_ids: set[str] = set()
    total_saved = 0

    for entry in entries:
        collected_at = now_kst()
        try:
            items = fetch_keyword_items(entry.keyword, limit_per_keyword)
        except Exception:  # noqa: BLE001
            logger.exception("키워드 수집 실패, 다음 키워드로 진행: %s", entry.keyword)
            continue

        rows = []
        skipped_noise = 0
        for item in items:
            product_id = str(item.get("productId", ""))
            if not product_id or product_id in seen_ids:
                continue
            if not is_fashion_item(item.get("category1")):
                skipped_noise += 1
                continue
            seen_ids.add(product_id)

            title = clean_title(item.get("title", ""))
            rule_attrs = extract_attributes(title)

            if tagger is not None:
                tags, tag_meta = tagger.tag(
                    title=title,
                    category_large=entry.category_large,
                    category_small=entry.category_small,
                    naver_categories=[item.get(f"category{i}", "") for i in range(1, 5)],
                    rule_attrs=rule_attrs,
                    image_url=item.get("image"),
                )
            else:
                tags = {**rule_attrs, "season": [], "style": [], "usage": []}
                tag_meta = {
                    "tagging_status": "pending",
                    "tag_source": {k: "rule" for k, v in rule_attrs.items() if v},
                }

            rows.append(build_row(item, entry, tags, tag_meta, collected_at))

        if dry_run:
            logger.info("[dry-run] keyword=%s: %s건 (노이즈 제외 %s)", entry.keyword, len(rows), skipped_noise)
            continue

        saved = db.upsert_products(conn, rows)
        total_saved += saved
        logger.info(
            "저장 완료: keyword=%s [%s>%s] %s건 (노이즈 제외 %s)",
            entry.keyword, entry.category_large, entry.category_small, saved, skipped_noise,
        )

    logger.info("수집 종료: 총 %s건 저장", total_saved)
    return total_saved


def run_collect_job(
    conn,
    entries,
    limit_per_keyword: int,
    skip_llm: bool,
    dry_run: bool = False,
) -> None:
    """TAGGING_MODE에 따라 동기 태깅 수집 또는 수집 후 배치 제출."""
    if TAGGING_MODE == "batch" and not skip_llm:
        # 배치 모드: 태깅 없이 pending으로 저장한 뒤 Batch API에 제출
        collect(conn, entries, limit_per_keyword, skip_llm=True, dry_run=dry_run)
        if not dry_run:
            import batch_tagger

            batch_tagger.submit_pending(conn)
    else:
        saved = collect(conn, entries, limit_per_keyword, skip_llm, dry_run)
        if not dry_run and not skip_llm:
            trigger_product_indexer(
                source="naver",
                reason="sync_completed",
                tagged_count=saved,
            )


def retag(conn, limit: int = 500) -> int:
    """tagging_status가 pending/failed인 상품 재태깅 (동기, provider 선택 적용)."""
    from util.tagging import get_sync_tagger

    tagger = get_sync_tagger()
    products = db.fetch_products_for_retag(conn, limit)
    logger.info("재태깅 대상: %s건", len(products))

    done = 0
    for product in products:
        rule_attrs = extract_attributes(product["title"])
        tags, meta = tagger.tag(
            title=product["title"],
            category_large=product["category_large"],
            category_small=product["category_small"],
            naver_categories=[
                product.get("naver_category1") or "", product.get("naver_category2") or "",
                product.get("naver_category3") or "", product.get("naver_category4") or "",
            ],
            rule_attrs=rule_attrs,
            image_url=product.get("image_url"),
        )
        if meta["tagging_status"] == "tagged":
            db.update_product_tags(conn, product["id"], tags, meta)
            done += 1
    logger.info("재태깅 완료: %s/%s건", done, len(products))
    if done:
        trigger_product_indexer(
            source="naver",
            reason="retag_completed",
            tagged_count=done,
        )
    return done


# ============================================================
# 스케줄러 (매일 SCHEDULE_HOUR:SCHEDULE_MINUTE KST에 전체 수집)
# ============================================================


def run_scheduler(limit_per_keyword: int, skip_llm: bool) -> None:
    """장기 실행 시 커넥션 끊김/aborted 트랜잭션에 대비해 작업마다 새 커넥션을 연다."""
    logger.info(
        "scheduler 시작: 매일 %02d:%02d KST 전체 수집 (tagging_mode=%s)",
        SCHEDULE_HOUR, SCHEDULE_MINUTE, TAGGING_MODE,
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
            try:
                conn = db.get_connection()
                try:
                    run_collect_job(conn, iter_keywords(), limit_per_keyword, skip_llm)
                finally:
                    conn.close()
            except Exception:  # noqa: BLE001
                logger.exception("스케줄 수집 실패. 다음 날 같은 시각에 재시도합니다.")

        # 배치 모드: 주기적으로 배치 상태 확인 및 완료분 반영
        if (
            TAGGING_MODE == "batch"
            and not skip_llm
            and time.monotonic() - last_batch_poll >= BATCH_POLL_SECONDS
        ):
            last_batch_poll = time.monotonic()
            try:
                conn = db.get_connection()
                try:
                    import batch_tagger

                    batch_tagger.poll_batches(conn)
                finally:
                    conn.close()
            except Exception:  # noqa: BLE001
                logger.exception("배치 폴링 실패. 다음 주기에 재시도합니다.")

        time.sleep(SCHEDULER_POLL_SECONDS)


# ============================================================
# CLI
# ============================================================


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="네이버 쇼핑 의류 상품 collector")
    parser.add_argument(
        "--job",
        choices=["collect", "retag", "batch-submit", "batch-poll"],
        help="1회 실행할 작업",
    )
    parser.add_argument("--scheduler", action="store_true", help="매일 자동 수집")
    parser.add_argument("--category-large", help="특정 대분류만 수집 (예: 상의)")
    parser.add_argument("--category-small", help="특정 소분류만 수집 (예: 티셔츠)")
    parser.add_argument("--keyword", help="단일 키워드만 수집 (테스트용)")
    parser.add_argument("--limit", type=int, default=MAX_ITEMS_PER_KEYWORD,
                        help="collect: 키워드당 최대 수집 건수 / retag: 재태깅 대상 건수")
    parser.add_argument("--skip-llm", action="store_true",
                        help="LLM 태깅 생략 (tagging_status=pending으로 저장, retag로 후처리)")
    parser.add_argument("--dry-run", action="store_true", help="DB 저장 없이 수집 결과만 로그")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    conn = db.get_connection()
    try:
        db.ensure_schema(conn)  # 스키마는 Django migration이 관리. 없으면 즉시 실패.
        if args.job == "collect":
            entries = iter_keywords(
                category_large=args.category_large,
                category_small=args.category_small,
                only_keyword=args.keyword,
            )
            run_collect_job(conn, entries, args.limit, args.skip_llm, args.dry_run)
        elif args.job == "retag":
            retag(conn, args.limit)
        elif args.job == "batch-submit":
            import batch_tagger

            batch_tagger.submit_pending(conn)
        elif args.job == "batch-poll":
            import batch_tagger

            batch_tagger.poll_batches(conn)
        if args.scheduler:
            run_scheduler(args.limit, args.skip_llm)
        if not any([args.job, args.scheduler]):
            logger.info("실행할 작업이 없습니다. --help를 확인하세요.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
