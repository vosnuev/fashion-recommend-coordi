from __future__ import annotations

from django.test import SimpleTestCase

from apps.chat.services.experimental_hypotheses import (
    ExperimentalHypothesis,
    ExperimentalHypothesisBatch,
    ExperimentAxis,
    ExperimentReasonCode,
)
from apps.chat.services.experimental_hypothesis_fallback import (
    ExperimentalHypothesisSource,
    ResolvedExperimentalHypotheses,
)
from apps.chat.services.experimental_stylist_strategy import (
    EXPERIMENTAL_CANDIDATE_LIMIT,
    ExperimentalStylistStrategy,
)
from apps.chat.services.stylist_strategy import (
    NumericMetric,
    StrategyCandidateView,
    StylistStrategyContext,
    StylistStrategyRunner,
)


class ExperimentalStylistStrategyTests(SimpleTestCase):
    def setUp(self) -> None:
        resolved = ResolvedExperimentalHypotheses(
            batch=ExperimentalHypothesisBatch(
                hypotheses=(
                    ExperimentalHypothesis(
                        change_axes=(ExperimentAxis.BOTTOM_SILHOUETTE,),
                        preserve_axes=(
                            ExperimentAxis.TOP_STYLE,
                            ExperimentAxis.COLOR_FAMILY,
                        ),
                        reason_code=(ExperimentReasonCode.RECENT_SILHOUETTE_REPETITION),
                    ),
                    ExperimentalHypothesis(
                        change_axes=(ExperimentAxis.UNDERUSED_ITEM_SLOT,),
                        preserve_axes=(
                            ExperimentAxis.TOP_STYLE,
                            ExperimentAxis.COLOR_FAMILY,
                        ),
                        reason_code=ExperimentReasonCode.CALENDAR_ITEM_UNDERUSE,
                    ),
                )
            ),
            source=ExperimentalHypothesisSource.RULE_FALLBACK,
        )
        self.strategy = ExperimentalStylistStrategy(hypotheses=resolved)
        self.context = StylistStrategyContext(
            request_text="가을 출근 코디를 추천해줘",
            base_search_query="가을 출근 코디",
            recommendation_mode="NEW_ITEM",
            occasion="출근",
            season="가을",
            recent_styles=("미니멀",),
            recent_colors=("네이비",),
            recent_fits=("레귤러핏",),
        )

    def test_plan_uses_resolved_axes_instead_of_fixed_trend_keyword(self) -> None:
        plan = self.strategy.build_plan(self.context)

        self.assertEqual(plan.candidate_limit, EXPERIMENTAL_CANDIDATE_LIMIT)
        self.assertIn("하의 실루엣 변화", plan.search_query)
        self.assertIn("최근 덜 입은 옷장 슬롯 활용", plan.search_query)
        self.assertNotIn("레트로", plan.search_query)

    def test_novel_underused_candidate_wins_after_common_validation(self) -> None:
        repeated = self._candidate(
            ordinal=1,
            metrics=(
                NumericMetric("novelty", 0.1),
                NumericMetric("underused_item", 0.0),
                NumericMetric("cross_style", 0.2),
            ),
            history=(
                NumericMetric("style_distance", 0.1),
                NumericMetric("color_distance", 0.1),
                NumericMetric("fit_distance", 0.1),
                NumericMetric("item_overlap_ratio", 0.9),
            ),
        )
        novel = self._candidate(
            ordinal=2,
            metrics=(
                NumericMetric("novelty", 0.9),
                NumericMetric("underused_item", 1.0),
                NumericMetric("cross_style", 0.8),
            ),
            history=(
                NumericMetric("style_distance", 0.8),
                NumericMetric("color_distance", 0.2),
                NumericMetric("fit_distance", 0.9),
                NumericMetric("item_overlap_ratio", 0.0),
            ),
        )

        result = StylistStrategyRunner().run(
            strategy=self.strategy,
            context=self.context,
            candidates=(repeated, novel),
        )

        self.assertEqual(result.ranked_candidates[0].candidate_ordinal, 2)

    def test_exact_recent_combination_gets_hard_novelty_penalty(self) -> None:
        candidate = self._candidate(
            ordinal=1,
            history=(
                NumericMetric("item_overlap_ratio", 1.0),
                NumericMetric("exact_combination_repeat", 1.0),
            ),
        )

        history = self.strategy.history_distance(self.context, candidate)

        self.assertEqual(history.score_delta, -3.0)

    @staticmethod
    def _candidate(
        *,
        ordinal: int,
        metrics: tuple[NumericMetric, ...] = (),
        history: tuple[NumericMetric, ...] = (),
    ) -> StrategyCandidateView:
        return StrategyCandidateView(
            ordinal=ordinal,
            base_score=80,
            similarity=0.8,
            tag_confidence=0.8,
            styles=("미니멀", "캐주얼"),
            colors=("네이비",),
            fits=("와이드핏",),
            slots=("TOP", "BOTTOM"),
            metrics=metrics,
            history_metrics=history,
        )
