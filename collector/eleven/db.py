"""11번가 collector PostgreSQL 저장 계층."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from config import (
    DATABASE_URL,
    DB_HOST,
    DB_NAME,
    DB_PASSWORD,
    DB_PORT,
    DB_USER,
    logger,
)
from util.embedding_jobs import (
    enqueue_new_products,
    find_new_external_ids,
    requeue_existing_products,
)

try:
    import psycopg2
    from psycopg2.extras import Json, execute_values
except ImportError:  # pragma: no cover
    psycopg2 = None
    Json = None
    execute_values = None


def get_connection():
    if psycopg2 is None:
        raise RuntimeError(
            "psycopg2가 설치되어 있지 않습니다. requirements.eleven.txt를 설치하세요."
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
    table_names = (
        "eleven_api_response",
        "eleven_category",
        "eleven_product",
        "eleven_tagging_batch",
        "product_embedding_job",
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename = ANY(%s)",
            (list(table_names),),
        )
        existing = {row[0] for row in cur.fetchall()}
    missing = set(table_names) - existing
    if missing:
        raise RuntimeError(
            f"11번가 테이블이 없습니다: {sorted(missing)}. "
            "api에서 python manage.py migrate를 먼저 실행하세요."
        )
    logger.info("스키마 확인 완료 (11번가 테이블 및 임베딩 작업 테이블 존재)")


def insert_api_response(
    conn,
    *,
    api_name: str,
    endpoint: str,
    request_params: dict[str, Any],
    response_status: int | None,
    content_type: str | None,
    raw_body: str,
    error_message: str | None,
    fetched_at: datetime,
) -> int:
    """API 키가 제거된 요청 정보와 원문 응답을 호출마다 저장한다."""
    response_hash = (
        hashlib.sha256(raw_body.encode("utf-8")).hexdigest() if raw_body else None
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO eleven_api_response (
                api_name, endpoint, http_method, request_params,
                response_status, content_type, raw_body, response_hash,
                error_message, fetched_at
            )
            VALUES (%s, %s, 'GET', %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                api_name,
                endpoint,
                Json(request_params),
                response_status,
                content_type,
                raw_body,
                response_hash,
                error_message,
                fetched_at,
            ),
        )
        response_id = cur.fetchone()[0]
    conn.commit()
    return response_id


def update_api_response_error(conn, response_id: int, error_message: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE eleven_api_response SET error_message = %s WHERE id = %s",
            (error_message[:4000], response_id),
        )
    conn.commit()


CATEGORY_COLUMNS = [
    "disp_no",
    "disp_nm",
    "parent_disp_no",
    "depth",
    "leaf_yn",
    "gbl_dlv_yn",
    "eng_disp_yn",
    "raw_data",
    "api_response_id",
    "collected_at",
]


def upsert_categories(
    conn,
    categories: Sequence[dict[str, Any]],
    api_response_id: int,
    collected_at: datetime,
    inactive_threshold: int,
) -> int:
    """전체 동기화 성공 시 보인 카테고리는 복구하고 누락 횟수를 갱신한다."""
    if not categories:
        raise ValueError("카테고리 응답이 비어 있어 비활성화 계산을 중단합니다.")

    rows = [
        (
            category["disp_no"],
            category["disp_nm"],
            category.get("parent_disp_no"),
            category.get("depth", 0),
            category.get("leaf_yn", False),
            category.get("gbl_dlv_yn"),
            category.get("eng_disp_yn"),
            Json(category.get("raw_data", {})),
            api_response_id,
            collected_at,
        )
        for category in categories
    ]
    columns = ", ".join(CATEGORY_COLUMNS)
    sql = f"""
    INSERT INTO eleven_category ({columns}) VALUES %s
    ON CONFLICT (disp_no) DO UPDATE SET
        disp_nm = EXCLUDED.disp_nm,
        parent_disp_no = EXCLUDED.parent_disp_no,
        depth = EXCLUDED.depth,
        leaf_yn = EXCLUDED.leaf_yn,
        gbl_dlv_yn = EXCLUDED.gbl_dlv_yn,
        eng_disp_yn = EXCLUDED.eng_disp_yn,
        is_active = TRUE,
        missing_count = 0,
        raw_data = EXCLUDED.raw_data,
        api_response_id = EXCLUDED.api_response_id,
        collected_at = EXCLUDED.collected_at,
        updated_at = NOW()
    """
    seen_disp_nos = [category["disp_no"] for category in categories]
    with conn.cursor() as cur:
        execute_values(cur, sql, rows)
        cur.execute(
            """
            UPDATE eleven_category
            SET
                missing_count = missing_count + 1,
                is_active = CASE
                    WHEN missing_count + 1 >= %s THEN FALSE
                    ELSE is_active
                END,
                updated_at = NOW()
            WHERE NOT (disp_no = ANY(%s))
            """,
            (inactive_threshold, seen_disp_nos),
        )
    conn.commit()
    return len(rows)


def load_category_paths(conn) -> dict[str, list[str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT disp_no, disp_nm, parent_disp_no
            FROM eleven_category
            WHERE is_active = TRUE
            """
        )
        nodes = {
            row[0]: {"name": row[1], "parent": row[2]} for row in cur.fetchall()
        }

    paths: dict[str, list[str]] = {}
    for disp_no in nodes:
        path: list[str] = []
        visited: set[str] = set()
        current = disp_no
        while current in nodes and current not in visited:
            visited.add(current)
            node = nodes[current]
            path.append(node["name"])
            current = node["parent"]
        paths[disp_no] = list(reversed(path))
    return paths


