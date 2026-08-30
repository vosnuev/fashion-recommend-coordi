"""공유 옷 이미지와 같은 슬롯에서 시각적으로 유사한 내 옷을 검색한다."""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from qdrant_client import models as qm

from apps.recommend.services.qdrant import IMAGE_VECTOR, get_client
from apps.recommend.services.shared_reference_loader import (
    SharedReferenceSearchBasis,
)


class WardrobeVisualSearchError(RuntimeError):
    """공유 옷 기반 내 옷 이미지 검색을 안전하게 수행할 수 없는 경우."""

    code = "WARDROBE_VISUAL_SEARCH_FAILED"


class WardrobeVisualSearchInvalid(WardrobeVisualSearchError):
    code = "WARDROBE_VISUAL_SEARCH_INVALID"


class WardrobeVisualIndexMismatch(WardrobeVisualSearchError):
    code = "WARDROBE_VISUAL_INDEX_MISMATCH"


class WardrobeVisualStoreUnavailable(WardrobeVisualSearchError):
    code = "WARDROBE_VISUAL_STORE_UNAVAILABLE"


@dataclass(frozen=True)
class WardrobeVisualSearchRequest:
    reference: SharedReferenceSearchBasis
    user_id: int
    limit: int = 10
    min_similarity: float | None = None


@dataclass(frozen=True)
class WardrobeVisualCandidate:
    match_type: str
    point_id: str
    wardrobe_item_id: str
    visual_score: float
    image_s3_key: str
    embedding_version: str
    category_large: str
    category_small: str
    layer_role: str
    style: tuple[str, ...]
    color: str


@dataclass(frozen=True)
class WardrobeVisualSearchResult:
    reference_point_id: str
    min_similarity: float
    candidates: tuple[WardrobeVisualCandidate, ...]


def _match_value(field_name: str, value: object) -> qm.FieldCondition:
    return qm.FieldCondition(
        key=field_name,
        match=qm.MatchValue(value=value),
    )


