"""유효한 추천 후보 중 정돈도와 반복 활용도를 평가하는 미니멀 전략."""

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

MINIMAL_CANDIDATE_LIMIT = 20
MINIMAL_SCORE_SCALE = 20.0

_EXPECTED_PROFILE_METRICS = (
    "color_cohesion",
    "silhouette_consistency",
    "visual_simplicity",
    "wardrobe_reusability",
    "tpo_fit",
)
_REASON_CODES = {
    "color_cohesion": "MINIMAL_COLOR_COHESION",
    "silhouette_consistency": "MINIMAL_SILHOUETTE_CONSISTENCY",
    "visual_simplicity": "MINIMAL_VISUAL_SIMPLICITY",
    "wardrobe_reusability": "MINIMAL_WARDROBE_REUSABILITY",
    "tpo_fit": "MINIMAL_TPO_FIT",
}


def _clamp_unit(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _mean(values: list[float], *, default: float = 0.5) -> float:
    if not values:
        return default
    return sum(values) / len(values)


def _jaccard_distance(left: tuple[str, ...], right: tuple[str, ...]) -> float | None:
    left_values = set(left)
    right_values = set(right)
    if not left_values or not right_values:
        return None
    return 1.0 - (len(left_values & right_values) / len(left_values | right_values))


class MinimalStylistStrategy:
    """Validator를 통과한 ID 없는 후보에 미니멀 소프트 점수만 부여한다."""

    persona_id = "minimal"

    def __init__(self, profile: StrategyProfile | None = None) -> None:
        self._profile = (
            profile or load_stylist_personas().get(self.persona_id).strategy_profile
        )
        self._validate_profile()

    @property
    def profile(self) -> StrategyProfile:
        return self._profile

    def build_plan(self, context: StylistStrategyContext) -> StrategyPlan:
        return StrategyPlan(
            search_query=(
                f"{context.base_search_query} "
                "정돈된 색상 조화 간결한 실루엣 활용도 높은 조합"
            ),
            preference_adjustments=(
                PreferenceAdjustment(
                    axis="style",
                    values=("미니멀", "베이직"),
                    polarity=PreferencePolarity.PREFER,
                    weight=0.65,
                ),
            ),
            candidate_limit=MINIMAL_CANDIDATE_LIMIT,
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
            "color_cohesion": self._color_cohesion(candidate, metrics),
            "silhouette_consistency": self._silhouette_consistency(metrics),
            "visual_simplicity": self._visual_simplicity(metrics),
            "wardrobe_reusability": self._wardrobe_reusability(metrics),
            "tpo_fit": _clamp_unit(metrics.get("tpo_fit", 0.5)),
        }
        return tuple(
            ScoreAdjustment(
                reason_code=_REASON_CODES[metric],
                delta=round(
                    (component_scores[metric] - 0.5)
                    * 2
                    * self.profile.weight_for(metric)
                    * MINIMAL_SCORE_SCALE,
                    6,
                ),
            )
            for metric in _EXPECTED_PROFILE_METRICS
        )

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
        if exact_repeat >= 0.5:
            score_delta = -2.0
        elif distance <= 0.1:
            score_delta = -1.0
        elif distance <= 0.25:
            score_delta = -0.5
        else:
            score_delta = 0.0
        return HistoryDistance(
            distance=distance,
            score_delta=score_delta,
            reason_code="MINIMAL_RECENT_HISTORY",
        )

    def _validate_profile(self) -> None:
        profile_metrics = tuple(row.metric for row in self.profile.score_weights)
        if set(profile_metrics) != set(_EXPECTED_PROFILE_METRICS):
            raise StylistStrategyContractError(
                "미니멀 전략 설정의 점수 지표가 구현 계약과 맞지 않습니다."
            )
        if self.profile.hypothesis_count != 0:
            raise StylistStrategyContractError(
                "미니멀 전략은 검색 가설을 생성할 수 없습니다."
            )

    @staticmethod
    def _color_cohesion(
        candidate: StrategyCandidateView,
        metrics: dict[str, float],
    ) -> float:
        if candidate.colors:
            if "멀티" in candidate.colors:
                return 0.0
            color_count = len(candidate.colors)
            if color_count <= 2:
                return 1.0
            if color_count == 3:
                return 0.5
            return 0.0
        return _clamp_unit(metrics.get("color_cohesion", 0.5))

    @staticmethod
    def _silhouette_consistency(metrics: dict[str, float]) -> float:
        if "silhouette_conflict" in metrics:
            return 1.0 - _clamp_unit(metrics["silhouette_conflict"])
        return _clamp_unit(metrics.get("silhouette_consistency", 0.5))

    @staticmethod
    def _visual_simplicity(metrics: dict[str, float]) -> float:
        scores: list[float] = []
        if "visual_focus_count" in metrics:
            focus_count = max(metrics["visual_focus_count"], 0.0)
            if focus_count <= 1:
                scores.append(1.0)
            elif focus_count <= 2:
                scores.append(0.65)
            elif focus_count <= 3:
                scores.append(0.3)
            else:
                scores.append(0.0)
        for name in ("layer_complexity", "pattern_detail_density"):
            if name in metrics:
                scores.append(1.0 - _clamp_unit(metrics[name]))
        if scores:
            return _mean(scores)
        return _clamp_unit(metrics.get("visual_simplicity", 0.5))

    @staticmethod
    def _wardrobe_reusability(metrics: dict[str, float]) -> float:
        scores: list[float] = []
        if "wardrobe_item_ratio" in metrics:
            scores.append(_clamp_unit(metrics["wardrobe_item_ratio"]))
        if "new_purchase_ratio" in metrics:
            scores.append(1.0 - _clamp_unit(metrics["new_purchase_ratio"]))
        if scores:
            return _mean(scores)
        return _clamp_unit(metrics.get("wardrobe_reusability", 0.5))