PRODUCT_COLUMNS = [
    "eleven_product_id",
    "title",
    "title_raw",
    "link",
    "image_url",
    "product_price",
    "sale_price",
    "mall_name",
    "rating",
    "review_count",
    "buy_satisfy",
    "delivery",
    "benefit",
    "eleven_category1",
    "eleven_category2",
    "eleven_category3",
    "eleven_category4",
    "eleven_category_disp_no",
    "category_large",
    "category_small",
    "category_source",
    "category_mapping_version",
    "season",
    "style",
    "color",
    "pattern",
    "fit",
    "material",
    "sleeve",
    "length",
    "usage",
    "layer_role",
    "layer_order",
    "tag_source",
    "tagging_status",
    "tagging_model",
    "tagging_used_image",
    "tagged_at",
    "search_keyword",
    "search_sort",
    "search_rank",
    "page_num",
    "raw_data",
    "api_response_id",
    "collected_at",
]


def upsert_products(conn, rows: Sequence[tuple[Any, ...]]) -> int:
    if not rows:
        return 0
    columns = ", ".join(PRODUCT_COLUMNS)
    sql = f"""
    INSERT INTO eleven_product ({columns}) VALUES %s
    ON CONFLICT (eleven_product_id) DO NOTHING
    RETURNING eleven_product_id
    """
    with conn.cursor() as cur:
        inserted_rows = execute_values(cur, sql, rows, fetch=True)
    inserted_ids = [str(row[0]) for row in inserted_rows]
    enqueue_new_products(conn, "eleven", inserted_ids)
    conn.commit()
    return len(inserted_ids)


def find_new_product_ids(conn, external_ids: Sequence[str]) -> list[str]:
    """입력 순서를 유지하며 DB에 아직 없는 11번가 상품 ID만 반환한다."""
    return find_new_external_ids(conn, "eleven", external_ids)


