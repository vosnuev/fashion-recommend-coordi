from __future__ import annotations

from django.test import SimpleTestCase

from apps.chat.services.practical_stylist_strategy import (
    PRACTICAL_CANDIDATE_LIMIT,
    PracticalStylistStrategy,
)
from apps.chat.services.stylist_personas import StrategyProfile, StrategyWeight
from apps.chat.services.stylist_strategy import (
    NumericMetric,
    PreferencePolarity,
    StrategyCandidateView,
    StylistStrategy,
    StylistStrategyContext,
    StylistStrategyContractError,
    StylistStrategyRunner,
    TagGroup,
)


class PracticalStylistStrategyTests(SimpleTestCase):
    def setUp(self) -> None:
        self.strategy = PracticalStylistStrategy()
        self.context = StylistStrategyContext(
            request_text="비 오는 날 많이 걷는 출근 코디를 추천해줘",
            base_search_query="비 오는 날 많이 걷는 출근 코디",
            recommendation_mode="NEW_ITEM",
            occasion="출근",
            season="여름",
            preferred_tags=(
                TagGroup(axis="style", values=("캐주얼",)),
                TagGroup(axis="fit", values=("레귤러핏",)),
            ),
            avoided_tags=(TagGroup(axis="color", values=("레드",)),),
            recent_styles=("캐주얼",),
            recent_colors=("네이비",),
            recent_fits=("레귤러핏",),
            weather_metrics=(NumericMetric("rain_probability", 0.8),),
        )

    def test_build_plan_preserves_preferences_and_practical_search_factors(
        self,
    ) -> None:
        plan = self.strategy.build_plan(self.context)

        self.assertIsInstance(self.strategy, StylistStrategy)
        self.assertEqual(self.strategy.persona_id, "practical")
        self.assertTrue(plan.search_query.startswith(self.context.base_search_query))
        for term in (
            "기온",
            "체감온도",
            "강수",
            "바람",
            "활동량",
            "착탈의",
            "세탁",
        ):
            self.assertIn(term, plan.search_query)
        self.assertEqual(plan.candidate_limit, PRACTICAL_CANDIDATE_LIMIT)
        self.assertEqual(
            [row.polarity for row in plan.preference_adjustments],
            [
                PreferencePolarity.PREFER,
                PreferencePolarity.PREFER,
                PreferencePolarity.AVOID,
            ],
        )
        self.assertEqual(
            [row.axis for row in plan.preference_adjustments],
            ["style", "fit", "color"],
        )

    def test_weather_and_activity_fit_win_between_equally_valid_candidates(
        self,
    ) -> None:
        practical = self._candidate(
            ordinal=1,
            metrics=self._full_metrics(0.9),
        )
        impractical = self._candidate(
            ordinal=0,
            metrics=self._full_metrics(0.1),
        )

        result = StylistStrategyRunner().run(
            strategy=self.strategy,
            context=self.context,
            candidates=(impractical, practical),
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
            "PRACTICAL_WEATHER_FIT",
            "PRACTICAL_ACTIVITY_FIT",
            "PRACTICAL_WEARING_CONVENIENCE",
            "PRACTICAL_MAINTENANCE_EASE",
            "PRACTICAL_WARDROBE_BUDGET_EFFICIENCY",
        ):
            self.assertGreater(scores[1][reason_code], scores[0][reason_code])

    def test_convenience_does_not_override_preference_and_tpo(self) -> None:
        convenient_only = self._candidate(
            ordinal=0,
            tag_confidence=0.1,
            metrics=(*self._full_metrics(0.9), NumericMetric("tpo_fit", 0.1)),
        )
        balanced = self._candidate(
            ordinal=1,
            tag_confidence=0.9,
            metrics=(*self._full_metrics(0.7), NumericMetric("tpo_fit", 0.9)),
        )

        result = StylistStrategyRunner().run(
            strategy=self.strategy,
            context=self.context,
            candidates=(convenient_only, balanced),
        )

        self.assertEqual(result.ranked_candidates[0].candidate_ordinal, 1)

    def test_unverified_maintenance_data_is_neutral(self) -> None:
        unverified = self._candidate(
            ordinal=0,
            metrics=(NumericMetric("maintenance_ease", 1),),
        )
        verified = self._candidate(
            ordinal=1,
            metrics=(
                NumericMetric("maintenance_ease", 1),
                NumericMetric("maintenance_evidence", 0.8),
            ),
        )

        unverified_score = self._adjustments(unverified)["PRACTICAL_MAINTENANCE_EASE"]
        verified_score = self._adjustments(verified)["PRACTICAL_MAINTENANCE_EASE"]

        self.assertEqual(unverified_score, 0)
        self.assertGreater(verified_score, 0)

    def test_missing_operational_metrics_are_neutral(self) -> None:
        adjustments = self._adjustments(self._candidate(ordinal=0))

        for reason_code in (
            "PRACTICAL_WEATHER_FIT",
            "PRACTICAL_ACTIVITY_FIT",
            "PRACTICAL_WEARING_CONVENIENCE",
            "PRACTICAL_MAINTENANCE_EASE",
            "PRACTICAL_WARDROBE_BUDGET_EFFICIENCY",
        ):
            self.assertEqual(adjustments[reason_code], 0)

    def test_history_rewards_strong_preference_but_penalizes_exact_repeat(
        self,
    ) -> None:
        preferred = self._candidate(
            ordinal=0,
            history_metrics=(
                NumericMetric("item_overlap_ratio", 0.4),
                NumericMetric("strong_preference_overlap_ratio", 0.8),
            ),
        )
        repeated = self._candidate(
            ordinal=1,
            history_metrics=(
                NumericMetric("item_overlap_ratio", 0.9),
                NumericMetric("strong_preference_overlap_ratio", 1),
                NumericMetric("exact_combination_repeat", 1),
            ),
        )

        preferred_history = self.strategy.history_distance(self.context, preferred)
        repeated_history = self.strategy.history_distance(self.context, repeated)

        self.assertEqual(preferred_history.distance, 0.6)
        self.assertEqual(preferred_history.score_delta, 1.2)
        self.assertEqual(repeated_history.distance, 0.1)
        self.assertEqual(repeated_history.score_delta, -1.5)

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
            PracticalStylistStrategy(invalid_profile)

    def _adjustments(self, candidate: StrategyCandidateView) -> dict[str, float]:
        return {
            row.reason_code: row.delta
            for row in self.strategy.score_candidate(self.context, candidate)
        }

    @staticmethod
    def _full_metrics(value: float) -> tuple[NumericMetric, ...]:
        return (
            NumericMetric("temperature_fit", value),
            NumericMetric("apparent_temperature_fit", value),
            NumericMetric("precipitation_fit", value),
            NumericMetric("wind_fit", value),
            NumericMetric("mobility_fit", value),
            NumericMetric("walking_fit", value),
            NumericMetric("footwear_convenience", value),
            NumericMetric("outerwear_convenience", value),
            NumericMetric("dressing_convenience", value),
            NumericMetric("maintenance_ease", value),
            NumericMetric("maintenance_evidence", 1),
            NumericMetric("wardrobe_item_ratio", value),
            NumericMetric("new_purchase_ratio", 1 - value),
            NumericMetric("budget_efficiency", value),
        )

    @staticmethod
    def _candidate(
        *,
        ordinal: int,
        tag_confidence: float = 0.8,
        metrics: tuple[NumericMetric, ...] = (),
        history_metrics: tuple[NumericMetric, ...] = (),
    ) -> StrategyCandidateView:
        return StrategyCandidateView(
            ordinal=ordinal,
            base_score=80,
            similarity=0.8,
            tag_confidence=tag_confidence,
            styles=("캐주얼",),
            colors=("네이비",),
            fits=("레귤러핏",),
            slots=("TOP", "BOTTOM", "SHOES", "OUTER"),
            metrics=metrics,
            history_metrics=history_metrics,
        )
