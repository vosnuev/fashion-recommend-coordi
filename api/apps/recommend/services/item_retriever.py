"""골든 코디의 슬롯별 대체·보완 아이템 후보 검색.

완성된 조합을 만드는 책임은 OutfitComposer에 있다. 이 모듈은 골든 아이템 하나를
기준으로 옷장, 골든셋 아이템, 상품 컬렉션에서 같은 슬롯의 후보와 근거만 반환한다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from qdrant_client import models as qm

from apps.recommend.services.gender import conflicting_item
from apps.recommend.services.qdrant import (
    GOLDEN_ITEM_COLLECTION,
    IMAGE_VECTOR,
    TEXT_VECTOR,
    collection_spec,
    get_client,
    product_collection_names,
)


class ItemRetrievalError(RuntimeError):
    """아이템 후보 검색을 안전하게 수행할 수 없는 경우."""


class TemplateItemNotFound(ItemRetrievalError):
    """기준 골든 아이템이 존재하지 않는 경우."""


class ItemSource(StrEnum):
    WARDROBE = "WARDROBE"
    GOLDENSET_ITEM = "GOLDENSET_ITEM"
    PRODUCT = "PRODUCT"


@dataclass(frozen=True)
class ItemRetrievalRequest:
    template_item_point_id: str
    sources: tuple[ItemSource, ...] = (
        ItemSource.WARDROBE,
        ItemSource.GOLDENSET_ITEM,
        ItemSource.PRODUCT,
    )
    user_id: int | None = None
    allowed_wardrobe_item_ids: tuple[str, ...] | None = None
    max_price: int | None = None
    category_budgets: dict[str, int] = field(default_factory=dict)
    dataset_version: str = ""
    dataset_statuses: tuple[str, ...] = ()
    limit_per_source: int = 10
    #: 사용자가 명시적으로 기피한 태그 (Qdrant 필드명 -> 라벨).
    avoided_tags: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: 요청 계절(봄/여름/가을/겨울/간절기). 상품 season 태그와 맞는 후보를 앞세운다.
    #: 검증 단계에 SEASON_MISMATCH가 있지만 그건 조합이 만들어진 뒤라 선택을 못 바꾼다.
    season: str = ""
    #: 사용자 성별. 골든 코디에만 걸려 있던 성별 충돌 검사를 아이템 치환에도 쓴다 —
    #: 상품 payload에 성별 필드가 없어 규칙 기반 conflicting_item()으로 판정한다.
    gender: str = ""
    #: 요청 상황 분류(OccasionKind). 자유 문구가 아니라 표준 분류값이라
    #: 표현이 갈라져도 규칙이 새지 않는다.
    occasion_kind: str = ""
    #: 사용자가 이번 발화에서 요청한 태그 (Qdrant 필드명 -> 라벨).
    #:
    #: 예전에는 기피 태그만 여기까지 왔고 선호·요청 조건은 골든 코디 검색에서
    #: 끝났다. 그래서 아이템은 오직 템플릿 아이템 벡터와의 유사도로만 뽑혔고,
    #: 같은 템플릿이 걸리면 "러블리로 바꿔줘"라고 해도 코디가 통째로 그대로
    #: 재현됐다. 요청 조건이 마지막 선택 단계까지 도달해야 한다.
    preferred_tags: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class TemplateItem:
    point_id: str
    payload: dict[str, Any]
    image_vector: tuple[float, ...] = ()
    text_vector: tuple[float, ...] = ()


@dataclass(frozen=True)
class ItemCandidate:
    point_id: str
    source_type: ItemSource
    source_id: str
    source_collection: str
    score: float | None
    reasons: tuple[str, ...]
    payload: dict[str, Any] = field(default_factory=dict)
    #: 템플릿 아이템 대비 교체 적합도 (1.0이면 같은 성격의 자리로 교체).
    replacement_fit: float = 1.0

    @property
    def is_owned(self) -> bool:
        return self.source_type is ItemSource.WARDROBE

    @property
    def is_purchasable(self) -> bool:
        return self.source_type is ItemSource.PRODUCT

    @property
    def price(self) -> int | None:
        raw = self.payload.get("price")
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def image_ref(self) -> str:
        for key in ("s3_key", "image_s3_key", "image_url"):
            if value := self.payload.get(key):
                return str(value)
        return ""


@dataclass(frozen=True)
class ItemRetrievalResult:
    template: TemplateItem
    candidates: tuple[ItemCandidate, ...]
    vector_name: str
    pinned_candidate: ItemCandidate | None = None

    def for_source(self, source_type: ItemSource) -> tuple[ItemCandidate, ...]:
        return tuple(
            candidate
            for candidate in self.candidates
            if candidate.source_type is source_type
        )


def _match_value(field_name: str, value: Any) -> qm.FieldCondition:
    return qm.FieldCondition(key=field_name, match=qm.MatchValue(value=value))


def _match_any(field_name: str, values: Iterable[str]) -> qm.FieldCondition:
    return qm.FieldCondition(
        key=field_name,
        match=qm.MatchAny(any=sorted(set(values))),
    )


#: 코디에 넣을 수 없는 용도. 겉옷 자리에 실내복·속옷이 들어오는 것을 막는다.
#:
#: "에어리즘 민소매 캐미솔 런닝"이 나들이룩 상의로 뽑힌 적이 있다. 이건 치환이 원본과
#: 달라진 문제가 아니라 애초에 코디 아이템이 아닌 것이라, 후보 단계에서 뺀다.
logger = logging.getLogger(__name__)


_EXCLUDED_USAGE = ("수면", "홈웨어", "언더웨어", "속옷")

#: TPO와 정면으로 어긋나는 용도. 위 _EXCLUDED_USAGE가 "언제든 코디 아이템이 아닌 것"이라면
#: 이쪽은 "이 자리에는 아닌 것"이라 요청에 따라 켜고 끈다.
#:
#: 출근룩 요청에 밀짚모자·라탄백이 들어가던 문제 때문에 넣었다. 골든 템플릿의 75퍼센트가
#: 액세서리 슬롯을 갖고 있고 그 슬롯은 대분류·소분류·벡터 유사도로만 채워지므로,
#: 요청한 TPO가 아이템 선택에 개입할 통로가 여기 말고는 없다.
#:
#: ⚠️ 이걸로 선글라스는 걸리지 않는다. 상품 태깅상 선글라스는 '외출'이라 근거가 없다
#:    (레이밴·페라가모·헌터 전부 usage=['외출']). 그건 슬롯을 비우는 쪽으로 풀어야 한다.
_TPO_EXCLUDED_USAGE = ("휴양지", "수영", "수영장", "여행", "운동")

#: 분류별 배제 용도. 자유 문구를 키워드로 훑지 않고 **분류값만** 본다 —
#: "출근할 때"·"사무실"·"출장"처럼 표현이 갈라져도 분류는 FORMAL 하나로 모이므로
#: 규칙이 새지 않는다. 분류는 요청 분석 단계(OccasionKind)가 책임진다.
#:
#: RESORT·ACTIVE·HOME은 그 용도가 곧 요청이라 아무것도 빼지 않는다.
#: UNKNOWN도 마찬가지다 — 근거 없이 후보를 줄이지 않는다.
_OCCASION_KIND_EXCLUDED_USAGE = {
    "FORMAL": _TPO_EXCLUDED_USAGE,
    "EVENT": _TPO_EXCLUDED_USAGE,
    "DATE": _TPO_EXCLUDED_USAGE,
    "DAILY": ("휴양지", "수영", "수영장"),
}


def occasion_excluded_usages(occasion_kind: str) -> tuple[str, ...]:
    """요청 상황 분류에서 배제할 용도 태그. 해당 없으면 빈 튜플."""

    return _OCCASION_KIND_EXCLUDED_USAGE.get((occasion_kind or "").strip().upper(), ())


#: 격식 있는 자리에 어울리지 않아 **슬롯 자체를 건너뛰는** 액세서리 소분류.
#:
#: usage 태그로는 막을 수 없어서 둔다. 확인된 선글라스는 레이밴·페라가모·헌터 모두
#: usage=['외출']이라 배제 근거가 없고, 버킷햇도 상당수가 '외출'·'데일리'다.
#: 골든 템플릿의 75퍼센트가 액세서리 슬롯을 갖고 그중 선글라스 슬롯이 116개라,
#: 슬롯을 채우는 한 출근룩에 선글라스가 계속 들어간다. 안 채우는 쪽이 확실하다.
_OCCASION_KIND_SKIPPED_SMALLS = {
    "FORMAL": frozenset({"모자", "안경/선글라스"}),
    "EVENT": frozenset({"모자", "안경/선글라스"}),
}


def occasion_skipped_smalls(occasion_kind: str) -> frozenset[str]:
    """이 상황에서는 채우지 않을 아이템 소분류. 해당 없으면 빈 집합."""

    return _OCCASION_KIND_SKIPPED_SMALLS.get(
        (occasion_kind or "").strip().upper(), frozenset()
    )


def _usage_exclusions(request: "ItemRetrievalRequest | None" = None) -> list[qm.Condition]:
    labels = _EXCLUDED_USAGE + occasion_excluded_usages(
        request.occasion_kind if request is not None else ""
    )
    return [_match_any("usage", labels)]


def _vector(point: Any, name: str) -> tuple[float, ...]:
    vectors = getattr(point, "vector", None)
    if not isinstance(vectors, dict):
        return ()
    raw = vectors.get(name)
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(float(value) for value in raw)


def _avoided_conditions(request: ItemRetrievalRequest) -> list[qm.Condition]:
    """기피 태그를 가진 아이템은 후보 검색 단계에서 제외한다.

    예전에는 이 조건이 골든 코디 검색에만 걸리고 아이템 후보 검색에는 없었다.
    그래서 기피 태그를 가진 아이템이 후보로 올라와 조합까지 만들어진 뒤에야
    Validator가 EXPLICIT_TAG_EXCLUDED로 떨어뜨렸고, 후보를 다 소진할 때까지
    반복하다 추천 전체가 실패했다. 걸러야 할 것은 검색에서 거른다.
    """

    return [
        _match_any(tag_field, labels)
        for tag_field, labels in request.avoided_tags.items()
        if labels
    ]


def _payload_tag_values(payload: dict[str, Any], field_name: str) -> set[str]:
    """태그 필드는 단일 문자열일 수도 배열일 수도 있어 집합으로 통일한다."""

    value = payload.get(field_name)
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, (list, tuple)):
        return {str(entry) for entry in value if entry}
    return set()


def _requested_matches(
    payload: dict[str, Any],
    preferred_tags: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    """아이템이 요청 조건 중 무엇을 만족하는지 라벨로 돌려준다."""

    matched: list[str] = []
    for tag_field, labels in preferred_tags.items():
        if not labels:
            continue
        matched.extend(sorted(set(labels) & _payload_tag_values(payload, tag_field)))
    return tuple(dict.fromkeys(matched))


#: 교체가 템플릿에서 얼마나 벗어났는지 잴 축과 감점.
#:
#: category_small이 주 신호다 — 골든·상품 양쪽 다 채워져 있고, 여기가 어긋났다는 건
#: _search_narrow_then_wide가 좁은 조건으로 못 찾아 범위를 넓혔다는 뜻이다.
#: 로퍼 자리에 미들힐, 셔츠 자리에 반팔티가 들어오는 경로가 정확히 이것이다.
#:
#: sleeve·length·fit은 보조다. 상품의 8할이 값이 비어 있어(sleeve 77퍼센트,
#: length 73퍼센트, fit 82퍼센트) 주 신호로 쓸 수 없다. **양쪽 다 값이 있을 때만**
#: 본다 — "정보 없음"과 "안 맞음"은 다르다(_score_context와 같은 원칙).
#:
#: ⚠️ 어휘가 완전히 통일돼 있지 않다(상품에 '롱슬리브'·'5부'·'부츠컷' 등 골든셋에
#:    없는 값이 있다). 정확히 일치할 때만 신뢰하고, 정규화는 태깅 쪽 과제로 남긴다.
_REPLACEMENT_PENALTY = (
    ("category_small", 0.40),
    ("layer_role", 0.20),
    ("sleeve", 0.15),
    ("length", 0.15),
    ("fit", 0.15),
)

#: 색은 별도로 다룬다. 형식이 양쪽에서 다르고(골든은 '브라운', 상품은 ['블랙'])
#: 상품의 68퍼센트가 비어 있어 단순 문자열 비교가 성립하지 않는다. 교집합으로 본다.
#:
#: 감점이 작은 이유는, 색까지 강하게 걸면 같은 종류 후보가 색 때문에 전부 밀려
#: 후보가 말라붙기 때문이다. 같은 종류·같은 계열이 있으면 그쪽을 고르고, 없으면
#: 다른 색이라도 쓴다. 출근 코디에 분홍 니트가 1순위로 오던 건 이 축이 아예
#: 없어서였다 — 종류만 같으면 무슨 색이든 만점이었다.
_COLOR_PENALTY = 0.10


def replacement_fit(
    template_payload: dict[str, Any],
    candidate_payload: dict[str, Any],
) -> tuple[float, tuple[str, ...]]:
    """템플릿 아이템 대비 교체 적합도(0~1)와 어긋난 축 설명."""

    fit = 1.0
    notes: list[str] = []
    for field_name, penalty in _REPLACEMENT_PENALTY:
        origin = _single_value(template_payload, field_name)
        replaced = _single_value(candidate_payload, field_name)
        if not origin or not replaced or origin == replaced:
            continue
        fit -= penalty
        notes.append(f"골든 기준 {field_name} '{origin}' 대신 '{replaced}'")

    origin_colors = _payload_tag_values(template_payload, "color")
    replaced_colors = _payload_tag_values(candidate_payload, "color")
    if origin_colors and replaced_colors and not (origin_colors & replaced_colors):
        fit -= _COLOR_PENALTY
        notes.append(
            f"골든 기준 색 '{'/'.join(sorted(origin_colors))}' 대신 "
            f"'{'/'.join(sorted(replaced_colors))}'"
        )
    return max(fit, 0.0), tuple(notes)


def _season_rank(payload: dict[str, Any], season: str) -> float:
    """계절 적합도. 일치 1.0 > 정보 없음 0.5 > 불일치 0.0.

    "정보 없음"을 불일치와 같이 취급하면 태그가 빈 상품(상당수다)이 전부 뒤로
    밀려 후보가 왜곡된다 — _score_context와 같은 원칙이다.
    """
    if not season:
        return 0.5
    values = _payload_tag_values(payload, "season")
    if not values:
        return 0.5
    return 1.0 if season in values else 0.0


def _drop_gender_conflicts(
    candidates: list[ItemCandidate],
    gender: str,
) -> list[ItemCandidate]:
    """사용자 성별과 충돌하는 아이템을 후보에서 뺀다.

    전부 걸리면 원본을 그대로 둔다 — 슬롯을 못 채우면 조합 자체가 실패한다.
    """
    if not gender:
        return candidates
    kept = []
    for candidate in candidates:
        if conflict := conflicting_item([candidate.payload], gender):
            logger.info("성별 충돌로 아이템 후보 제외: %s (%s)", candidate.source_id, conflict)
            continue
        kept.append(candidate)
    return kept or candidates


def _rank_by_fit(
    candidates: list[ItemCandidate],
    preferred_tags: Mapping[str, tuple[str, ...]],
    template_payload: dict[str, Any],
    season: str = "",
) -> list[ItemCandidate]:
    """요청 일치 → 교체 적합도 → 유사도 순으로 최종 정렬한다.

    유사도만 보면 "이미지가 닮았지만 종류가 다른 옷"이 1순위가 된다. 코디가
    말이 되려면 템플릿이 정해 둔 자리의 성격을 지키는 쪽이 우선이다.
    """

    ranked: list[tuple[int, float, float, float, int, ItemCandidate]] = []
    for order, candidate in enumerate(candidates):
        fit, notes = replacement_fit(template_payload, candidate.payload)
        matches = len(_requested_matches(candidate.payload, preferred_tags))
        season_fit = _season_rank(candidate.payload, season)
        if season_fit == 0.0:
            notes = notes + (f"{season}과 맞지 않는 계절 태그",)
        enriched = replace(candidate, replacement_fit=fit, reasons=candidate.reasons + notes)
        ranked.append(
            (matches, season_fit, fit, candidate.score or 0.0, -order, enriched)
        )
    ranked.sort(key=lambda row: row[:5], reverse=True)
    return [row[5] for row in ranked]


def _rank_by_request(
    candidates: list[ItemCandidate],
    preferred_tags: Mapping[str, tuple[str, ...]],
) -> list[ItemCandidate]:
    """요청 조건을 만족하는 아이템을 앞으로 당기고 근거를 남긴다.

    유사도(score)는 건드리지 않는다 — 검증·조합 단계가 그 값을 그대로 쓰고,
    의미가 '템플릿 아이템과 얼마나 닮았나'라서 요청 일치와 섞으면 안 된다.
    순서와 reasons에만 반영해, 왜 골랐는지가 설명에도 남게 한다.
    """

    if not preferred_tags:
        return candidates
    ranked: list[tuple[int, float, int, ItemCandidate]] = []
    for order, candidate in enumerate(candidates):
        matched = _requested_matches(candidate.payload, preferred_tags)
        enriched = (
            replace(
                candidate,
                reasons=candidate.reasons
                + tuple(f"요청한 '{label}' 일치" for label in matched),
            )
            if matched
            else candidate
        )
        # order를 3번째 키로 둬 동점일 때 원래 유사도 순서를 보존한다.
        ranked.append((len(matched), candidate.score or 0.0, -order, enriched))
    ranked.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    return [row[3] for row in ranked]


def _single_value(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if isinstance(value, str):
        return value.strip()
    return ""


class ItemCandidateRetriever:
    def __init__(self, *, client=None) -> None:
        self.client = client or get_client()

    def retrieve(self, request: ItemRetrievalRequest) -> ItemRetrievalResult:
        self._validate_request(request)
        template = self._load_template(request.template_item_point_id)
        category_budget = request.category_budgets.get(
            _single_value(template.payload, "category_large")
        )
        effective_max_price = (
            min(
                price
                for price in (
                    request.max_price,
                    category_budget,
                )
                if price is not None
            )
            if any(
                price is not None
                for price in (
                    request.max_price,
                    category_budget,
                )
            )
            else None
        )
        vector_name, vector = self._select_vector(template)
        common_conditions = self._common_conditions(template.payload)
        narrow_conditions = self._narrow_conditions(template.payload)

        by_source: dict[ItemSource, list[ItemCandidate]] = {}
        for source_type in request.sources:
            if source_type is ItemSource.WARDROBE:
                by_source[source_type] = self._retrieve_wardrobe(
                    request,
                    common_conditions,
                    narrow_conditions,
                    vector_name,
                    vector,
                )
            elif source_type is ItemSource.GOLDENSET_ITEM:
                by_source[source_type] = self._retrieve_goldenset(
                    request,
                    common_conditions,
                    narrow_conditions,
                    vector_name,
                    vector,
                )
            elif source_type is ItemSource.PRODUCT:
                by_source[source_type] = self._retrieve_products(
                    request,
                    common_conditions,
                    narrow_conditions,
                    vector_name,
                    vector,
                    max_price=effective_max_price,
                )

        # 호출부가 지정한 출처 순서를 유지한다. 출처 내부에서는
        # 요청 일치 → 교체 적합도 → 유사도 순이다.
        candidates = tuple(
            candidate
            for source_type in request.sources
            for candidate in _rank_by_fit(
                _drop_gender_conflicts(by_source.get(source_type, []), request.gender),
                request.preferred_tags,
                template.payload,
                request.season,
            )
        )
        return ItemRetrievalResult(
            template=template,
            candidates=candidates,
            vector_name=vector_name,
        )

    @staticmethod
    def _validate_request(request: ItemRetrievalRequest) -> None:
        if (
            not isinstance(request.template_item_point_id, str)
            or not request.template_item_point_id.strip()
        ):
            raise ValueError("template_item_point_id가 필요합니다.")
        if not 1 <= request.limit_per_source <= 50:
            raise ValueError("limit_per_source는 1 이상 50 이하여야 합니다.")
        if len(set(request.sources)) != len(request.sources):
            raise ValueError("sources에 같은 출처를 중복 지정할 수 없습니다.")
        if ItemSource.WARDROBE in request.sources and (
            not isinstance(request.user_id, int)
            or isinstance(request.user_id, bool)
            or request.user_id <= 0
        ):
            raise ValueError("옷장 후보 검색에는 양수 user_id가 필요합니다.")
        if request.max_price is not None and (
            not isinstance(request.max_price, int)
            or isinstance(request.max_price, bool)
            or request.max_price < 0
        ):
            raise ValueError("max_price는 0 이상의 정수여야 합니다.")
        if any(
            not isinstance(category, str)
            or not isinstance(amount, int)
            or isinstance(amount, bool)
            or amount < 0
            for category, amount in request.category_budgets.items()
        ):
            raise ValueError("category_budgets는 대분류별 0 이상의 정수여야 합니다.")
    def _load_template(self, point_id: str) -> TemplateItem:
        points = self.client.retrieve(
            collection_name=GOLDEN_ITEM_COLLECTION,
            ids=[point_id],
            with_payload=True,
            with_vectors=True,
        )
        if not points:
            raise TemplateItemNotFound(f"골든 아이템을 찾을 수 없습니다: {point_id}")
        point = points[0]
        return TemplateItem(
            point_id=str(point.id),
            payload=point.payload or {},
            image_vector=_vector(point, IMAGE_VECTOR),
            text_vector=_vector(point, TEXT_VECTOR),
        )

    @staticmethod
    def _select_vector(template: TemplateItem) -> tuple[str, list[float] | None]:
        if template.image_vector:
            return IMAGE_VECTOR, list(template.image_vector)
        if template.text_vector:
            return TEXT_VECTOR, list(template.text_vector)
        return "filter", None

    @staticmethod
    def _common_conditions(payload: dict[str, Any]) -> list[qm.Condition]:
        conditions: list[qm.Condition] = []
        # 최소 대분류는 있어야 상의를 하의로 교체하는 식의 잘못된 후보를 막는다.
        category_large = _single_value(payload, "category_large")
        if not category_large:
            raise ItemRetrievalError("골든 아이템에 category_large가 없습니다.")
        conditions.append(_match_value("category_large", category_large))

        return conditions

    def _search_narrow_then_wide(
        self,
        *,
        base: list[qm.Condition],
        narrow: list[qm.Condition],
        want: int,
        run,
    ) -> list[ItemCandidate]:
        """세부 카테고리로 먼저 찾고, 모자라면 그 조건만 빼고 채운다.

        좁힌 결과가 앞이고 넓힌 결과가 뒤다 — 같은 종류의 옷이 늘 더 적절하다.
        중복은 제거한다.
        """
        if not narrow:
            return run(base)
        found = run(base + narrow)
        if len(found) >= want:
            return found
        seen = {candidate.point_id for candidate in found}
        # 전부 푸는 대신 뒤에서부터 하나씩 푼다. category_small(옷 종류)이 layer_role
        # 보다 뒤에 있어 마지막까지 남는다 — 종류가 바뀌는 쪽이 더 어색하다.
        for depth in range(len(narrow) - 1, -1, -1):
            for candidate in run(base + narrow[depth:]):
                if candidate.point_id not in seen:
                    found.append(candidate)
                    seen.add(candidate.point_id)
                if len(found) >= want:
                    return found
        for candidate in run(base):
            if candidate.point_id not in seen:
                found.append(candidate)
                seen.add(candidate.point_id)
            if len(found) >= want:
                break
        return found

    @staticmethod
    def _narrow_conditions(payload: dict[str, Any]) -> list[qm.Condition]:
        """세부 카테고리까지 좁히는 조건. 후보가 부족하면 이것만 뺀다.

        예전에는 "교체 범위를 과도하게 좁힌다"는 이유로 category_small을 아예 걸지
        않았다. 그 결과 민소매 티셔츠가 긴팔 셔츠로, 장갑이 머플러로 바뀌었다. 골든
        코디가 민소매+셔츠 레이어드였는데 치환 뒤 셔츠 두 장이 되는 식이다.

        좁게 먼저 찾고 모자랄 때만 푸는 방식이면 원래 우려도 함께 해소된다.
        """
        conditions: list[qm.Condition] = []
        # layer_role은 골든 아이템에는 있지만 상품에는 거의 없다(400건 중 4건).
        # 이걸 무조건 걸면 후보가 4건으로 붕괴하고, 상의 슬롯 둘이 같은 웅덩이에서
        # 뽑혀 셔츠가 두 장 나오는 일이 생긴다. 그래서 좁은 조건으로 내려 부족하면
        # 풀리게 한다.
        if layer_role := _single_value(payload, "layer_role"):
            conditions.append(_match_value("layer_role", layer_role))
        if category_small := _single_value(payload, "category_small"):
            conditions.append(_match_value("category_small", category_small))
        return conditions

    def _retrieve_wardrobe(
        self,
        request: ItemRetrievalRequest,
        common_conditions: list[qm.Condition],
        narrow_conditions: list[qm.Condition],
        vector_name: str,
        vector: list[float] | None,
    ) -> list[ItemCandidate]:
        if request.allowed_wardrobe_item_ids == ():
            return []
        conditions = [*common_conditions, _match_value("confirmed", True)]
        if request.allowed_wardrobe_item_ids is None:
            conditions.append(_match_value("user_id", request.user_id))
        else:
            conditions.append(
                qm.HasIdCondition(has_id=list(request.allowed_wardrobe_item_ids))
            )
        return _rank_by_request(
            self._retrieve_collection(
                collection_name=collection_spec("wardrobe").name,
                source_type=ItemSource.WARDROBE,
                conditions=conditions,
                vector_name=vector_name,
                vector=vector,
                limit=request.limit_per_source,
                must_not=_avoided_conditions(request),
            ),
            request.preferred_tags,
        )

    def _retrieve_goldenset(
        self,
        request: ItemRetrievalRequest,
        common_conditions: list[qm.Condition],
        narrow_conditions: list[qm.Condition],
        vector_name: str,
        vector: list[float] | None,
    ) -> list[ItemCandidate]:
        conditions = list(common_conditions)
        if request.dataset_version:
            conditions.append(_match_value("dataset_version", request.dataset_version))
        # golenset_new의 아이템 포인트에는 dataset_status/status가 없고 상태는
        # 부모 outfit_goldenset 포인트가 소유한다. 부모 코디를 승인 상태로
        # 검색한 뒤 전달된 item_point_id이므로 여기서는 버전만 다시 검증한다.
        candidates = self._search_narrow_then_wide(
            base=conditions,
            narrow=narrow_conditions,
            want=request.limit_per_source + 1,
            run=lambda where: self._retrieve_collection(
                collection_name=GOLDEN_ITEM_COLLECTION,
                source_type=ItemSource.GOLDENSET_ITEM,
                conditions=where,
                vector_name=vector_name,
                vector=vector,
                limit=request.limit_per_source + 1,
                must_not=_avoided_conditions(request),
            ),
        )
        return _rank_by_request(
            [
                candidate
                for candidate in candidates
                if candidate.point_id != request.template_item_point_id
            ],
            request.preferred_tags,
        )[: request.limit_per_source]

    def _retrieve_products(
        self,
        request: ItemRetrievalRequest,
        common_conditions: list[qm.Condition],
        narrow_conditions: list[qm.Condition],
        vector_name: str,
        vector: list[float] | None,
        *,
        max_price: int | None,
    ) -> list[ItemCandidate]:
        conditions = [
            *common_conditions,
            _match_value("tagging_status", "tagged"),
        ]
        if max_price is not None:
            conditions.append(
                qm.FieldCondition(
                    key="price",
                    range=qm.Range(lte=max_price),
                )
            )

        candidates: list[ItemCandidate] = []
        for collection_name in product_collection_names():
            candidates.extend(
                self._search_narrow_then_wide(
                    base=conditions,
                    narrow=narrow_conditions,
                    want=request.limit_per_source,
                    run=lambda where, name=collection_name: self._retrieve_collection(
                        collection_name=name,
                        source_type=ItemSource.PRODUCT,
                        conditions=where,
                        vector_name=vector_name,
                        vector=vector,
                        limit=request.limit_per_source,
                        must_not=_avoided_conditions(request) + _usage_exclusions(request),
                    ),
                )
            )
        candidates.sort(
            key=lambda candidate: (
                candidate.score is not None,
                candidate.score if candidate.score is not None else 0.0,
            ),
            reverse=True,
        )
        # 자르기 **전에** 요청 조건을 반영한다. 뒤에서 정렬하면 요청에 맞는
        # 상품이 유사도 컷에 먼저 잘려 나가 반영할 대상 자체가 없어진다.
        return _rank_by_request(candidates, request.preferred_tags)[
            : request.limit_per_source
        ]

    def _retrieve_collection(
        self,
        *,
        collection_name: str,
        source_type: ItemSource,
        conditions: list[qm.Condition],
        vector_name: str,
        vector: list[float] | None,
        limit: int,
        must_not: list[qm.Condition] | None = None,
    ) -> list[ItemCandidate]:
        query_filter = qm.Filter(must=conditions or None, must_not=must_not or None)
        if vector is None:
            points, _ = self.client.scroll(
                collection_name=collection_name,
                scroll_filter=query_filter,
                with_payload=True,
                with_vectors=False,
                limit=limit,
            )
            records = [(str(point.id), None, point.payload or {}) for point in points]
        elif hasattr(self.client, "query_points"):
            response = self.client.query_points(
                collection_name=collection_name,
                query=vector,
                using=vector_name,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
            records = [
                (str(point.id), float(point.score), point.payload or {})
                for point in response.points
            ]
        else:  # qdrant-client 구버전 호환
            points = self.client.search(
                collection_name=collection_name,
                query_vector=(vector_name, vector),
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
            records = [
                (str(point.id), float(point.score), point.payload or {})
                for point in points
            ]

        return [
            ItemCandidate(
                point_id=point_id,
                source_type=source_type,
                source_id=self._source_id(source_type, point_id, payload),
                source_collection=collection_name,
                score=round(score, 4) if score is not None else None,
                reasons=self._reasons(payload, score),
                payload=payload,
            )
            for point_id, score, payload in records
        ]

    @staticmethod
    def _source_id(
        source_type: ItemSource,
        point_id: str,
        payload: dict[str, Any],
    ) -> str:
        if source_type is ItemSource.WARDROBE:
            return str(payload.get("item_id") or point_id)
        if source_type is ItemSource.PRODUCT:
            return str(payload.get("external_product_id") or point_id)
        return point_id

    @staticmethod
    def _reasons(payload: dict[str, Any], score: float | None) -> tuple[str, ...]:
        reasons = [f"대분류 일치: {payload.get('category_large')}"]
        if layer_role := payload.get("layer_role"):
            reasons.append(f"레이어 역할 일치: {layer_role}")
        if score is not None:
            reasons.append(f"템플릿 아이템 유사도: {score:.4f}")
        else:
            reasons.append("벡터 없음: 태그 조건으로 검색")
        return tuple(reasons)
