"""
네이버 상품 collector DB 계층.

테이블
- naver_product      : 수집 상품 원본 + 문서 분류/태그 (insert 전에 태깅 완료가 원칙)
- naver_product_size : 상품 사이즈별 치수/측정값 (하위 종속 테이블)

스키마 소유권
- 테이블 스키마는 Django migration(api/apps/catalog)이 소유한다.
  collector는 upsert만 수행하므로, 수집 실행 전에 api에서
  `python manage.py migrate`가 적용되어 있어야 한다.
  컬럼 변경 시 catalog 모델과 이 파일의 PRODUCT_COLUMNS를 함께 갱신한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from config import DATABASE_URL, DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER, logger
from util.embedding_jobs import (
    enqueue_new_products,
    find_new_external_ids,
    requeue_existing_products,
)

try:
    import psycopg2
    from psycopg2.extras import Json, execute_values
except ImportError:  # pragma: no cover - Docker/서버에서 설치 필요
    psycopg2 = None
    Json = None
    execute_values = None


def get_connection():
    if psycopg2 is None:
        raise RuntimeError(
            "psycopg2가 설치되어 있지 않습니다. requirements.naver.txt를 설치하세요."
        )
    if DATABASE_URL:
        return psycopg2.connect(DATABASE_URL)
    return psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
    )


def ensure_schema(conn) -> None:
    """상품·임베딩 작업 테이블 존재 확인."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT to_regclass('public.naver_product'),
                   to_regclass('public.product_embedding_job')
            """
        )
        product_table, job_table = cur.fetchone()
    if product_table is None or job_table is None:
        raise RuntimeError(
            "naver_product 또는 product_embedding_job 테이블이 없습니다. "
            "스키마는 Django migration이 관리합니다. "
            "api 컨테이너(또는 api/에서 `python manage.py migrate`)를 먼저 실행하세요."
        )
    logger.info("스키마 확인 완료 (naver_product, product_embedding_job 존재)")


PRODUCT_COLUMNS = [
    "naver_product_id", "product_type", "title", "title_raw", "link", "image_url",
    "lprice", "hprice", "mall_name", "brand", "maker",
    "naver_category1", "naver_category2", "naver_category3", "naver_category4",
    "category_large", "category_small", "category_source",
    "season", "style", "color", "pattern", "fit", "material", "sleeve", "length",
    "usage", "layer_role", "layer_order",
    "tag_source", "tagging_status", "tagging_model", "tagging_used_image", "tagged_at",
    "search_keyword", "raw_data", "collected_at",
]


def upsert_products(conn, rows: Sequence[tuple[Any, ...]]) -> int:
    """
    naver_product upsert. naver_product_id 충돌 시 가격/태그/메타를 갱신한다.
    rows의 각 원소는 PRODUCT_COLUMNS 순서의 튜플이어야 한다.
    """
    if not rows:
        return 0
    external_ids = [str(row[0]) for row in rows if row and row[0]]
    new_external_ids = find_new_external_ids(conn, "naver", external_ids)
    columns = ", ".join(PRODUCT_COLUMNS)
    sql = f"""
    INSERT INTO naver_product ({columns}) VALUES %s
    ON CONFLICT (naver_product_id)
    DO UPDATE SET
        product_type = EXCLUDED.product_type,
        title = EXCLUDED.title,
        title_raw = EXCLUDED.title_raw,
        link = EXCLUDED.link,
        image_url = EXCLUDED.image_url,
        lprice = EXCLUDED.lprice,
        hprice = EXCLUDED.hprice,
        mall_name = EXCLUDED.mall_name,
        brand = EXCLUDED.brand,
        maker = EXCLUDED.maker,
        naver_category1 = EXCLUDED.naver_category1,
        naver_category2 = EXCLUDED.naver_category2,
        naver_category3 = EXCLUDED.naver_category3,
        naver_category4 = EXCLUDED.naver_category4,
        category_large = EXCLUDED.category_large,
        category_small = EXCLUDED.category_small,
        category_source = EXCLUDED.category_source,
        season = EXCLUDED.season,
        style = EXCLUDED.style,
        color = EXCLUDED.color,
        pattern = EXCLUDED.pattern,
        fit = EXCLUDED.fit,
        material = EXCLUDED.material,
        sleeve = EXCLUDED.sleeve,
        length = EXCLUDED.length,
        usage = EXCLUDED.usage,
        layer_role = EXCLUDED.layer_role,
        layer_order = EXCLUDED.layer_order,
        tag_source = EXCLUDED.tag_source,
        tagging_status = EXCLUDED.tagging_status,
        tagging_model = EXCLUDED.tagging_model,
        tagging_used_image = EXCLUDED.tagging_used_image,
        tagged_at = EXCLUDED.tagged_at,
        search_keyword = EXCLUDED.search_keyword,
        raw_data = EXCLUDED.raw_data,
        collected_at = EXCLUDED.collected_at,
        updated_at = NOW()
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, rows)
    enqueue_new_products(conn, "naver", new_external_ids)
    conn.commit()
    return len(rows)


