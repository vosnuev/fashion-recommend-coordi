"""추천 도메인이 사용하는 Qdrant 컬렉션 계약.

PostgreSQL 스키마를 Django migration이 소유하듯 추천 API가 조회하는 Qdrant
컬렉션의 이름, named vector, payload index 계약은 이 모듈에서 관리한다.

실제 벡터 적재는 골든셋 파이프라인, 상품 indexer, wardrobe worker가 각각
담당한다. 이 모듈은 모든 생산자와 검색 계층이 같은 계약을 쓰도록 컬렉션을
초기화하고, 이미 존재하는 컬렉션의 호환성을 검사한다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from django.conf import settings
from qdrant_client import QdrantClient
from qdrant_client import models as qm

IMAGE_VECTOR = "image"
TEXT_VECTOR = "text"

# Retriever와 적재기가 공유하는 컬렉션 이름. 검색 코드가 문자열 리터럴을
# 직접 사용하지 않도록 이전 golenset_new 인터페이스도 유지한다.
GOLDEN_OUTFIT_COLLECTION = settings.QDRANT_GOLDEN_OUTFIT_COLLECTION
GOLDEN_ITEM_COLLECTION = settings.QDRANT_GOLDEN_ITEM_COLLECTION
GOLDEN_KNOWLEDGE_COLLECTION = settings.QDRANT_KNOWLEDGE_COLLECTION
# 기존 feature 브랜치가 직접 참조하던 상수도 유지하되, 컬렉션 이름의 단일
# 출처는 Django 설정으로 둔다. 환경변수는 settings 로딩 단계에서 반영된다.
WARDROBE_ITEM_COLLECTION = settings.QDRANT_WARDROBE_COLLECTION

# point ID 생성용 고정 네임스페이스. 같은 원본 키는 항상 같은 UUID가 되어
# 재실행 시 upsert가 멱등하게 동작한다. 절대 변경하지 않는다.
_POINT_NAMESPACE = uuid.UUID("6b2c1f3a-9d4e-4c8b-8a71-2f0e5d9c3b17")


class QdrantContractError(RuntimeError):
    """기존 컬렉션을 안전하게 자동 보정할 수 없을 때 발생한다."""


def point_id(source_key: str) -> str:
    """원본 식별자를 결정적 Qdrant UUID로 변환한다."""
    return str(uuid.uuid5(_POINT_NAMESPACE, source_key))


@dataclass(frozen=True)
class CollectionSpec:
    """컬렉션의 named vector와 필터용 payload index 계약."""

    role: str
    name: str
    vectors: dict[str, int]
    payload_indexes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CollectionStatus:
    """실제 컬렉션을 계약과 비교한 결과."""

    role: str
    name: str
    exists: bool
    vector_mismatches: tuple[str, ...] = ()
    missing_payload_indexes: tuple[str, ...] = ()
    payload_index_mismatches: tuple[str, ...] = ()
    points_count: int | None = None

    @property
    def valid(self) -> bool:
        return (
            self.exists
            and not self.vector_mismatches
            and not self.missing_payload_indexes
            and not self.payload_index_mismatches
        )


def _image_dim() -> int:
    return settings.QDRANT_IMAGE_VECTOR_DIM


def _text_dim() -> int:
    return settings.QDRANT_TEXT_VECTOR_DIM


def _item_vectors() -> dict[str, int]:
    return {IMAGE_VECTOR: _image_dim(), TEXT_VECTOR: _text_dim()}


# 골든·옷장·상품 아이템은 같은 필터 언어로 교차 검색할 수 있어야 한다.
_ITEM_TAG_INDEXES: dict[str, str] = {
    "category_large": "keyword",
    "category_small": "keyword",
    "layer_role": "keyword",
    "season": "keyword",
    "style": "keyword",
    "occasion": "keyword",
    "color": "keyword",
    "fit": "keyword",
    "sleeve": "keyword",
    "length": "keyword",
    "pattern": "keyword",
    "material": "keyword",
}

_DATASET_INDEXES: dict[str, str] = {
    "dataset_version": "keyword",
    # golenset_new 적재본은 status를, 채팅 설계 이후 적재본은
    # dataset_status를 사용한다. 재색인이 끝날 때까지 두 계약을 함께 읽는다.
    "status": "keyword",
    "dataset_status": "keyword",
}


def collection_specs() -> list[CollectionSpec]:
    """환경별 실제 이름을 반영한 전체 추천 컬렉션 계약을 반환한다."""

    specs = [
        CollectionSpec(
            role="golden_outfits",
            name=settings.QDRANT_GOLDEN_OUTFIT_COLLECTION,
            vectors=_item_vectors(),
            payload_indexes={
                **_DATASET_INDEXES,
                "source": "keyword",
                "golden_id": "keyword",
                "split": "keyword",
                "presentation_group": "keyword",
                "style": "keyword",
                "season": "keyword",
                "occasion": "keyword",
                "score_band": "keyword",
                "human_score": "float",
                "anchor_scope": "keyword",
                "item_layer_roles": "keyword",
                "item_categories": "keyword",
                "exposable": "bool",
            },
        ),
        CollectionSpec(
            role="golden_items",
            name=settings.QDRANT_GOLDEN_ITEM_COLLECTION,
            vectors=_item_vectors(),
            payload_indexes={
                **_ITEM_TAG_INDEXES,
                **_DATASET_INDEXES,
                "source": "keyword",
                "golden_id": "keyword",
                "item_key": "keyword",
                "outfit_golden_id": "keyword",
                "outfit_point_id": "keyword",
                "split": "keyword",
                "presentation_group": "keyword",
                "exposable": "bool",
            },
        ),
        CollectionSpec(
            role="wardrobe",
            name=WARDROBE_ITEM_COLLECTION,
            vectors=_item_vectors(),
            payload_indexes={
                **_ITEM_TAG_INDEXES,
                "user_id": "integer",
                "item_id": "keyword",
                "confirmed": "bool",
                "embedding_version": "keyword",
            },
        ),
        CollectionSpec(
            role="products_naver",
            name=settings.PRODUCT_NAVER_QDRANT_COLLECTION,
            vectors=_item_vectors(),
            payload_indexes={
                **_ITEM_TAG_INDEXES,
                "source": "keyword",
                "external_product_id": "keyword",
                "brand": "keyword",
                "tagging_status": "keyword",
                "embedding_version": "keyword",
                "usage": "keyword",
                "price": "integer",
            },
        ),
        CollectionSpec(
            role="products_eleven",
            name=settings.PRODUCT_ELEVEN_QDRANT_COLLECTION,
            vectors=_item_vectors(),
            payload_indexes={
                **_ITEM_TAG_INDEXES,
                "source": "keyword",
                "external_product_id": "keyword",
                "brand": "keyword",
                "tagging_status": "keyword",
                "embedding_version": "keyword",
                "usage": "keyword",
                "price": "integer",
            },
        ),
        CollectionSpec(
            role="knowledge",
            name=settings.QDRANT_KNOWLEDGE_COLLECTION,
            vectors={TEXT_VECTOR: _text_dim()},
            payload_indexes={
                "knowledge_type": "keyword",
                "dimension": "keyword",
                "axis": "keyword",
                "status": "keyword",
                "knowledge_role": "keyword",
                "principle_type": "keyword",
                "eligible_for_scoring": "bool",
                "source": "keyword",
                "dataset_version": "keyword",
                "style": "keyword",
                "body_type": "keyword",
                "skin_tone": "keyword",
                "season": "keyword",
                "occasion": "keyword",
            },
        ),
    ]
    _validate_unique_names(specs)
    return specs


def collection_names_by_role() -> dict[str, str]:
    """검색 계층이 문자열 리터럴 없이 컬렉션을 선택하도록 역할별 이름을 제공한다."""
    return {spec.role: spec.name for spec in collection_specs()}


def collection_spec(role: str) -> CollectionSpec:
    """논리 역할에 해당하는 컬렉션 계약 하나를 반환한다."""
    for spec in collection_specs():
        if spec.role == role:
            return spec
    raise KeyError(f"정의되지 않은 Qdrant 컬렉션 역할: {role}")


def product_collection_names() -> tuple[str, ...]:
    """상품 검색 시 함께 조회해야 하는 쇼핑몰별 컬렉션 이름."""
    roles = collection_names_by_role()
    return roles["products_naver"], roles["products_eleven"]


def _validate_unique_names(specs: list[CollectionSpec]) -> None:
    empty_roles = sorted(spec.role for spec in specs if not spec.name)
    if empty_roles:
        raise QdrantContractError(
            "Qdrant 컬렉션 이름이 비어 있습니다: " + ", ".join(empty_roles)
        )

    names = [spec.name for spec in specs]
    duplicated = sorted({name for name in names if names.count(name) > 1})
    if duplicated:
        raise QdrantContractError(
            "서로 다른 역할의 Qdrant 컬렉션 이름이 중복되었습니다: "
            + ", ".join(duplicated)
        )


@lru_cache(maxsize=1)
def get_client() -> QdrantClient:
    """프로세스당 하나의 REST 클라이언트를 재사용한다."""
    return QdrantClient(
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY or None,
        timeout=settings.QDRANT_TIMEOUT,
        port=None,
        prefer_grpc=False,
    )


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw).lower()


def _actual_vector_dimensions(info: Any) -> dict[str, int] | None:
    """실제 벡터 차원을 읽는다.

    운영 Qdrant 응답에는 config가 항상 있지만 일부 구버전 클라이언트 응답과
    테스트 더블에는 payload_schema만 있을 수 있다. 그 경우 차원이 다르다고
    단정하지 않고 검증 불가(None)로 두되 payload index 보완은 계속한다.
    """
    config = getattr(info, "config", None)
    params = getattr(config, "params", None)
    if params is None or not hasattr(params, "vectors"):
        return None
    vectors = params.vectors
    if not isinstance(vectors, dict):
        return {}
    return {name: int(config.size) for name, config in vectors.items()}


def _actual_payload_indexes(info: Any) -> dict[str, str]:
    schema = getattr(info, "payload_schema", None) or {}
    result: dict[str, str] = {}
    for name, config in schema.items():
        data_type = getattr(config, "data_type", config)
        result[name] = _enum_value(data_type)
    return result


def inspect_collection(client: QdrantClient, spec: CollectionSpec) -> CollectionStatus:
    """Qdrant를 변경하지 않고 컬렉션 한 개를 계약과 비교한다."""
    if not client.collection_exists(spec.name):
        return CollectionStatus(role=spec.role, name=spec.name, exists=False)

    info = client.get_collection(spec.name)
    actual_vectors = _actual_vector_dimensions(info)
    vector_mismatches = (
        tuple(
            f"{name}: expected={expected}, actual={actual_vectors.get(name)}"
            for name, expected in spec.vectors.items()
            if actual_vectors.get(name) != expected
        )
        if actual_vectors is not None
        else ()
    )
    unexpected_vectors = (
        tuple(
            f"{name}: expected=absent, actual={actual}"
            for name, actual in actual_vectors.items()
            if name not in spec.vectors
        )
        if actual_vectors is not None
        else ()
    )

    actual_indexes = _actual_payload_indexes(info)
    missing_indexes = tuple(
        name for name in spec.payload_indexes if name not in actual_indexes
    )
    index_mismatches = tuple(
        f"{name}: expected={expected}, actual={actual_indexes[name]}"
        for name, expected in spec.payload_indexes.items()
        if name in actual_indexes and actual_indexes[name] != expected
    )
    return CollectionStatus(
        role=spec.role,
        name=spec.name,
        exists=True,
        vector_mismatches=vector_mismatches + unexpected_vectors,
        missing_payload_indexes=missing_indexes,
        payload_index_mismatches=index_mismatches,
        points_count=getattr(info, "points_count", None),
    )


def inspect_collections(client: QdrantClient) -> list[CollectionStatus]:
    """전체 컬렉션 계약 상태를 조회한다."""
    return [inspect_collection(client, spec) for spec in collection_specs()]


def _create_collection(client: QdrantClient, spec: CollectionSpec) -> None:
    client.create_collection(
        collection_name=spec.name,
        vectors_config={
            vector_name: qm.VectorParams(size=dimension, distance=qm.Distance.COSINE)
            for vector_name, dimension in spec.vectors.items()
        },
    )
    for field_name, schema in spec.payload_indexes.items():
        client.create_payload_index(
            collection_name=spec.name,
            field_name=field_name,
            field_schema=schema,
        )


def _raise_incompatible(status: CollectionStatus) -> None:
    issues = [*status.vector_mismatches, *status.payload_index_mismatches]
    if issues:
        raise QdrantContractError(
            f"Qdrant 컬렉션 '{status.name}' 계약이 호환되지 않습니다: "
            + "; ".join(issues)
            + ". 데이터를 보존한 채 자동 변경할 수 없으므로 재색인 계획을 먼저 세워야 합니다."
        )


def ensure_collection_contract(client: QdrantClient, spec: CollectionSpec) -> bool:
    """컬렉션 하나를 계약에 맞추고 새로 생성했는지 반환한다."""
    status = inspect_collection(client, spec)
    if not status.exists:
        _create_collection(client, spec)
        return True

    _raise_incompatible(status)
    for field_name in status.missing_payload_indexes:
        client.create_payload_index(
            collection_name=spec.name,
            field_name=field_name,
            field_schema=spec.payload_indexes[field_name],
        )
    return False


def ensure_collections(client: QdrantClient, *, recreate: bool = False) -> list[str]:
    """컬렉션을 생성하고 기존 컬렉션에는 누락된 payload index만 보완한다.

    벡터 차원이나 기존 payload index 타입이 다른 경우 데이터 손실 가능성이 있어
    자동 변경하지 않고 :class:`QdrantContractError`를 발생시킨다.
    """
    created: list[str] = []
    for spec in collection_specs():
        if recreate and client.collection_exists(spec.name):
            client.delete_collection(spec.name)

        if ensure_collection_contract(client, spec):
            created.append(spec.name)
    return created
