"""유효한 추천 후보 중 실제 생활 적합도를 평가하는 실용형 전략."""

from __future__ import annotations

from apps.chat.services.stylist_personas import (
    StrategyProfile,
    load_stylist_personas,
)
from apps.chat.services.stylist_strategy import (
    HistoryDistance,
    PreferenceAdjustment,
    PreferencePolarity,
    ScoreAdjustment,
    SortDirection,
    SortMetric,
    SortRule,
    StrategyCandidateView,
    StrategyPlan,
    StylistStrategyContext,
    StylistStrategyContractError,
)

PRACTICAL_CANDIDATE_LIMIT = 18
PRACTICAL_SCORE_SCALE = 20.0
PREFERENCE_TPO_SCORE_SCALE = 10.0

_EXPECTED_PROFILE_METRICS = (
    "weather_fit",
    "activity_fit",
    "wearing_convenience",
    "maintenance_ease",
    "wardrobe_budget_efficiency",
)
_REASON_CODES = {
    "weather_fit": "PRACTICAL_WEATHER_FIT",
    "activity_fit": "PRACTICAL_ACTIVITY_FIT",
    "wearing_convenience": "PRACTICAL_WEARING_CONVENIENCE",
    "maintenance_ease": "PRACTICAL_MAINTENANCE_EASE",
    "wardrobe_budget_efficiency": "PRACTICAL_WARDROBE_BUDGET_EFFICIENCY",
}


