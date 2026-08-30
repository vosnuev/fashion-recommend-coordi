"""쇼핑 상품 임베딩 작업 등록 공용 유틸리티.

이 모듈은 모델 추론이나 Qdrant에 의존하지 않는다. 네이버/11번가 collector가
상품 INSERT와 같은 PostgreSQL 트랜잭션에서 작업을 등록하는 역할만 담당한다.
실제 임베딩 작업 선점과 상태 변경은 catalog 내부 API가 담당하고, 별도 indexer
프로세스는 해당 API를 호출해 임베딩과 Qdrant 적재를 수행한다.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

EMBEDDING_VERSION = os.getenv(
    "PRODUCT_EMBEDDING_VERSION",
    "marqo-fashionSigLIP+bge-m3-v1",
).strip()

_SOURCE_TABLES = {
    "naver": ("naver_product", "naver_product_id"),
    "eleven": ("eleven_product", "eleven_product_id"),
}


def _source_table(source: str) -> tuple[str, str]:
    try:
        return _SOURCE_TABLES[source]
    except KeyError as exc:
        raise ValueError(f"지원하지 않는 상품 source: {source}") from exc


def find_new_external_ids(
    conn,
    source: str,
    external_ids: Sequence[str],
) -> list[str]:
    """upsert 전에 DB에 없는 외부 상품 ID만 반환한다."""
    table, id_column = _source_table(source)
    unique_ids = list(dict.fromkeys(str(value) for value in external_ids if value))
    if not unique_ids:
        return []

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {id_column} FROM {table} WHERE {id_column} = ANY(%s)",
            (unique_ids,),
        )
        existing = {str(row[0]) for row in cur.fetchall()}
    return [external_id for external_id in unique_ids if external_id not in existing]


def enqueue_new_products(
    conn,
    source: str,
    external_ids: Sequence[str],
    target_version: str = EMBEDDING_VERSION,
) -> int:
    """신규 상품의 임베딩 작업을 등록한다.

    ON CONFLICT DO NOTHING으로 source+외부 상품 ID 기준 멱등성을 보장한다.
    기존 DB 상품은 이 함수를 호출하기 전에 걸러지므로 자동 백필되지 않는다.
    """
    table, id_column = _source_table(source)
    unique_ids = list(dict.fromkeys(str(value) for value in external_ids if value))
    if not unique_ids:
        return 0

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO product_embedding_job (
                source, external_product_id, status, target_version
            )
            VALUES (%s, %s, 'pending', %s)
            ON CONFLICT (source, external_product_id) DO NOTHING
            """,
            [(source, external_id, target_version) for external_id in unique_ids],
        )
        cur.execute(
            f"""
            UPDATE {table}
            SET embedding_status = 'pending',
                embedding_retry_count = 0,
                embedding_error = NULL,
                updated_at = NOW()
            WHERE {id_column} = ANY(%s)
            """,
            (unique_ids,),
        )
    return len(unique_ids)


def requeue_existing_products(
    conn,
    source: str,
    external_ids: Sequence[str],
    target_version: str = EMBEDDING_VERSION,
) -> int:
    """태깅 완료 후 이미 등록된 신규 상품 작업만 다시 pending으로 만든다.

    작업 행이 없는 과거 상품은 UPDATE 대상이 아니므로 기존 데이터 백필을
    의도치 않게 시작하지 않는다. generation을 증가시켜 처리 중이던 worker가
    이전 태그로 완료 상태를 덮어쓰는 경쟁 조건을 막는다.
    """
    table, id_column = _source_table(source)
    unique_ids = list(dict.fromkeys(str(value) for value in external_ids if value))
    if not unique_ids:
        return 0

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE product_embedding_job
            SET status = 'pending',
                target_version = %s,
                generation = generation + 1,
                attempt_count = 0,
                last_error = NULL,
                available_at = NOW(),
                claimed_at = NULL,
                completed_at = NULL,
                updated_at = NOW()
            WHERE source = %s
              AND external_product_id = ANY(%s)
            RETURNING external_product_id
            """,
            (target_version, source, unique_ids),
        )
        requeued_ids = [str(row[0]) for row in cur.fetchall()]
        if requeued_ids:
            cur.execute(
                f"""
                UPDATE {table}
                SET embedding_status = 'pending',
                    embedding_retry_count = 0,
                    embedding_error = NULL,
                    updated_at = NOW()
                WHERE {id_column} = ANY(%s)
                """,
                (requeued_ids,),
            )
    return len(requeued_ids)
