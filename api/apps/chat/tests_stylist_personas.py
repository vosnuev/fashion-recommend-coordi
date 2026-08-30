from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from apps.chat.services.stylist_personas import (
    DEFAULT_CONFIG_PATH,
    EXPECTED_PERSONA_ORDER,
    StylistPersonaConfigurationError,
    clear_stylist_persona_cache,
    load_stylist_personas,
    strategy_profile_from_snapshot,
)


class StylistPersonaConfigurationTests(SimpleTestCase):
    def tearDown(self) -> None:
        clear_stylist_persona_cache()

    @staticmethod
    def _default_document() -> dict:
        return json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))

    def _write_document(self, directory: str, document: dict) -> Path:
        path = Path(directory) / "stylist_personas.json"
        path.write_text(
            json.dumps(document, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_default_configuration_loads_in_fixed_display_order(self) -> None:
        catalog = load_stylist_personas()

        self.assertEqual(catalog.schema_version, "stylist-personas-v1")
        self.assertEqual(catalog.min_select, 1)
        self.assertEqual(catalog.max_select, 3)
        self.assertEqual(
            tuple(persona.id for persona in catalog.personas),
            EXPECTED_PERSONA_ORDER,
        )
        self.assertEqual(
            tuple(persona.display_order for persona in catalog.personas),
            (1, 2, 3),
        )
        self.assertEqual(catalog.supported_persona_ids, EXPECTED_PERSONA_ORDER)
        self.assertTrue(all(persona.enabled for persona in catalog.personas))

    def test_strategy_and_voice_profiles_are_separate_versioned_settings(self) -> None:
        catalog = load_stylist_personas()
        experimental = catalog.get("experimental")

        self.assertEqual(experimental.version, 1)
        self.assertEqual(experimental.prompt_version, "stylist-experimental-v1")
        self.assertEqual(experimental.strategy_profile.hypothesis_count, 2)
        self.assertEqual(
            experimental.strategy_profile.weight_for("history_distance"),
            0.3,
        )
        self.assertEqual(experimental.voice_profile.max_sentences, 1)
        self.assertIn("호기심", experimental.voice_profile.tone_traits)

    def test_strategy_profile_is_restored_from_run_snapshot(self) -> None:
        source = self._default_document()["personas"][0]["strategy_profile"]

        restored = strategy_profile_from_snapshot(source)

        self.assertEqual(restored.objectives, tuple(source["objectives"]))
        self.assertEqual(restored.weight_for("color_cohesion"), 0.25)

    def test_invalid_run_strategy_snapshot_is_rejected(self) -> None:
        with self.assertRaisesMessage(
            StylistPersonaConfigurationError,
            "score_weights",
        ):
            strategy_profile_from_snapshot({"objectives": ["불완전"]})

    def test_versions_reject_duplicate_snapshot_ids(self) -> None:
        catalog = load_stylist_personas()

        with self.assertRaisesMessage(
            StylistPersonaConfigurationError,
            "중복될 수 없습니다",
        ):
            catalog.versions(["minimal", "minimal"])

    def test_missing_fixed_persona_is_rejected(self) -> None:
        document = self._default_document()
        document["personas"] = document["personas"][:-1]

        with TemporaryDirectory() as directory:
            path = self._write_document(directory, document)
            with self.assertRaisesMessage(
                StylistPersonaConfigurationError,
                "minimal, experimental, practical",
            ):
                load_stylist_personas(path)

    def test_duplicate_metric_is_rejected(self) -> None:
        document = self._default_document()
        weights = document["personas"][0]["strategy_profile"]["score_weights"]
        weights[1]["metric"] = weights[0]["metric"]

        with TemporaryDirectory() as directory:
            path = self._write_document(directory, document)
            with self.assertRaisesMessage(
                StylistPersonaConfigurationError,
                "지표 이름은 중복될 수 없습니다",
            ):
                load_stylist_personas(path)

    def test_weight_sum_must_equal_one(self) -> None:
        document = self._default_document()
        document["personas"][0]["strategy_profile"]["score_weights"][0]["weight"] = 0.5

        with TemporaryDirectory() as directory:
            path = self._write_document(directory, document)
            with self.assertRaisesMessage(
                StylistPersonaConfigurationError,
                "가중치 합은 1",
            ):
                load_stylist_personas(path)

    def test_fixed_display_order_is_enforced(self) -> None:
        document = self._default_document()
        document["personas"][0]["display_order"] = 2

        with TemporaryDirectory() as directory:
            path = self._write_document(directory, document)
            with self.assertRaisesMessage(
                StylistPersonaConfigurationError,
                "display_order: 1이어야 합니다",
            ):
                load_stylist_personas(path)

    def test_unknown_field_is_rejected(self) -> None:
        document = deepcopy(self._default_document())
        document["personas"][0]["strategy_profile"]["style_query"] = "black"

        with TemporaryDirectory() as directory:
            path = self._write_document(directory, document)
            with self.assertRaisesMessage(
                StylistPersonaConfigurationError,
                "허용되지 않은 필드 style_query",
            ):
                load_stylist_personas(path)

    def test_invalid_json_reports_source_location(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text('{"personas":', encoding="utf-8")

            with self.assertRaisesMessage(
                StylistPersonaConfigurationError,
                "JSON 문법 오류",
            ) as caught:
                load_stylist_personas(path)

        self.assertIn("line=1", str(caught.exception))