def fetch_pending_products(conn, limit: int) -> list[dict[str, Any]]:
    """OpenAI Batch 제출 대상인 pending 상품을 조회한다."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, image_url,
                   eleven_category1, eleven_category2,
                   eleven_category3, eleven_category4,
                   category_large, category_small
            FROM eleven_product
            WHERE tagging_status = 'pending'
            ORDER BY id
            LIMIT %s
            """,
            (limit,),
        )
        names = [description[0] for description in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def set_products_tagging_status(
    conn, product_ids: list[int], status: str
) -> None:
    if not product_ids:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE eleven_product
            SET tagging_status = %s, updated_at = NOW()
            WHERE id = ANY(%s)
            """,
            (status, product_ids),
        )
    conn.commit()


def reset_orphan_queued_products(conn) -> int:
    """진행 중 Batch가 없을 때 고아 queued 상품을 pending으로 복구한다."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE eleven_product
            SET tagging_status = 'pending', updated_at = NOW()
            WHERE tagging_status = 'queued'
              AND NOT EXISTS (
                  SELECT 1 FROM eleven_tagging_batch
                  WHERE status IN (
                      'submitted', 'validating', 'in_progress', 'finalizing'
                  )
              )
            """
        )
        count = cur.rowcount
    conn.commit()
    return count


def insert_tagging_batch(
    conn,
    batch_id: str,
    model: str,
    request_count: int,
    include_image: bool,
    input_file_id: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO eleven_tagging_batch (
                batch_id, status, model, request_count,
                include_image, input_file_id
            )
            VALUES (%s, 'submitted', %s, %s, %s, %s)
            """,
            (
                batch_id,
                model,
                request_count,
                include_image,
                input_file_id,
            ),
        )
    conn.commit()


def fetch_open_tagging_batches(conn) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, batch_id, model, request_count, include_image
            FROM eleven_tagging_batch
            WHERE status IN (
                'submitted', 'validating', 'in_progress', 'finalizing'
            )
            ORDER BY id
            """
        )
        names = [description[0] for description in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def update_tagging_batch(
    conn,
    batch_id: str,
    status: str,
    output_file_id: str | None = None,
    error_file_id: str | None = None,
    error: str | None = None,
    completed: bool = False,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE eleven_tagging_batch SET
                status = %s,
                output_file_id = COALESCE(%s, output_file_id),
                error_file_id = COALESCE(%s, error_file_id),
                error = COALESCE(%s, error),
                completed_at = CASE WHEN %s THEN NOW() ELSE completed_at END,
                updated_at = NOW()
            WHERE batch_id = %s
            """,
            (
                status,
                output_file_id,
                error_file_id,
                error,
                completed,
                batch_id,
            ),
        )
    conn.commit()


def fetch_products_by_ids(
    conn, product_ids: list[int]
) -> dict[int, dict[str, Any]]:
    if not product_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, title FROM eleven_product WHERE id = ANY(%s)",
            (product_ids,),
        )
        return {
            row[0]: {"id": row[0], "title": row[1]}
            for row in cur.fetchall()
        }


def fetch_products_for_retag(conn, limit: int) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, image_url,
                   eleven_category1, eleven_category2,
                   eleven_category3, eleven_category4,
                   category_large, category_small
            FROM eleven_product
            WHERE tagging_status IN ('pending', 'failed')
            ORDER BY id
            LIMIT %s
            """,
            (limit,),
        )
        names = [description[0] for description in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def update_product_tags(
    conn, product_id: int, tags: dict[str, Any], meta: dict[str, Any]
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE eleven_product SET
                season = %s, style = %s, color = %s, pattern = %s,
                fit = %s, material = %s, sleeve = %s, length = %s,
                usage = %s, layer_role = %s, layer_order = %s,
                tag_source = %s, tagging_status = %s, tagging_model = %s,
                tagging_used_image = %s, tagged_at = NOW(), updated_at = NOW()
            WHERE id = %s
            RETURNING eleven_product_id
            """,
            (
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
                Json(meta.get("tag_source", {})),
                meta.get("tagging_status", "tagged"),
                meta.get("tagging_model"),
                meta.get("tagging_used_image", False),
                product_id,
            ),
        )
        row = cur.fetchone()
    if row and meta.get("tagging_status", "tagged") == "tagged":
        requeue_existing_products(conn, "eleven", [str(row[0])])
    conn.commit()
