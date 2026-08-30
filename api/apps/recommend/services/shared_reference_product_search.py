"""공유 옷 이미지와 유사한 판매 상품을 NEW_ITEM 기준 아이템으로 검색한다."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from qdrant_client import models as qm

from apps.recommend.services.item_retriever import ItemSource
from apps.recommend.services.outfit_types import OutfitItem
from apps.recommend.services.qdrant import (
    IMAGE_VECTOR,
    collection_spec,
    get_client,
    product_collection_names,
)
from apps.recommend.services.shared_reference_loader import (
    SharedReferenceSearchBasis,
)
from apps.recommend.services.validator import (
    DjangoEligibilityGateway,
    EligibilityGateway,
)


class SharedReferenceProductSearchError(RuntimeError):
    """공유 옷 기반 상품 검색을 안전하게 수행할 수 없는 경우."""

    code = "SHARED_REFERENCE_PRODUCT_SEARCH_FAILED"


class SharedReferenceProductSearchInvalid(SharedReferenceProductSearchError):
    code = "SHARED_REFERENCE_PRODUCT_SEARCH_INVALID"


class SharedReferenceProductIndexMismatch(SharedReferenceProductSearchError):
    code = "SHARED_REFERENCE_PRODUCT_INDEX_MISMATCH"


class SharedReferenceProductStoreUnavailable(SharedReferenceProductSearchError):
    code = "SHARED_REFERENCE_PRODUCT_STORE_UNAVAILABLE"


@dataclass(frozen=True)
class SharedReferenceProductSearchRequest:
    reference: SharedReferenceSearchBasis
    total_budget: int | None = None
    category_budgets: Mapping[str, int] = field(default_factory=dict)
    already_selected_total: int = 0
    limit: int = 10
    min_similarity: float | None = None


@dataclass(frozen=True)
class SimilarProductCandidate:
    """현재 판매 가능 상태까지 재검증된 NEW_ITEM 상품 후보."""

    match_type: str
    selection_role: str
    point_id: str
    source: str
    external_product_id: str
    source_collection: str
    visual_score: float
    price: int
    title: str
    brand: str
    mall_name: str
    link: str
    image_url: str
    image_s3_key: str
    category_large: str
    category_small: str
    layer_role: str
    tagging_status: str
    sale_status: str


@dataclass(frozen=True)
class SharedReferenceProductSearchResult:
    reference_point_id: str
    category_large: str
    layer_role: str
    category_budget: int | None
    remaining_total_budget: int | None
    effective_max_price: int | None
    min_similarity: float
    candidates: tuple[SimilarProductCandidate, ...]
    selected_anchor: SimilarProductCandidate | None


@dataclass(frozen=True)
class _RawProductCandidate:
    item: OutfitItem
    score: float


def _match_value(field_name: str, value: object) -> qm.FieldCondition:
    return qm.FieldCondition(
        key=field_name,
        match=qm.MatchValue(value=value),
    )


def _string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SharedReferenceProductIndexMismatch(
            f"상품 후보의 {key} 값이 문자열이 아닙니다."
        )
    return value.strip()


class SharedReferenceProductSearcher:
    """두 상품 컬렉션을 검색한 뒤 PostgreSQL의 현재 판매 상태를 확인한다."""

    def __init__(
        self,
        *,
        client=None,
        eligibility_gateway: EligibilityGateway | None = None,
    ) -> None:
        self.client = client if client is not None else get_client()
        self.eligibility_gateway = eligibility_gateway or DjangoEligibilityGateway()

    def search(
        self,
        request: SharedReferenceProductSearchRequest,
    ) -> SharedReferenceProductSearchResult:
        threshold = self._validate_request(request)
        category_budget, remaining_total, effective_max = self._budgets(request)
        query_filter = self._query_filter(request, effective_max)

        raw_candidates: list[_RawProductCandidate] = []
        try:
            for collection_name in product_collection_names():
                raw_candidates.extend(
                    self._query_collection(
                        collection_name=collection_name,
                        request=request,
                        query_filter=query_filter,
                        threshold=threshold,
                    )
                )
        except SharedReferenceProductSearchError:
            raise
        except Exception as exc:
            raise SharedReferenceProductStoreUnavailable(
                "상품 이미지 벡터 검색을 수행할 수 없습니다."
            ) from exc

        raw_candidates.sort(key=lambda row: (-row.score, row.item.point_id))
        eligibility = self.eligibility_gateway.check(
            tuple(row.item for row in raw_candidates),
            user_id=None,
        )

        candidates: list[SimilarProductCandidate] = []
        seen: set[tuple[str, str]] = set()
        for raw in raw_candidates:
            status = eligibility.get(raw.item.identity)
            if status is None or not status.eligible or status.current_price is None:
                continue
            if effective_max is not None and status.current_price > effective_max:
                continue
            key = (_string(raw.item.payload, "source"), raw.item.source_id)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(self._candidate(raw, current_price=status.current_price))
            if len(candidates) >= request.limit:
                break

        result_candidates = tuple(candidates)
        return SharedReferenceProductSearchResult(
            reference_point_id=request.reference.point_id,
            category_large=request.reference.tags.category_large,
            layer_role=request.reference.tags.layer_role,
            category_budget=category_budget,
            remaining_total_budget=remaining_total,
            effective_max_price=effective_max,
            min_similarity=threshold,
            candidates=result_candidates,
            selected_anchor=result_candidates[0] if result_candidates else None,
        )

    @staticmethod
    def _validate_request(request: SharedReferenceProductSearchRequest) -> float:
        reference = request.reference
        if not reference.image_vector:
            raise SharedReferenceProductSearchInvalid(
                "공유 옷의 이미지 벡터가 필요합니다."
            )
        if not reference.tags.category_large:
            raise SharedReferenceProductSearchInvalid(
                "공유 옷의 대분류 태그가 필요합니다."
            )
        if not reference.tags.layer_role:
            raise SharedReferenceProductSearchInvalid(
                "공유 옷의 슬롯(layer_role) 태그가 필요합니다."
            )
        if not 1 <= request.limit <= 50:
            raise SharedReferenceProductSearchInvalid(
                "limit은 1 이상 50 이하여야 합니다."
            )
        for label, amount in (
            ("total_budget", request.total_budget),
            ("already_selected_total", request.already_selected_total),
        ):
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                if label == "total_budget" and amount is None:
                    continue
                raise SharedReferenceProductSearchInvalid(
                    f"{label}은 0 이상의 정수여야 합니다."
                )
        if any(
            not isinstance(category, str)
            or not category.strip()
            or isinstance(amount, bool)
            or not isinstance(amount, int)
            or amount < 0
            for category, amount in request.category_budgets.items()
        ):
            raise SharedReferenceProductSearchInvalid(
                "category_budgets는 대분류별 0 이상의 정수여야 합니다."
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
            raise SharedReferenceProductSearchInvalid(
                "min_similarity는 0 이상 1 이하의 숫자여야 합니다."
            )
        return float(threshold)

    @staticmethod
    def _budgets(
        request: SharedReferenceProductSearchRequest,
    ) -> tuple[int | None, int | None, int | None]:
        category_budget = request.category_budgets.get(
            request.reference.tags.category_large
        )
        remaining_total = (
            None
            if request.total_budget is None
            else max(0, request.total_budget - request.already_selected_total)
        )
        caps = [
            amount
            for amount in (category_budget, remaining_total)
            if amount is not None
        ]
        return (
            category_budget,
            remaining_total,
            min(caps) if caps else None,
        )

    @staticmethod
    def _query_filter(
        request: SharedReferenceProductSearchRequest,
        effective_max_price: int | None,
    ) -> qm.Filter:
        conditions: list[qm.Condition] = [
            _match_value("tagging_status", "tagged"),
            _match_value(
                "category_large",
                request.reference.tags.category_large,
            ),
            _match_value("layer_role", request.reference.tags.layer_role),
        ]
        if effective_max_price is not None:
            conditions.append(
                qm.FieldCondition(
                    key="price",
                    range=qm.Range(gte=0, lte=effective_max_price),
                )
            )
        return qm.Filter(must=conditions)

    def _query_collection(
        self,
        *,
        collection_name: str,
        request: SharedReferenceProductSearchRequest,
        query_filter: qm.Filter,
        threshold: float,
    ) -> list[_RawProductCandidate]:
        if hasattr(self.client, "query_points"):
            response = self.client.query_points(
                collection_name=collection_name,
                query=list(request.reference.image_vector),
                using=IMAGE_VECTOR,
                query_filter=query_filter,
                score_threshold=threshold,
                limit=request.limit,
                with_payload=True,
                with_vectors=False,
            )
            hits = response.points
        else:  # qdrant-client 구버전 호환
            hits = self.client.search(
                collection_name=collection_name,
                query_vector=(IMAGE_VECTOR, list(request.reference.image_vector)),
                query_filter=query_filter,
                score_threshold=threshold,
                limit=request.limit,
                with_payload=True,
                with_vectors=False,
            )
        return [
            self._raw_candidate(
                hit,
                collection_name=collection_name,
                request=request,
                threshold=threshold,
            )
            for hit in hits
        ]

    @staticmethod
    def _raw_candidate(
        hit: Any,
        *,
        collection_name: str,
        request: SharedReferenceProductSearchRequest,
        threshold: float,
    ) -> _RawProductCandidate:
        try:
            score = float(hit.score)
        except (TypeError, ValueError, AttributeError) as exc:
            raise SharedReferenceProductIndexMismatch(
                "상품 후보의 이미지 유사도 점수가 올바르지 않습니다."
            ) from exc
        if not math.isfinite(score) or score < threshold:
            raise SharedReferenceProductIndexMismatch(
                "상품 후보의 이미지 유사도 점수가 검색 계약과 다릅니다."
            )
        payload = getattr(hit, "payload", None)
        if not isinstance(payload, Mapping):
            raise SharedReferenceProductIndexMismatch(
                "상품 후보의 Qdrant payload가 없습니다."
            )

        expected_source = (
            "naver"
            if collection_name == collection_spec("products_naver").name
            else "eleven"
        )
        source = _string(payload, "source")
        external_product_id = _string(payload, "external_product_id")
        category_large = _string(payload, "category_large")
        layer_role = _string(payload, "layer_role")
        if source != expected_source or not external_product_id:
            raise SharedReferenceProductIndexMismatch(
                "상품 후보의 출처 또는 상품 ID가 컬렉션 계약과 다릅니다."
            )
        if payload.get("tagging_status") != "tagged":
            raise SharedReferenceProductIndexMismatch(
                "태깅 완료 전 상품이 검색 결과에 포함됐습니다."
            )
        if category_large != request.reference.tags.category_large:
            raise SharedReferenceProductIndexMismatch(
                "상품 후보의 대분류가 공유 옷과 다릅니다."
            )
        if layer_role != request.reference.tags.layer_role:
            raise SharedReferenceProductIndexMismatch(
                "상품 후보의 슬롯이 공유 옷과 다릅니다."
            )

        point_id = str(hit.id)
        item = OutfitItem(
            slot_id=f"shared-reference:{request.reference.point_id}",
            template_point_id=request.reference.point_id,
            category_large=category_large,
            layer_role=layer_role,
            source_type=ItemSource.PRODUCT,
            source_id=external_product_id,
            source_collection=collection_name,
            point_id=point_id,
            image_ref=_string(payload, "image_s3_key") or _string(payload, "image_url"),
            price=None,
            score=score,
            reasons=(
                f"공유 옷 이미지 유사도: {score:.4f}",
                f"동일 슬롯: {layer_role}",
                f"동일 대분류: {category_large}",
            ),
            payload=dict(payload),
        )
        return _RawProductCandidate(item=item, score=score)

    @staticmethod
    def _candidate(
        raw: _RawProductCandidate,
        *,
        current_price: int,
    ) -> SimilarProductCandidate:
        payload = raw.item.payload
        return SimilarProductCandidate(
            match_type="VISUAL_SIMILAR",
            selection_role="NEW_ITEM_ANCHOR",
            point_id=raw.item.point_id,
            source=_string(payload, "source"),
            external_product_id=raw.item.source_id,
            source_collection=raw.item.source_collection,
            visual_score=round(raw.score, 4),
            price=int(current_price),
            title=_string(payload, "title"),
            brand=_string(payload, "brand"),
            mall_name=_string(payload, "mall_name"),
            link=_string(payload, "link"),
            image_url=_string(payload, "image_url"),
            image_s3_key=_string(payload, "image_s3_key"),
            category_large=raw.item.category_large,
            category_small=_string(payload, "category_small"),
            layer_role=raw.item.layer_role,
            tagging_status="tagged",
            sale_status="ON_SALE",
        )


def search_similar_products(
    request: SharedReferenceProductSearchRequest,
    *,
    client=None,
    eligibility_gateway: EligibilityGateway | None = None,
) -> SharedReferenceProductSearchResult:
    return SharedReferenceProductSearcher(
        client=client,
        eligibility_gateway=eligibility_gateway,
    ).search(request)
