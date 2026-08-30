from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase, override_settings

from apps.chat.services.experimental_hypotheses import (
    EXPERIMENT_AXIS_VALUES,
    EXPERIMENT_REASON_CODE_VALUES,
    ExperimentalHypothesisBatch,
    ExperimentalHypothesisCandidateBatch,
)
from apps.chat.services.experimental_hypothesis_generation import (
    EXPERIMENTAL_HYPOTHESIS_INSTRUCTIONS,
    build_experimental_hypothesis_payload,
)
from apps.chat.services.openai_adapter import OpenAIChatAdapter


def _batch() -> ExperimentalHypothesisBatch:
    return ExperimentalHypothesisBatch.model_validate(
        {
            "hypotheses": [
                {
                    "change_axes": ["bottom_silhouette"],
                    "preserve_axes": ["top_style", "color_family"],
                    "reason_code": "RECENT_SILHOUETTE_REPETITION",
                },
                {
                    "change_axes": ["underused_item_slot"],
                    "preserve_axes": ["color_family"],
                    "reason_code": "CALENDAR_ITEM_UNDERUSE",
                },
            ]
        }
    )


def _candidate_batch() -> ExperimentalHypothesisCandidateBatch:
    return ExperimentalHypothesisCandidateBatch.model_validate(
        _batch().model_dump(mode="json")
    )


