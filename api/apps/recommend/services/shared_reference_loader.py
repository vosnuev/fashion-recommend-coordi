"""채팅 실행의 공유 옷 스냅샷을 검증된 벡터 검색 기준으로 변환한다."""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from apps.recommend.services.qdrant import (
    IMAGE_VECTOR,
    TEXT_VECTOR,
    collection_spec,
    get_client,
)

REFERENCE_SCHEMA_VERSION = "1.0"
REFERENCE_TYPE_SHARED = "SHARED_WARDROBE_ITEM"
REFERENCE_TYPE_OWNED = "WARDROBE_ITEM"
REFERENCE_TYPES = {REFERENCE_TYPE_SHARED, REFERENCE_TYPE_OWNED}
_ARRAY_TAGS = ("season", "style", "usage")
_STRING_TAGS = (
    "item_name",
    "category_large",
    "category_small",
    "color",
    "pattern",
    "fit",
    "material",
    "sleeve",
    "length",
    "layer_role",
)
_INDEXED_TAGS = (
    "category_large",
    "category_small",
    "season",
    "style",
    "color",
    "pattern",
    "fit",
    "material",
    "sleeve",
    "length",
    "layer_role",
)

StageTimingObserver = Callable[[str, float], None]


@contextmanager
def measure_reference_stage(
    observer: StageTimingObserver | None,
    stage: str,
) -> Iterator[None]:
    started_at = time.perf_counter()
    try:
        yield
    finally:
        if observer is not None:
            observer(stage, max(0.0, (time.perf_counter() - started_at) * 1000))


class SharedReferenceLoaderError(RuntimeError):
    """공유 옷을 안전한 검색 기준으로 만들 수 없는 경우."""

    code = "REFERENCE_VECTOR_LOAD_FAILED"


class ReferenceSnapshotInvalid(SharedReferenceLoaderError):
    code = "REFERENCE_SNAPSHOT_INVALID"


class ReferenceVectorNotFound(SharedReferenceLoaderError):
    code = "REFERENCE_VECTOR_NOT_FOUND"


class ReferenceVectorMissing(SharedReferenceLoaderError):
    code = "REFERENCE_VECTOR_MISSING"


class ReferenceIndexMismatch(SharedReferenceLoaderError):
    code = "REFERENCE_INDEX_MISMATCH"


class ReferenceVectorStoreUnavailable(SharedReferenceLoaderError):
    code = "REFERENCE_VECTOR_STORE_UNAVAILABLE"


@dataclass(frozen=True)
class SharedReferenceTags:
    item_name: str
    category_large: str
    category_small: str
    season: tuple[str, ...]
    style: tuple[str, ...]
    color: str
    pattern: str
    fit: str
    material: str
    sleeve: str
    length: str
    usage: tuple[str, ...]
    layer_role: str
    layer_order: int | None


@dataclass(frozen=True)
class ReferenceSearchExclusions:
    """친구 옷 원본이 최종 후보로 재유입되지 않게 하는 검색 계약."""

    wardrobe_item_ids: tuple[str, ...]
    qdrant_point_ids: tuple[str, ...]


@dataclass(frozen=True)
class SharedReferenceSearchBasis:
    schema_version: str
    shared_item_id: str | None
    room_id: str | None
    source_wardrobe_item_id: str
    collection_name: str
    point_id: str
    embedding_version: str
    image_s3_key: str
    image_vector: tuple[float, ...]
    text_vector: tuple[float, ...]
    tags: SharedReferenceTags
    exclusions: ReferenceSearchExclusions


