"""코디 태깅·동기화 스크립트 테스트.

이 값들은 사람이 읽는 설명이 아니라 **리트리버가 필터로 쓰는 키**다. 표기가
갈리면 예외 없이 조용히 검색에서 빠진다. 그래서 어휘가 태그 체계와 붙어 있는지,
목록 밖 값이 payload로 새지 않는지를 붙잡아 둔다.
"""

from __future__ import annotations

import unittest

from ml.golden_set import look_tags
from ml.golden_set.tag_manifests import find_manifests, needs_tagging


class SchemaTests(unittest.TestCase):
    def test_vocabulary_is_enum_not_examples(self) -> None:
        """예시를 나열하면 모델이 인접한 새 값을 만들어 낸다."""
        props = look_tags.build_schema()["properties"]
        self.assertIn("enum", props["presentation_group"])
        for axis in ("style", "season", "occasion"):
            self.assertIn("enum", props[axis]["items"], axis)

    def test_style_and_season_come_from_taxonomy(self) -> None:
        """복제하면 태그 체계가 바뀔 때 조용히 갈라진다."""
        from pipeline.taxonomy import SEASONS, STYLES

        props = look_tags.build_schema()["properties"]
        self.assertEqual(props["style"]["items"]["enum"], list(STYLES))
        self.assertEqual(props["season"]["items"]["enum"], list(SEASONS))

    def test_presentation_group_has_an_unknown_escape(self) -> None:
        """확신이 없으면 빠져나갈 값이 있어야 한다 — 미분류가 오분류보다 낫다."""
        enum = look_tags.build_schema()["properties"]["presentation_group"]["enum"]
        self.assertIn(look_tags.PRESENTATION_UNKNOWN, enum)

    def test_enum_has_no_empty_string(self) -> None:
        """Gemini는 enum에 빈 문자열이 있으면 400으로 거부한다.

            response_schema.properties[presentation_group].enum[3]: cannot be empty
        """
        schema = look_tags.build_schema()
        self.assertNotIn("", schema["properties"]["presentation_group"]["enum"])
        for axis in ("style", "season", "occasion"):
            self.assertNotIn("", schema["properties"][axis]["items"]["enum"], axis)

    def test_unknown_is_stored_as_empty(self) -> None:
        """모델은 'unknown'을 주고, 저장 형태는 빈 문자열이다.

        리트리버가 빈 값을 '라벨 없음'으로 읽어 성별 필터에서 제외한다.
        여기서 unisex로 흘리면 여성 코디가 남성에게 그대로 나간다.
        """
        tags = look_tags.normalize(
            {
                "presentation_group": look_tags.PRESENTATION_UNKNOWN,
                "style": [], "season": [], "occasion": [], "confidence": 1,
            }
        )
        self.assertEqual(tags["presentation_group"], "")

    def test_occasion_keeps_existing_examples(self) -> None:
        """metadata.example.csv가 쓰던 값과 이어져야 한다."""
        self.assertIn("데일리", look_tags.OCCASIONS)
        self.assertIn("출근", look_tags.OCCASIONS)


class NormalizeTests(unittest.TestCase):
    def _tags(self, **kwargs):
        base = {
            "presentation_group": "",
            "style": [],
            "season": [],
            "occasion": [],
            "confidence": 1,
        }
        return look_tags.normalize({**base, **kwargs})

    def test_values_outside_vocabulary_are_dropped(self) -> None:
        """유령 태그가 payload에 남으면 검색에서 영원히 안 걸린다."""
        self.assertEqual(self._tags(style=["미니멀", "힙합", "클래식"])["style"], ["미니멀"])

    def test_values_are_capped(self) -> None:
        tags = self._tags(style=["미니멀", "캐주얼", "시크", "빈티지", "포멀"])
        self.assertEqual(len(tags["style"]), look_tags.MAX_VALUES_PER_AXIS)

    def test_duplicates_removed_in_order(self) -> None:
        self.assertEqual(self._tags(style=["시크", "미니멀", "시크"])["style"], ["시크", "미니멀"])

    def test_presentation_group_normalized(self) -> None:
        self.assertEqual(self._tags(presentation_group="MEN ")["presentation_group"], "men")
        for value in ("남성", "male", "몰라", ""):
            self.assertEqual(self._tags(presentation_group=value)["presentation_group"], "")

    def test_confidence_clamped(self) -> None:
        for raw, expected in ((5, 3), (-1, 0), ("2", 2), (None, 0), ("x", 0)):
            self.assertEqual(self._tags(confidence=raw)["confidence"], expected, repr(raw))


class RetagTests(unittest.TestCase):
    def test_untagged_needs_tagging(self) -> None:
        self.assertTrue(needs_tagging({}, force=False))
        self.assertTrue(needs_tagging({"look_tags": {}}, force=False))

    def test_current_version_is_skipped(self) -> None:
        manifest = {"look_tags": {"schema_version": look_tags.LOOK_TAG_SCHEMA_VERSION}}
        self.assertFalse(needs_tagging(manifest, force=False))

    def test_old_version_is_retagged(self) -> None:
        """어휘·프롬프트를 바꾸면 버전을 올려 다시 태깅되게 한다."""
        self.assertTrue(needs_tagging({"look_tags": {"schema_version": "v0"}}, force=False))

    def test_force_overrides_everything(self) -> None:
        manifest = {"look_tags": {"schema_version": look_tags.LOOK_TAG_SCHEMA_VERSION}}
        self.assertTrue(needs_tagging(manifest, force=True))


class FindManifestsTests(unittest.TestCase):
    def test_picks_only_manifest_files(self) -> None:
        from ml.golden_set import s3io as s3io_module

        original = s3io_module.list_keys
        s3io_module.list_keys = lambda bucket, prefix: [
            "goldenset/derived/v1/095/manifest.json",
            "goldenset/derived/v1/095/item_000.png",
            "goldenset/derived/v1/095/item_vectors.npz",
            "goldenset/derived/v1/096/manifest.json",
            "goldenset/derived/v1/run_summary.json",
        ]
        try:
            found = find_manifests("bucket", "goldenset/derived/v1")
        finally:
            s3io_module.list_keys = original

        self.assertEqual(len(found), 2)
        self.assertTrue(all(key.endswith("/manifest.json") for key in found))


class OutfitTextTests(unittest.TestCase):
    def test_text_includes_tags_and_items(self) -> None:
        from ml.golden_set.sync_qdrant import outfit_text

        text = outfit_text(
            {"items": [{"category_large": "상의", "color": "화이트", "fit": "레귤러핏"}]},
            {"style": ["미니멀"], "season": ["여름"], "occasion": ["출근"],
             "presentation_group": "women"},
        )
        for expected in ("미니멀", "여름", "출근", "상의", "화이트"):
            self.assertIn(expected, text)

    def test_empty_tags_still_produce_text(self) -> None:
        """태그를 아직 안 붙인 코디도 아이템 구성만으로 텍스트가 나와야 한다."""
        from ml.golden_set.sync_qdrant import outfit_text

        text = outfit_text({"items": [{"category_large": "하의"}]}, {})
        self.assertIn("하의", text)