def _uuid_string(value: object, *, field_name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise WardrobeVisualIndexMismatch(
            f"후보의 {field_name} 값이 UUID 형식이 아닙니다."
        ) from exc


def _string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise WardrobeVisualIndexMismatch(f"후보의 {key} 값이 문자열이 아닙니다.")
    return value.strip()


def _string_tuple(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(tag, str) for tag in value
    ):
        raise WardrobeVisualIndexMismatch(
            f"후보의 {key} 값이 문자열 배열이 아닙니다."
        )
    return tuple(tag.strip() for tag in value if tag.strip())


class SharedReferenceWardrobeVisualSearcher:
    def __init__(self, *, client=None) -> None:
        self.client = client if client is not None else get_client()

    def search(
        self,
        request: WardrobeVisualSearchRequest,
    ) -> WardrobeVisualSearchResult:
        threshold = self._validate_request(request)
        reference = request.reference
        query_filter = qm.Filter(
            must=[
                _match_value("user_id", request.user_id),
                _match_value("confirmed", True),
                _match_value("category_large", reference.tags.category_large),
                _match_value("layer_role", reference.tags.layer_role),
                _match_value("embedding_version", reference.embedding_version),
            ],
            must_not=[
                qm.HasIdCondition(
                    has_id=list(reference.exclusions.qdrant_point_ids),
                )
            ],
        )

        try:
            hits = self._query(
                collection_name=reference.collection_name,
                image_vector=list(reference.image_vector),
                query_filter=query_filter,
                limit=request.limit,
                threshold=threshold,
            )
        except WardrobeVisualSearchError:
            raise
        except Exception as exc:
            raise WardrobeVisualStoreUnavailable(
                "내 옷 이미지 벡터 검색을 수행할 수 없습니다."
            ) from exc

        candidates = []
        for hit in hits:
            candidate = self._candidate(
                hit,
                request=request,
                threshold=threshold,
            )
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(key=lambda row: (-row.visual_score, row.point_id))
        return WardrobeVisualSearchResult(
            reference_point_id=reference.point_id,
            min_similarity=threshold,
            candidates=tuple(candidates[: request.limit]),
        )

    @staticmethod
    def _validate_request(request: WardrobeVisualSearchRequest) -> float:
        if (
            isinstance(request.user_id, bool)
            or not isinstance(request.user_id, int)
            or request.user_id <= 0
        ):
            raise WardrobeVisualSearchInvalid("검색할 회원의 양수 user_id가 필요합니다.")
        if not 1 <= request.limit <= 50:
            raise WardrobeVisualSearchInvalid("limit은 1 이상 50 이하여야 합니다.")
        if not request.reference.tags.category_large:
            raise WardrobeVisualSearchInvalid("공유 옷의 대분류 태그가 필요합니다.")
        if not request.reference.tags.layer_role:
            raise WardrobeVisualSearchInvalid("공유 옷의 슬롯(layer_role) 태그가 필요합니다.")
        if not request.reference.image_vector:
            raise WardrobeVisualSearchInvalid("공유 옷의 이미지 벡터가 필요합니다.")
        if (
            request.reference.point_id
            not in request.reference.exclusions.qdrant_point_ids
            or request.reference.source_wardrobe_item_id
            not in request.reference.exclusions.wardrobe_item_ids
        ):
            raise WardrobeVisualSearchInvalid(
                "공유 옷 원본 ID 제외 계약이 누락됐습니다."
            )

        threshold = (
            settings.SHARED_REFERENCE_VISUAL_MIN_SCORE
            if request.min_similarity is None
            else request.min_similarity
        )
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not 0.0 <= float(threshold) <= 1.0
        ):
            raise WardrobeVisualSearchInvalid(
                "min_similarity는 0 이상 1 이하의 숫자여야 합니다."
            )
        return float(threshold)

    def _query(
        self,
        *,
        collection_name: str,
        image_vector: list[float],
        query_filter: qm.Filter,
        limit: int,
        threshold: float,
    ) -> list[Any]:
        if hasattr(self.client, "query_points"):
            response = self.client.query_points(
                collection_name=collection_name,
                query=image_vector,
                using=IMAGE_VECTOR,
                query_filter=query_filter,
                score_threshold=threshold,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            return list(response.points)
        return list(
            self.client.search(
                collection_name=collection_name,
                query_vector=(IMAGE_VECTOR, image_vector),
                query_filter=query_filter,
                score_threshold=threshold,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
        )

    @staticmethod
    def _candidate(
        hit: Any,
        *,
        request: WardrobeVisualSearchRequest,
        threshold: float,
    ) -> WardrobeVisualCandidate | None:
        reference = request.reference
        point_id = _uuid_string(hit.id, field_name="point_id")
        if point_id in reference.exclusions.qdrant_point_ids:
            return None
        try:
            score = float(hit.score)
        except (TypeError, ValueError, AttributeError) as exc:
            raise WardrobeVisualIndexMismatch(
                "후보의 이미지 유사도 점수가 올바르지 않습니다."
            ) from exc
        if not math.isfinite(score):
            raise WardrobeVisualIndexMismatch(
                "후보의 이미지 유사도 점수가 유한하지 않습니다."
            )
        if score < threshold:
            return None

        payload = getattr(hit, "payload", None)
        if not isinstance(payload, Mapping):
            raise WardrobeVisualIndexMismatch("후보의 Qdrant payload가 없습니다.")
        wardrobe_item_id = _uuid_string(
            payload.get("item_id"),
            field_name="item_id",
        )
        if wardrobe_item_id in reference.exclusions.wardrobe_item_ids:
            return None
        if wardrobe_item_id != point_id:
            raise WardrobeVisualIndexMismatch(
                "후보 포인트 ID와 원본 옷장 아이템 ID가 다릅니다."
            )
        if payload.get("user_id") != request.user_id:
            raise WardrobeVisualIndexMismatch("내 소유가 아닌 옷이 검색 결과에 포함됐습니다.")
        if payload.get("confirmed") is not True:
            raise WardrobeVisualIndexMismatch("확정되지 않은 옷이 검색 결과에 포함됐습니다.")

        category_large = _string(payload, "category_large")
        layer_role = _string(payload, "layer_role")
        if category_large != reference.tags.category_large:
            raise WardrobeVisualIndexMismatch("후보의 대분류가 공유 옷과 다릅니다.")
        if layer_role != reference.tags.layer_role:
            raise WardrobeVisualIndexMismatch("후보의 슬롯이 공유 옷과 다릅니다.")

        image_s3_key = _string(payload, "s3_key")
        embedding_version = _string(payload, "embedding_version")
        if not image_s3_key or not embedding_version:
            raise WardrobeVisualIndexMismatch(
                "후보의 이미지 키 또는 임베딩 버전이 없습니다."
            )
        if embedding_version != reference.embedding_version:
            raise WardrobeVisualIndexMismatch(
                "후보와 공유 옷의 임베딩 버전이 다릅니다."
            )
        return WardrobeVisualCandidate(
            match_type="VISUAL_SIMILAR",
            point_id=point_id,
            wardrobe_item_id=wardrobe_item_id,
            visual_score=round(score, 4),
            image_s3_key=image_s3_key,
            embedding_version=embedding_version,
            category_large=category_large,
            category_small=_string(payload, "category_small"),
            layer_role=layer_role,
            style=_string_tuple(payload, "style"),
            color=_string(payload, "color"),
        )


def search_owned_visual_matches(
    request: WardrobeVisualSearchRequest,
    *,
    client=None,
) -> WardrobeVisualSearchResult:
    return SharedReferenceWardrobeVisualSearcher(client=client).search(request)