def _clamp_unit(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _mean(values: list[float], *, default: float = 0.5) -> float:
    if not values:
        return default
    return sum(values) / len(values)


def _weighted_metric(
    metrics: dict[str, float],
    weights: tuple[tuple[str, float], ...],
    *,
    fallback: str,
) -> float:
    available = [
        (_clamp_unit(metrics[name]), weight)
        for name, weight in weights
        if name in metrics
    ]
    if not available:
        return _clamp_unit(metrics.get(fallback, 0.5))
    total_weight = sum(weight for _, weight in available)
    return sum(value * weight for value, weight in available) / total_weight


def _jaccard_distance(left: tuple[str, ...], right: tuple[str, ...]) -> float | None:
    left_values = set(left)
    right_values = set(right)
    if not left_values or not right_values:
        return None
    return 1.0 - (len(left_values & right_values) / len(left_values | right_values))


class PracticalStylistStrategy:
    """Validator를 통과한 ID 없는 후보에 실용성 소프트 점수만 부여한다."""

    persona_id = "practical"

    def __init__(self, profile: StrategyProfile | None = None) -> None:
        self._profile = (
            profile or load_stylist_personas().get(self.persona_id).strategy_profile
        )
        self._validate_profile()

    @property
    def profile(self) -> StrategyProfile:
        return self._profile

    def build_plan(self, context: StylistStrategyContext) -> StrategyPlan:
        adjustments = tuple(
            PreferenceAdjustment(
                axis=group.axis,
                values=group.values,
                polarity=polarity,
                weight=weight,
            )
            for groups, polarity, weight in (
                (context.preferred_tags, PreferencePolarity.PREFER, 0.75),
                (context.avoided_tags, PreferencePolarity.AVOID, 1.0),
            )
            for group in groups
        )
        return StrategyPlan(
            search_query=(
                f"{context.base_search_query} "
                "기온 체감온도 강수 바람 활동량 착탈의 세탁 관리 편의"
            ),
            preference_adjustments=adjustments,
            candidate_limit=PRACTICAL_CANDIDATE_LIMIT,
            sort_rules=(
                SortRule(SortMetric.TOTAL_SCORE, SortDirection.DESC),
                SortRule(SortMetric.TAG_CONFIDENCE, SortDirection.DESC),
                SortRule(SortMetric.SIMILARITY, SortDirection.DESC),
                SortRule(SortMetric.ORIGINAL_ORDER, SortDirection.ASC),
            ),
        )

    def score_candidate(
        self,
        context: StylistStrategyContext,
        candidate: StrategyCandidateView,
    ) -> tuple[ScoreAdjustment, ...]:
        del context
        metrics = {row.name: row.value for row in candidate.metrics}
        component_scores = {
            "weather_fit": self._weather_fit(metrics),
            "activity_fit": self._activity_fit(metrics),
            "wearing_convenience": self._wearing_convenience(metrics),
            "maintenance_ease": self._maintenance_ease(metrics),
            "wardrobe_budget_efficiency": self._wardrobe_budget_efficiency(metrics),
        }
        adjustments = [
            ScoreAdjustment(
                reason_code=_REASON_CODES[metric],
                delta=round(
                    (component_scores[metric] - 0.5)
                    * 2
                    * self.profile.weight_for(metric)
                    * PRACTICAL_SCORE_SCALE,
                    6,
                ),
            )
            for metric in _EXPECTED_PROFILE_METRICS
        ]

        common_fit = _mean(
            [
                candidate.tag_confidence,
                *([_clamp_unit(metrics["tpo_fit"])] if "tpo_fit" in metrics else []),
                *(
                    [_clamp_unit(metrics["preference_fit"])]
                    if "preference_fit" in metrics
                    else []
                ),
            ]
        )
        adjustments.append(
            ScoreAdjustment(
                reason_code="PRACTICAL_PREFERENCE_TPO_FIT",
                delta=round(
                    (common_fit - 0.5) * 2 * PREFERENCE_TPO_SCORE_SCALE,
                    6,
                ),
            )
        )
        return tuple(adjustments)

    def history_distance(
        self,
        context: StylistStrategyContext,
        candidate: StrategyCandidateView,
    ) -> HistoryDistance:
        metrics = {row.name: row.value for row in candidate.history_metrics}
        distances = [
            _clamp_unit(metrics[name])
            for name in ("style_distance", "color_distance", "fit_distance")
            if name in metrics
        ]
        if "item_overlap_ratio" in metrics:
            distances.append(1.0 - _clamp_unit(metrics["item_overlap_ratio"]))
        if not distances:
            for candidate_values, recent_values in (
                (candidate.styles, context.recent_styles),
                (candidate.colors, context.recent_colors),
                (candidate.fits, context.recent_fits),
            ):
                if (
                    distance := _jaccard_distance(candidate_values, recent_values)
                ) is not None:
                    distances.append(distance)

        distance = round(_mean(distances), 6)
        exact_repeat = _clamp_unit(metrics.get("exact_combination_repeat", 0.0))
        strong_preference_overlap = _clamp_unit(
            metrics.get("strong_preference_overlap_ratio", 0.0)
        )
        if exact_repeat >= 0.5:
            score_delta = -1.5
        else:
            score_delta = round(strong_preference_overlap * 1.5, 6)
        return HistoryDistance(
            distance=distance,
            score_delta=score_delta,
            reason_code="PRACTICAL_RECENT_HISTORY",
        )

    def _validate_profile(self) -> None:
        profile_metrics = tuple(row.metric for row in self.profile.score_weights)
        if set(profile_metrics) != set(_EXPECTED_PROFILE_METRICS):
            raise StylistStrategyContractError(
                "실용형 전략 설정의 점수 지표가 구현 계약과 맞지 않습니다."
            )
        if self.profile.hypothesis_count != 0:
            raise StylistStrategyContractError(
                "실용형 전략은 검색 가설을 생성할 수 없습니다."
            )

    @staticmethod
    def _weather_fit(metrics: dict[str, float]) -> float:
        return _weighted_metric(
            metrics,
            (
                ("temperature_fit", 0.3),
                ("apparent_temperature_fit", 0.25),
                ("precipitation_fit", 0.25),
                ("wind_fit", 0.2),
            ),
            fallback="weather_fit",
        )

    @staticmethod
    def _activity_fit(metrics: dict[str, float]) -> float:
        return _weighted_metric(
            metrics,
            (("mobility_fit", 0.6), ("walking_fit", 0.4)),
            fallback="activity_fit",
        )

    @staticmethod
    def _wearing_convenience(metrics: dict[str, float]) -> float:
        return _weighted_metric(
            metrics,
            (
                ("footwear_convenience", 0.4),
                ("outerwear_convenience", 0.35),
                ("dressing_convenience", 0.25),
            ),
            fallback="wearing_convenience",
        )

    @staticmethod
    def _maintenance_ease(metrics: dict[str, float]) -> float:
        if "maintenance_ease" not in metrics:
            return 0.5
        evidence = _clamp_unit(metrics.get("maintenance_evidence", 0.0))
        ease = _clamp_unit(metrics["maintenance_ease"])
        return 0.5 + ((ease - 0.5) * evidence)

    @staticmethod
    def _wardrobe_budget_efficiency(metrics: dict[str, float]) -> float:
        scores: list[float] = []
        if "wardrobe_item_ratio" in metrics:
            scores.append(_clamp_unit(metrics["wardrobe_item_ratio"]))
        if "new_purchase_ratio" in metrics:
            scores.append(1.0 - _clamp_unit(metrics["new_purchase_ratio"]))
        if "budget_efficiency" in metrics:
            scores.append(_clamp_unit(metrics["budget_efficiency"]))
        if scores:
            return _mean(scores)
        return _clamp_unit(metrics.get("wardrobe_budget_efficiency", 0.5))
