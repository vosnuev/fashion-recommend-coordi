from __future__ import annotations

from django.test import SimpleTestCase

from apps.chat.services.minimal_stylist_strategy import (
    MINIMAL_CANDIDATE_LIMIT,
    MinimalStylistStrategy,
)
from apps.chat.services.stylist_personas import StrategyProfile, StrategyWeight
from apps.chat.services.stylist_strategy import (
    NumericMetric,
    SortDirection,
    SortMetric,
    StrategyCandidateView,
    StylistStrategy,
    StylistStrategyContext,
    StylistStrategyContractError,
    StylistStrategyRunner,
)


class MinimalStylistStrategyTests(SimpleTestCase):
    def setUp(self) -> None:
        self.strategy = MinimalStylistStrategy()
        self.context = StylistStrategyContext(
            request_text="여름 출근 코디를 추천해줘",
            base_search_query="여름 출근 코디",
            recommendation_mode="NEW_ITEM",
            occasion="출근",
            season="여름",
            recent_styles=("미니멀",),
            recent_colors=("네이비", "화이트"),
            recent_fits=("레귤러핏",),
        )

    def test_build_plan_keeps_request_without_forcing_black_or_gray(self) -> None:
        plan = self.strategy.build_plan(self.context)

        self.assertIsInstance(self.strategy, StylistStrategy)
        self.assertEqual(self.strategy.persona_id, "minimal")
        self.assertTrue(plan.search_query.startswith(self.context.base_search_query))
        self.assertNotIn("블랙", plan.search_query)
        self.assertNotIn("그레이", plan.search_query)
        self.assertEqual(plan.candidate_limit, MINIMAL_CANDIDATE_LIMIT)
        self.assertEqual(plan.preference_adjustments[0].axis, "style")
        self.assertEqual(
            plan.preference_adjustments[0].values,
            ("미니멀", "베이직"),
        )
        self.assertEqual(
            tuple((row.metric, row.direction) for row in plan.sort_rules),
            (
                (SortMetric.TOTAL_SCORE, SortDirection.DESC),
                (SortMetric.TAG_CONFIDENCE, SortDirection.DESC),
                (SortMetric.SIMILARITY, SortDirection.DESC),
                (SortMetric.ORIGINAL_ORDER, SortDirection.ASC),
            ),
        )

    def test_lower_complexity_candidate_wins_between_equally_valid_candidates(
        self,
    ) -> None:
        simple = self._candidate(
            ordinal=1,
            colors=("네이비", "화이트"),
            metrics=(
                NumericMetric("visual_focus_count", 1),
                NumericMetric("layer_complexity", 0.1),
                NumericMetric("pattern_detail_density", 0.1),
                NumericMetric("silhouette_conflict", 0.1),
                NumericMetric("wardrobe_item_ratio", 0.9),
                NumericMetric("new_purchase_ratio", 0.1),
                NumericMetric("tpo_fit", 0.9),
            ),
        )
        complex_candidate = self._candidate(
            ordinal=0,
            colors=("레드", "블루", "옐로우", "그린"),
            metrics=(
                NumericMetric("visual_focus_count", 4),
                NumericMetric("layer_complexity", 0.9),
                NumericMetric("pattern_detail_density", 0.9),
                NumericMetric("silhouette_conflict", 0.9),
                NumericMetric("wardrobe_item_ratio", 0.1),
                NumericMetric("new_purchase_ratio", 0.9),
                NumericMetric("tpo_fit", 0.9),
            ),
        )

        result = StylistStrategyRunner().run(
            strategy=self.strategy,
            context=self.context,
            candidates=(complex_candidate, simple),
        )

        self.assertEqual(result.ranked_candidates[0].candidate_ordinal, 1)
        scores = {
            row.candidate_ordinal: {
                adjustment.reason_code: adjustment.delta
                for adjustment in row.score_adjustments
            }
            for row in result.ranked_candidates
        }
        for reason_code in (
            "MINIMAL_COLOR_COHESION",
            "MINIMAL_SILHOUETTE_CONSISTENCY",
            "MINIMAL_VISUAL_SIMPLICITY",
            "MINIMAL_WARDROBE_REUSABILITY",
        ):
            self.assertGreater(scores[1][reason_code], scores[0][reason_code])

    def test_missing_complexity_metrics_are_neutral_not_automatically_simple(
        self,
    ) -> None:
        adjustments = self.strategy.score_candidate(
            self.context,
            self._candidate(ordinal=0),
        )

        self.assertTrue(all(row.delta == 0 for row in adjustments))

    def test_history_distance_uses_tags_and_penalizes_near_repeat(self) -> None:
        repeated = self._candidate(
            ordinal=0,
            colors=("네이비", "화이트"),
            styles=("미니멀",),
            fits=("레귤러핏",),
        )
        different = self._candidate(
            ordinal=1,
            colors=("브라운", "베이지"),
            styles=("캐주얼",),
            fits=("와이드핏",),
        )

        repeated_history = self.strategy.history_distance(self.context, repeated)
        different_history = self.strategy.history_distance(self.context, different)

        self.assertEqual(repeated_history.distance, 0)
        self.assertEqual(repeated_history.score_delta, -1)
        self.assertEqual(different_history.distance, 1)
        self.assertEqual(different_history.score_delta, 0)

    def test_exact_combination_repeat_gets_stronger_penalty(self) -> None:
        candidate = self._candidate(
            ordinal=0,
            history_metrics=(
                NumericMetric("item_overlap_ratio", 0.2),
                NumericMetric("exact_combination_repeat", 1),
            ),
        )

        history = self.strategy.history_distance(self.context, candidate)

        self.assertEqual(history.distance, 0.8)
        self.assertEqual(history.score_delta, -2)

    def test_profile_metric_drift_is_rejected(self) -> None:
        invalid_profile = StrategyProfile(
            objectives=("테스트",),
            search_directives=("테스트",),
            score_weights=(StrategyWeight("unknown", 1.0),),
            hypothesis_count=0,
        )

        with self.assertRaisesMessage(
            StylistStrategyContractError,
            "점수 지표가 구현 계약",
        ):
            MinimalStylistStrategy(invalid_profile)

    @staticmethod
    def _candidate(
        *,
        ordinal: int,
        colors: tuple[str, ...] = (),
        styles: tuple[str, ...] = (),
        fits: tuple[str, ...] = (),
        metrics: tuple[NumericMetric, ...] = (),
        history_metrics: tuple[NumericMetric, ...] = (),
    ) -> StrategyCandidateView:
        return StrategyCandidateView(
            ordinal=ordinal,
            base_score=80,
            similarity=0.8,
            tag_confidence=0.8,
            colors=colors,
            styles=styles,
            fits=fits,
            metrics=metrics,
            history_metrics=history_metrics,
        )