def _required_string(source: Mapping[str, Any], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReferenceSnapshotInvalid(f"참조 스냅샷의 {key} 값이 필요합니다.")
    return value.strip()


def _uuid_string(source: Mapping[str, Any], key: str) -> str:
    value = _required_string(source, key)
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise ReferenceSnapshotInvalid(
            f"참조 스냅샷의 {key} 값이 UUID 형식이 아닙니다."
        ) from exc


def _string_tag(source: Mapping[str, Any], key: str) -> str:
    value = source.get(key, "")
    if not isinstance(value, str):
        raise ReferenceSnapshotInvalid(f"참조 태그 {key}는 문자열이어야 합니다.")
    return value.strip()


def _array_tag(source: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = source.get(key, [])
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(tag, str) for tag in value
    ):
        raise ReferenceSnapshotInvalid(
            f"참조 태그 {key}는 문자열 배열이어야 합니다."
        )
    return tuple(tag.strip() for tag in value if tag.strip())


def _layer_order(source: Mapping[str, Any]) -> int | None:
    value = source.get("layer_order")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReferenceSnapshotInvalid("참조 태그 layer_order는 1 이상의 정수여야 합니다.")
    return value


def _vector(point: Any, name: str, expected_size: int) -> tuple[float, ...]:
    vectors = getattr(point, "vector", None)
    if not isinstance(vectors, Mapping) or name not in vectors:
        raise ReferenceVectorMissing(f"공유 옷의 {name} 벡터가 없습니다.")
    raw = vectors[name]
    if not isinstance(raw, (list, tuple)):
        raise ReferenceIndexMismatch(f"공유 옷의 {name} 벡터 형식이 올바르지 않습니다.")
    try:
        vector = tuple(float(value) for value in raw)
    except (TypeError, ValueError) as exc:
        raise ReferenceIndexMismatch(
            f"공유 옷의 {name} 벡터에 숫자가 아닌 값이 있습니다."
        ) from exc
    if len(vector) != expected_size:
        raise ReferenceIndexMismatch(
            f"공유 옷의 {name} 벡터 차원이 다릅니다: "
            f"expected={expected_size}, actual={len(vector)}"
        )
    if not all(math.isfinite(value) for value in vector):
        raise ReferenceIndexMismatch(
            f"공유 옷의 {name} 벡터에 유한하지 않은 값이 있습니다."
        )
    return vector


def _normalized_indexed_tag(payload: Mapping[str, Any], key: str) -> object:
    if key in _ARRAY_TAGS:
        value = payload.get(key)
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(tag, str) for tag in value
        ):
            raise ReferenceIndexMismatch(f"Qdrant의 {key} 태그 형식이 올바르지 않습니다.")
        return tuple(sorted({tag.strip() for tag in value if tag.strip()}))
    value = payload.get(key)
    if not isinstance(value, str):
        raise ReferenceIndexMismatch(f"Qdrant의 {key} 태그 형식이 올바르지 않습니다.")
    return value.strip()


def _validate_indexed_tags(
    *,
    payload: Mapping[str, Any],
    snapshot_item: Mapping[str, Any],
) -> None:
    if "category_large" not in payload:
        raise ReferenceIndexMismatch("Qdrant에 category_large 태그가 없습니다.")
    for key in _INDEXED_TAGS:
        if key not in payload:
            continue
        indexed = _normalized_indexed_tag(payload, key)
        expected = (
            tuple(sorted(set(_array_tag(snapshot_item, key))))
            if key in _ARRAY_TAGS
            else _string_tag(snapshot_item, key)
        )
        if indexed != expected:
            raise ReferenceIndexMismatch(
                f"Qdrant의 {key} 태그가 실행 스냅샷과 일치하지 않습니다."
            )


def _tags(snapshot_item: Mapping[str, Any]) -> SharedReferenceTags:
    strings = {key: _string_tag(snapshot_item, key) for key in _STRING_TAGS}
    arrays = {key: _array_tag(snapshot_item, key) for key in _ARRAY_TAGS}
    if not strings["category_large"]:
        raise ReferenceSnapshotInvalid("참조 태그 category_large가 필요합니다.")
    return SharedReferenceTags(
        **strings,
        **arrays,
        layer_order=_layer_order(snapshot_item),
    )


def _merged_tags(
    *,
    payload: Mapping[str, Any],
    snapshot_item: Mapping[str, Any],
) -> SharedReferenceTags:
    merged = dict(snapshot_item)
    for key in _INDEXED_TAGS:
        if key in payload:
            merged[key] = payload[key]
    return _tags(merged)