# ------------------------------------------------------------
# Batch API 태깅 지원
# tagging_status 흐름: pending → queued(배치 제출됨) → tagged | failed
# ------------------------------------------------------------


def fetch_pending_products(conn, limit: int) -> list[dict]:
    """배치 태깅 대상(pending) 상품 조회."""
    sql = """
    SELECT id, title, image_url,
           naver_category1, naver_category2, naver_category3, naver_category4,
           category_large, category_small
    FROM naver_product
    WHERE tagging_status = 'pending'
    ORDER BY id
    LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (limit,))
        col_names = [desc[0] for desc in cur.description]
        return [dict(zip(col_names, row)) for row in cur.fetchall()]


def set_products_tagging_status(conn, product_ids: list[int], status: str) -> None:
    if not product_ids:
        return
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE naver_product SET tagging_status = %s, updated_at = NOW() WHERE id = ANY(%s)",
            (status, product_ids),
        )
    conn.commit()


def reset_orphan_queued_products(conn) -> int:
    """진행 중 배치가 하나도 없을 때, queued로 남은 상품을 pending으로 되돌린다."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE naver_product SET tagging_status = 'pending', updated_at = NOW()
            WHERE tagging_status = 'queued'
              AND NOT EXISTS (
                  SELECT 1 FROM naver_tagging_batch
                  WHERE status IN ('submitted', 'validating', 'in_progress', 'finalizing')
              )
            """
        )
        count = cur.rowcount
    conn.commit()
    return count


def insert_tagging_batch(
    conn, batch_id: str, model: str, request_count: int, include_image: bool, input_file_id: str
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO naver_tagging_batch
                (batch_id, status, model, request_count, include_image, input_file_id)
            VALUES (%s, 'submitted', %s, %s, %s, %s)
            """,
            (batch_id, model, request_count, include_image, input_file_id),
        )
    conn.commit()


def fetch_open_tagging_batches(conn) -> list[dict]:
    sql = """
    SELECT id, batch_id, model, request_count, include_image
    FROM naver_tagging_batch
    WHERE status IN ('submitted', 'validating', 'in_progress', 'finalizing')
    ORDER BY id
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        col_names = [desc[0] for desc in cur.description]
        return [dict(zip(col_names, row)) for row in cur.fetchall()]


def update_tagging_batch(
    conn,
    batch_id: str,
    status: str,
    output_file_id: str | None = None,
    error_file_id: str | None = None,
    error: str | None = None,
    completed: bool = False,
) -> None:
    sql = """
    UPDATE naver_tagging_batch SET
        status = %s,
        output_file_id = COALESCE(%s, output_file_id),
        error_file_id = COALESCE(%s, error_file_id),
        error = COALESCE(%s, error),
        completed_at = CASE WHEN %s THEN NOW() ELSE completed_at END,
        updated_at = NOW()
    WHERE batch_id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (status, output_file_id, error_file_id, error, completed, batch_id))
    conn.commit()


def fetch_products_by_ids(conn, product_ids: list[int]) -> dict[int, dict]:
    """배치 결과 반영 시 규칙 추출 재계산용."""
    if not product_ids:
        return {}
    sql = "SELECT id, title FROM naver_product WHERE id = ANY(%s)"
    with conn.cursor() as cur:
        cur.execute(sql, (product_ids,))
        return {row[0]: {"id": row[0], "title": row[1]} for row in cur.fetchall()}


def fetch_products_for_retag(conn, limit: int = 500) -> list[dict]:
    """태깅 실패/미완료 상품 조회 (--job retag 용)."""
    sql = """
    SELECT id, naver_product_id, title, image_url,
           naver_category1, naver_category2, naver_category3, naver_category4,
           category_large, category_small
    FROM naver_product
    WHERE tagging_status IN ('pending', 'failed')
    ORDER BY id
    LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (limit,))
        col_names = [desc[0] for desc in cur.description]
        return [dict(zip(col_names, row)) for row in cur.fetchall()]


def update_product_tags(conn, product_id: int, tags: dict, meta: dict) -> None:
    """retag 결과 반영."""
    sql = """
    UPDATE naver_product SET
        season = %s, style = %s, color = %s, pattern = %s,
        fit = %s, material = %s, sleeve = %s, length = %s,
        usage = %s, layer_role = %s, layer_order = %s,
        tag_source = %s, tagging_status = %s, tagging_model = %s,
        tagging_used_image = %s, tagged_at = NOW(), updated_at = NOW()
    WHERE id = %s
    RETURNING naver_product_id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            tags.get("season", []), tags.get("style", []),
            tags.get("color", []), tags.get("pattern", []),
            tags.get("fit"), tags.get("material", []),
            tags.get("sleeve"), tags.get("length"),
            tags.get("usage", []), tags.get("layer_role"), tags.get("layer_order"),
            Json(meta.get("tag_source", {})), meta.get("tagging_status", "tagged"),
            meta.get("tagging_model"), meta.get("tagging_used_image", False),
            product_id,
        ))
        row = cur.fetchone()
    if row and meta.get("tagging_status", "tagged") == "tagged":
        requeue_existing_products(conn, "naver", [str(row[0])])
    conn.commit()
