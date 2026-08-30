"""구조화 가설로 검색 방향을 바꾸고 최근 이력과 다른 후보를 고르는 실험형 전략."""

from __future__ import annotations

from apps.chat.services.experimental_hypotheses import (
    ExperimentalHypothesis,
    ExperimentAxis,
)
from apps.chat.services.experimental_hypothesis_fallback import (
    ResolvedExperimentalHypotheses,
)
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

EXPERIMENTAL_CANDIDATE_LIMIT = 24
EXPERIMENTAL_SCORE_SCALE = 20.0

_EXPECTED_PROFILE_METRICS = (
    "novelty",
    "history_distance",
    "underused_item",
    "cross_style",
)
_AXIS_QUERY_LABELS = {
    ExperimentAxis.TOP_STYLE: "상의 스타일 변화",
    ExperimentAxis.BOTTOM_STYLE: "하의 스타일 변화",
    ExperimentAxis.OUTER_STYLE: "아우터 스타일 변화",
    ExperimentAxis.FOOTWEAR_STYLE: "신발 스타일 변화",
    ExperimentAxis.STYLE_MIX: "스타일 조합 변화",
    ExperimentAxis.TOP_SILHOUETTE: "상의 실루엣 변화",
    ExperimentAxis.BOTTOM_SILHOUETTE: "하의 실루엣 변화",
    ExperimentAxis.OUTER_SILHOUETTE: "아우터 실루엣 변화",
    ExperimentAxis.COLOR_FAMILY: "색상 계열 변화",
    ExperimentAxis.COLOR_CONTRAST: "색상 대비 변화",
    ExperimentAxis.PROPORTION: "비율 변화",
    ExperimentAxis.LAYERING: "레이어링 변화",
    ExperimentAxis.MATERIAL_MIX: "소재 조합 변화",
    ExperimentAxis.PATTERN_DENSITY: "패턴 밀도 변화",
    ExperimentAxis.UNDERUSED_ITEM_SLOT: "최근 덜 입은 옷장 슬롯 활용",
}


