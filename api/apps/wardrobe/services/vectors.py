"""Qdrant 벡터 upsert (파생 저장소).

- 컬렉션: wardrobe_items, named vector 2종
  image(768d, FashionSigLIP) / text(1024d, 캡션 임베딩)
- payload의 user_id는 테넌트 필터 — 검색 쿼리에서 반드시 강제한다.
- DB 저장이 성공한 뒤 호출되며, 실패해도 콜백 처리를 막지 않는다(best-effort).
  실패 아이템은 embedding_version이 비어 있으므로 배치 재색인으로 복구한다.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

from django.conf import settings
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from apps.recommend.services.qdrant import (
    collection_spec,
    ensure_collection_contract,
    get_client,
)

logger = logging.getLogger(__name__)

COLLECTION = settings.QDRANT_WARDROBE_COLLECTION
IMAGE_DIM = settings.QDRANT_IMAGE_VECTOR_DIM
TEXT_DIM = settings.QDRANT_TEXT_VECTOR_DIM
EMBEDDING_VERSION = os.getenv("WARDROBE_EMBEDDING_VERSION", "fashionsiglip-v1")


@lru_cache(maxsize=1)
def _client() -> QdrantClient:
    return get_client()


def ensure_collection() -> None:
    ensure_collection_contract(_client(), collection_spec("wardrobe"))


def _tag_payload(item) -> dict:
    """시각·스타일 검색이 공통으로 읽는 최신 옷 태그 payload."""

    return {
        "item_name": item.item_name,
        "category_large": item.category_large,
        "category_small": item.category_small,
        "season": item.season,
        "style": item.style,
        "color": item.color,
        "pattern": item.pattern,
        "fit": item.fit,
        "material": item.material,
        "sleeve": item.sleeve,
        "length": item.length,
        "usage": item.usage,
        "layer_role": item.layer_role,
        "layer_order": item.layer_order,
        "confirmed": item.confirmed,
    }


def upsert_item(item, image_vector: list[float] | None,
                text_vector: list[float] | None) -> bool:
    """아이템 벡터 upsert. 성공 시 True. 벡터가 하나도 없으면 건너뛴다."""
    vectors: dict[str, list[float]] = {}
    if image_vector:
        vectors["image"] = image_vector
    if text_vector:
        vectors["text"] = text_vector
    if not vectors:
        return False

    try:
        ensure_collection()
        _client().upsert(
            collection_name=COLLECTION,
            points=[
                PointStruct(
                    id=str(item.id),
                    vector=vectors,
                    payload={
                        "user_id": item.user_id,
                        "item_id": str(item.id),
                        "s3_key": item.s3_key,
                        "embedding_version": EMBEDDING_VERSION,
                        **_tag_payload(item),
                    },
                )
            ],
        )
        return True
    except Exception:  # 파생 저장소 실패는 콜백을 막지 않는다.
        logger.exception("Qdrant upsert 실패: item=%s", item.id)
        return False


def update_payload(item) -> None:
    """태깅 수정·확정 시 payload 동기화 (best-effort)."""
    # 아직 임베딩되지 않은 아이템은 Qdrant point가 없으므로 갱신하지 않는다.
    # 이후 재색인 시 최신 DB 값을 기반으로 payload가 생성된다.
    if not item.embedding_version:
        return

    try:
        ensure_collection()
        _client().set_payload(
            collection_name=COLLECTION,
            payload=_tag_payload(item),
            points=[str(item.id)],
        )
    except Exception:
        logger.exception("Qdrant payload 갱신 실패: item=%s", item.id)


def delete_item(item_id) -> None:
    """아이템 삭제 시 벡터도 제거 (best-effort)."""
    try:
        _client().delete(collection_name=COLLECTION, points_selector=[str(item_id)])
    except Exception:
        logger.exception("Qdrant 삭제 실패: item=%s", item_id)
