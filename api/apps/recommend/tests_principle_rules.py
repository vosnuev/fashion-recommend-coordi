"""원칙 조건을 코디 조합에 대조하는 규칙.

여기서 조용히 틀리면 **엉뚱한 슬롯을 바꾼 추천**이 나간다. 에러가 아니라 잘못된
추천으로 드러나기 때문에 눈에 안 띈다. 그래서 세 가지를 고정한다.

- 모름은 어긋남이 아니다 (상품 태그가 대부분 비어 있다)
- 관여 문턱 아래의 원칙은 무시한다 (우연히 하나 맞은 원칙이 슬롯을 바꾸면 안 된다)
- 짧은 키워드가 다른 단어 안에 묻혀 걸리지 않는다 ("꽈배기"의 "배기")
"""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.recommend.services.principle_rules import (
    ENGAGE_MIN,
    Condition,
    PrincipleRule,
    _contains,
    evaluate,
    evaluate_rule,
    extract_attributes,
    violation_count,
)


def _rule(*conditions: Condition) -> PrincipleRule:
    return PrincipleRule(
        principle_key="댄디:A1:p01",
        cluster_id="댄디",
        statement="문장",
        conditions=conditions,
    )


def _single(slot: str, attribute: str, value: str) -> Condition:
    return Condition(kind="single", slot=slot, attribute=attribute, value=value)


def _relation(relation: str, a: str, b: str) -> Condition:
    return Condition(kind="relation", relation=relation, slot_a=a, slot_b=b)


class KeywordBoundaryTests(SimpleTestCase):
    def test_short_keyword_inside_another_word_is_ignored(self) -> None:
        """실제로 '꽈배기 헤어밴드'가 핏=배기로 읽혔다."""
        self.assertFalse(_contains("꽈배기 헤어밴드", "배기"))

    def test_short_keyword_standing_alone_is_found(self) -> None:
        self.assertTrue(_contains("배기 데님 팬츠", "배기"))

    def test_suffix_compound_is_still_found(self) -> None:
        """'배기핏'처럼 뒤에 붙는 건 한국어 합성의 정상 형태다."""
        self.assertTrue(_contains("배기핏 팬츠", "배기"))

    def test_long_keyword_needs_no_boundary(self) -> None:
        self.assertTrue(_contains("여름스트라이프티", "스트라이프"))


class ExtractAttributeTests(SimpleTestCase):
    def test_reads_from_tags_first(self) -> None:
        attributes = extract_attributes(
            {"color": ["블랙"], "pattern": ["스트라이프"], "title": ""}
        )
        self.assertEqual(attributes["명도"], "어두움")
        self.assertEqual(attributes["패턴"], "스트라이프")

    def test_falls_back_to_the_title(self) -> None:
        """상품 color 태그는 19퍼센트만 채워져 있다. 이름에는 거의 항상 있다."""
        attributes = extract_attributes({"title": "여성 아이보리 크롭 니트"})
        self.assertEqual(attributes["명도"], "밝음")
        self.assertEqual(attributes["기장"], "크롭")
        self.assertEqual(attributes["소재"], "니트")

    def test_mixed_brightness_is_left_unknown(self) -> None:
        """밝은 색과 어두운 색이 섞이면 명도를 단정할 수 없다."""
        attributes = extract_attributes({"color": ["화이트", "블랙"]})
        self.assertNotIn("명도", attributes)

    def test_achromatic_is_detected(self) -> None:
        attributes = extract_attributes({"color": ["블랙", "화이트"]})
        self.assertEqual(attributes["색"], "무채색")

    def test_unknown_payload_yields_nothing(self) -> None:
        self.assertEqual(extract_attributes({"title": "머리끈 세트"}), {})


