"""골든 코디 리트리버 — 추천 기능 세 개가 공유하는 베이스.

"오늘의 룩 추천", "옷장 기반 추천", "추구미 기반 추천"이 전부 이 위에 올라간다.
셋의 차이는 입력을 어떻게 채우느냐뿐이고, 검색·필터·점수화는 여기서 한 번만 한다.

가이드 6장의 하이브리드 분담을 그대로 따른다.

    기피/탈락 요건 (Hard)  → 이 모듈. 필터로 즉시 떨어뜨린다.
    가중치·컨텍스트 (Soft) → 이 모듈이 점수와 근거만 계산한다.
    설명문 생성            → 이 모듈 밖(Agent). reasons를 재료로 쓴다.

우선순위는 가이드 Q2를 따른다. **사용자 취향이 1순위, 체형 규칙이 2순위.**
가중치로 그 서열을 표현한다 (preference_avoid -60 vs rule_avoid -20).

이 모듈은 LLM을 부르지 않는다. 순수 함수형 검색 계층이라 테스트가 쉽고, 응답
지연이 예측 가능하다.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Sequence

from django.conf import settings
from qdrant_client import models as qm

from apps.recommend.services import vocabulary
from apps.recommend.services.body_profile import BodyProfile
from apps.recommend.services.gender import (
    GENDER_TO_PRESENTATION as GENDER_TO_PRESENTATION,
)
from apps.recommend.services.gender import (
    PRESENTATION_UNISEX as PRESENTATION_UNISEX,
)
from apps.recommend.services.gender import (
    allowed_presentation_groups,
    conflicting_item,
    normalize_gender,
)
from apps.recommend.services.qdrant import (
    GOLDEN_ITEM_COLLECTION,
    GOLDEN_KNOWLEDGE_COLLECTION,
    GOLDEN_OUTFIT_COLLECTION,
    WARDROBE_ITEM_COLLECTION,
    IMAGE_VECTOR,
    TEXT_VECTOR,
    get_client,
)
from apps.recommend.services.style_rules import (
    BodyRules,
    Rule,
    WeatherBand,
    load_body_rules,
    load_weather_rules,
)
from apps.recommend.services.text_embedding import (
    TextEmbeddingClient,
    TextEmbeddingError,
    get_text_embedding_client,
)

logger = logging.getLogger(__name__)

#: 코디 payload에 인덱스가 있어 Qdrant 필터로 바로 쓸 수 있는 축.
#: `fit`·`length`·`sleeve`는 코디 단계에 인덱스가 없다 — 아이템 컬렉션을 거친다.
OUTFIT_FILTER_FIELDS = frozenset(
    {"style", "season", "occasion", "item_layer_roles", "item_categories"}
)

#: 성별 표현 그룹 상수와 표기 해석은 services/gender.py가 단일 출처다. 예전엔
#: 여기서 직접 들고 있었는데, 해석이 세 파일에 흩어진 탓에 그 중 한 곳에서 빈
#: 문자열이 "None"으로 굳어 성별 필터가 통째로 사라진 적이 있다 (gender.py 참고).
#: 재노출은 기존 import 경로를 쓰는 호출부·테스트를 위한 것이다.

#: 후보를 몇 배수로 넉넉히 뽑아 놓고 점수화 후 자를지. 소프트 감점 때문에
#: 상위 N개가 뒤바뀌므로 limit만큼만 뽑으면 좋은 후보를 놓친다.
_OVERFETCH = 4


@dataclass(frozen=True)
class RetrievalRequest:
    """세 기능이 공유하는 입력. 채우지 않은 축은 그냥 반영되지 않는다."""

    body: BodyProfile | None = None
    #: users/services/pursuit.get_pursuit() 의 payload 그대로.
    #: **축적된 취향**이다 — 온보딩·행동에서 쌓인 것이라 이번 발화와 무관하게
    #: 항상 얹힌다.
    pursuit: dict[str, dict[str, list[str]]] | None = None
    #: 사용자가 **이번 발화에서 직접 말한** 조건. pursuit과 같은 모양
    #: ({"preferred": {...}, "avoided": {...}})이지만 축을 나눠 둔 이유가 있다.
    #: 한 자루에 담으면 "이번엔 러블리로"가 기존 취향과 같은 무게의 가산점
    #: 하나가 돼 서열을 못 바꾼다 — 방금 한 말이 무시되는 것처럼 보인다.
    #: preferred는 Weights.request_match로 크게 가산하고, avoided는 pursuit과
    #: 마찬가지로 하드 필터다.
    requested: dict[str, dict[str, list[str]]] | None = None
    weather: dict[str, Any] | None = None
    #: 사용자 성별 (BodyMeasurement.gender: "male" | "female" | ""). 값이 있으면
    #: 성별 표현이 다른 코디를 검색에서 즉시 탈락시킨다 — 가이드 6장의 하드 필터.
    gender: str = ""
    occasion: str = ""
    season: str = ""
    #: 자유 문구 (예: "비 오는 날 출근룩"). 있으면 텍스트 벡터로 검색한다.
    query_text: str = ""
    text_vector: list[float] | None = None
    #: 코디를 찍은 이미지 벡터로 검색할 때 (옷장 기반 추천의 '비슷한 코디')
    image_vector: list[float] | None = None
    presentation_groups: tuple[str, ...] = ()
    #: 공유 옷 레퍼런스를 쓸 때 해당 아이템을 꽂을 수 있는 골든 슬롯이
    #: 반드시 포함되도록 하는 코디 단위 하드 필터다.
    required_item_categories: tuple[str, ...] = ()
    required_item_layer_roles: tuple[str, ...] = ()
    dataset_version: str = ""
    dataset_statuses: tuple[str, ...] = ()
    limit: int = 10
    #: 기피 규칙을 하드 필터로 걸지. 가이드 6장 기본 동작.
    hard_filter: bool = True
    #: 노출 가능한 원본만 (기본은 제한 없음 — 골든 원본은 대개 exposable=False다)
    exposable_only: bool = False
    #: 결과에서 뺄 골든 코디 id. 오늘의 룩이 "최근 며칠 안에 이미 나간 코디"를
    #: 넘겨 같은 추천이 반복되는 것을 막는다. 점수화가 끝난 뒤 상위 N을 자르기
    #: 직전에 빼므로, 빠진 자리는 다음 순위가 자연스럽게 채운다. 단 후보가
    #: **전부** 여기 걸리면 제외를 풀고 그대로 돌려준다 — 골든셋이 작을 때
    #: 반복 추천이 '추천 없음'보다 낫다.
    exclude_golden_ids: frozenset[str] = frozenset()
    #: 축적된 기피(pursuit.avoided)를 **하드 필터에서만** 뺀다. 점수의
    #: preference_avoid(-60)는 그대로 살아 있어 기피는 여전히 존중된다.
    #: 요구할 골든셋 occasion 태그(데이트/나들이/데일리/출근/여행/모임/행사/운동/홈웨어).
    #: 자유 문구가 아니라 닫힌 어휘라 완전 일치가 성립한다 — 분석기의 OccasionKind를
    #: occasion_kind_tags()로 옮겨서 넘긴다.
    occasion_kinds: tuple[str, ...] = ()
    #: 후보 부족으로 이미 푼 제약. 리트리버가 사다리를 오르며 스스로 채운다.
    relaxed: frozenset[str] = frozenset()
    #: 후보가 이 수 **미만**이면 사다리를 한 칸 오른다. 기본 1은 "0건일 때만".
    #:
    #: limit(골든 템플릿 후보 수, 기본 5)을 기준으로 삼으면 안 된다 — 일반 모드는
    #: max_validated_templates=1이라 템플릿 하나만 통과해도 사용자에게 보여줄 카드
    #: 3장이 나온다. limit 기준으로 걸면 골든셋이 작은 동안 거의 매번 발동해
    #: 필터가 사실상 사라진다.
    relax_below: int = 1


@dataclass(frozen=True)
class Reason:
    """점수가 움직인 이유 한 줄. Agent가 설명문을 만들 재료다."""

    source: str          # "preference" | "rule" | "similarity"
    delta: float
    text: str


@dataclass(frozen=True)
class OutfitCandidate:
    point_id: str
    golden_id: str
    score: float
    similarity: float
    reasons: tuple[Reason, ...] = ()
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def items(self) -> list[dict[str, Any]]:
        return list(self.payload.get("items", []))


@dataclass(frozen=True)
class RetrievalResult:
    candidates: tuple[OutfitCandidate, ...]
    search_mode: str
    embedding_model: str = ""
    embedding_version: str = ""


_PRESENTATION_GROUP_ALIASES = {
    "male": "men",
    "man": "men",
    "men": "men",
    "masculine": "men",
    "남성": "men",
    "female": "women",
    "woman": "women",
    "women": "women",
    "feminine": "women",
    "여성": "women",
    "unisex": "unisex",
    "유니섹스": "unisex",
}


def normalize_presentation_groups(values: Iterable[str]) -> tuple[str, ...]:
    """외부 표기를 골든셋의 men/women/unisex 값으로 통일한다."""
    normalized = {
        mapped
        for value in values
        if (mapped := _PRESENTATION_GROUP_ALIASES.get(str(value).strip().casefold()))
    }
    return tuple(sorted(normalized))


def _effective_presentation_groups(request: RetrievalRequest) -> tuple[str, ...]:
    """프로필 성별 안전 필터와 대화의 명시 조건을 함께 적용한다."""
    allowed = set(allowed_presentation_groups(request.gender))
    requested = set(normalize_presentation_groups(request.presentation_groups))
    if allowed and requested:
        intersection = allowed & requested
        # 서로 충돌할 때 필터를 빼면 반대 성별 코디가 전부 통과한다.
        return tuple(sorted(intersection or {"__no_matching_presentation_group__"}))
    return tuple(sorted(allowed or requested))


def _any_of(field_name: str, values: Iterable[str]) -> qm.FieldCondition:
    return qm.FieldCondition(key=field_name, match=qm.MatchAny(any=sorted(values)))


def _status_condition(statuses: Iterable[str]) -> qm.Filter:
    variants = {
        variant
        for value in statuses
        for variant in (
            str(value).strip(),
            str(value).strip().upper(),
            str(value).strip().lower(),
        )
        if variant
    }
    return qm.Filter(
        should=[
            _any_of("dataset_status", variants),
            _any_of("status", variants),
        ]
    )


def build_filter(
    request: RetrievalRequest,
    *,
    rules: BodyRules | None = None,
) -> qm.Filter | None:
    """검색 단계에서 걸 수 있는 조건만 Qdrant 필터로 만든다.

    코디 포인트에 인덱스가 있는 축만 다룬다. 나머지(핏·기장 등)는 아이템 단계나
    파이썬 후처리로 넘어간다 — `_hard_excluded_outfits()` 참고.
    """
    must: list[qm.Condition] = []
    must_not: list[qm.Condition] = []

    if request.exposable_only:
        must.append(qm.FieldCondition(key="exposable", match=qm.MatchValue(value=True)))

    if request.dataset_version:
        must.append(
            qm.FieldCondition(
                key="dataset_version",
                match=qm.MatchValue(value=request.dataset_version),
            )
        )
    if request.dataset_statuses:
        must.append(_status_condition(request.dataset_statuses))
    if request.required_item_categories:
        must.append(_any_of("item_categories", request.required_item_categories))
    if request.required_item_layer_roles:
        must.append(_any_of("item_layer_roles", request.required_item_layer_roles))

    # 성별은 하드 필터다. 남성 사용자에게 여성 코디를 "순위만 낮춰" 보여주는 건
    # 추천이 아니라 오작동으로 읽힌다. 계절과 달리 감점으로 둘 수 없는 축이다.
    #
    # 라벨이 없는 코디(presentation_group="")는 여기서 함께 빠진다. 미분류를
    # unisex로 취급하면 여성 코디가 그대로 남성에게 나가므로, 조용히 통과시키는
    # 대신 빠지게 두고 EMPTY 사유에 그 사실을 적는다.
    if groups := _effective_presentation_groups(request):
        must.append(_any_of("presentation_group", groups))

    # 계절·상황은 하드 필터로 걸지 않는다.
    #
    # 가이드 6장은 하드 필터를 "절대적인 기피 규칙"에만 쓰라고 했는데 처음엔
    # 여기에 계절까지 얹었다. 그러자 모든 추천이 EMPTY로 끝났다 — 골든 코디의
    # season/style/occasion은 analyses.jsonl에서 오는데 그 분석 단계가 유료
    # 호출이 커서 기본으로 꺼져 있어, 적재된 포인트가 전부 빈 배열이었기
    # 때문이다. 있지도 않은 값에 must를 걸면 결과는 언제나 0건이다.
    #
    # 계절은 "맞으면 좋은 것"이지 "틀리면 탈락"이 아니다. 소프트 가산으로 옮겨
    # _score_context()가 처리한다. 태그가 채워진 뒤에도 이 판단은 유효하다.

    if request.pursuit:
        preferred = vocabulary.translate(request.pursuit.get("preferred"))
        avoided = _resolve_avoided_conflict(
            vocabulary.translate(request.pursuit.get("avoided")).tags,
            request,
        )
        # 선호 스타일은 좁히는 조건이 아니라 넓히는 조건이라 must에 넣지 않는다.
        # 반면 기피 스타일은 사용자가 명시적으로 거부한 것이라 즉시 탈락시킨다.
        if request.hard_filter and "pursuit_avoided" not in request.relaxed:
            for tag_field, labels in avoided.items():
                if tag_field in OUTFIT_FILTER_FIELDS and labels:
                    must_not.append(_any_of(tag_field, labels))
        if preferred.unmapped:
            logger.info(
                "검색에 반영하지 못한 선호 항목: %s",
                sorted(set(preferred.unmapped)),
            )

    if request.occasion_kinds and "occasion" not in request.relaxed:
        must.append(_any_of("occasion", request.occasion_kinds))

    if request.requested and request.hard_filter:
        # 이번 발화의 기피는 축적된 기피와 같게 다룬다 — "검정은 빼줘"는
        # 가산점 문제가 아니라 즉시 탈락 조건이다.
        requested_avoided = vocabulary.translate(request.requested.get("avoided"))
        for tag_field, labels in requested_avoided.tags.items():
            if tag_field in OUTFIT_FILTER_FIELDS and labels:
                must_not.append(_any_of(tag_field, labels))

    if not must and not must_not:
        return None
    return qm.Filter(must=must or None, must_not=must_not or None)


#: 분석기의 OccasionKind → 골든셋 occasion 태그. 골든셋 어휘는 아래 9개로 닫혀 있어
#: 자유 문구("놀러가기")로는 한 건도 안 맞는다. 분류를 거쳐야 매칭이 성립한다.
_OCCASION_KIND_TAGS = {
    "FORMAL": ("출근",),
    "EVENT": ("행사", "모임"),
    "DATE": ("데이트",),
    "DAILY": ("데일리", "나들이"),
    "ACTIVE": ("운동",),
    "RESORT": ("여행",),
    "HOME": ("홈웨어",),
}

#: 후보가 부족할 때 푸는 순서. **덜 중요한 것부터, 사용자가 직접 말한 것은 끝까지 지킨다.**
#:
#: 이 사다리가 필요한 이유는 필터가 계속 쌓여 왔기 때문이다. 성별·데이터셋·기피
#: 스타일·TPO·예산·계절이 각각은 타당한데 곱해지면 후보가 0이 되고, 그게 "조건을
#: 충족 못 해 추천이 안 뜬다"는 오래된 증상의 정체다. 같은 아이디어가 이미
#: exclude_golden_ids와 _search_narrow_then_wide에 따로 구현돼 있었다.
#:
#: 성별·발화에서 직접 말한 조건·예산은 사다리에 없다 — 절대 풀지 않는다.
RELAXATION_LADDER = ("recent", "occasion", "pursuit_avoided")


def occasion_kind_tags(occasion_kind: str) -> tuple[str, ...]:
    """분석기 분류값을 골든셋 occasion 태그로 옮긴다. 모르면 빈 튜플(제약 없음)."""

    return _OCCASION_KIND_TAGS.get((occasion_kind or "").strip().upper(), ())


def _relaxable(request: RetrievalRequest, step: str) -> bool:
    """그 제약이 실제로 걸려 있을 때만 사다리를 한 칸 쓴다."""

    if step in request.relaxed:
        return False
    if step == "recent":
        return bool(request.exclude_golden_ids)
    if step == "occasion":
        return bool(request.occasion_kinds)
    if step == "pursuit_avoided":
        if not request.hard_filter:
            return False
        avoided = vocabulary.translate((request.pursuit or {}).get("avoided")).tags
        return any(avoided.values())
    return False


def _resolve_avoided_conflict(
    avoided_tags: dict[str, set[str]],
    request: RetrievalRequest,
) -> dict[str, set[str]]:
    """이번 발화에서 **직접 요청한** 라벨은 축적된 기피에서 뺀다.

    온보딩에서 "러블리는 싫다"고 했더라도 지금 "러블리로 추천해줘"라고 말했다면
    이번 턴은 요청이 이겨야 한다. 예전에는 축적된 기피가 하드 필터라 항상 이겨서,
    요청한 스타일이 must_not에 걸려 후보가 0건이 되고 추천 자체가 실패했다.

    발화의 기피(requested.avoided)는 그대로 둔다 — 방금 "빼달라"고 한 것이라
    충돌 대상이 아니다.
    """
    requested_preferred = (
        vocabulary.translate((request.requested or {}).get("preferred")).tags
        if request.requested
        else {}
    )
    if not requested_preferred:
        return {field: set(labels) for field, labels in avoided_tags.items()}
    resolved: dict[str, set[str]] = {}
    for tag_field, labels in avoided_tags.items():
        kept = set(labels) - requested_preferred.get(tag_field, set())
        if dropped := set(labels) - kept:
            logger.info(
                "이번 발화 요청이 축적된 기피를 덮어씀: %s=%s",
                tag_field,
                sorted(dropped),
            )
        resolved[tag_field] = kept
    return resolved


def _season_from_weather(weather: dict[str, Any] | None) -> str:
    """기온을 태그 어휘의 계절로 바꾼다. 날씨가 없으면 빈 문자열."""
    if not weather:
        return ""
    temperature = weather.get("temperature")
    if temperature is None:
        return ""
    try:
        celsius = float(temperature)
    except (TypeError, ValueError):
        return ""
    if celsius >= 23:
        return "여름"
    if celsius >= 17:
        return "간절기"
    if celsius >= 9:
        return "가을"
    return "겨울"


#: 코디 payload의 아이템 요약에 없는 태그. 체형 규칙이 정확히 이 축으로 조건을
#: 건다 (body_fit_rules.json의 fit·length·pattern).
#:
#: sync_qdrant의 ITEM_SUMMARY_FIELDS는 화면 구성에 필요한 최소치만 담는다 —
#: item_key·item_name·category_large·category_small·layer_role·color·s3_key.
#: 그래서 `Rule.matches()`가 item.get("fit") == None을 보고 전부 False를
#: 돌려주었고, **모든 체형에서 규칙 점수가 0**이었다. 실루엣이 뭐든 순위가
#: 같으니 체형을 바꿔도 같은 룩이 나온다.
#:
#: 태그 자체는 아이템 컬렉션에 이미 있으므로 조회 시점에 합친다. 재적재로
#: 코디 payload를 늘리면 이 왕복은 사라지고, 그때는 여기가 그냥 무해해진다
#: (이미 값이 있으면 덮어쓰지 않는다).
JOINED_ITEM_TAG_FIELDS = (
    "fit",
    "length",
    "pattern",
    "material",
    "sleeve",
    "style",
    "season",
)

#: point_id -> 태그. 프로세스 수명 동안만 산다.
_ITEM_TAG_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_ITEM_TAG_CACHE_MAX = 20000


def _cache_get(point_id: str, now: float) -> dict[str, Any] | None:
    ttl = settings.RETRIEVER_ITEM_TAG_CACHE_SECONDS
    if ttl <= 0:
        return None
    entry = _ITEM_TAG_CACHE.get(point_id)
    if entry is None or now - entry[0] > ttl:
        return None
    return entry[1]


def _cache_put(point_id: str, tags: dict[str, Any], now: float) -> None:
    if settings.RETRIEVER_ITEM_TAG_CACHE_SECONDS <= 0:
        return
    if len(_ITEM_TAG_CACHE) >= _ITEM_TAG_CACHE_MAX:
        # 정교한 축출은 필요 없다. 골든셋 크기를 넘으면 그냥 비운다.
        _ITEM_TAG_CACHE.clear()
    _ITEM_TAG_CACHE[point_id] = (now, tags)


def clear_item_tag_cache() -> None:
    """테스트와 재적재 직후에 쓴다."""
    _ITEM_TAG_CACHE.clear()


def _fetch_item_tags(client, point_ids: list[str]) -> dict[str, dict[str, Any]]:
    """아이템 포인트에서 태그만 가져온다. 캐시에 있는 건 건너뛴다."""
    now = time.monotonic()
    found: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for point_id in point_ids:
        cached = _cache_get(point_id, now)
        if cached is None:
            missing.append(point_id)
        else:
            found[point_id] = cached

    batch = max(1, settings.RETRIEVER_ITEM_TAG_BATCH)
    for start in range(0, len(missing), batch):
        chunk = missing[start : start + batch]
        try:
            points = client.retrieve(
                collection_name=GOLDEN_ITEM_COLLECTION,
                ids=chunk,
                with_payload=list(JOINED_ITEM_TAG_FIELDS),
                with_vectors=False,
            )
        except Exception:  # noqa: BLE001 — 태그를 못 붙여도 추천은 나가야 한다
            logger.warning(
                "아이템 태그 조회 실패 (%d건). 체형 규칙이 이번 요청에서는 "
                "적용되지 않습니다.", len(chunk), exc_info=True,
            )
            continue
        for point in points:
            tags = {
                field: value
                for field, value in (point.payload or {}).items()
                if field in JOINED_ITEM_TAG_FIELDS and value not in (None, "", [])
            }
            found[str(point.id)] = tags
            _cache_put(str(point.id), tags, now)

    return found


def attach_item_tags(client, records: list[tuple[str, float, dict[str, Any]]]) -> int:
    """코디 payload의 아이템 요약에 핏·기장·패턴 태그를 채워 넣는다.

    Returns: 태그를 붙인 아이템 수 (진단용).

    payload를 제자리에서 고친다. 이 dict는 방금 Qdrant에서 받아온 사본이고
    호출부(`_build_result`)도 같은 값을 쓰므로, 복사본을 따로 두면 화면과
    점수가 서로 다른 아이템을 보게 된다.
    """
    if not settings.RETRIEVER_ITEM_TAG_JOIN:
        return 0

    wanted: list[str] = []
    for _point_id, _similarity, payload in records:
        for item in payload.get("items") or []:
            point_id = str(item.get("point_id") or "")
            # 이미 태그가 있으면(재적재 이후) 굳이 조회하지 않는다.
            if point_id and not any(item.get(f) for f in JOINED_ITEM_TAG_FIELDS):
                wanted.append(point_id)
    if not wanted:
        return 0

    tags_by_point = _fetch_item_tags(client, sorted(set(wanted)))
    if not tags_by_point:
        return 0

    attached = 0
    for _point_id, _similarity, payload in records:
        for item in payload.get("items") or []:
            tags = tags_by_point.get(str(item.get("point_id") or ""))
            if not tags:
                continue
            for tag_field, value in tags.items():
                # 코디 payload의 값이 우선이다. 재적재로 값이 생기면 그쪽이
                # 그 시점의 진실이다.
                item.setdefault(tag_field, value)
            attached += 1
    return attached


def _hard_excluded_outfits(
    client, rules: BodyRules, profile: BodyProfile, limit: int
) -> set[str]:
    """하드 기피 규칙에 걸리는 아이템을 가진 코디의 point_id를 모은다.

    코디 포인트에는 핏·기장 인덱스가 없다. 그래서 아이템 컬렉션에서 먼저
    '걸리는 아이템'을 찾고 그 `outfit_point_id`를 제외 목록으로 쓴다. 코디
    payload에 핏을 심어 인덱싱하면 이 왕복이 사라지지만, 그건 재적재가 필요하다.
    """
    hard = [rule for rule in rules.for_profile(profile).avoid if rule.hard]
    if not hard:
        return set()

    excluded: set[str] = set()
    for rule in hard:
        conditions = [
            qm.FieldCondition(key=field_name, match=qm.MatchValue(value=value))
            for field_name, value in rule.match.items()
            # 아이템 컬렉션에 인덱스가 있는 축만. `length`·`sleeve`는 payload에
            # 있어도 인덱스가 없어 필터로 못 쓴다 (services/qdrant.py 참고).
            if field_name in {"category_large", "category_small", "fit", "color",
                              "pattern", "material", "layer_role", "style", "season"}
        ]
        if len(conditions) != len(rule.match):
            # 인덱스 없는 축이 섞인 규칙은 이 단계에서 정확히 걸 수 없다.
            # 소프트 감점으로는 여전히 작동하므로 조용히 넘어가되 흔적을 남긴다.
            logger.debug("하드 필터로 옮기지 못한 규칙: %s", rule.match)
            continue
        points, _ = client.scroll(
            collection_name=GOLDEN_ITEM_COLLECTION,
            scroll_filter=qm.Filter(must=conditions),
            with_payload=["outfit_point_id"],
            with_vectors=False,
            limit=limit,
        )
        excluded.update(
            str(point.payload.get("outfit_point_id"))
            for point in points
            if point.payload.get("outfit_point_id")
        )
    return excluded


def _tag_values(item: dict[str, Any], field: str) -> set[str]:
    """아이템 태그 하나를 **항상 집합으로** 읽는다.

    같은 축이라도 값이 하나일 수도(fit="오버핏") 여럿일 수도(style=["미니멀",
    "캐주얼"]) 있다. 아이템 컬렉션의 style·season이 리스트라, 태그 조인을
    붙인 뒤 `value in labels`가 리스트를 집합에 넣으려다 죽었다:

        TypeError: unhashable type: 'list'

    Rule.matches()는 이미 리스트를 다루고 있었는데 취향 매칭만 스칼라를
    가정하고 있었다. 두 곳이 같은 방식으로 읽도록 여기서 통일한다.
    """
    value = item.get(field)
    if isinstance(value, (list, tuple, set)):
        return {v for v in value if isinstance(v, str) and v}
    return {value} if isinstance(value, str) and value else set()


def _score_items(
    items: list[dict[str, Any]],
    *,
    rules_prefer: tuple[Rule, ...],
    rules_avoid: tuple[Rule, ...],
    preferred_tags: dict[str, set[str]],
    avoided_tags: dict[str, set[str]],
    weights,
    requested_tags: dict[str, set[str]] | None = None,
) -> tuple[float, list[Reason]]:
    """코디에 속한 아이템들을 규칙·취향에 비추어 점수화한다.

    같은 근거가 아이템마다 반복되면 설명이 지저분해지므로 이유는 한 번만 남긴다.
    """
    total = 0.0
    reasons: list[Reason] = []
    seen: set[str] = set()

    def add(delta: float, source: str, text: str) -> None:
        """같은 근거는 코디당 한 번만 센다.

        예전에는 점수만 아이템마다 누적하고 이유는 한 번만 남겼다. 그래서
        상의가 셋인 코디는 같은 규칙으로 +45를 받는데 설명에는 +15 한 줄만
        보였다 — 점수와 설명이 서로 다른 말을 했다. 게다가 순위가 '규칙에
        얼마나 맞는가'가 아니라 '아이템이 몇 개인가'로 정해진다.
        """
        nonlocal total
        if text in seen:
            return
        seen.add(text)
        total += delta
        reasons.append(Reason(source=source, delta=delta, text=text))

    for item in items:
        for tag_field, labels in avoided_tags.items():
            for value in sorted(labels & _tag_values(item, tag_field)):
                add(
                    weights.preference_avoid,
                    "preference",
                    f"기피 항목 '{value}'이(가) 포함됨",
                )
        for tag_field, labels in preferred_tags.items():
            for value in sorted(labels & _tag_values(item, tag_field)):
                add(weights.preference_match, "preference", f"선호 항목 '{value}' 일치")
        for tag_field, labels in (requested_tags or {}).items():
            for value in sorted(labels & _tag_values(item, tag_field)):
                add(weights.request_match, "request", f"요청한 '{value}' 일치")

        for rule in rules_avoid:
            if rule.matches(item):
                add(weights.rule_avoid, "rule", rule.reason)
        for rule in rules_prefer:
            if rule.matches(item):
                add(weights.rule_prefer, "rule", rule.reason)

    return total, reasons


def celsius_of(weather: dict[str, Any] | None) -> float | None:
    """날씨 dict에서 섭씨 기온을 꺼낸다. 값이 없거나 숫자가 아니면 None."""
    if not weather:
        return None
    try:
        return float(weather.get("temperature"))
    except (TypeError, ValueError):
        return None


def _score_weather(
    items: list[dict[str, Any]], band: WeatherBand | None, weights
) -> tuple[float, list[Reason]]:
    """기온대에 맞지 않는 아이템을 감점하고 맞는 아이템을 가산한다.

    검색 필터로 아예 제외하지 않는 이유가 있다. 27도에 아우터가 든 코디를 전부
    빼버리면, 골든셋이 아우터 코디 위주일 때 후보가 0건이 되어 사용자는 아무것도
    못 본다 — 계절을 하드 필터로 걸었다가 모든 추천이 EMPTY로 끝난 적이 있다.
    감점은 순위만 밀어내므로 그 사고가 없다.

    같은 근거는 아이템이 여럿이어도 한 번만 남긴다.
    """
    if band is None:
        return 0.0, []

    total = 0.0
    reasons: list[Reason] = []
    seen: set[str] = set()

    def add(delta: float, text: str) -> None:
        """_score_items와 같은 규칙 — 같은 근거는 코디당 한 번만."""
        nonlocal total
        if text in seen:
            return
        seen.add(text)
        total += delta
        reasons.append(Reason(source="weather", delta=delta, text=text))

    for item in items:
        for rule in band.discourage:
            if rule.matches(item):
                add(weights.discourage, rule.reason)
        for rule in band.encourage:
            if rule.matches(item):
                add(weights.encourage, rule.reason)
    return total, reasons


def _score_context(
    payload: dict[str, Any], *, season: str, occasion: str, weights
) -> tuple[float, list[Reason]]:
    """계절·상황이 맞으면 가산한다. 안 맞아도 탈락시키지 않는다.

    태그가 비어 있으면(분석 단계를 돌리지 않은 골든셋) 가산도 감산도 없다 —
    "정보가 없음"과 "안 맞음"은 다르다.
    """
    total = 0.0
    reasons: list[Reason] = []
    if season and season in (payload.get("season") or []):
        total += weights.context_match
        reasons.append(
            Reason(source="context", delta=weights.context_match, text=f"{season} 코디")
        )
    if occasion and occasion in (payload.get("occasion") or []):
        total += weights.context_match
        reasons.append(
            Reason(
                source="context", delta=weights.context_match, text=f"{occasion}에 어울림"
            )
        )
    return total, reasons


def _score_human_review(payload: dict[str, Any], *, weight: float):
    """사람 쌍대 비교 앵커를 규칙 가감점과 같은 단위로 환산한다.

    `human_score`는 **그래프 안에서 최저 0 최고 100으로 편 상대값**이다(남성·여성이
    각각 별도 그래프다). 절대 품질 점수가 아니므로 그대로 더하면 앵커가 있는 코디가
    없는 코디를 압도한다. 그래서 중앙값 50을 0으로 두고 ±weight 범위로 옮긴다 —
    중앙보다 잘 만든 코디는 가산, 못 만든 코디는 감산이다.

    `score_confidence`로 한 번 더 줄인다. 비교 횟수가 적거나 검수자 판정이 갈린
    앵커는 점수 자체가 덜 미더운데, 그 사정을 무시하고 같은 크기로 밀면 표본이
    얇은 코디가 순위를 흔든다. 이 값을 만들어 둔 이유가 그것이다.

    검수를 안 거친 코디는 0이다. **감산이 아니라 무가감이어야 한다** — 미검수는
    "나쁘다"가 아니라 "모른다"이고, 골든셋 645건 중 검수분은 아직 일부다.
    """
    if weight <= 0 or "human_score" not in payload:
        return 0.0, []
    try:
        human_score = float(payload.get("human_score") or 0.0)
    except (TypeError, ValueError):
        return 0.0, []
    confidence = min(1.0, max(0.0, float(payload.get("score_confidence") or 1.0)))
    centered = (human_score - 50.0) / 50.0
    delta = centered * weight * confidence
    if abs(delta) < 0.01:
        return 0.0, []
    band = str(payload.get("score_band") or "")
    return delta, [
        Reason(
            source="human_review",
            delta=round(delta, 2),
            text=f"사람 검수 상대 평가 {band or '중간'}".strip(),
        )
    ]


def retrieve_outfits(
    request: RetrievalRequest,
    *,
    client=None,
    rules: BodyRules | None = None,
) -> list[OutfitCandidate]:
    """골든 코디 후보를 점수순으로 돌려준다.

    검색 방식은 입력에 따라 갈린다.
      - `image_vector`가 있으면 이미지 벡터 유사도 (비슷한 코디 찾기)
      - `query_text`가 있으면 텍스트 벡터 유사도 — 다만 질의 임베딩은 호출부가
        만들어 넣어야 한다 (이 모듈은 모델을 로드하지 않는다)
      - 둘 다 없으면 필터만으로 훑는다 (추구미 기반 추천의 기본 경로)
    """
    client = client or get_client()
    rules = rules or load_body_rules()
    profile = request.body or BodyProfile()
    weights = rules.weights

    search_filter = build_filter(request, rules=rules)
    fetch = max(request.limit * _OVERFETCH, request.limit)

    excluded: set[str] = set()
    if request.hard_filter and not profile.is_empty:
        excluded = _hard_excluded_outfits(client, rules, profile, fetch * 4)

    records = _fetch(client, request, search_filter, fetch)

    # 체형 규칙은 fit·length·pattern으로 조건을 거는데 코디 payload의 아이템
    # 요약에는 그 축이 없다. 붙이지 않으면 모든 체형에서 규칙 점수가 0이 되어
    # 순위가 똑같아진다 — 체형을 바꿔도 같은 룩이 나오던 가장 큰 원인이다.
    attached = attach_item_tags(client, records)
    if records and not attached and settings.RETRIEVER_ITEM_TAG_JOIN:
        logger.info(
            "아이템 태그를 하나도 붙이지 못했습니다 (코디 %d건). 이미 payload에 "
            "있거나(재적재 완료) 아이템 컬렉션이 비어 있습니다.", len(records),
        )

    # 성별은 Qdrant 필터로도 걸지만, 파이썬에서 **한 번 더** 검사한다. 중복이
    # 아니라 다른 실패에 대비한 것이다: presentation_group 인덱스가 없거나,
    # 재적재로 payload 키가 빠졌거나, 오래된 이미지가 필터 없는 코드를 돌고
    # 있으면 Qdrant 쪽 must는 조용히 무력해진다. 그때도 남성 사용자에게 여성
    # 코디가 나가서는 안 된다. 통과하지 못한 건수는 로그로 드러낸다.
    allowed_groups = _effective_presentation_groups(request)
    blocked_by_gender = 0
    blocked_by_item = 0

    axis = rules.for_profile(profile)
    preferred = (
        vocabulary.translate((request.pursuit or {}).get("preferred")).tags
        if request.pursuit
        else {}
    )
    # 점수화도 필터와 같은 규칙을 써야 한다. 한쪽만 충돌을 풀면 필터는 통과시키고
    # 점수는 -60을 때려, 요청한 스타일이 살아남고도 꼴찌로 밀린다.
    avoided = (
        _resolve_avoided_conflict(
            vocabulary.translate((request.pursuit or {}).get("avoided")).tags,
            request,
        )
        if request.pursuit
        else {}
    )
    requested = (
        vocabulary.translate((request.requested or {}).get("preferred")).tags
        if request.requested
        else {}
    )

    season = request.season.strip() or _season_from_weather(request.weather)
    weather_rules = load_weather_rules()
    band = weather_rules.band_for(celsius_of(request.weather))

    candidates: list[OutfitCandidate] = []
    for point_id, similarity, payload in records:
        if point_id in excluded:
            continue
        if allowed_groups and str(payload.get("presentation_group") or "") not in allowed_groups:
            blocked_by_gender += 1
            continue

        # 라벨을 통과했어도 **옷 자체**를 한 번 더 본다.
        #
        # presentation_group은 LLM이 사진을 보고 붙인 값이라 틀릴 수 있고,
        # 특히 "unisex"는 애매한 코디의 도피처가 된다. 실제로 여성 코디가
        # unisex로 태깅돼 남성 사용자에게 나갔다. 라벨만 믿는 한 반복된다.
        if conflict := conflicting_item(payload.get("items") or [], request.gender):
            blocked_by_item += 1
            logger.info(
                "성별 충돌로 제외: golden_id=%s group=%s 사유=%s",
                payload.get("golden_id"),
                payload.get("presentation_group") or "(미분류)",
                conflict,
            )
            continue
        delta, reasons = _score_items(
            list(payload.get("items", [])),
            rules_prefer=axis.prefer,
            rules_avoid=axis.avoid,
            preferred_tags=preferred,
            avoided_tags=avoided,
            requested_tags=requested,
            weights=weights,
        )
        context_delta, context_reasons = _score_context(
            payload, season=season, occasion=request.occasion, weights=weights
        )
        delta += context_delta
        reasons.extend(context_reasons)

        # 기온은 계절 태그와 달리 아이템 구성만으로 판단된다. 골든셋에 계절
        # 태그가 없어도 "27도에 아우터"는 여기서 걸린다.
        weather_delta, weather_reasons = _score_weather(
            list(payload.get("items", [])), band, weather_rules.weights
        )
        delta += weather_delta
        reasons.extend(weather_reasons)

        # 사람 검수 앵커. 예전에는 필터 검색 경로에서만 human_score가 기준선으로
        # 쓰였고 벡터 검색 경로에서는 **아예 읽히지 않았다.** 채팅은 질의 임베딩을
        # 쓰므로 사실상 사람 검수가 랭킹에 반영되지 않았다는 뜻이다. 이제 경로와
        # 무관하게 여기 한 곳에서만 반영한다.
        human_delta, human_reasons = _score_human_review(
            payload, weight=settings.RETRIEVER_HUMAN_SCORE_WEIGHT
        )
        delta += human_delta
        reasons.extend(human_reasons)

        # 유사도(0~1)를 100점 척도로 올려 규칙 가감점과 같은 단위에 둔다.
        base = similarity * 100
        candidates.append(
            OutfitCandidate(
                point_id=point_id,
                golden_id=str(payload.get("golden_id", "")),
                score=round(base + delta, 2),
                similarity=round(similarity, 4),
                reasons=tuple(reasons),
                payload=payload,
            )
        )

    if blocked_by_gender:
        # 검색 필터가 이미 걸렀어야 할 것이 여기까지 왔다는 뜻이다. 결과는
        # 안전하지만 원인(인덱스 누락·payload 누락·구버전 배포)은 남는다.
        logger.warning(
            "성별 필터를 통과한 뒤에도 %d건이 파이썬 단계에서 걸렸습니다 "
            "(성별=%s, 허용=%s). presentation_group 인덱스와 payload를 확인하세요.",
            blocked_by_gender,
            normalize_gender(request.gender) or "(미지정)",
            list(allowed_groups),
        )

    if blocked_by_item:
        # 라벨이 틀린 코디가 몇 건인지 남긴다. 이 수가 크면 태깅을 다시
        # 돌려야 한다는 뜻이다 — 매번 파이썬으로 걸러내는 건 임시방편이다.
        logger.warning(
            "presentation_group을 통과했지만 아이템이 성별과 충돌해 제외한 코디 "
            "%d건 (성별=%s). 태깅 정확도를 확인하세요.",
            blocked_by_item,
            normalize_gender(request.gender) or "(미지정)",
        )

    if not candidates:
        # 왜 0건인지 남긴다. 필터가 문제인지 적재가 문제인지 로그만 보고
        # 갈릴 수 있어야 한다 — 사용자에게는 둘 다 똑같이 "추천 없음"이다.
        logger.warning(
            "골든 코디 후보 0건: 조회 %d건 / 하드제외 %d건 / 성별=%s / 필터=%s",
            len(records),
            len(excluded),
            request.gender or "(미지정)",
            search_filter,
        )

    # 동점일 때 무엇이 1등인지 못 박는다. 예전에는 파이썬의 안정 정렬 때문에
    # **스크롤 순서 1등이 그대로 1등**이었다. 필터 검색 경로에는 유사도가 없어
    # 동점이 흔하다.
    #
    # 2순위는 사람이 관찰 검수를 통과시킨 코디다. tag_confidence는 LLM이 자기
    # 태깅에 매긴 확신이라 틀린 태그에도 높게 나올 수 있는 반면, human_verified는
    # 사람이 아이템 목록을 직접 맞다고 판정한 것이라 더 믿을 만하다. 그것도 같으면
    # 태그 신뢰도, 마지막은 golden_id로 갈라 조회 순서와 무관하게 재현되게 한다.
    candidates.sort(
        key=lambda c: (
            -c.score,
            not bool(c.payload.get("human_verified")),
            -float(c.payload.get("tag_confidence") or 0),
            c.golden_id,
        )
    )

    # 최근에 이미 나간 코디는 상위 N을 자르기 직전에 뺀다 — 그래야 빠진 자리를
    # 다음 순위가 채워 top k가 온전히 '새 코디'로 만들어진다. 점수화 앞에서 빼지
    # 않는 이유는 없음: 결과가 같고, 여기 두면 "몇 건이 걸러졌는지"를 한 곳에서
    # 셀 수 있다.
    if request.exclude_golden_ids and "recent" not in request.relaxed:
        fresh = [
            c for c in candidates if c.golden_id not in request.exclude_golden_ids
        ]
        if fresh:
            if len(fresh) < len(candidates):
                logger.info(
                    "최근 추천분 %d건을 후보에서 제외 (남은 후보 %d건)",
                    len(candidates) - len(fresh),
                    len(fresh),
                )
            candidates = fresh
        elif candidates:
            # 전부 최근 추천분이면 제외를 풀어 반복을 허용한다. 조용히 EMPTY가
            # 되면 사용자는 "며칠 잘 나오다가 갑자기 추천이 사라졌다"를 겪는다.
            logger.warning(
                "후보 %d건이 전부 최근 추천분 — 제외를 풀고 반복 추천을 허용한다 "
                "(골든셋이 작거나 이 사용자 조건이 좁다)",
                len(candidates),
            )
    return candidates[: request.limit]


def _fetch(
    client, request: RetrievalRequest, search_filter, fetch: int
) -> list[tuple[str, float, dict[str, Any]]]:
    if request.image_vector is not None:
        hits = client.search(
            collection_name=GOLDEN_OUTFIT_COLLECTION,
            query_vector=(IMAGE_VECTOR, request.image_vector),
            query_filter=search_filter,
            limit=fetch,
            with_payload=True,
        )
        return [(str(h.id), float(h.score), h.payload or {}) for h in hits]

    if request.text_vector is not None:
        if hasattr(client, "query_points"):
            response = client.query_points(
                collection_name=GOLDEN_OUTFIT_COLLECTION,
                query=request.text_vector,
                using=TEXT_VECTOR,
                query_filter=search_filter,
                limit=fetch,
                with_payload=True,
            )
            hits = response.points
        else:
            hits = client.search(
                collection_name=GOLDEN_OUTFIT_COLLECTION,
                query_vector=(TEXT_VECTOR, request.text_vector),
                query_filter=search_filter,
                limit=fetch,
                with_payload=True,
            )
        return [(str(h.id), float(h.score), h.payload or {}) for h in hits]

    # 벡터 질의가 없으면 scroll이다. scroll은 관련도가 아니라 **포인트 ID
    # 순서**로 돌려준다. 예전에는 여기서 앞 20건만 끊었는데, 그러면 골든셋이
    # 몇 건이든 언제나 같은 20건만 후보가 된다 — 체형·취향을 바꿔도 결과가
    # 안 변하던 원인 중 하나다. 이제 필터를 통과한 코디를 전부 훑는다.
    #
    # `fetch`는 무시한다. 그 값은 "상위 N의 재정렬 여유"를 뜻하는데, 순서가
    # 없는 스크롤에는 상위라는 개념이 없다.
    points = _scroll_all(client, search_filter)
    # 스크롤에는 유사도가 없으므로 기준선은 0이고 점수는 가감점이 정한다.
    #
    # 예전에는 여기서 human_score를 유사도 자리에 넣었다. 그러면 같은 값이 이
    # 경로에서만, 그것도 0~100 원값 그대로 반영돼 벡터 검색 경로와 규모가 달랐다.
    # 사람 검수는 이제 _score_human_review()가 모든 경로에서 같은 방식으로 다룬다 —
    # 여기서 또 더하면 이중 계산이다.
    return [(str(point.id), 0.0, point.payload or {}) for point in points]


def _scroll_all(client, search_filter) -> list[Any]:
    """필터를 통과한 코디를 페이지네이션으로 전부 모은다.

    상한(RETRIEVER_SCROLL_CAP)에 걸리면 **경고를 남긴다.** 조용히 잘리면
    "골든셋을 다 봤다"고 오해하게 되고, 그 오해가 이번 버그를 오래 숨겼다.
    """
    cap = max(1, settings.RETRIEVER_SCROLL_CAP)
    page = max(1, settings.RETRIEVER_SCROLL_PAGE)

    collected: list[Any] = []
    offset = None
    while len(collected) < cap:
        points, offset = client.scroll(
            collection_name=GOLDEN_OUTFIT_COLLECTION,
            scroll_filter=search_filter,
            with_payload=True,
            with_vectors=False,
            limit=min(page, cap - len(collected)),
            offset=offset,
        )
        collected.extend(points)
        if offset is None or not points:
            break

    if len(collected) >= cap and offset is not None:
        logger.warning(
            "코디 후보를 %d건에서 잘랐습니다 (RETRIEVER_SCROLL_CAP). 남은 코디는 "
            "이번 추천에서 아예 고려되지 않습니다.", cap,
        )
    return collected


def retrieve_substitutes(
    item: dict[str, Any],
    *,
    collection: str = WARDROBE_ITEM_COLLECTION,
    client=None,
    allowed_item_ids: Sequence[str] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """골든 코디의 아이템 하나를 교체할 후보를 찾는다.

    `collection`은 실제 Qdrant 컬렉션 이름 (기본값 WARDROBE_ITEM_COLLECTION 또는 GOLDEN_ITEM_COLLECTION).
    `allowed_item_ids`가 지정된 경우(공유/개인 옷장 검색 등), 화이트리스트 point id 목록에 속하는 아이템만 검색한다.
    `allowed_item_ids`가 빈 배열 `[]`이면 검색하지 않고 `[]`를 즉시 반환한다 (교차 유저 유출 차단).
    """
    if allowed_item_ids is not None and len(allowed_item_ids) == 0:
        return []

    client = client or get_client()
    must: list[qm.Condition] = []
    for field_name in ("category_large", "layer_role"):
        if value := item.get(field_name):
            must.append(
                qm.FieldCondition(key=field_name, match=qm.MatchValue(value=value))
            )

    if allowed_item_ids is not None:
        must.append(qm.HasIdCondition(has_id=list(allowed_item_ids)))

    vector = item.get("image_vector")
    if vector is None:
        # 벡터 없이 태그만으로 좁힌다. 정확도는 떨어지지만 결과가 비지는 않는다.
        points, _ = client.scroll(
            collection_name=collection,
            scroll_filter=qm.Filter(must=must) if must else None,
            with_payload=True,
            with_vectors=False,
            limit=limit,
        )
        return [{"id": str(p.id), "score": None, **(p.payload or {})} for p in points]

    hits = client.search(
        collection_name=collection,
        query_vector=(IMAGE_VECTOR, vector),
        query_filter=qm.Filter(must=must) if must else None,
        limit=limit,
        with_payload=True,
    )
    return [{"id": str(h.id), "score": float(h.score), **(h.payload or {})} for h in hits]


class GoldenOutfitRetriever:
    """main 리트리버의 성별·체형 검증을 유지하는 채팅용 인터페이스."""

    def __init__(
        self,
        *,
        client=None,
        embedding_client: TextEmbeddingClient | None = None,
        body_rules: BodyRules | None = None,
    ) -> None:
        self.client = client or get_client()
        self.embedding_client = embedding_client
        self.body_rules = body_rules

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        if not 1 <= request.limit <= 50:
            raise ValueError("limit은 1 이상 50 이하여야 합니다.")
        if request.image_vector is not None and request.query_text.strip():
            raise ValueError("image_vector와 query_text는 동시에 사용할 수 없습니다.")

        embedding_model = ""
        embedding_version = ""
        resolved = request
        if request.image_vector is not None:
            search_mode = "image"
        elif request.query_text.strip():
            search_mode = "text"
            embedding = (self.embedding_client or get_text_embedding_client()).embed(
                request.query_text
            )
            embedding_model = embedding.model
            embedding_version = embedding.version
            resolved = replace(request, text_vector=list(embedding.vector))
        else:
            search_mode = "filter"

        candidates = retrieve_outfits(
            resolved,
            client=self.client,
            rules=self.body_rules,
        )
        # 후보가 부족하면 사다리를 한 칸씩 오르며 제약을 푼다. 한 번에 다 풀지 않는
        # 이유는 어떤 제약이 병목이었는지 로그에 남기기 위해서다 — 지금까지는 0건이
        # 되면 이유를 알 수 없어 매번 수동으로 파야 했다.
        threshold = max(resolved.relax_below, 1)
        relaxed: set[str] = set(resolved.relaxed)
        for step in RELAXATION_LADDER:
            if len(candidates) >= threshold:
                break
            probe = replace(resolved, relaxed=frozenset(relaxed))
            if not _relaxable(probe, step):
                continue
            relaxed.add(step)
            logger.warning(
                "골든 후보 %d건(<%d)이라 제약 '%s'을 풀고 재검색한다 (누적 완화: %s)",
                len(candidates),
                threshold,
                step,
                sorted(relaxed),
            )
            candidates = retrieve_outfits(
                replace(resolved, relaxed=frozenset(relaxed)),
                client=self.client,
                rules=self.body_rules,
            )
        return RetrievalResult(
            candidates=tuple(candidates),
            search_mode=search_mode,
            embedding_model=embedding_model,
            embedding_version=embedding_version,
        )


def retrieve_accessible_substitutes(
    user,
    item: dict[str, Any],
    *,
    client=None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """사용자의 개인·공유 옷 중 접근 가능한 교체 후보만 검색한다.

    Confluence 설계안 B의 2단계 조회 진입점이다. 접근 권한은 PostgreSQL을
    단일 진실 공급원으로 삼아 먼저 UUID 화이트리스트로 계산하고, Qdrant에는
    그 ID만 ``HasIdCondition``으로 전달한다. 공유방 정보를 벡터 payload에
    복제하지 않으므로 멤버 탈퇴·공개 상태 변경도 다음 요청부터 즉시 반영된다.

    익명 사용자 또는 접근 가능한 확정 아이템이 없는 사용자는 Qdrant를 호출하지
    않고 빈 목록을 반환한다. 호출부는 보안을 위해 ``retrieve_substitutes``에
    ID를 직접 조립하지 말고 이 함수를 사용해야 한다.
    """
    # crossing-app ORM 접근은 wardrobe_link 한 곳에만 둔다. 여기서 지연 import해
    # 기존 골든셋 전용 리트리버 사용 시 wardrobe 모델 로딩까지 강제하지 않는다.
    from apps.recommend.services.wardrobe_link import accessible_item_ids

    return retrieve_substitutes(
        item,
        collection=WARDROBE_ITEM_COLLECTION,
        client=client,
        allowed_item_ids=accessible_item_ids(user),
        limit=limit,
    )


# ── 원칙 검색 (knowledge 컬렉션) ─────────────────────────────

#: 스타일로 좁혔을 때 이만큼도 안 나오면 필터를 푼다. 골든셋 1차 사이클 기준으로
#: 스타일당 승인 원칙이 1~15건이라, 시크(1건)·아메카지(2건) 같은 스타일은 스타일
#: 필터만으로는 설명 재료가 부족하다.
PRINCIPLE_WIDEN_THRESHOLD = 2


@dataclass(frozen=True)
class Principle:
    """추천 이유를 쓸 때 참고하는 조건부 원칙 한 건.

    사람이 검수해 승인한 문장이지만 그대로 노출하지 않고 LLM 프롬프트의 참고
    자료로만 넣는다. 문장은 LLM이 상황에 맞게 다시 쓴다.
    """

    principle_key: str
    statement: str
    axis: str
    styles: tuple[str, ...]
    exceptions: tuple[str, ...]
    support_image_count: int
    score: float
    widened: bool = False

    def as_prompt_context(self) -> dict[str, Any]:
        """LLM에 넘길 최소 형태. 내부 식별자와 점수는 빼고 판단 재료만 남긴다."""
        return {
            "statement": self.statement,
            "axis": self.axis,
            "styles": list(self.styles),
            "exceptions": list(self.exceptions),
        }


def _principle_filter(styles: Iterable[str]) -> qm.Filter:
    """승인된 원칙만, 주어진 스타일로 좁힌다.

    status=APPROVED가 핵심이다. DRAFT는 "반례가 더 필요하다"고 검수자가 판단한
    것이라 설명 근거로 쓰면 안 된다. style은 payload 인덱스가 있어 필터가 가볍다.
    """
    must: list[Any] = [
        qm.FieldCondition(key="status", match=qm.MatchValue(value="APPROVED")),
        qm.FieldCondition(
            key="knowledge_type", match=qm.MatchValue(value="golden_principle")
        ),
    ]
    values = sorted({str(value).strip() for value in styles if str(value).strip()})
    if values:
        must.append(_any_of("style", values))
    return qm.Filter(must=must)


def _to_principle(record: Any, *, widened: bool) -> Principle:
    payload = getattr(record, "payload", None) or {}
    styles = payload.get("style") or []
    if isinstance(styles, str):
        styles = [styles]
    return Principle(
        principle_key=str(payload.get("principle_key", "")),
        statement=str(payload.get("statement", "")),
        axis=str(payload.get("axis") or payload.get("dimension", "")),
        styles=tuple(str(value) for value in styles),
        exceptions=tuple(str(value) for value in payload.get("exceptions", []) or []),
        support_image_count=int(payload.get("support_image_count", 0) or 0),
        score=float(getattr(record, "score", 0.0) or 0.0),
        widened=widened,
    )


def retrieve_principles(
    *,
    query: str,
    styles: Iterable[str] = (),
    limit: int = 5,
    client=None,
    embedding_client=None,
) -> tuple[Principle, ...]:
    """코디에 붙일 원칙을 찾는다. 없으면 빈 튜플.

    스타일로 먼저 좁히고, 그것만으로 너무 적으면 필터를 풀어 벡터 유사도로만 다시
    찾는다. 좁힌 결과가 앞이고 넓힌 결과가 뒤다 — 스타일이 맞는 원칙이 늘 더 적절하다.

    **원칙이 하나도 없어도 추천은 진행한다.** 골든셋은 아직 1차 사이클이라 스타일에
    따라 승인된 원칙이 없을 수 있고, 임베딩 서비스나 Qdrant가 죽어도 추천 자체를
    막으면 안 된다. 그래서 실패를 예외로 올리지 않고 빈 결과로 돌려준다. 호출자는
    빈 튜플을 "설명에 참고 자료를 안 넣는다"로만 다루면 된다.
    """
    text = (query or "").strip()
    if not text:
        return ()
    timeout = int(getattr(settings, "PRINCIPLE_RETRIEVAL_TIMEOUT_SECONDS", 4))
    try:
        embedder = embedding_client or get_text_embedding_client(timeout=timeout)
        embedding = embedder.embed(text)
    except TextEmbeddingError:
        logger.warning("원칙 검색용 질의 임베딩 실패, 원칙 없이 진행한다.", exc_info=True)
        return ()

    qdrant = client or get_client()
    vector = list(embedding.vector)

    def _search(search_filter: qm.Filter, count: int) -> list[Any] | None:
        """검색 결과. 실패하면 None — 빈 목록(찾았는데 0건)과 구분해야 한다.

        이 둘을 같이 다루면, 좁힌 검색이 타임아웃으로 실패했을 때 "결과가 부족하다"로
        읽혀 넓힌 검색을 또 시도하고 타임아웃을 두 번 낸다.
        """
        # query_points/search 분기는 _fetch와 같다. 최신 qdrant-client는 search를
        # 없앴고, 구버전은 query_points가 없다.
        try:
            if hasattr(qdrant, "query_points"):
                return qdrant.query_points(
                    collection_name=GOLDEN_KNOWLEDGE_COLLECTION,
                    query=vector,
                    using=TEXT_VECTOR,
                    query_filter=search_filter,
                    limit=count,
                    with_payload=True,
                    timeout=timeout,
                ).points
            return qdrant.search(
                collection_name=GOLDEN_KNOWLEDGE_COLLECTION,
                query_vector=(TEXT_VECTOR, vector),
                query_filter=search_filter,
                limit=count,
                with_payload=True,
                timeout=timeout,
            )
        except Exception:
            logger.warning("원칙 검색 실패, 원칙 없이 진행한다.", exc_info=True)
            return None

    style_values = [str(value).strip() for value in styles if str(value).strip()]
    if not style_values:
        records = _search(_principle_filter(()), limit)
        if not records:
            return ()
        return tuple(_to_principle(record, widened=True) for record in records)

    narrow = _search(_principle_filter(style_values), limit)
    if narrow is None:
        # 좁힌 검색이 실패했다. 넓혀도 같은 이유로 실패할 가능성이 높고, 그러면
        # 타임아웃을 두 번 낸다. 원칙은 없어도 되는 정보라 여기서 접는다.
        return ()

    found = [_to_principle(record, widened=False) for record in narrow]
    if len(found) >= PRINCIPLE_WIDEN_THRESHOLD:
        return tuple(found[:limit])

    # 스타일로 좁힌 결과가 부족하다. 필터를 풀어 채우되 중복은 뺀다.
    seen = {item.principle_key for item in found}
    for record in _search(_principle_filter(()), limit * 2) or []:
        principle = _to_principle(record, widened=True)
        if principle.principle_key in seen:
            continue
        found.append(principle)
        seen.add(principle.principle_key)
        if len(found) >= limit:
            break
    return tuple(found[:limit])
