from __future__ import annotations

from dataclasses import replace

from django.test import SimpleTestCase

from apps.chat.services.openai_adapter import LLMUsage
from apps.chat.services.recommendation_pipeline import (
    GeneratedRecommendationCandidates,
    ValidatedRecommendationCandidate,
)
from apps.chat.services.stylist_duplicate_resolver import (
    DiversityReasonCode,
    DuplicateKind,
    StylistDuplicateResolutionError,
    StylistDuplicateResolver,
    classify_duplicate,
)
from apps.chat.services.stylist_recommendation_pipeline import (
    PersonaRecommendationCandidates,
    RankedValidatedCandidate,
)
from apps.chat.services.stylist_strategy import (
    CandidateStrategyEvaluation,
    HistoryDistance,
    SortDirection,
    SortMetric,
    SortRule,
    StrategyExecutionResult,
    StrategyPlan,
)
from apps.recommend.services.item_retriever import ItemSource
from apps.recommend.services.outfit_types import (
    OutfitComposition,
    OutfitItem,
    RecommendationMode,
)
from apps.recommend.services.retriever import OutfitCandidate
from apps.recommend.services.validator import (
    OutfitValidationResult,
    ValidationIssue,
    ValidationSeverity,
)


class StylistDuplicateResolverTests(SimpleTestCase):
    def test_exact_duplicate_uses_next_valid_candidate_in_fixed_persona_order(
        self,
    ) -> None:
        shared = self._major_items("shared")
        minimal = self._result("minimal", [(96, shared)])
        experimental = self._result(
            "experimental",
            [
                (95, shared),
                (92, self._major_items("experimental-alt")),
            ],
        )

        resolved = StylistDuplicateResolver().resolve((experimental, minimal))

        self.assertEqual(
            [selection.persona_id for selection in resolved.selections],
            ["minimal", "experimental"],
        )
        selection = resolved.get("experimental")
        self.assertEqual(selection.selected_rank, 2)
        self.assertEqual(
            selection.reason_code,
            DiversityReasonCode.DUPLICATE_REPLACED,
        )
        self.assertEqual(selection.duplicate_matches[0].kind, DuplicateKind.EXACT)
        self.assertEqual(selection.score_drop, 3)
        self.assertIn(
            "STYLIST_DUPLICATE_REPLACED",
            selection.validated_reason_codes,
        )
        self.assertNotIn("shared-top", str(selection.snapshot()))

    def test_three_matching_major_slots_are_duplicate(self) -> None:
        minimal_items = {
            **self._major_items("shared"),
            "ACCESSORY": "minimal-accessory",
        }
        practical_first = {
            "TOP": "shared-top",
            "BOTTOM": "shared-bottom",
            "OUTER": "shared-outer",
            "FOOTWEAR": "practical-shoes",
            "ACCESSORY": "practical-accessory",
        }
        minimal = self._result("minimal", [(90, minimal_items)])
        practical = self._result(
            "practical",
            [
                (89, practical_first),
                (87, self._major_items("practical-alt")),
            ],
        )

        selection = (
            StylistDuplicateResolver().resolve((minimal, practical)).get("practical")
        )

        self.assertEqual(selection.selected_rank, 2)
        self.assertEqual(
            selection.duplicate_matches[0].kind,
            DuplicateKind.MAJOR_SLOTS,
        )
        self.assertEqual(
            selection.duplicate_matches[0].matching_major_slots,
            ("TOP", "BOTTOM", "OUTER"),
        )

    def test_user_fixed_slot_is_excluded_from_major_duplicate_count(self) -> None:
        left = self._composition(
            {
                "TOP": "fixed-top",
                "BOTTOM": "shared-bottom",
                "OUTER": "shared-outer",
                "FOOTWEAR": "left-shoes",
            }
        )
        right = self._composition(
            {
                "TOP": "fixed-top",
                "BOTTOM": "shared-bottom",
                "OUTER": "shared-outer",
                "FOOTWEAR": "right-shoes",
            }
        )

        self.assertEqual(
            classify_duplicate(left, right),
            (DuplicateKind.MAJOR_SLOTS, ("TOP", "BOTTOM", "OUTER")),
        )
        self.assertIsNone(
            classify_duplicate(
                left,
                right,
                allowed_duplicate_slots=("TOP",),
            )
        )

        minimal = self._result("minimal", [(90, self._item_map(left))])
        experimental = self._result(
            "experimental",
            [(89, self._item_map(right))],
        )
        selection = (
            StylistDuplicateResolver()
            .resolve(
                (minimal, experimental),
                allowed_duplicate_slots=("TOP",),
            )
            .get("experimental")
        )
        self.assertEqual(selection.selected_rank, 1)
        self.assertIsNone(selection.reason_code)
        self.assertEqual(selection.allowed_duplicate_slots, ("TOP",))

    def test_same_dress_is_a_core_duplicate_even_when_shoes_change(self) -> None:
        left = self._composition(
            {"원피스/세트": "shared-dress", "SHOES": "left-shoes"}
        )
        right = self._composition(
            {"DRESS": "shared-dress", "FOOTWEAR": "right-shoes"}
        )

        self.assertEqual(
            classify_duplicate(left, right),
            (DuplicateKind.MAJOR_SLOTS, ("DRESS",)),
        )

    def test_matching_bag_contributes_to_major_slot_overlap(self) -> None:
        left = self._composition(
            {
                "TOP": "shared-top",
                "BOTTOM": "shared-bottom",
                "가방": "shared-bag",
                "FOOTWEAR": "left-shoes",
            }
        )
        right = self._composition(
            {
                "TOP": "shared-top",
                "BOTTOM": "shared-bottom",
                "ACCESSORY": "shared-bag",
                "FOOTWEAR": "right-shoes",
            }
        )

        self.assertEqual(
            classify_duplicate(left, right),
            (
                DuplicateKind.MAJOR_SLOTS,
                ("TOP", "BOTTOM", "ACCESSORY"),
            ),
        )

    def test_high_item_overlap_is_duplicate_when_core_overlap_is_below_threshold(
        self,
    ) -> None:
        left = self._composition(
            {
                "TOP": "left-top",
                "BOTTOM": "shared-bottom",
                "FOOTWEAR": "shared-footwear",
                "MISC_A": "shared-hat",
                "MISC_B": "shared-bag",
            }
        )
        right = self._composition(
            {
                "TOP": "right-top",
                "BOTTOM": "shared-bottom",
                "FOOTWEAR": "shared-footwear",
                "MISC_A": "shared-hat",
                "MISC_B": "shared-bag",
            }
        )

        self.assertEqual(
            classify_duplicate(left, right),
            (DuplicateKind.HIGH_ITEM_OVERLAP, ()),
        )

    def test_quality_guard_keeps_duplicate_when_distinct_candidate_drops_too_much(
        self,
    ) -> None:
        shared = self._major_items("shared")
        minimal = self._result("minimal", [(100, shared)])
        experimental = self._result(
            "experimental",
            [
                (99, shared),
                (90, self._major_items("low-quality-alt")),
            ],
        )

        selection = (
            StylistDuplicateResolver(max_score_drop=5)
            .resolve((minimal, experimental))
            .get("experimental")
        )

        self.assertEqual(selection.selected_rank, 1)
        self.assertEqual(
            selection.reason_code,
            DiversityReasonCode.DUPLICATE_ALLOWED_QUALITY_GUARD,
        )
        self.assertEqual(selection.score_drop, 9)
        self.assertEqual(selection.duplicate_matches[0].kind, DuplicateKind.EXACT)

    def test_default_policy_prefers_distinct_valid_candidate_over_score_drop(
        self,
    ) -> None:
        shared = self._major_items("shared")
        minimal = self._result("minimal", [(100, shared)])
        experimental = self._result(
            "experimental",
            [
                (99, shared),
                (90, self._major_items("distinct")),
            ],
        )

        selection = (
            StylistDuplicateResolver()
            .resolve((minimal, experimental))
            .get("experimental")
        )

        self.assertEqual(selection.selected_rank, 2)
        self.assertEqual(
            selection.reason_code,
            DiversityReasonCode.DUPLICATE_REPLACED,
        )
        self.assertEqual(selection.score_drop, 9)

    def test_candidate_exhaustion_allows_duplicate_and_cross_run_is_rejected(
        self,
    ) -> None:
        shared = self._major_items("shared")
        minimal = self._result("minimal", [(90, shared)])
        practical = self._result("practical", [(88, shared)])

        selection = (
            StylistDuplicateResolver().resolve((minimal, practical)).get("practical")
        )

        self.assertEqual(
            selection.reason_code,
            DiversityReasonCode.DUPLICATE_ALLOWED_CANDIDATE_EXHAUSTED,
        )

        other_run = self._result("experimental", [(87, shared)], run_id="run-2")
        with self.assertRaises(StylistDuplicateResolutionError):
            StylistDuplicateResolver().resolve((minimal, other_run))

    def test_all_alternatives_duplicate_records_no_distinct_candidate(self) -> None:
        shared = self._major_items("shared")
        minimal = self._result("minimal", [(95, shared)])
        experimental = self._result(
            "experimental",
            [
                (94, shared),
                (
                    93,
                    {
                        "TOP": "shared-top",
                        "BOTTOM": "shared-bottom",
                        "OUTER": "shared-outer",
                        "FOOTWEAR": "different-footwear",
                    },
                ),
            ],
        )

        selection = (
            StylistDuplicateResolver()
            .resolve((minimal, experimental))
            .get("experimental")
        )

        self.assertEqual(
            selection.reason_code,
            DiversityReasonCode.DUPLICATE_ALLOWED_NO_DISTINCT_CANDIDATE,
        )
        self.assertEqual(selection.selected_rank, 1)

    def test_validator_rejected_candidate_cannot_enter_duplicate_resolution(
        self,
    ) -> None:
        minimal = self._result("minimal", [(90, self._major_items("minimal"))])
        practical = self._result(
            "practical",
            [(89, self._major_items("practical"))],
        )
        ranked = practical.ranked_candidates[0]
        invalid_candidate = replace(
            ranked.candidate,
            validation=OutfitValidationResult(
                issues=(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="HARD_CONDITION_FAILED",
                        message="공통 하드 조건 위반",
                    ),
                ),
                effective_total_product_price=40_000,
            ),
        )
        invalid_result = replace(
            practical,
            generated=replace(
                practical.generated,
                candidates=(invalid_candidate,),
            ),
            ranked_candidates=(replace(ranked, candidate=invalid_candidate),),
        )

        with self.assertRaises(StylistDuplicateResolutionError):
            StylistDuplicateResolver().resolve((minimal, invalid_result))

    @staticmethod
    def _major_items(prefix: str) -> dict[str, str]:
        return {
            "TOP": f"{prefix}-top",
            "BOTTOM": f"{prefix}-bottom",
            "OUTER": f"{prefix}-outer",
            "FOOTWEAR": f"{prefix}-footwear",
        }

    @staticmethod
    def _item_map(composition: OutfitComposition) -> dict[str, str]:
        return {item.slot_id: item.source_id for item in composition.items}

    @staticmethod
    def _composition(items: dict[str, str]) -> OutfitComposition:
        return OutfitComposition(
            mode=RecommendationMode.NEW_ITEM,
            items=tuple(
                OutfitItem(
                    slot_id=slot,
                    template_point_id=f"template-{slot.lower()}",
                    category_large=slot,
                    layer_role="",
                    source_type=ItemSource.PRODUCT,
                    source_id=source_id,
                    source_collection="products_naver_v1",
                    point_id=f"point-{source_id}",
                    image_ref=f"https://example.com/{source_id}.jpg",
                    price=10_000,
                    score=0.9,
                    reasons=(),
                    payload={"title": source_id},
                )
                for slot, source_id in items.items()
            ),
            missing_slot_ids=(),
            total_product_price=len(items) * 10_000,
        )

    def _result(
        self,
        persona_id: str,
        candidates: list[tuple[float, dict[str, str]]],
        *,
        run_id: str = "run-1",
    ) -> PersonaRecommendationCandidates:
        ranked: list[RankedValidatedCandidate] = []
        generated_candidates: list[ValidatedRecommendationCandidate] = []
        evaluations: list[CandidateStrategyEvaluation] = []
        for ordinal, (score, items) in enumerate(candidates, start=1):
            composition = self._composition(items)
            validated = ValidatedRecommendationCandidate(
                ordinal=ordinal,
                template_rank=ordinal,
                composition_rank=1,
                golden=OutfitCandidate(
                    point_id=f"{persona_id}-point-{ordinal}",
                    golden_id=f"{persona_id}-golden-{ordinal}",
                    score=score,
                    similarity=0.8,
                    payload={"item_point_ids": ["template-top"]},
                ),
                composition=composition,
                validation=OutfitValidationResult(
                    issues=(),
                    effective_total_product_price=composition.total_product_price,
                ),
            )
            evaluation = CandidateStrategyEvaluation(
                candidate_ordinal=ordinal,
                base_score=score,
                score_adjustments=(),
                history_distance=HistoryDistance(
                    distance=0.5,
                    score_delta=0,
                    reason_code=f"{persona_id.upper()}_HISTORY",
                ),
                total_score=score,
                similarity=0.8,
                tag_confidence=0.8,
            )
            generated_candidates.append(validated)
            evaluations.append(evaluation)
            ranked.append(
                RankedValidatedCandidate(
                    candidate=validated,
                    evaluation=evaluation,
                )
            )

        plan = StrategyPlan(
            search_query=f"{persona_id} query",
            preference_adjustments=(),
            candidate_limit=3,
            sort_rules=(SortRule(SortMetric.ORIGINAL_ORDER, SortDirection.ASC),),
        )
        generated = GeneratedRecommendationCandidates(
            run_id=run_id,
            session_id="session-1",
            identity_id="identity-1",
            response_mode="STYLIST",
            mode="NEW_ITEM",
            search_mode="text",
            candidates=tuple(generated_candidates),
        )
        return PersonaRecommendationCandidates(
            persona_id=persona_id,
            persona_execution_id=f"execution-{persona_id}",
            generated=generated,
            strategy_result=StrategyExecutionResult(
                persona_id=persona_id,
                plan=plan,
                ranked_candidates=tuple(evaluations),
            ),
            ranked_candidates=tuple(ranked),
            hypothesis_snapshot={},
            hypothesis_usage=LLMUsage(),
            hypothesis_response_id="",
        )
