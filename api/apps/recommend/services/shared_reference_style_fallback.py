"""시각 유사 후보가 없을 때 같은 슬롯의 내 옷을 스타일 기준으로 찾는다."""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from qdrant_client import models as qm

from apps.recommend.services.qdrant import TEXT_VECTOR, get_client
from apps.recommend.services.shared_reference_loader import (
    SharedReferenceSearchBasis,
)
from apps.recommend.services.shared_reference_visual_search import (
    WardrobeVisualSearchResult,
)

_STYLE_WEIGHT = 0.45
_COLOR_WEIGHT = 0.20
_FIT_WEIGHT = 0.20
_MATERIAL_WEIGHT = 0.15


class StyleFallbackError(RuntimeError):
    code = "STYLE_FALLBACK_FAILED"


class StyleFallbackInvalid(StyleFallbackError):
    code = "STYLE_FALLBACK_INVALID"


class StyleFallbackIndexMismatch(StyleFallbackError):
    code = "STYLE_FALLBACK_INDEX_MISMATCH"


class StyleFallbackStoreUnavailable(StyleFallbackError):
    code = "STYLE_FALLBACK_STORE_UNAVAILABLE"


@dataclass(frozen=True)
class StyleFallbackRequest:
    reference: SharedReferenceSearchBasis
    visual_result: WardrobeVisualSearchResult
    user_id: int
    limit: int = 10
    retrieval_limit: int = 30
    min_style_score: float | None = None


@dataclass(frozen=True)
class StyleScoreBreakdown:
    style_overlap: float | None
    color_match: float | None
    fit_match: float | None
    material_match: float | None
    total: float


@dataclass(frozen=True)
class StyleSimilarCandidate:
    match_type: str
    point_id: str
    wardrobe_item_id: str
    style_score: float
    text_similarity: float | None
    score_breakdown: StyleScoreBreakdown
    evidence: tuple[str, ...]
    image_s3_key: str
    embedding_version: str
    category_large: str
    category_small: str
    layer_role: str
    style: tuple[str, ...]
    color: str
    fit: str
    material: str


@dataclass(frozen=True)
class StyleFallbackResult:
    fallback_used: bool
    decision: str
    search_mode: str
    visual_threshold: float
    min_style_score: float
    candidates: tuple[StyleSimilarCandidate, ...]


def _match_value(field_name: str, value: object) -> qm.FieldCondition:
    return qm.FieldCondition(key=field_name, match=qm.MatchValue(value=value))


