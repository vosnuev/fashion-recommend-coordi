"""세 스타일리스트가 공유하는 검색·점수화 전략 계약과 공통 실행기."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from apps.chat.services.stylist_personas import EXPECTED_PERSONA_ORDER, StrategyProfile

MAX_CANDIDATE_LIMIT = 50
MAX_PREFERENCE_ADJUSTMENTS = 32
MAX_SCORE_ADJUSTMENTS = 32
MAX_ABSOLUTE_SCORE_DELTA = 100.0
ALLOWED_PREFERENCE_AXES = frozenset({"style", "color", "fit"})
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


class StylistStrategyContractError(ValueError):
    """전략이 공통 추천 파이프라인의 허용 범위를 벗어났을 때 발생한다."""


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StylistStrategyContractError(
            f"{field}는 비어 있지 않은 문자열이어야 합니다."
        )
    return value.strip()


def _unique_texts(values: tuple[str, ...], *, field: str) -> tuple[str, ...]:
    normalized = tuple(_required_text(value, field=field) for value in values)
    if len(normalized) != len(set(normalized)):
        raise StylistStrategyContractError(f"{field}에는 중복 값을 넣을 수 없습니다.")
    return normalized


def _finite_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StylistStrategyContractError(f"{field}는 유한한 숫자여야 합니다.")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise StylistStrategyContractError(f"{field}는 유한한 숫자여야 합니다.")
    return normalized


def _validate_reason_code(value: str, *, field: str) -> str:
    normalized = _required_text(value, field=field)
    if not _REASON_CODE.fullmatch(normalized):
        raise StylistStrategyContractError(
            f"{field}는 대문자 영문·숫자·밑줄로 된 reason code여야 합니다."
        )
    return normalized


class PreferencePolarity(StrEnum):
    PREFER = "PREFER"
    AVOID = "AVOID"


class SortMetric(StrEnum):
    TOTAL_SCORE = "TOTAL_SCORE"
    HISTORY_DISTANCE = "HISTORY_DISTANCE"
    BASE_SCORE = "BASE_SCORE"
    SIMILARITY = "SIMILARITY"
    TAG_CONFIDENCE = "TAG_CONFIDENCE"
    ORIGINAL_ORDER = "ORIGINAL_ORDER"


class SortDirection(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


@dataclass(frozen=True)
class NumericMetric:
    """전략에 전달하는 ID 없는 정규화 또는 파생 수치."""

    name: str
    value: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required_text(self.name, field="metric.name"))
        object.__setattr__(
            self,
            "value",
            _finite_number(self.value, field=f"metric.{self.name}"),
        )


@dataclass(frozen=True)
class TagGroup:
    """검색·후보 특성에서 사용하는 표준 태그 축과 값."""

    axis: str
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "axis", _required_text(self.axis, field="tag.axis"))
        if self.axis not in ALLOWED_PREFERENCE_AXES:
            raise StylistStrategyContractError(
                f"태그 축은 {', '.join(sorted(ALLOWED_PREFERENCE_AXES))}만 허용합니다."
            )
        values = _unique_texts(self.values, field=f"tag.{self.axis}.values")
        if not values:
            raise StylistStrategyContractError("태그 값은 하나 이상이어야 합니다.")
        object.__setattr__(self, "values", values)


@dataclass(frozen=True)
class PreferenceAdjustment:
    """기존 사용자 조건에 더할 소프트 선호·기피 보정."""

    axis: str
    values: tuple[str, ...]
    polarity: PreferencePolarity
    weight: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "axis", _required_text(self.axis, field="adjustment.axis")
        )
        if self.axis not in ALLOWED_PREFERENCE_AXES:
            raise StylistStrategyContractError(
                f"선호·기피 보정 축은 {', '.join(sorted(ALLOWED_PREFERENCE_AXES))}만 허용합니다."
            )
        values = _unique_texts(self.values, field=f"adjustment.{self.axis}.values")
        if not values:
            raise StylistStrategyContractError(
                "선호·기피 보정값은 하나 이상이어야 합니다."
            )
        object.__setattr__(self, "values", values)
        try:
            polarity = PreferencePolarity(self.polarity)
        except ValueError as exc:
            raise StylistStrategyContractError(
                "알 수 없는 선호·기피 방향입니다."
            ) from exc
        object.__setattr__(self, "polarity", polarity)
        weight = _finite_number(self.weight, field="adjustment.weight")
        if not 0 < weight <= 1:
            raise StylistStrategyContractError(
                "선호·기피 보정 가중치는 0 초과 1 이하여야 합니다."
            )
        object.__setattr__(self, "weight", weight)


@dataclass(frozen=True)
class SortRule:
    metric: SortMetric
    direction: SortDirection

    def __post_init__(self) -> None:
        try:
            metric = SortMetric(self.metric)
            direction = SortDirection(self.direction)
        except ValueError as exc:
            raise StylistStrategyContractError(
                "알 수 없는 후보 정렬 규칙입니다."
            ) from exc
        object.__setattr__(self, "metric", metric)
        object.__setattr__(self, "direction", direction)


@dataclass(frozen=True)
class StrategyPlan:
    """Retriever 호출 전에 스타일리스트가 결정하는 검색 계획."""

    search_query: str
    preference_adjustments: tuple[PreferenceAdjustment, ...]
    candidate_limit: int
    sort_rules: tuple[SortRule, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "search_query",
            _required_text(self.search_query, field="search_query"),
        )
        if (
            isinstance(self.candidate_limit, bool)
            or not isinstance(self.candidate_limit, int)
            or not 1 <= self.candidate_limit <= MAX_CANDIDATE_LIMIT
        ):
            raise StylistStrategyContractError(
                f"candidate_limit은 1 이상 {MAX_CANDIDATE_LIMIT} 이하여야 합니다."
            )
        adjustment_keys = [
            (row.axis, row.polarity, row.values) for row in self.preference_adjustments
        ]
        if len(adjustment_keys) > MAX_PREFERENCE_ADJUSTMENTS:
            raise StylistStrategyContractError(
                f"선호·기피 보정은 최대 {MAX_PREFERENCE_ADJUSTMENTS}개까지 허용합니다."
            )
        if len(adjustment_keys) != len(set(adjustment_keys)):
            raise StylistStrategyContractError(
                "동일한 선호·기피 보정을 중복할 수 없습니다."
            )
        if not self.sort_rules:
            raise StylistStrategyContractError(
                "최종 후보 정렬 규칙이 하나 이상 필요합니다."
            )
        metrics = [rule.metric for rule in self.sort_rules]
        if len(metrics) != len(set(metrics)):
            raise StylistStrategyContractError("후보 정렬 지표는 중복될 수 없습니다.")
        if self.sort_rules[-1] != SortRule(
            SortMetric.ORIGINAL_ORDER,
            SortDirection.ASC,
        ):
            raise StylistStrategyContractError(
                "재현 가능한 동점 처리를 위해 마지막 정렬 규칙은 ORIGINAL_ORDER ASC여야 합니다."
            )


@dataclass(frozen=True)
class StylistStrategyContext:
    """모든 전략이 공유하는 ID 없는 요청·개인화 요약."""

    request_text: str
    base_search_query: str
    recommendation_mode: str
    occasion: str = ""
    season: str = ""
    preferred_tags: tuple[TagGroup, ...] = ()
    avoided_tags: tuple[TagGroup, ...] = ()
    recent_styles: tuple[str, ...] = ()
    recent_colors: tuple[str, ...] = ()
    recent_fits: tuple[str, ...] = ()
    repeated_slots: tuple[str, ...] = ()
    underused_slots: tuple[str, ...] = ()
    weather_metrics: tuple[NumericMetric, ...] = ()
    behavior_metrics: tuple[NumericMetric, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "request_text",
            _required_text(self.request_text, field="request_text"),
        )
        object.__setattr__(
            self,
            "base_search_query",
            _required_text(self.base_search_query, field="base_search_query"),
        )
        object.__setattr__(
            self,
            "recommendation_mode",
            _required_text(self.recommendation_mode, field="recommendation_mode"),
        )
        for field_name in (
            "recent_styles",
            "recent_colors",
            "recent_fits",
            "repeated_slots",
            "underused_slots",
        ):
            object.__setattr__(
                self,
                field_name,
                _unique_texts(getattr(self, field_name), field=field_name),
            )
        self._validate_unique_axes(self.preferred_tags, field="preferred_tags")
        self._validate_unique_axes(self.avoided_tags, field="avoided_tags")
        self._validate_unique_metrics(self.weather_metrics, field="weather_metrics")
        self._validate_unique_metrics(self.behavior_metrics, field="behavior_metrics")

    @staticmethod
    def _validate_unique_axes(values: tuple[TagGroup, ...], *, field: str) -> None:
        axes = [row.axis for row in values]
        if len(axes) != len(set(axes)):
            raise StylistStrategyContractError(
                f"{field}의 태그 축은 중복될 수 없습니다."
            )

    @staticmethod
    def _validate_unique_metrics(
        values: tuple[NumericMetric, ...],
        *,
        field: str,
    ) -> None:
        names = [row.name for row in values]
        if len(names) != len(set(names)):
            raise StylistStrategyContractError(
                f"{field}의 지표 이름은 중복될 수 없습니다."
            )


@dataclass(frozen=True)
class StrategyCandidateView:
    """실제 후보 ID·아이템을 제거하고 전략에 제공하는 특징 벡터."""

    ordinal: int
    base_score: float
    similarity: float
    tag_confidence: float = 0.0
    styles: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    fits: tuple[str, ...] = ()
    slots: tuple[str, ...] = ()
    metrics: tuple[NumericMetric, ...] = ()
    history_metrics: tuple[NumericMetric, ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.ordinal, bool)
            or not isinstance(self.ordinal, int)
            or self.ordinal < 0
        ):
            raise StylistStrategyContractError(
                "candidate ordinal은 0 이상의 정수여야 합니다."
            )
        object.__setattr__(
            self,
            "base_score",
            _finite_number(self.base_score, field="candidate.base_score"),
        )
        for field_name in ("similarity", "tag_confidence"):
            value = _finite_number(
                getattr(self, field_name),
                field=f"candidate.{field_name}",
            )
            if not 0 <= value <= 1:
                raise StylistStrategyContractError(
                    f"candidate.{field_name}는 0 이상 1 이하여야 합니다."
                )
            object.__setattr__(self, field_name, value)
        for field_name in ("styles", "colors", "fits", "slots"):
            object.__setattr__(
                self,
                field_name,
                _unique_texts(
                    getattr(self, field_name), field=f"candidate.{field_name}"
                ),
            )
        StylistStrategyContext._validate_unique_metrics(
            self.metrics,
            field="candidate.metrics",
        )
        StylistStrategyContext._validate_unique_metrics(
            self.history_metrics,
            field="candidate.history_metrics",
        )

    def metric(self, name: str, *, history: bool = False) -> float:
        source = self.history_metrics if history else self.metrics
        return next((row.value for row in source if row.name == name), 0.0)


@dataclass(frozen=True)
class ScoreAdjustment:
    """전략이 후보에 주는 유한하고 제한된 가산·감점 한 건."""

    reason_code: str
    delta: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reason_code",
            _validate_reason_code(self.reason_code, field="score.reason_code"),
        )
        delta = _finite_number(self.delta, field="score.delta")
        if not -MAX_ABSOLUTE_SCORE_DELTA <= delta <= MAX_ABSOLUTE_SCORE_DELTA:
            raise StylistStrategyContractError(
                f"후보 점수 보정은 ±{MAX_ABSOLUTE_SCORE_DELTA:g} 범위여야 합니다."
            )
        object.__setattr__(self, "delta", delta)


@dataclass(frozen=True)
class HistoryDistance:
    """최근 추천·착용과의 거리와 해당 전략의 점수 반영값."""

    distance: float
    score_delta: float
    reason_code: str

    def __post_init__(self) -> None:
        distance = _finite_number(self.distance, field="history.distance")
        if not 0 <= distance <= 1:
            raise StylistStrategyContractError(
                "최근 이력 거리는 0 이상 1 이하여야 합니다."
            )
        object.__setattr__(self, "distance", distance)
        score_delta = _finite_number(self.score_delta, field="history.score_delta")
        if not -MAX_ABSOLUTE_SCORE_DELTA <= score_delta <= MAX_ABSOLUTE_SCORE_DELTA:
            raise StylistStrategyContractError(
                f"이력 거리 점수는 ±{MAX_ABSOLUTE_SCORE_DELTA:g} 범위여야 합니다."
            )
        object.__setattr__(self, "score_delta", score_delta)
        object.__setattr__(
            self,
            "reason_code",
            _validate_reason_code(self.reason_code, field="history.reason_code"),
        )


@runtime_checkable
class StylistStrategy(Protocol):
    """미니멀·실험형·실용형이 반드시 공유하는 전략 인터페이스."""

    @property
    def persona_id(self) -> str: ...

    @property
    def profile(self) -> StrategyProfile: ...

    def build_plan(self, context: StylistStrategyContext) -> StrategyPlan: ...

    def score_candidate(
        self,
        context: StylistStrategyContext,
        candidate: StrategyCandidateView,
    ) -> tuple[ScoreAdjustment, ...]: ...

    def history_distance(
        self,
        context: StylistStrategyContext,
        candidate: StrategyCandidateView,
    ) -> HistoryDistance: ...


@dataclass(frozen=True)
class CandidateStrategyEvaluation:
    """실제 후보 대신 ordinal로 기존 파이프라인에 돌려주는 전략 결과."""

    candidate_ordinal: int
    base_score: float
    score_adjustments: tuple[ScoreAdjustment, ...]
    history_distance: HistoryDistance
    total_score: float
    similarity: float
    tag_confidence: float


@dataclass(frozen=True)
class StrategyExecutionResult:
    persona_id: str
    plan: StrategyPlan
    ranked_candidates: tuple[CandidateStrategyEvaluation, ...]


class StylistStrategyRunner:
    """전략 결과를 검증하고 ID 없는 ordinal 순서만 기존 파이프라인에 반환한다."""

    def run(
        self,
        *,
        strategy: StylistStrategy,
        context: StylistStrategyContext,
        candidates: tuple[StrategyCandidateView, ...],
    ) -> StrategyExecutionResult:
        if not isinstance(strategy, StylistStrategy):
            raise StylistStrategyContractError(
                "공통 스타일리스트 전략 인터페이스 구현이 아닙니다."
            )
        persona_id = _required_text(strategy.persona_id, field="persona_id")
        if persona_id not in EXPECTED_PERSONA_ORDER:
            raise StylistStrategyContractError("지원하지 않는 스타일리스트 ID입니다.")
        if not isinstance(strategy.profile, StrategyProfile):
            raise StylistStrategyContractError(
                "전략 설정은 StrategyProfile이어야 합니다."
            )
        plan = strategy.build_plan(context)
        if not isinstance(plan, StrategyPlan):
            raise StylistStrategyContractError(
                "전략 검색 계획이 StrategyPlan이 아닙니다."
            )
        if len(candidates) > plan.candidate_limit:
            raise StylistStrategyContractError(
                "전략 후보 수가 검색 계획의 candidate_limit을 넘었습니다."
            )
        ordinals = [candidate.ordinal for candidate in candidates]
        if len(ordinals) != len(set(ordinals)):
            raise StylistStrategyContractError(
                "candidate ordinal은 중복될 수 없습니다."
            )

        evaluated: list[CandidateStrategyEvaluation] = []
        for candidate in candidates:
            adjustments = strategy.score_candidate(context, candidate)
            if not isinstance(adjustments, tuple) or any(
                not isinstance(row, ScoreAdjustment) for row in adjustments
            ):
                raise StylistStrategyContractError(
                    "후보 점수 보정은 ScoreAdjustment 튜플이어야 합니다."
                )
            if len(adjustments) > MAX_SCORE_ADJUSTMENTS:
                raise StylistStrategyContractError(
                    f"후보 점수 보정은 최대 {MAX_SCORE_ADJUSTMENTS}개까지 허용합니다."
                )
            reason_codes = [row.reason_code for row in adjustments]
            if len(reason_codes) != len(set(reason_codes)):
                raise StylistStrategyContractError(
                    "후보 점수 reason code는 중복될 수 없습니다."
                )
            history = strategy.history_distance(context, candidate)
            if not isinstance(history, HistoryDistance):
                raise StylistStrategyContractError(
                    "최근 이력 계산 결과가 HistoryDistance가 아닙니다."
                )
            total_score = _finite_number(
                candidate.base_score
                + sum(row.delta for row in adjustments)
                + history.score_delta,
                field="candidate.total_score",
            )
            evaluated.append(
                CandidateStrategyEvaluation(
                    candidate_ordinal=candidate.ordinal,
                    base_score=candidate.base_score,
                    score_adjustments=adjustments,
                    history_distance=history,
                    total_score=round(total_score, 6),
                    similarity=candidate.similarity,
                    tag_confidence=candidate.tag_confidence,
                )
            )

        ranked = tuple(sorted(evaluated, key=lambda row: self._sort_key(row, plan)))
        return StrategyExecutionResult(
            persona_id=persona_id,
            plan=plan,
            ranked_candidates=ranked,
        )

    @staticmethod
    def _sort_key(
        candidate: CandidateStrategyEvaluation,
        plan: StrategyPlan,
    ) -> tuple[float, ...]:
        values = {
            SortMetric.TOTAL_SCORE: candidate.total_score,
            SortMetric.HISTORY_DISTANCE: candidate.history_distance.distance,
            SortMetric.BASE_SCORE: candidate.base_score,
            SortMetric.SIMILARITY: candidate.similarity,
            SortMetric.TAG_CONFIDENCE: candidate.tag_confidence,
            SortMetric.ORIGINAL_ORDER: float(candidate.candidate_ordinal),
        }
        return tuple(
            -values[rule.metric]
            if rule.direction is SortDirection.DESC
            else values[rule.metric]
            for rule in plan.sort_rules
        )
