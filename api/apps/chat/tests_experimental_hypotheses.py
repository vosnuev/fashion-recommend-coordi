from __future__ import annotations

from django.test import SimpleTestCase
from pydantic import ValidationError

from apps.chat.services.experimental_hypotheses import (
    EXPERIMENT_AXIS_VALUES,
    EXPERIMENT_HYPOTHESIS_COUNT,
    EXPERIMENT_REASON_CODE_VALUES,
    ExperimentalHypothesis,
    ExperimentalHypothesisBatch,
    ExperimentAxis,
    ExperimentReasonCode,
)


class ExperimentalHypothesisSchemaTests(SimpleTestCase):
    def test_allowed_values_are_fixed_and_id_free(self) -> None:
        self.assertEqual(
            EXPERIMENT_AXIS_VALUES,
            (
                "top_style",
                "bottom_style",
                "outer_style",
                "footwear_style",
                "style_mix",
                "top_silhouette",
                "bottom_silhouette",
                "outer_silhouette",
                "color_family",
                "color_contrast",
                "proportion",
                "layering",
                "material_mix",
                "pattern_density",
                "underused_item_slot",
            ),
        )
        self.assertEqual(
            EXPERIMENT_REASON_CODE_VALUES,
            (
                "RECENT_SLOT_REPETITION",
                "RECENT_SILHOUETTE_REPETITION",
                "RECENT_STYLE_REPETITION",
                "RECENT_COLOR_REPETITION",
                "RECENT_COMBINATION_REPETITION",
                "CALENDAR_ITEM_UNDERUSE",
                "STRONG_PREFERENCE_ANCHOR",
                "SAME_COLOR_MATERIAL_VARIATION",
            ),
        )
        for value in (*EXPERIMENT_AXIS_VALUES, *EXPERIMENT_REASON_CODE_VALUES):
            self.assertNotIn("_id", value.casefold())

    def test_json_schema_exposes_strict_structured_output_contract(self) -> None:
        schema = ExperimentalHypothesisBatch.model_json_schema()
        hypothesis_schema = schema["$defs"]["ExperimentalHypothesis"]

        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(hypothesis_schema["additionalProperties"])
        self.assertEqual(schema["properties"]["hypotheses"]["minItems"], 2)
        self.assertEqual(schema["properties"]["hypotheses"]["maxItems"], 2)
        self.assertEqual(
            hypothesis_schema["properties"]["change_axes"]["minItems"],
            1,
        )
        self.assertEqual(
            hypothesis_schema["properties"]["change_axes"]["maxItems"],
            2,
        )
        self.assertEqual(
            schema["$defs"]["ExperimentAxis"]["enum"],
            list(EXPERIMENT_AXIS_VALUES),
        )
        self.assertEqual(
            schema["$defs"]["ExperimentReasonCode"]["enum"],
            list(EXPERIMENT_REASON_CODE_VALUES),
        )

    def test_valid_batch_has_exactly_two_canonical_hypotheses(self) -> None:
        batch = ExperimentalHypothesisBatch.model_validate(
            {
                "hypotheses": [
                    {
                        "change_axes": ["bottom_silhouette"],
                        "preserve_axes": ["color_family", "top_style"],
                        "reason_code": "RECENT_SILHOUETTE_REPETITION",
                    },
                    {
                        "change_axes": ["material_mix", "proportion"],
                        "preserve_axes": ["color_family"],
                        "reason_code": "SAME_COLOR_MATERIAL_VARIATION",
                    },
                ]
            }
        )

        self.assertEqual(len(batch.hypotheses), EXPERIMENT_HYPOTHESIS_COUNT)
        self.assertEqual(
            batch.hypotheses[0].preserve_axes,
            (ExperimentAxis.TOP_STYLE, ExperimentAxis.COLOR_FAMILY),
        )
        self.assertEqual(
            batch.model_dump(mode="json")["hypotheses"][0],
            {
                "change_axes": ["bottom_silhouette"],
                "preserve_axes": ["top_style", "color_family"],
                "reason_code": "RECENT_SILHOUETTE_REPETITION",
            },
        )

    def test_unknown_axis_reason_and_forbidden_fields_are_rejected(self) -> None:
        invalid_rows = (
            self._hypothesis(change_axes=["retro_keyword"]),
            self._hypothesis(reason_code="FREE_FORM_REASON"),
            {**self._hypothesis(), "item_id": 123},
            {**self._hypothesis(), "skip_validation": True},
        )

        for row in invalid_rows:
            with self.subTest(row=row), self.assertRaises(ValidationError):
                ExperimentalHypothesis.model_validate(row)

    def test_change_and_preserve_axes_must_be_unique_and_disjoint(self) -> None:
        invalid_rows = (
            self._hypothesis(change_axes=["bottom_silhouette", "bottom_silhouette"]),
            self._hypothesis(
                change_axes=["bottom_silhouette"],
                preserve_axes=["bottom_silhouette"],
            ),
            self._hypothesis(preserve_axes=["underused_item_slot"]),
        )

        for row in invalid_rows:
            with self.subTest(row=row), self.assertRaises(ValidationError):
                ExperimentalHypothesis.model_validate(row)

    def test_batch_requires_exactly_two_distinct_axis_plans(self) -> None:
        first = self._hypothesis()
        duplicate_with_other_reason = self._hypothesis(
            reason_code=ExperimentReasonCode.STRONG_PREFERENCE_ANCHOR,
        )

        with self.assertRaises(ValidationError):
            ExperimentalHypothesisBatch.model_validate({"hypotheses": [first]})
        with self.assertRaises(ValidationError):
            ExperimentalHypothesisBatch.model_validate(
                {"hypotheses": [first, duplicate_with_other_reason]}
            )
        with self.assertRaises(ValidationError):
            ExperimentalHypothesisBatch.model_validate(
                {
                    "hypotheses": [
                        first,
                        self._hypothesis(change_axes=["color_contrast"]),
                        self._hypothesis(change_axes=["material_mix"]),
                    ]
                }
            )

    @staticmethod
    def _hypothesis(**overrides):
        value = {
            "change_axes": ["bottom_silhouette"],
            "preserve_axes": ["top_style", "color_family"],
            "reason_code": "RECENT_SILHOUETTE_REPETITION",
        }
        value.update(overrides)
        return value