@override_settings(
    CHAT_OPENAI_MODEL="gpt-4o-mini",
    CHAT_PROMPT_VERSION="test-prompt-v1",
    CHAT_OPENAI_MAX_OUTPUT_TOKENS=500,
)
class ExperimentalHypothesisLLMTests(SimpleTestCase):
    def test_calls_existing_main_llm_with_strict_hypothesis_schema(self) -> None:
        response = SimpleNamespace(
            id="resp-hypothesis",
            output_parsed=_candidate_batch(),
            usage=SimpleNamespace(
                input_tokens=80,
                output_tokens=24,
                input_tokens_details=SimpleNamespace(cached_tokens=20),
            ),
        )
        client = Mock()
        client.responses.parse.return_value = response

        result = OpenAIChatAdapter(client=client).generate_experimental_hypotheses(
            identity_id="internal-user-id",
            context=self._context(),
        )

        kwargs = client.responses.parse.call_args.kwargs
        payload = json.loads(kwargs["input"][0]["content"])
        self.assertIs(kwargs["text_format"], ExperimentalHypothesisCandidateBatch)
        self.assertIs(kwargs["instructions"], EXPERIMENTAL_HYPOTHESIS_INSTRUCTIONS)
        self.assertFalse(kwargs["store"])
        self.assertNotEqual(kwargs["safety_identifier"], "internal-user-id")
        self.assertIn(
            "ExperimentalHypothesisCandidateBatch",
            kwargs["prompt_cache_key"],
        )
        self.assertEqual(
            payload["allowed_values"]["axes"], list(EXPERIMENT_AXIS_VALUES)
        )
        self.assertEqual(
            payload["allowed_values"]["reason_codes"],
            list(EXPERIMENT_REASON_CODE_VALUES),
        )
        self.assertEqual(result.value, _candidate_batch())
        self.assertEqual(result.response_id, "resp-hypothesis")
        self.assertEqual(result.usage.cached_input_tokens, 20)

    def test_payload_removes_internal_ids_and_keeps_aggregate_evidence(self) -> None:
        payload = build_experimental_hypothesis_payload(self._context())
        serialized = json.dumps(payload, ensure_ascii=False)

        for secret in (
            "session-secret",
            "user-secret",
            "run-secret",
            "result-secret",
            "composition-secret",
            "product-secret",
            "wardrobe-secret",
            "calendar-secret",
        ):
            self.assertNotIn(secret, serialized)
        self.assertEqual(
            payload["behavior"]["recent_recommendations"]["style_counts"],
            [{"value": "캐주얼", "count": 1}],
        )
        self.assertEqual(
            payload["behavior"]["recent_recommendations"]["repeated_slots"],
            [{"slot": "TOP", "count": 3}],
        )
        self.assertEqual(
            payload["behavior"]["calendar_wear"]["underused_item_features"][
                "styles_counts"
            ],
            [{"value": "미니멀", "count": 1}],
        )
        self.assertEqual(
            payload["behavior"]["feedback"]["liked"]["styles_counts"],
            [{"value": "캐주얼", "count": 1}],
        )

    def test_prompt_forbids_item_selection_and_hard_condition_bypass(self) -> None:
        for phrase in (
            "ID를 만들거나 선택하지 않는다",
            "최종 코디를 정하지 않고",
            "Validator를 우회하거나 완화하지 않는다",
            "고정 키워드",
        ):
            self.assertIn(phrase, EXPERIMENTAL_HYPOTHESIS_INSTRUCTIONS)

    def test_non_mapping_context_is_rejected_before_llm_call(self) -> None:
        client = Mock()

        with self.assertRaisesMessage(TypeError, "JSON 객체"):
            OpenAIChatAdapter(client=client).generate_experimental_hypotheses(
                identity_id="identity",
                context=[],
            )

        client.responses.parse.assert_not_called()

    @staticmethod
    def _context() -> dict:
        return {
            "current_request": "비 오는 날 출근 코디",
            "session": {
                "id": "session-secret",
                "mode": "NEW_ITEM",
                "conditions": {
                    "occasion": "출근",
                    "styles": ["캐주얼"],
                    "item_id": "product-secret",
                },
            },
            "profile": {
                "user_id": "user-secret",
                "pursuit": {
                    "preferred": {"styles": ["캐주얼"]},
                    "avoided": {"colors": ["레드"]},
                },
            },
            "weather": {
                "temperature": 23,
                "rain_probability": 0.8,
                "station_id": "weather-secret",
            },
            "behavior_signals": {
                "collection_status": {
                    "saved_outfits": {
                        "available": False,
                        "reason": "STORAGE_NOT_IMPLEMENTED",
                    }
                },
                "summary": {
                    "calendar_registrations_30d": 2,
                    "liked_recommendation_cards": 1,
                },
                "signals": {
                    "weak_preferences": {
                        "liked_recommendation_cards": [
                            {
                                "run_id": "run-secret",
                                "result_id": "result-secret",
                                "composition_id": "composition-secret",
                                "outfit": {
                                    "styles": ["캐주얼"],
                                    "colors": ["네이비"],
                                    "fits": ["레귤러핏"],
                                    "items": [{"source_id": "product-secret"}],
                                },
                                "reason_codes": ["STYLE"],
                            }
                        ]
                    },
                    "negative_preferences": {"disliked_recommendation_cards": []},
                },
                "source_data": {
                    "recent_recommendations": {
                        "runs": [
                            {
                                "run_id": "run-secret",
                                "results": [
                                    {
                                        "result_id": "result-secret",
                                        "cards": [
                                            {
                                                "composition_id": "composition-secret",
                                                "major_slots": ["TOP"],
                                                "styles": ["캐주얼"],
                                                "colors": ["네이비"],
                                                "fits": ["레귤러핏"],
                                                "items": [
                                                    {"source_id": "product-secret"}
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                        "repetitions": {
                            "items": [{"source_id": "product-secret", "count": 3}],
                            "combinations": [
                                {"items": [{"source_id": "product-secret"}], "count": 2}
                            ],
                            "slots": [{"slot": "TOP", "count": 3}],
                        },
                    },
                    "calendar_wear": {
                        "as_of_date": "2026-08-15",
                        "entry_counts": {"7d": 1, "14d": 1, "30d": 2},
                        "linked_item_occurrence_counts": {
                            "7d": 1,
                            "14d": 1,
                            "30d": 2,
                        },
                        "recent_entries": [
                            {
                                "calendar_id": "calendar-secret",
                                "tpo": ["출근"],
                                "items": [{"wardrobe_item_id": "wardrobe-secret"}],
                            }
                        ],
                        "worn_items": [
                            {
                                "wardrobe_item_id": "wardrobe-secret",
                                "category_large": "상의",
                                "styles": ["캐주얼"],
                                "color": "네이비",
                                "fit": "레귤러핏",
                            }
                        ],
                        "not_worn_in_30d_items": [
                            {
                                "wardrobe_item_id": "wardrobe-secret",
                                "category_large": "아우터",
                                "styles": ["미니멀"],
                                "color": "베이지",
                                "fit": "오버핏",
                            }
                        ],
                        "repeated_combinations_30d": [
                            {
                                "wardrobe_item_ids": ["wardrobe-secret"],
                                "count": 2,
                            }
                        ],
                    },
                },
            },
        }
