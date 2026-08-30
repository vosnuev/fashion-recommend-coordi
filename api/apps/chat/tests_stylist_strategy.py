from __future__ import annotations

from dataclasses import fields

from django.test import SimpleTestCase

from apps.chat.services.stylist_personas import load_stylist_personas
from apps.chat.services.stylist_strategy import (
    CandidateStrategyEvaluation,
    HistoryDistance,
    NumericMetric,
    PreferenceAdjustment,
    PreferencePolarity,
    ScoreAdjustment,
    SortDirection,
    SortMetric,
    SortRule,
    StrategyCandidateView,
    StrategyPlan,
    StylistStrategy,
    StylistStrategyContext,
    StylistStrategyContractError,
    StylistStrategyRunner,
    TagGroup,
)


class ConfigurableTestStrategy:
    def __init__(
        self,
        *,
        persona_id,
        profile,
        query,
        candidate_limit,
        adjustment_by_ordinal,
        distance_by_ordinal,
        sort_rules,
    ) -> None:
        self._persona_id = persona_id
        self._profile = profile
        self.query = query
        self.candidate_limit = candidate_limit
        self.adjustment_by_ordinal = adjustment_by_ordinal
        self.distance_by_ordinal = distance_by_ordinal
        self.sort_rules = sort_rules

    @property
    def persona_id(self):
        return self._persona_id

    @property
    def profile(self):
        return self._profile

    def build_plan(self, context):
        return StrategyPlan(
            search_query=f"{context.base_search_query} {self.query}",
            preference_adjustments=(
                PreferenceAdjustment(
                    axis="style",
                    values=(self.query,),
                    polarity=PreferencePolarity.PREFER,
                    weight=0.5,
                ),
            ),
            candidate_limit=self.candidate_limit,
            sort_rules=self.sort_rules,
        )

    def score_candidate(self, context, candidate):
        del context
        delta = self.adjustment_by_ordinal.get(candidate.ordinal, 0.0)
        return (
            ScoreAdjustment(
                reason_code=f"{self.persona_id.upper()}_SCORE",
                delta=delta,
            ),
        )

    def history_distance(self, context, candidate):
        del context
        distance = self.distance_by_ordinal.get(candidate.ordinal, 0.0)
        return HistoryDistance(
            distance=distance,
            score_delta=distance * 10,
            reason_code=f"{self.persona_id.upper()}_HISTORY_DISTANCE",
        )