class SharedReferenceVectorLoader:
    def __init__(self, *, client=None) -> None:
        self.client = client if client is not None else get_client()

    def load(
        self,
        snapshot: Mapping[str, Any],
        *,
        stage_observer: StageTimingObserver | None = None,
    ) -> SharedReferenceSearchBasis:
        with measure_reference_stage(stage_observer, "SNAPSHOT_VALIDATION"):
            if not isinstance(snapshot, Mapping) or not snapshot:
                raise ReferenceSnapshotInvalid("공유 옷 참조 스냅샷이 필요합니다.")
            if _required_string(snapshot, "schema_version") != REFERENCE_SCHEMA_VERSION:
                raise ReferenceSnapshotInvalid("지원하지 않는 참조 스냅샷 버전입니다.")
            reference_type = _required_string(snapshot, "type")
            if reference_type not in REFERENCE_TYPES:
                raise ReferenceSnapshotInvalid("지원하지 않는 참조 유형입니다.")
            if reference_type == REFERENCE_TYPE_SHARED:
                # 공유 상태 기능은 제거됐다. 과거 스냅샷의 값은 구조 확인에만 쓰고,
                # borrowed/private 같은 레거시 값으로 기존 대화를 차단하지 않는다.
                _required_string(snapshot, "source_status")

            spec = collection_spec("wardrobe")
            collection_name = _required_string(snapshot, "qdrant_collection")
            if collection_name != spec.name:
                raise ReferenceIndexMismatch(
                    "참조 스냅샷의 Qdrant 컬렉션이 현재 옷장 인덱스와 다릅니다."
                )

            shared_item_id = (
                _uuid_string(snapshot, "shared_item_id")
                if reference_type == REFERENCE_TYPE_SHARED
                else None
            )
            room_id = (
                _uuid_string(snapshot, "room_id")
                if reference_type == REFERENCE_TYPE_SHARED
                else None
            )
            wardrobe_item_id = _uuid_string(snapshot, "wardrobe_item_id")
            point_id = _uuid_string(snapshot, "qdrant_point_id")
            if point_id != wardrobe_item_id:
                raise ReferenceIndexMismatch(
                    "공유 옷의 Qdrant 포인트 ID와 원본 옷장 아이템 ID가 다릅니다."
                )

            embedding_version = _required_string(snapshot, "embedding_version")
            image_s3_key = _required_string(snapshot, "image_s3_key")
            snapshot_item = snapshot.get("item")
            if not isinstance(snapshot_item, Mapping):
                raise ReferenceSnapshotInvalid("참조 스냅샷의 item 객체가 필요합니다.")
            _tags(snapshot_item)

        with measure_reference_stage(stage_observer, "VECTOR_LOADING"):
            try:
                points = self.client.retrieve(
                    collection_name=collection_name,
                    ids=[point_id],
                    with_payload=True,
                    with_vectors=True,
                )
            except Exception as exc:
                raise ReferenceVectorStoreUnavailable(
                    "공유 옷 벡터 저장소를 조회할 수 없습니다."
                ) from exc
            if not points:
                raise ReferenceVectorNotFound(
                    "공유 옷의 Qdrant 포인트를 찾을 수 없습니다."
                )

            point = points[0]
            if str(point.id) != point_id:
                raise ReferenceIndexMismatch("조회된 Qdrant 포인트 ID가 요청과 다릅니다.")
            payload = getattr(point, "payload", None)
            if not isinstance(payload, Mapping):
                raise ReferenceIndexMismatch("공유 옷의 Qdrant payload가 없습니다.")
            if str(payload.get("item_id", "")) != wardrobe_item_id:
                raise ReferenceIndexMismatch(
                    "Qdrant payload의 원본 옷장 아이템 ID가 스냅샷과 다릅니다."
                )
            if payload.get("confirmed") is not True:
                raise ReferenceIndexMismatch("Qdrant에서 확정되지 않은 공유 옷입니다.")
            if str(payload.get("embedding_version", "")) != embedding_version:
                raise ReferenceIndexMismatch(
                    "Qdrant 임베딩 버전이 실행 스냅샷과 일치하지 않습니다."
                )
            if str(payload.get("s3_key", "")) != image_s3_key:
                raise ReferenceIndexMismatch(
                    "Qdrant 이미지 키가 실행 스냅샷과 일치하지 않습니다."
                )
            _validate_indexed_tags(payload=payload, snapshot_item=snapshot_item)
            tags = _merged_tags(payload=payload, snapshot_item=snapshot_item)

            image_vector = _vector(point, IMAGE_VECTOR, spec.vectors[IMAGE_VECTOR])
            text_vector = _vector(point, TEXT_VECTOR, spec.vectors[TEXT_VECTOR])
            exclusions = ReferenceSearchExclusions(
                wardrobe_item_ids=(wardrobe_item_id,),
                qdrant_point_ids=(point_id,),
            )
            return SharedReferenceSearchBasis(
                schema_version=REFERENCE_SCHEMA_VERSION,
                shared_item_id=shared_item_id,
                room_id=room_id,
                source_wardrobe_item_id=wardrobe_item_id,
                collection_name=collection_name,
                point_id=point_id,
                embedding_version=embedding_version,
                image_s3_key=image_s3_key,
                image_vector=image_vector,
                text_vector=text_vector,
                tags=tags,
                exclusions=exclusions,
            )


def load_shared_reference(
    snapshot: Mapping[str, Any],
    *,
    client=None,
) -> SharedReferenceSearchBasis:
    return SharedReferenceVectorLoader(client=client).load(snapshot)
