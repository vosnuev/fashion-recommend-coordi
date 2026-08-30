"""옷장 DB와 Qdrant 벡터 인덱스의 정합성을 읽기 전용으로 판정한다."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from apps.recommend.services.qdrant import (
    IMAGE_VECTOR,
    TEXT_VECTOR,
    collection_spec,
    get_client,
)
from apps.wardrobe.services.vectors import EMBEDDING_VERSION

POINT_MISSING = "POINT_MISSING"
PAYLOAD_MISSING = "PAYLOAD_MISSING"
ITEM_ID_MISMATCH = "ITEM_ID_MISMATCH"
USER_ID_MISMATCH = "USER_ID_MISMATCH"
S3_KEY_MISMATCH = "S3_KEY_MISMATCH"
EMBEDDING_VERSION_MISMATCH = "EMBEDDING_VERSION_MISMATCH"
CONFIRMED_MISMATCH = "CONFIRMED_MISMATCH"
CATEGORY_LARGE_MISMATCH = "CATEGORY_LARGE_MISMATCH"
IMAGE_VECTOR_MISSING = "IMAGE_VECTOR_MISSING"
IMAGE_VECTOR_INVALID = "IMAGE_VECTOR_INVALID"
TEXT_VECTOR_MISSING = "TEXT_VECTOR_MISSING"
TEXT_VECTOR_INVALID = "TEXT_VECTOR_INVALID"


class WardrobeVectorStoreUnavailable(RuntimeError):
    """Qdrant 장애 때문에 안전한 정합성 판정을 완료할 수 없는 경우."""


@dataclass(frozen=True)
class WardrobeVectorAuditResult:
    """옷장 아이템 한 건의 DB 플래그와 실제 Qdrant 상태 비교 결과."""

    item_id: str
    user_id: int
    db_embedding_version: str
    indexed_embedding_version: str
    expected_embedding_version: str
    issues: tuple[str, ...]

    @property
    def vector_ready(self) -> bool:
        return not self.issues

    @property
    def desired_embedding_version(self) -> str:
        return self.expected_embedding_version if self.vector_ready else ""

    @property
    def needs_flag_repair(self) -> bool:
        return self.db_embedding_version != self.desired_embedding_version


def _vector_issue(
    point: Any,
    *,
    vector_name: str,
    expected_size: int,
) -> str | None:
    vectors = getattr(point, "vector", None)
    if not isinstance(vectors, Mapping) or vector_name not in vectors:
        return (
            IMAGE_VECTOR_MISSING
            if vector_name == IMAGE_VECTOR
            else TEXT_VECTOR_MISSING
        )

    raw_vector = vectors[vector_name]
    if not isinstance(raw_vector, (list, tuple)) or len(raw_vector) != expected_size:
        return (
            IMAGE_VECTOR_INVALID
            if vector_name == IMAGE_VECTOR
            else TEXT_VECTOR_INVALID
        )
    try:
        if not all(math.isfinite(float(value)) for value in raw_vector):
            raise ValueError
    except (TypeError, ValueError):
        return (
            IMAGE_VECTOR_INVALID
            if vector_name == IMAGE_VECTOR
            else TEXT_VECTOR_INVALID
        )
    return None


class WardrobeVectorReconciler:
    """Qdrant를 변경하지 않고 옷장 벡터의 실제 사용 가능 여부를 판정한다."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        expected_embedding_version: str = EMBEDDING_VERSION,
        expected_vector_dimensions: Mapping[str, int] | None = None,
    ) -> None:
        spec = collection_spec("wardrobe")
        self.client = client if client is not None else get_client()
        self.collection_name = spec.name
        self.expected_embedding_version = expected_embedding_version
        self.expected_vector_dimensions = dict(
            expected_vector_dimensions
            if expected_vector_dimensions is not None
            else spec.vectors
        )

    def audit(
        self,
        items: Sequence[Any],
    ) -> list[WardrobeVectorAuditResult]:
        """아이템 묶음을 한 번에 조회해 입력 순서대로 판정 결과를 반환한다."""

        if not items:
            return []

        point_ids = [str(item.id) for item in items]
        try:
            points = self.client.retrieve(
                collection_name=self.collection_name,
                ids=point_ids,
                with_payload=True,
                with_vectors=True,
            )
        except Exception as exc:
            raise WardrobeVectorStoreUnavailable(
                "Qdrant 옷장 컬렉션을 조회할 수 없습니다."
            ) from exc

        points_by_id = {str(point.id): point for point in points}
        return [
            self._audit_item(item, points_by_id.get(str(item.id))) for item in items
        ]

    def _audit_item(self, item: Any, point: Any | None) -> WardrobeVectorAuditResult:
        item_id = str(item.id)
        db_version = str(item.embedding_version or "")
        if point is None:
            return WardrobeVectorAuditResult(
                item_id=item_id,
                user_id=item.user_id,
                db_embedding_version=db_version,
                indexed_embedding_version="",
                expected_embedding_version=self.expected_embedding_version,
                issues=(POINT_MISSING,),
            )

        issues: list[str] = []
        payload = getattr(point, "payload", None)
        indexed_version = ""
        if not isinstance(payload, Mapping):
            issues.append(PAYLOAD_MISSING)
        else:
            indexed_version = str(payload.get("embedding_version", ""))
            if str(payload.get("item_id", "")) != item_id:
                issues.append(ITEM_ID_MISMATCH)
            if str(payload.get("user_id", "")) != str(item.user_id):
                issues.append(USER_ID_MISMATCH)
            if str(payload.get("s3_key", "")) != str(item.s3_key):
                issues.append(S3_KEY_MISMATCH)
            if indexed_version != self.expected_embedding_version:
                issues.append(EMBEDDING_VERSION_MISMATCH)
            if payload.get("confirmed") is not item.confirmed:
                issues.append(CONFIRMED_MISMATCH)
            if str(payload.get("category_large", "")) != str(item.category_large):
                issues.append(CATEGORY_LARGE_MISMATCH)

        for vector_name in (IMAGE_VECTOR, TEXT_VECTOR):
            issue = _vector_issue(
                point,
                vector_name=vector_name,
                expected_size=self.expected_vector_dimensions[vector_name],
            )
            if issue is not None:
                issues.append(issue)

        return WardrobeVectorAuditResult(
            item_id=item_id,
            user_id=item.user_id,
            db_embedding_version=db_version,
            indexed_embedding_version=indexed_version,
            expected_embedding_version=self.expected_embedding_version,
            issues=tuple(issues),
        )