class StylistStrategyInterfaceTests(SimpleTestCase):
    def setUp(self) -> None:
        self.catalog = load_stylist_personas()
        self.context = StylistStrategyContext(
            request_text="비 오는 날 출근 코디를 추천해줘",
            base_search_query="비 오는 날 출근 코디",
            recommendation_mode="NEW_ITEM",
            occasion="출근",
            season="여름",
            preferred_tags=(TagGroup(axis="style", values=("미니멀",)),),
            avoided_tags=(TagGroup(axis="color", values=("레드",)),),
            recent_styles=("미니멀",),
            recent_colors=("네이비",),
            recent_fits=("레귤러핏",),
            repeated_slots=("TOP",),
            underused_slots=("OUTER",),
            weather_metrics=(NumericMetric("rain_probability", 0.8),),
            behavior_metrics=(NumericMetric("wardrobe_reuse", 0.6),),
        )
        self.candidates = (
            StrategyCandidateView(
                ordinal=0,
                base_score=88,
                similarity=0.88,
                tag_confidence=0.9,
                styles=("미니멀",),
                colors=("네이비",),
                fits=("레귤러핏",),
                slots=("TOP", "BOTTOM"),
                metrics=(NumericMetric("visual_simplicity", 0.9),),
                history_metrics=(NumericMetric("item_overlap_ratio", 0.8),),
            ),
            StrategyCandidateView(
                ordinal=1,
                base_score=86,
                similarity=0.86,
                tag_confidence=0.85,
                styles=("캐주얼",),
                colors=("블루",),
                fits=("와이드핏",),
                slots=("TOP", "BOTTOM"),
                metrics=(NumericMetric("weather_fit", 0.95),),
                history_metrics=(NumericMetric("item_overlap_ratio", 0.1),),
            ),
        )

    def _strategy(self, persona_id: str) -> ConfigurableTestStrategy:
        settings = {
            "minimal": {
                "query": "정돈된 조합",
                "candidate_limit": 5,
                "adjustments": {0: 8, 1: 0},
                "distances": {0: 0.2, 1: 0.9},
                "sort_rules": (
                    SortRule(SortMetric.TOTAL_SCORE, SortDirection.DESC),
                    SortRule(SortMetric.ORIGINAL_ORDER, SortDirection.ASC),
                ),
            },
            "experimental": {
                "query": "최근과 다른 조합",
                "candidate_limit": 8,
                "adjustments": {0: 0, 1: 4},
                "distances": {0: 0.2, 1: 0.9},
                "sort_rules": (
                    SortRule(SortMetric.HISTORY_DISTANCE, SortDirection.DESC),
                    SortRule(SortMetric.TOTAL_SCORE, SortDirection.DESC),
                    SortRule(SortMetric.ORIGINAL_ORDER, SortDirection.ASC),
                ),
            },
            "practical": {
                "query": "비와 이동을 고려한 조합",
                "candidate_limit": 6,
                "adjustments": {0: 0, 1: 10},
                "distances": {0: 0.2, 1: 0.4},
                "sort_rules": (
                    SortRule(SortMetric.TOTAL_SCORE, SortDirection.DESC),
                    SortRule(SortMetric.TAG_CONFIDENCE, SortDirection.DESC),
                    SortRule(SortMetric.ORIGINAL_ORDER, SortDirection.ASC),
                ),
            },
        }[persona_id]
        return ConfigurableTestStrategy(
            persona_id=persona_id,
            profile=self.catalog.get(persona_id).strategy_profile,
            query=settings["query"],
            candidate_limit=settings["candidate_limit"],
            adjustment_by_ordinal=settings["adjustments"],
            distance_by_ordinal=settings["distances"],
            sort_rules=settings["sort_rules"],
        )

    def test_three_personas_share_one_contract_but_can_return_different_values(self):
        runner = StylistStrategyRunner()
        results = {
            persona_id: runner.run(
                strategy=self._strategy(persona_id),
                context=self.context,
                candidates=self.candidates,
            )
            for persona_id in ("minimal", "experimental", "practical")
        }

        self.assertTrue(
            all(
                isinstance(self._strategy(persona_id), StylistStrategy)
                for persona_id in results
            )
        )
        self.assertEqual(
            {result.plan.search_query for result in results.values()},
            {
                "비 오는 날 출근 코디 정돈된 조합",
                "비 오는 날 출근 코디 최근과 다른 조합",
                "비 오는 날 출근 코디 비와 이동을 고려한 조합",
            },
        )
        self.assertEqual(
            {result.plan.candidate_limit for result in results.values()},
            {5, 6, 8},
        )
        self.assertEqual(
            results["minimal"].ranked_candidates[0].candidate_ordinal,
            0,
        )
        self.assertEqual(
            results["experimental"].ranked_candidates[0].candidate_ordinal,
            1,
        )
        self.assertEqual(
            results["practical"].ranked_candidates[0].candidate_ordinal,
            1,
        )

    def test_runner_returns_only_ordinal_scores_reasons_and_sort_order(self):
        result = StylistStrategyRunner().run(
            strategy=self._strategy("minimal"),
            context=self.context,
            candidates=self.candidates,
        )

        evaluation_fields = {
            field.name for field in fields(CandidateStrategyEvaluation)
        }
        candidate_view_fields = {field.name for field in fields(StrategyCandidateView)}
        forbidden = {
            "item_id",
            "source_id",
            "point_id",
            "golden_id",
            "composition",
            "items",
            "validator",
            "validation",
            "skip_validation",
        }
        self.assertTrue(forbidden.isdisjoint(evaluation_fields))
        self.assertTrue(forbidden.isdisjoint(candidate_view_fields))
        self.assertEqual(
            [row.candidate_ordinal for row in result.ranked_candidates],
            [0, 1],
        )
        self.assertEqual(
            result.ranked_candidates[0].score_adjustments[0].reason_code,
            "MINIMAL_SCORE",
        )

    def test_contract_rejects_invalid_limits_scores_distance_and_unstable_sort(self):
        stable_rule = SortRule(SortMetric.ORIGINAL_ORDER, SortDirection.ASC)
        with self.assertRaises(StylistStrategyContractError):
            StrategyPlan("query", (), 0, (stable_rule,))
        with self.assertRaises(StylistStrategyContractError):
            StrategyPlan(
                "query",
                (),
                5,
                (SortRule(SortMetric.TOTAL_SCORE, SortDirection.DESC),),
            )
        with self.assertRaises(StylistStrategyContractError):
            ScoreAdjustment("INVALID-CODE", 1)
        with self.assertRaises(StylistStrategyContractError):
            ScoreAdjustment("TOO_LARGE", 101)
        with self.assertRaises(StylistStrategyContractError):
            HistoryDistance(1.01, 0, "HISTORY_DISTANCE")
        with self.assertRaises(StylistStrategyContractError):
            NumericMetric("invalid", float("nan"))
        with self.assertRaises(StylistStrategyContractError):
            PreferenceAdjustment(
                axis="item_id",
                values=("fabricated-item",),
                polarity=PreferencePolarity.PREFER,
            )

    def test_runner_rejects_non_contract_outputs_and_voice_profile(self):
        strategy = self._strategy("minimal")
        strategy._profile = self.catalog.get("minimal").voice_profile
        with self.assertRaises(StylistStrategyContractError):
            StylistStrategyRunner().run(
                strategy=strategy,
                context=self.context,
                candidates=self.candidates,
            )

        strategy = self._strategy("minimal")
        strategy.build_plan = lambda context: {
            "search_query": context.base_search_query,
            "item_id": "fabricated-item",
            "skip_validation": True,
        }
        with self.assertRaises(StylistStrategyContractError):
            StylistStrategyRunner().run(
                strategy=strategy,
                context=self.context,
                candidates=self.candidates,
            )

    def test_candidate_count_and_ordinals_are_guarded(self):
        strategy = self._strategy("minimal")
        strategy.candidate_limit = 1
        with self.assertRaises(StylistStrategyContractError):
            StylistStrategyRunner().run(
                strategy=strategy,
                context=self.context,
                candidates=self.candidates,
            )

        duplicate = (self.candidates[0], self.candidates[0])
        with self.assertRaises(StylistStrategyContractError):
            StylistStrategyRunner().run(
                strategy=self._strategy("minimal"),
                context=self.context,
                candidates=duplicate,
            )

        unsupported = self._strategy("minimal")
        unsupported._persona_id = "fabricated-stylist"
        with self.assertRaises(StylistStrategyContractError):
            StylistStrategyRunner().run(
                strategy=unsupported,
                context=self.context,
                candidates=self.candidates,
            )
