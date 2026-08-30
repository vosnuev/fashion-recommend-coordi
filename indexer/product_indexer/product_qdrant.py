"""네이버·11번가 상품용 Qdrant 컬렉션과 적재 계층."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

logger = logging.getLogger(__name__)
_ID_NAMESPACE = uuid.NAMESPACE_URL


def product_point_id(source: str, external_product_id: str) -> str:
    return str(
        uuid.uuid5(
            _ID_NAMESPACE,
            f"shopping-product:{source}:{external_product_id}",
        )
    )


def make_client(url: str, api_key: str | None) -> QdrantClient:
    """QDRANT_URL을 그대로 존중하는 REST 클라이언트를 만든다.

    port=None이 핵심이다. qdrant-client의 port 기본값은 6333이라, 포트가 없는
    URL을 주면 스킴의 기본 포트(443/80)가 아니라 6333을 붙여버린다.
        https://qdrant.example.com  →  https://qdrant.example.com:6333
    리버스 프록시(cloudflared·ALB·nginx) 뒤에 Qdrant를 두면 엣지는 443만 열려
    있으므로 "[Errno 101] Network is unreachable"로 실패한다.

    port=None이면 URL을 손대지 않고, URL에 포트가 명시돼 있으면 그 값을 쓴다.
    따라서 프록시가 없는 환경에서는 QDRANT_URL에 포트를 명시해야 한다
    (예: http://qdrant-host:6333 — .env 템플릿의 기본값도 포트를 포함한다).
    """
    return QdrantClient(
        url=url,
        api_key=api_key,
        port=None,
        prefer_grpc=False,
    )


def _vector_size(vectors_config: Any, name: str) -> int | None:
    if isinstance(vectors_config, dict):
        vector = vectors_config.get(name)
        return int(vector.size) if vector is not None else None
    return None


def ensure_collection(
    client: QdrantClient,
    collection_name: str,
    *,
    image_dim: int,
    text_dim: int,
) -> None:
    if client.collection_exists(collection_name):
        info = client.get_collection(collection_name)
        vectors_config = info.config.params.vectors
        actual_image_dim = _vector_size(vectors_config, "image")
        actual_text_dim = _vector_size(vectors_config, "text")
        if (actual_image_dim, actual_text_dim) != (image_dim, text_dim):
            raise RuntimeError(
                f"Qdrant 컬렉션 {collection_name} 벡터 차원이 다릅니다: "
                f"actual image={actual_image_dim}, text={actual_text_dim}; "
                f"expected image={image_dim}, text={text_dim}"
            )
        return

    client.create_collection(
        collection_name=collection_name,
        vectors_config={
            "image": VectorParams(size=image_dim, distance=Distance.COSINE),
            "text": VectorParams(size=text_dim, distance=Distance.COSINE),
        },
    )
    for field in (
        "source",
        "external_product_id",
        "category_large",
        "category_small",
        "brand",
        "tagging_status",
        "embedding_version",
    ):
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field,
            field_schema=PayloadSchemaType.KEYWORD,
        )
    logger.info(
        "상품 컬렉션 생성 완료: %s (image=%d, text=%d)",
        collection_name,
        image_dim,
        text_dim,
    )


def build_point(
    *,
    source: str,
    external_product_id: str,
    image_vector: list[float],
    text_vector: list[float],
    payload: dict[str, Any],
) -> PointStruct:
    return PointStruct(
        id=product_point_id(source, external_product_id),
        vector={
            "image": image_vector,
            "text": text_vector,
        },
        payload=payload,
    )


def upsert_points(
    client: QdrantClient,
    collection_name: str,
    points: list[PointStruct],
) -> None:
    if points:
        client.upsert(
            collection_name=collection_name,
            points=points,
            wait=True,
        )