class EvaluateRuleTests(SimpleTestCase):
    def test_all_conditions_matched_has_no_violation(self) -> None:
        rule = _rule(
            _single("top", "명도", "어두움"), _single("bottom", "명도", "밝음")
        )
        outcome = evaluate_rule(
            rule, {"top": {"명도": "어두움"}, "bottom": {"명도": "밝음"}}
        )
        self.assertEqual(outcome.matched, 2)
        self.assertEqual(outcome.violations, ())
        self.assertTrue(outcome.engaged)

    def test_violation_points_at_the_offending_slot(self) -> None:
        rule = _rule(
            _single("top", "명도", "어두움"),
            _single("bottom", "명도", "밝음"),
            _single("shoes", "명도", "어두움"),
        )
        outcome = evaluate_rule(
            rule,
            {
                "top": {"명도": "어두움"},
                "bottom": {"명도": "밝음"},
                "shoes": {"명도": "밝음"},
            },
        )
        self.assertEqual(outcome.matched, 2)
        self.assertEqual(outcome.violation_slots, ("shoes",))

    def test_unknown_attribute_is_neither_match_nor_violation(self) -> None:
        """태그가 비어 있다는 이유로 벌점을 주면 안 된다."""
        rule = _rule(
            _single("top", "명도", "어두움"), _single("shoes", "패턴", "무지")
        )
        outcome = evaluate_rule(rule, {"top": {"명도": "어두움"}, "shoes": {}})
        self.assertEqual(outcome.matched, 1)
        self.assertEqual(outcome.violations, ())

    def test_relation_contrast(self) -> None:
        rule = _rule(_relation("명도대비", "top", "bottom"))
        hit = evaluate_rule(
            rule, {"top": {"명도": "어두움"}, "bottom": {"명도": "밝음"}}
        )
        miss = evaluate_rule(
            rule, {"top": {"명도": "밝음"}, "bottom": {"명도": "밝음"}}
        )
        self.assertEqual(hit.matched, 1)
        self.assertEqual(len(miss.violations), 1)

    def test_relation_needs_both_sides_known(self) -> None:
        rule = _rule(_relation("명도대비", "top", "bottom"))
        outcome = evaluate_rule(rule, {"top": {"명도": "어두움"}, "bottom": {}})
        self.assertEqual(outcome.matched, 0)
        self.assertEqual(outcome.violations, ())

    def test_unknown_relation_is_ignored(self) -> None:
        rule = _rule(_relation("존재하지않는관계", "top", "bottom"))
        outcome = evaluate_rule(
            rule, {"top": {"명도": "어두움"}, "bottom": {"명도": "밝음"}}
        )
        self.assertEqual(outcome.matched, 0)
        self.assertEqual(outcome.violations, ())


class EngagementTests(SimpleTestCase):
    def test_barely_matching_rule_is_not_engaged(self) -> None:
        """3개 중 1개만 우연히 맞은 원칙이 슬롯을 바꾸면 안 된다."""
        rule = _rule(
            _single("top", "명도", "어두움"),
            _single("bottom", "명도", "밝음"),
            _single("shoes", "명도", "어두움"),
        )
        slots = {
            "top": {"명도": "어두움"},
            "bottom": {"명도": "어두움"},
            "shoes": {"명도": "밝음"},
        }
        self.assertEqual(evaluate_rule(rule, slots).matched, 1)
        self.assertEqual(evaluate([rule], slots), ())
        self.assertEqual(violation_count([rule], slots), 0)

    def test_engaged_rule_contributes_its_violations(self) -> None:
        rule = _rule(
            _single("top", "명도", "어두움"),
            _single("bottom", "명도", "밝음"),
            _single("shoes", "명도", "어두움"),
        )
        slots = {
            "top": {"명도": "어두움"},
            "bottom": {"명도": "밝음"},
            "shoes": {"명도": "밝음"},
        }
        self.assertEqual(violation_count([rule], slots), 1)

    def test_threshold_is_the_documented_value(self) -> None:
        self.assertEqual(ENGAGE_MIN, 2)


class ConditionLoaderTests(SimpleTestCase):
    """조건 파일 로딩. 실패해도 추천이 죽으면 안 된다."""

    def test_loads_the_shipped_conditions(self) -> None:
        from apps.recommend.services.principle_rules import load_principle_rules

        rules = load_principle_rules()
        self.assertGreater(len(rules), 0)
        self.assertTrue(all(rule.conditions for rule in rules))

    def test_styles_narrow_the_rule_set(self) -> None:
        from apps.recommend.services.principle_rules import (
            load_principle_rules,
            rules_for_styles,
        )

        everything = load_principle_rules()
        dandy = rules_for_styles(["댄디"])
        self.assertTrue(all(rule.cluster_id == "댄디" for rule in dandy))
        self.assertLess(len(dandy), len(everything))

    def test_no_style_returns_everything(self) -> None:
        from apps.recommend.services.principle_rules import (
            load_principle_rules,
            rules_for_styles,
        )

        self.assertEqual(len(rules_for_styles([])), len(load_principle_rules()))

    def test_missing_file_returns_empty_instead_of_raising(self) -> None:
        from unittest.mock import patch
        from pathlib import Path
        from apps.recommend.services import principle_rules as module

        module.load_principle_rules.cache_clear()
        try:
            with patch.object(module, "_conditions_path", return_value=Path("없는파일.json")):
                self.assertEqual(module.load_principle_rules(), ())
        finally:
            module.load_principle_rules.cache_clear()


class SlotMappingTests(SimpleTestCase):
    def test_category_maps_to_slot(self) -> None:
        from apps.recommend.services.principle_rules import slot_of

        self.assertEqual(slot_of({"category_large": "상의"}), "top")
        self.assertEqual(slot_of({"category_large": "하의"}), "bottom")
        self.assertEqual(slot_of({"category_large": "신발"}), "shoes")

    def test_belt_is_split_out_of_accessory(self) -> None:
        """액세서리에는 벨트와 그 외가 섞여 있다. 조건은 둘을 구분한다."""
        from apps.recommend.services.principle_rules import slot_of

        self.assertEqual(
            slot_of({"category_large": "액세서리", "title": "블랙 레더 벨트"}), "belt"
        )
        self.assertEqual(
            slot_of({"category_large": "액세서리", "title": "골드 목걸이"}), "accessory"
        )

    def test_unknown_category_is_blank(self) -> None:
        from apps.recommend.services.principle_rules import slot_of

        self.assertEqual(slot_of({"category_large": "원피스/세트"}), "")


