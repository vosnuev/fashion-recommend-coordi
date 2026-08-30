"""Qdrant 적재 계층 (REST API).

컬렉션 스키마:
- named vector "image": 이미지 임베딩 (cosine)
- named vector "text" : 텍스트 임베딩 (cosine, mdata 있는 아이템만)
- payload: item_id / part / category / features / s3 위치 등
- point id: uuid5(item_id) — 재실행 시 같은 아이템은 덮어써져 멱등하다.
"""

from __future__ import annotations

import logging
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from .config import QDRANT_API_KEY, QDRANT_COLLECTION, QDRANT_URL

logger = logging.getLogger(__name__)

_ID_NAMESPACE = uuid.NAMESPACE_URL


def point_id(dataset: str, item_id: str) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, f"{dataset}:{item_id}"))


def make_client() -> QdrantClient:
    # prefer_grpc=False → REST API 사용 (요구사항)
    # port=None: 포트 없는 URL에 qdrant-client가 6333을 붙이는 것을 막는다
    # (리버스 프록시 뒤 https 엔드포인트에서 필수 — product_qdrant.make_client 참고)
    return QdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API_KEY,
        port=None,
        prefer_grpc=False,
    )


def ensure_collection(client: QdrantClient, dim: int, recreate: bool = False) -> None:
    exists = client.collection_exists(QDRANT_COLLECTION)
    if exists and recreate:
        logger.warning("컬렉션 재생성: %s", QDRANT_COLLECTION)
        client.delete_collection(QDRANT_COLLECTION)
        exists = False
    if exists:
        return

    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config={
            "image": VectorParams(size=dim, distance=Distance.COSINE),
            "text": VectorParams(size=dim, distance=Distance.COSINE),
        },
    )
    # 카테고리/구분 필터 검색 대비 payload 인덱스
    for field in ("item_id", "category", "part", "dataset"):
        client.create_payload_index(
            collection_name=QDRANT_COLLECTION,
            field_name=field,
            field_schema=PayloadSchemaType.KEYWORD,
        )
    logger.info("컬렉션 생성 완료: %s (dim=%d)", QDRANT_COLLECTION, dim)


def upsert_points(client: QdrantClient, points: list[PointStruct]) -> None:
    if points:
        client.upsert(collection_name=QDRANT_COLLECTION, points=points, wait=True)