def _clamp_unit(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _mean(values: list[float], *, default: float = 0.5) -> float:
    return sum(values) / len(values) if values else default


class ExperimentalStylistStrategy:
    """가설이 지정한 관계만 바꾸고 공통 Validator 결과 안에서 새로움을 점수화한다."""

    persona_id = "experimental"

    def __init__(
        self,
        *,
        hypotheses: ResolvedExperimentalHypotheses,
        profile: StrategyProfile | None = None,
    ) -> None:
        self.hypotheses = hypotheses
        self._profile = (
            profile or load_stylist_personas().get(self.persona_id).strategy_profile
        )
        self._validate_profile()

    @property
    def profile(self) -> StrategyProfile:
        return self._profile

    def build_plan(self, context: StylistStrategyContext) -> StrategyPlan:
        change_labels = tuple(
            dict.fromkeys(
                _AXIS_QUERY_LABELS[axis]
                for hypothesis in self.hypotheses.batch.hypotheses
                for axis in hypothesis.change_axes
            )
        )
        preserve_labels = tuple(
            dict.fromkeys(
                _AXIS_QUERY_LABELS[axis].replace("변화", "유지")
                for hypothesis in self.hypotheses.batch.hypotheses
                for axis in hypothesis.preserve_axes
            )
        )
        adjustments = tuple(
            PreferenceAdjustment(
                axis=group.axis,
                values=group.values,
                polarity=polarity,
                weight=weight,
            )
            for groups, polarity, weight in (
                (context.preferred_tags, PreferencePolarity.PREFER, 0.6),
                (context.avoided_tags, PreferencePolarity.AVOID, 1.0),
            )
            for group in groups
        )
        return StrategyPlan(
            search_query=" ".join(
                (
                    context.base_search_query,
                    "최근 추천과 다른 관계 탐색",
                    *change_labels,
                    *preserve_labels,
                )
            ),
            preference_adjustments=adjustments,
            candidate_limit=EXPERIMENTAL_CANDIDATE_LIMIT,
            sort_rules=(
                SortRule(SortMetric.TOTAL_SCORE, SortDirection.DESC),
                SortRule(SortMetric.HISTORY_DISTANCE, SortDirection.DESC),
                SortRule(SortMetric.TAG_CONFIDENCE, SortDirection.DESC),
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
        history = {row.name: row.value for row in candidate.history_metrics}
        component_scores = {
            "novelty": _clamp_unit(metrics.get("novelty", 0.5)),
            "underused_item": _clamp_unit(metrics.get("underused_item", 0.0)),
            "cross_style": _clamp_unit(metrics.get("cross_style", 0.5)),
        }
        adjustments = [
            ScoreAdjustment(
                reason_code=f"EXPERIMENTAL_{metric.upper()}",
                delta=round(
                    (score - 0.5)
                    * 2
                    * self.profile.weight_for(metric)
                    * EXPERIMENTAL_SCORE_SCALE,
                    6,
                ),
            )
            for metric, score in component_scores.items()
        ]
        adjustments.append(
            ScoreAdjustment(
                reason_code="EXPERIMENTAL_HYPOTHESIS_ALIGNMENT",
                delta=round(
                    (self._hypothesis_alignment(metrics, history) - 0.5) * 4,
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
        del context
        metrics = {row.name: row.value for row in candidate.history_metrics}
        distances = [
            _clamp_unit(metrics[name])
            for name in ("style_distance", "color_distance", "fit_distance")
            if name in metrics
        ]
        if "item_overlap_ratio" in metrics:
            distances.append(1.0 - _clamp_unit(metrics["item_overlap_ratio"]))
        distance = round(_mean(distances), 6)
        if _clamp_unit(metrics.get("exact_combination_repeat", 0.0)) >= 0.5:
            score_delta = -3.0
        else:
            score_delta = round(
                (distance - 0.5)
                * 2
                * self.profile.weight_for("history_distance")
                * EXPERIMENTAL_SCORE_SCALE,
                6,
            )
        return HistoryDistance(
            distance=distance,
            score_delta=score_delta,
            reason_code="EXPERIMENTAL_RECENT_HISTORY",
        )

    def _validate_profile(self) -> None:
        metrics = tuple(row.metric for row in self.profile.score_weights)
        if set(metrics) != set(_EXPECTED_PROFILE_METRICS):
            raise StylistStrategyContractError(
                "실험형 전략 설정의 점수 지표가 구현 계약과 맞지 않습니다."
            )
        if self.profile.hypothesis_count != len(self.hypotheses.batch.hypotheses):
            raise StylistStrategyContractError(
                "실험형 전략의 가설 수가 설정 계약과 맞지 않습니다."
            )

    def _hypothesis_alignment(
        self,
        metrics: dict[str, float],
        history: dict[str, float],
    ) -> float:
        return max(
            self._single_hypothesis_alignment(hypothesis, metrics, history)
            for hypothesis in self.hypotheses.batch.hypotheses
        )

    @staticmethod
    def _single_hypothesis_alignment(
        hypothesis: ExperimentalHypothesis,
        metrics: dict[str, float],
        history: dict[str, float],
    ) -> float:
        axis_scores = {
            ExperimentAxis.TOP_STYLE: history.get("style_distance", 0.5),
            ExperimentAxis.BOTTOM_STYLE: history.get("style_distance", 0.5),
            ExperimentAxis.OUTER_STYLE: history.get("style_distance", 0.5),
            ExperimentAxis.FOOTWEAR_STYLE: history.get("style_distance", 0.5),
            ExperimentAxis.STYLE_MIX: metrics.get("cross_style", 0.5),
            ExperimentAxis.TOP_SILHOUETTE: history.get("fit_distance", 0.5),
            ExperimentAxis.BOTTOM_SILHOUETTE: history.get("fit_distance", 0.5),
            ExperimentAxis.OUTER_SILHOUETTE: history.get("fit_distance", 0.5),
            ExperimentAxis.COLOR_FAMILY: history.get("color_distance", 0.5),
            ExperimentAxis.COLOR_CONTRAST: metrics.get("color_contrast", 0.5),
            ExperimentAxis.PROPORTION: metrics.get("proportion_variation", 0.5),
            ExperimentAxis.LAYERING: metrics.get("layer_complexity", 0.5),
            ExperimentAxis.MATERIAL_MIX: metrics.get("material_mix", 0.5),
            ExperimentAxis.PATTERN_DENSITY: metrics.get(
                "pattern_detail_density",
                0.5,
            ),
            ExperimentAxis.UNDERUSED_ITEM_SLOT: metrics.get("underused_item", 0.0),
        }
        change_scores = [
            _clamp_unit(axis_scores[axis]) for axis in hypothesis.change_axes
        ]
        preserve_scores = [
            1.0 - _clamp_unit(axis_scores[axis]) for axis in hypothesis.preserve_axes
        ]
        return _mean([*change_scores, *preserve_scores])