class DriftTests(SimpleTestCase):
    """치환이 골든 원본의 성질을 바꿨는지.

    상품 태그가 비어 있어 "후보가 원칙을 만족하는가"는 대부분 판정 불가다. 골든
    원본은 이미 그 원칙을 만족하므로, 원본과 달라진 것 자체를 신호로 쓴다.
    """

    def test_changed_attribute_counts_as_drift(self) -> None:
        """원칙이 지목한 축이면 두 배로 센다."""
        from apps.recommend.services.principle_rules import drift_count

        self.assertEqual(
            drift_count({"패턴": "무지"}, {"패턴": "스트라이프"}, frozenset({"패턴"})), 2
        )

    def test_same_attribute_is_not_drift(self) -> None:
        from apps.recommend.services.principle_rules import drift_count

        self.assertEqual(
            drift_count({"패턴": "무지"}, {"패턴": "무지"}, frozenset({"패턴"})), 0
        )

    def test_unknown_side_is_not_drift(self) -> None:
        """한쪽이라도 못 읽으면 세지 않는다. 태깅 없는 상품이 벌점을 받으면 안 된다."""
        from apps.recommend.services.principle_rules import drift_count

        self.assertEqual(drift_count({"패턴": "무지"}, {}, frozenset({"패턴"})), 0)
        self.assertEqual(drift_count({}, {"패턴": "무지"}, frozenset({"패턴"})), 0)

    def test_unwatched_attribute_still_counts_once(self) -> None:
        """원칙 밖의 축도 센다. 다만 가중치가 없다 — 니트가 코튼이 되는 것도 변화다."""
        from apps.recommend.services.principle_rules import drift_count

        self.assertEqual(
            drift_count({"소재": "니트"}, {"소재": "코튼"}, frozenset({"패턴"})), 1
        )

    def test_attributes_in_play_reads_single_conditions(self) -> None:
        """원칙이 지목한 축. 여기 든 속성만 두 배로 센다."""
        from apps.recommend.services.principle_rules import attributes_in_play

        rule = _rule(_single("top", "명도", "어두움"), _single("bottom", "핏", "와이드"))
        self.assertEqual(attributes_in_play([rule], "top"), frozenset({"명도"}))
        self.assertEqual(attributes_in_play([rule], "bottom"), frozenset({"핏"}))
        self.assertEqual(attributes_in_play([rule], "shoes"), frozenset())

    def test_attributes_in_play_reads_relations(self) -> None:
        from apps.recommend.services.principle_rules import attributes_in_play

        rule = _rule(_relation("명도대비", "top", "bottom"))
        self.assertIn("명도", attributes_in_play([rule], "top"))
        self.assertIn("명도", attributes_in_play([rule], "bottom"))
        self.assertNotIn("명도", attributes_in_play([rule], "shoes"))


class GeneralDriftTests(SimpleTestCase):
    """드리프트는 사례별 목록이 아니라 일반 규칙이다.

    "무지가 그래픽이 됐다", "여름 옷이 가을 옷이 됐다"는 각각 특수 처리할 문제가
    아니라 **원본과 다른 옷이 들어왔다**는 한 문제의 단면이다. 읽을 수 있는 모든
    속성을 보고, 원칙이 지목한 축만 무겁게 센다.
    """

    def test_any_readable_attribute_counts_even_without_a_principle(self) -> None:
        from apps.recommend.services.principle_rules import drift_count

        # 원칙이 패턴을 언급하지 않아도 무지 -> 그래픽은 잡힌다.
        self.assertEqual(
            drift_count({"패턴": "무지"}, {"패턴": "그래픽"}, frozenset()), 1
        )

    def test_principle_attribute_weighs_double(self) -> None:
        from apps.recommend.services.principle_rules import drift_count

        self.assertEqual(
            drift_count({"명도": "밝음"}, {"명도": "어두움"}, frozenset({"명도"})), 2
        )

    def test_overlapping_multivalue_is_not_drift(self) -> None:
        """봄;가을 과 가을;겨울 은 가을에 함께 입을 수 있다."""
        from apps.recommend.services.principle_rules import drift_count

        self.assertEqual(
            drift_count({"계절": "봄;가을"}, {"계절": "가을;겨울"}, frozenset()), 0
        )

    def test_disjoint_multivalue_is_drift(self) -> None:
        from apps.recommend.services.principle_rules import drift_count

        self.assertEqual(
            drift_count({"계절": "봄;가을"}, {"계절": "여름"}, frozenset()), 1
        )

    def test_season_is_extracted_as_an_ordinary_attribute(self) -> None:
        from apps.recommend.services.principle_rules import extract_attributes

        attributes = extract_attributes({"season": ["봄", "가을"], "title": ""})
        self.assertEqual(attributes["계절"], "가을;봄")
