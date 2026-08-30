from __future__ import annotations

from unittest.mock import Mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from apps.chat.services.experimental_hypotheses import (
    ExperimentalHypothesisBatch,
    ExperimentalHypothesisCandidateBatch,
    ExperimentAxis,
    ExperimentReasonCode,
)
from apps.chat.services.experimental_hypothesis_fallback import (
    ExperimentalHypothesisResolver,
    ExperimentalHypothesisSource,
    build_rule_based_experimental_hypotheses,
)
from apps.chat.services.openai_adapter import ChatLLMError, LLMResult, LLMUsage


class ExperimentalHypothesisFallbackTests(SimpleTestCase):
    def test_uses_repeated_slot_then_repeated_silhouette_in_priority_order(
        self,
    ) -> None:
        batch = build_rule_based_experimental_hypotheses(
            self._context(
                repeated_slots=[{"slot": "BOTTOM", "count": 5}],
                recent_fits=["와이드핏", "와이드핏", "와이드핏"],
            )
        )

        self.assertEqual(
            [row.change_axes for row in batch.hypotheses],
            [
                (ExperimentAxis.BOTTOM_STYLE,),
                (ExperimentAxis.BOTTOM_SILHOUETTE,),
            ],
        )
        self.assertEqual(
            [row.reason_code for row in batch.hypotheses],
            [
                ExperimentReasonCode.RECENT_SLOT_REPETITION,
                ExperimentReasonCode.RECENT_SILHOUETTE_REPETITION,
            ],
        )

    def test_uses_underused_calendar_slot_before_conservative_rule(self) -> None:
        batch = build_rule_based_experimental_hypotheses(
            self._context(with_underused_item=True)
        )

        self.assertEqual(
            [row.change_axes for row in batch.hypotheses],
            [
                (ExperimentAxis.UNDERUSED_ITEM_SLOT,),
                (ExperimentAxis.MATERIAL_MIX,),
            ],
        )
        self.assertEqual(
            batch.hypotheses[0].reason_code,
            ExperimentReasonCode.CALENDAR_ITEM_UNDERUSE,
        )

    def test_sparse_context_still_returns_two_deterministic_id_free_rules(self) -> None:
        first = build_rule_based_experimental_hypotheses(self._context())
        second = build_rule_based_experimental_hypotheses(self._context())

        self.assertEqual(first, second)
        self.assertEqual(
            [row.change_axes for row in first.hypotheses],
            [
                (ExperimentAxis.MATERIAL_MIX,),
                (ExperimentAxis.PROPORTION,),
            ],
        )
        snapshot = first.model_dump(mode="json")
        self.assertNotIn("item_id", str(snapshot))
        self.assertNotIn("skip_validation", str(snapshot))

    def test_resolver_returns_llm_result_without_running_fallback(self) -> None:
        llm = Mock()
        batch = build_rule_based_experimental_hypotheses(self._context())
        llm.generate_experimental_hypotheses.return_value = LLMResult(
            value=batch,
            response_id="response-1",
            usage=LLMUsage(input_tokens=30, output_tokens=12),
        )

        resolved = ExperimentalHypothesisResolver(llm=llm).resolve(
            identity_id="identity",
            context=self._context(),
        )

        self.assertEqual(resolved.source, ExperimentalHypothesisSource.LLM)
        self.assertEqual(resolved.batch, batch)
        self.assertEqual(resolved.response_id, "response-1")
        self.assertEqual(resolved.usage.input_tokens, 30)
        self.assertEqual(resolved.fallback_error_code, "")
        self.assertEqual(resolved.llm_accepted_count, 2)
        self.assertEqual(resolved.llm_rejection_codes, ())

    def test_rejects_only_invalid_hypothesis_and_fills_one_with_rule(self) -> None:
        llm = Mock()
        llm.generate_experimental_hypotheses.return_value = LLMResult(
            value=ExperimentalHypothesisCandidateBatch.model_validate(
                {
                    "hypotheses": [
                        {
                            "change_axes": ["bottom_style"],
                            "preserve_axes": ["top_style", "color_family"],
                            "reason_code": "RECENT_SLOT_REPETITION",
                        },
                        {
                            "change_axes": ["invented_axis"],
                            "preserve_axes": ["color_family"],
                            "reason_code": "SAME_COLOR_MATERIAL_VARIATION",
                        },
                    ]
                }
            ),
            response_id="partial-response",
            usage=LLMUsage(input_tokens=40, output_tokens=14),
        )

        resolved = ExperimentalHypothesisResolver(llm=llm).resolve(
            identity_id="identity",
            context=self._context(
                repeated_slots=[{"slot": "BOTTOM", "count": 5}]
            ),
        )

        self.assertEqual(resolved.source, ExperimentalHypothesisSource.HYBRID)
        self.assertEqual(resolved.llm_accepted_count, 1)
        self.assertEqual(
            resolved.llm_rejection_codes,
            ("INVALID_HYPOTHESIS_SCHEMA",),
        )
        self.assertEqual(resolved.response_id, "partial-response")
        self.assertEqual(resolved.usage.input_tokens, 40)
        self.assertEqual(
            [row.change_axes for row in resolved.batch.hypotheses],
            [
                (ExperimentAxis.BOTTOM_STYLE,),
                (ExperimentAxis.MATERIAL_MIX,),
            ],
        )
        self.assertEqual(
            resolved.fallback_error_code,
            "EXPERIMENTAL_HYPOTHESIS_PARTIAL_REJECTION",
        )
        self.assertEqual(resolved.snapshot()["llm_rejected_count"], 1)

    def test_filters_unsupported_reason_evidence_independently(self) -> None:
        llm = Mock()
        llm.generate_experimental_hypotheses.return_value = LLMResult(
            value=ExperimentalHypothesisCandidateBatch.model_validate(
                {
                    "hypotheses": [
                        {
                            "change_axes": ["underused_item_slot"],
                            "preserve_axes": ["color_family"],
                            "reason_code": "CALENDAR_ITEM_UNDERUSE",
                        },
                        {
                            "change_axes": ["material_mix"],
                            "preserve_axes": ["top_style", "color_family"],
                            "reason_code": "SAME_COLOR_MATERIAL_VARIATION",
                        },
                    ]
                }
            ),
            response_id="unsupported-evidence-response",
            usage=LLMUsage(input_tokens=20, output_tokens=8),
        )

        resolved = ExperimentalHypothesisResolver(llm=llm).resolve(
            identity_id="identity",
            context=self._context(),
        )

        self.assertEqual(resolved.source, ExperimentalHypothesisSource.HYBRID)
        self.assertEqual(
            resolved.llm_rejection_codes,
            ("UNSUPPORTED_REASON_EVIDENCE",),
        )
        self.assertEqual(
            resolved.batch.hypotheses[0].change_axes,
            (ExperimentAxis.MATERIAL_MIX,),
        )

    def test_duplicate_candidate_is_removed_before_rule_fill(self) -> None:
        llm = Mock()
        candidate = {
            "change_axes": ["material_mix"],
            "preserve_axes": ["top_style", "color_family"],
            "reason_code": "SAME_COLOR_MATERIAL_VARIATION",
        }
        llm.generate_experimental_hypotheses.return_value = LLMResult(
            value=ExperimentalHypothesisCandidateBatch.model_validate(
                {"hypotheses": [candidate, candidate]}
            ),
            response_id="duplicate-response",
            usage=LLMUsage(input_tokens=20, output_tokens=8),
        )

        resolved = ExperimentalHypothesisResolver(llm=llm).resolve(
            identity_id="identity",
            context=self._context(),
        )

        self.assertEqual(resolved.source, ExperimentalHypothesisSource.HYBRID)
        self.assertEqual(
            resolved.llm_rejection_codes,
            ("DUPLICATE_HYPOTHESIS",),
        )
        self.assertEqual(
            [row.change_axes for row in resolved.batch.hypotheses],
            [(ExperimentAxis.MATERIAL_MIX,), (ExperimentAxis.PROPORTION,)],
        )

    def test_reason_must_match_changed_axis_even_when_evidence_exists(self) -> None:
        llm = Mock()
        llm.generate_experimental_hypotheses.return_value = LLMResult(
            value=ExperimentalHypothesisCandidateBatch.model_validate(
                {
                    "hypotheses": [
                        {
                            "change_axes": ["material_mix"],
                            "preserve_axes": ["top_style", "color_family"],
                            "reason_code": "CALENDAR_ITEM_UNDERUSE",
                        },
                        {
                            "change_axes": ["proportion"],
                            "preserve_axes": ["top_style", "color_family"],
                            "reason_code": "SAME_COLOR_MATERIAL_VARIATION",
                        },
                    ]
                }
            ),
            response_id="axis-mismatch-response",
            usage=LLMUsage(input_tokens=20, output_tokens=8),
        )

        resolved = ExperimentalHypothesisResolver(llm=llm).resolve(
            identity_id="identity",
            context=self._context(with_underused_item=True),
        )

        self.assertEqual(resolved.source, ExperimentalHypothesisSource.HYBRID)
        self.assertEqual(
            resolved.llm_rejection_codes,
            ("REASON_AXIS_MISMATCH",),
        )

    def test_all_rejected_candidates_use_full_rule_fallback(self) -> None:
        llm = Mock()
        llm.generate_experimental_hypotheses.return_value = LLMResult(
            value=ExperimentalHypothesisCandidateBatch.model_validate(
                {
                    "hypotheses": [
                        {
                            "change_axes": ["unknown_one"],
                            "preserve_axes": ["color_family"],
                            "reason_code": "SAME_COLOR_MATERIAL_VARIATION",
                        },
                        {
                            "change_axes": ["unknown_two"],
                            "preserve_axes": ["top_style"],
                            "reason_code": "SAME_COLOR_MATERIAL_VARIATION",
                        },
                    ]
                }
            ),
            response_id="rejected-response",
            usage=LLMUsage(input_tokens=31, output_tokens=10),
        )

        resolved = ExperimentalHypothesisResolver(llm=llm).resolve(
            identity_id="identity",
            context=self._context(),
        )

        self.assertEqual(
            resolved.source,
            ExperimentalHypothesisSource.RULE_FALLBACK,
        )
        self.assertEqual(resolved.llm_accepted_count, 0)
        self.assertEqual(
            resolved.llm_rejection_codes,
            ("INVALID_HYPOTHESIS_SCHEMA", "INVALID_HYPOTHESIS_SCHEMA"),
        )
        self.assertEqual(
            resolved.fallback_error_code,
            "EXPERIMENTAL_HYPOTHESES_REJECTED",
        )
        self.assertEqual(resolved.response_id, "rejected-response")
        self.assertEqual(resolved.usage.input_tokens, 31)

    def test_provider_failure_returns_fallback_instead_of_raising(self) -> None:
        llm = Mock()
        llm.generate_experimental_hypotheses.side_effect = ChatLLMError(
            "provider unavailable"
        )

        resolved = ExperimentalHypothesisResolver(llm=llm).resolve(
            identity_id="identity",
            context=self._context(with_underused_item=True),
        )

        self.assertEqual(
            resolved.source,
            ExperimentalHypothesisSource.RULE_FALLBACK,
        )
        self.assertIsInstance(resolved.batch, ExperimentalHypothesisBatch)
        self.assertEqual(resolved.fallback_error_code, "CHAT_LLM_UNAVAILABLE")
        self.assertEqual(resolved.response_id, "")
        self.assertEqual(resolved.usage, LLMUsage())
        self.assertEqual(
            resolved.snapshot()["source"],
            ExperimentalHypothesisSource.RULE_FALLBACK.value,
        )

    def test_malformed_llm_result_type_uses_fallback(self) -> None:
        llm = Mock()
        llm.generate_experimental_hypotheses.return_value = LLMResult(
            value={"hypotheses": []},
            response_id="invalid-response",
            usage=LLMUsage(input_tokens=10, output_tokens=2),
        )

        resolved = ExperimentalHypothesisResolver(llm=llm).resolve(
            identity_id="identity",
            context=self._context(),
        )

        self.assertEqual(
            resolved.source,
            ExperimentalHypothesisSource.RULE_FALLBACK,
        )
        self.assertEqual(resolved.fallback_error_code, "CHAT_LLM_UNAVAILABLE")
        self.assertEqual(resolved.response_id, "")

    def test_missing_llm_configuration_also_uses_fallback(self) -> None:
        llm = Mock()
        llm.generate_experimental_hypotheses.side_effect = ImproperlyConfigured(
            "model missing"
        )

        resolved = ExperimentalHypothesisResolver(llm=llm).resolve(
            identity_id="identity",
            context=self._context(),
        )

        self.assertEqual(
            resolved.source,
            ExperimentalHypothesisSource.RULE_FALLBACK,
        )
        self.assertEqual(
            resolved.fallback_error_code,
            "CHAT_LLM_CONFIGURATION_ERROR",
        )

    @staticmethod
    def _context(
        *,
        repeated_slots: list[dict] | None = None,
        recent_fits: list[str] | None = None,
        with_underused_item: bool = False,
    ) -> dict:
        cards = [
            {
                "major_slots": ["BOTTOM"],
                "styles": ["캐주얼"],
                "colors": ["네이비"],
                "fits": [fit],
                "items": [{"source_id": f"secret-{index}"}],
            }
            for index, fit in enumerate(recent_fits or [])
        ]
        return {
            "current_request": "출근 코디를 추천해줘",
            "session": {"mode": "NEW_ITEM", "conditions": {"occasion": "출근"}},
            "profile": {"pursuit": {"preferred": {}, "avoided": {}}},
            "weather": {"temperature": 20},
            "behavior_signals": {
                "source_data": {
                    "recent_recommendations": {
                        "runs": ([{"results": [{"cards": cards}]}] if cards else []),
                        "repetitions": {
                            "slots": repeated_slots or [],
                            "items": [],
                            "combinations": [],
                        },
                    },
                    "calendar_wear": {
                        "not_worn_in_30d_items": (
                            [
                                {
                                    "wardrobe_item_id": "secret-wardrobe-id",
                                    "category_large": "아우터",
                                    "styles": ["미니멀"],
                                    "color": "베이지",
                                    "fit": "오버핏",
                                }
                            ]
                            if with_underused_item
                            else []
                        )
                    },
                }
            },
        }