def _uuid_string(value: object, *, field_name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise StyleFallbackIndexMismatch(
            f"스타일 후보의 {field_name} 값이 UUID 형식이 아닙니다."
        ) from exc


def _string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise StyleFallbackIndexMismatch(
            f"스타일 후보의 {key} 값이 문자열이 아닙니다."
        )
    return value.strip()


def _string_tuple(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(tag, str) for tag in value
    ):
        raise StyleFallbackIndexMismatch(
            f"스타일 후보의 {key} 값이 문자열 배열이 아닙니다."
        )
    return tuple(tag.strip() for tag in value if tag.strip())


def _jaccard(reference: tuple[str, ...], candidate: tuple[str, ...]) -> float | None:
    reference_set = set(reference)
    if not reference_set:
        return None
    candidate_set = set(candidate)
    union = reference_set | candidate_set
    return len(reference_set & candidate_set) / len(union) if union else 0.0


def _exact(reference: str, candidate: str) -> float | None:
    if not reference:
        return None
    return 1.0 if reference == candidate else 0.0


def _score(
    reference: SharedReferenceSearchBasis,
    *,
    style: tuple[str, ...],
    color: str,
    fit: str,
    material: str,
) -> StyleScoreBreakdown:
    values = {
        "style_overlap": (_jaccard(reference.tags.style, style), _STYLE_WEIGHT),
        "color_match": (_exact(reference.tags.color, color), _COLOR_WEIGHT),
        "fit_match": (_exact(reference.tags.fit, fit), _FIT_WEIGHT),
        "material_match": (
            _exact(reference.tags.material, material),
            _MATERIAL_WEIGHT,
        ),
    }
    available_weight = sum(
        weight for value, weight in values.values() if value is not None
    )
    total = (
        sum(value * weight for value, weight in values.values() if value is not None)
        / available_weight
        if available_weight
        else 0.0
    )
    return StyleScoreBreakdown(
        style_overlap=values["style_overlap"][0],
        color_match=values["color_match"][0],
        fit_match=values["fit_match"][0],
        material_match=values["material_match"][0],
        total=round(total, 4),
    )


def _evidence(
    reference: SharedReferenceSearchBasis,
    *,
    style: tuple[str, ...],
    color: str,
    fit: str,
    material: str,
    text_similarity: float | None,
) -> tuple[str, ...]:
    reasons: list[str] = []
    common_styles = sorted(set(reference.tags.style) & set(style))
    if common_styles:
        reasons.append("스타일 일치: " + ", ".join(common_styles))
    if reference.tags.color and reference.tags.color == color:
        reasons.append(f"색상 일치: {color}")
    if reference.tags.fit and reference.tags.fit == fit:
        reasons.append(f"핏 일치: {fit}")
    if reference.tags.material and reference.tags.material == material:
        reasons.append(f"소재 일치: {material}")
    if text_similarity is not None:
        reasons.append(f"텍스트 벡터 유사도: {text_similarity:.4f}")
    return tuple(reasons)


class SharedReferenceStyleFallbackSearcher:
    def __init__(self, *, client=None) -> None:
        self.client = client if client is not None else get_client()

    def search(self, request: StyleFallbackRequest) -> StyleFallbackResult:
        min_style_score = self._validate_request(request)
        if request.visual_result.candidates:
            return StyleFallbackResult(
                fallback_used=False,
                decision="VISUAL_MATCH_FOUND",
                search_mode="none",
                visual_threshold=request.visual_result.min_similarity,
                min_style_score=min_style_score,
                candidates=(),
            )

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
        search_modes = ("text", "tags") if reference.text_vector else ("tags",)
        try:
            candidates: list[StyleSimilarCandidate] = []
            used_modes: list[str] = []
            for search_mode in search_modes:
                used_modes.append(search_mode)
                hits = self._query(
                    reference=reference,
                    query_filter=query_filter,
                    limit=request.retrieval_limit,
                    search_mode=search_mode,
                )
                candidates = [
                    candidate
                    for hit in hits
                    if (
                        candidate := self._candidate(
                            hit,
                            request=request,
                            search_mode=search_mode,
                        )
                    )
                    is not None
                    and candidate.style_score >= min_style_score
                ]
                if candidates:
                    break
        except StyleFallbackError:
            raise
        except Exception as exc:
            raise StyleFallbackStoreUnavailable(
                "내 옷 스타일 fallback 검색을 수행할 수 없습니다."
            ) from exc

        candidates.sort(
            key=lambda row: (
                -row.style_score,
                -(row.text_similarity if row.text_similarity is not None else -1.0),
                row.point_id,
            )
        )
        return StyleFallbackResult(
            fallback_used=True,
            decision="VISUAL_THRESHOLD_NOT_MET",
            search_mode="_then_".join(used_modes),
            visual_threshold=request.visual_result.min_similarity,
            min_style_score=min_style_score,
            candidates=tuple(candidates[: request.limit]),
        )

    @staticmethod
    def _validate_request(request: StyleFallbackRequest) -> float:
        if (
            isinstance(request.user_id, bool)
            or not isinstance(request.user_id, int)
            or request.user_id <= 0
        ):
            raise StyleFallbackInvalid("검색할 회원의 양수 user_id가 필요합니다.")
        if not 1 <= request.limit <= 50:
            raise StyleFallbackInvalid("limit은 1 이상 50 이하여야 합니다.")
        if not request.limit <= request.retrieval_limit <= 100:
            raise StyleFallbackInvalid(
                "retrieval_limit은 limit 이상 100 이하여야 합니다."
            )
        if (
            request.visual_result.reference_point_id
            != request.reference.point_id
        ):
            raise StyleFallbackInvalid(
                "시각 검색 결과와 스타일 fallback의 공유 옷이 다릅니다."
            )
        if not request.reference.tags.category_large or not request.reference.tags.layer_role:
            raise StyleFallbackInvalid("공유 옷의 대분류와 슬롯 태그가 필요합니다.")
        if (
            request.reference.point_id
            not in request.reference.exclusions.qdrant_point_ids
            or request.reference.source_wardrobe_item_id
            not in request.reference.exclusions.wardrobe_item_ids
        ):
            raise StyleFallbackInvalid("공유 옷 원본 ID 제외 계약이 누락됐습니다.")
        threshold = (
            settings.SHARED_REFERENCE_STYLE_MIN_SCORE
            if request.min_style_score is None
            else request.min_style_score
        )
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not 0.0 <= float(threshold) <= 1.0
        ):
            raise StyleFallbackInvalid(
                "min_style_score는 0 이상 1 이하의 숫자여야 합니다."
            )
        return float(threshold)

    def _query(
        self,
        *,
        reference: SharedReferenceSearchBasis,
        query_filter: qm.Filter,
        limit: int,
        search_mode: str,
    ) -> list[Any]:
        if search_mode == "tags":
            points, _ = self.client.scroll(
                collection_name=reference.collection_name,
                scroll_filter=query_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            return list(points)
        if hasattr(self.client, "query_points"):
            response = self.client.query_points(
                collection_name=reference.collection_name,
                query=list(reference.text_vector),
                using=TEXT_VECTOR,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            return list(response.points)
        return list(
            self.client.search(
                collection_name=reference.collection_name,
                query_vector=(TEXT_VECTOR, list(reference.text_vector)),
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
        )

    @staticmethod
    def _candidate(
        hit: Any,
        *,
        request: StyleFallbackRequest,
        search_mode: str,
    ) -> StyleSimilarCandidate | None:
        reference = request.reference
        point_id = _uuid_string(hit.id, field_name="point_id")
        if point_id in reference.exclusions.qdrant_point_ids:
            return None
        payload = getattr(hit, "payload", None)
        if not isinstance(payload, Mapping):
            raise StyleFallbackIndexMismatch("스타일 후보의 Qdrant payload가 없습니다.")
        wardrobe_item_id = _uuid_string(payload.get("item_id"), field_name="item_id")
        if wardrobe_item_id in reference.exclusions.wardrobe_item_ids:
            return None
        if wardrobe_item_id != point_id:
            raise StyleFallbackIndexMismatch(
                "스타일 후보 포인트 ID와 옷장 아이템 ID가 다릅니다."
            )
        if payload.get("user_id") != request.user_id:
            raise StyleFallbackIndexMismatch("내 소유가 아닌 옷이 포함됐습니다.")
        if payload.get("confirmed") is not True:
            raise StyleFallbackIndexMismatch("확정되지 않은 옷이 포함됐습니다.")

        category_large = _string(payload, "category_large")
        layer_role = _string(payload, "layer_role")
        embedding_version = _string(payload, "embedding_version")
        if category_large != reference.tags.category_large:
            raise StyleFallbackIndexMismatch("스타일 후보의 대분류가 다릅니다.")
        if layer_role != reference.tags.layer_role:
            raise StyleFallbackIndexMismatch("스타일 후보의 슬롯이 다릅니다.")
        if embedding_version != reference.embedding_version:
            raise StyleFallbackIndexMismatch("스타일 후보의 임베딩 버전이 다릅니다.")

        text_similarity = None
        if search_mode == "text":
            try:
                text_similarity = float(hit.score)
            except (TypeError, ValueError, AttributeError) as exc:
                raise StyleFallbackIndexMismatch(
                    "스타일 후보의 텍스트 유사도 점수가 올바르지 않습니다."
                ) from exc
            if not math.isfinite(text_similarity):
                raise StyleFallbackIndexMismatch(
                    "스타일 후보의 텍스트 유사도 점수가 유한하지 않습니다."
                )
            text_similarity = round(text_similarity, 4)

        style = _string_tuple(payload, "style")
        color = _string(payload, "color")
        fit = _string(payload, "fit")
        material = _string(payload, "material")
        breakdown = _score(
            reference,
            style=style,
            color=color,
            fit=fit,
            material=material,
        )
        image_s3_key = _string(payload, "s3_key")
        if not image_s3_key:
            raise StyleFallbackIndexMismatch("스타일 후보의 이미지 키가 없습니다.")
        return StyleSimilarCandidate(
            match_type="STYLE_SIMILAR",
            point_id=point_id,
            wardrobe_item_id=wardrobe_item_id,
            style_score=breakdown.total,
            text_similarity=text_similarity,
            score_breakdown=breakdown,
            evidence=_evidence(
                reference,
                style=style,
                color=color,
                fit=fit,
                material=material,
                text_similarity=text_similarity,
            ),
            image_s3_key=image_s3_key,
            embedding_version=embedding_version,
            category_large=category_large,
            category_small=_string(payload, "category_small"),
            layer_role=layer_role,
            style=style,
            color=color,
            fit=fit,
            material=material,
        )


def search_style_fallback(
    request: StyleFallbackRequest,
    *,
    client=None,
) -> StyleFallbackResult:
    return SharedReferenceStyleFallbackSearcher(client=client).search(request)
