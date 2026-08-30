"""리트리버 베이스 테스트.

"오늘의 룩", "옷장 기반", "추구미 기반" 세 기능이 전부 이 계층 위에 올라가므로,
여기서 조용히 틀리면 세 곳에서 동시에 틀린다. 특히 두 가지를 붙잡아 둔다.

- 어휘 번역이 값을 조용히 버리지 않는가 (넥라인처럼 대응 태그가 없는 축)
- 취향이 체형 규칙보다 항상 우선하는가 (가이드 7장 Q2)
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.recommend.services import vocabulary
from apps.recommend.services.body_profile import (
    HOURGLASS,
    INVERTED_TRIANGLE,
    NORMAL,
    OBESE,
    OVERWEIGHT,
    RECTANGLE,
    ROUND,
    TRIANGLE,
    UNDERWEIGHT,
    UNKNOWN,
    BodyProfile,
    _empirical_percentile,
    build_profile,
)
from apps.recommend.services.gender import (
    allowed_presentation_groups,
    conflicting_item,
    load_gender_rules,
    normalize_gender,
)
from django.test import TestCase, override_settings

from apps.recommend.services import retriever
from apps.recommend.services.retriever import (
    Reason,
    RetrievalRequest,
    retrieve_outfits,
    _score_context,
    _score_items,
    _score_weather,
    _season_from_weather,
    celsius_of,
)
from apps.recommend.services.style_rules import (
    RULES_DIR,
    Rule,
    load_body_rules,
    load_weather_rules,
    validate_rules,
    validate_weather_rules,
)

translate = vocabulary.translate


BODY_THRESHOLDS = {
    "active_threshold_key": "all",
    "horizontal_classification_references": {
        sex: {"all": {
            "shoulder": [35, 40, 45, 50],
            "chest": [80, 90, 100, 110],
            "hip": [80, 90, 100, 110],
        }} for sex in ("M", "F")
    },
    "thresholds": {
        sex: {"all": {
            "upper_lower": {"p33": -0.2, "p67": 0.2},
            "waist_definition": {"p33": 0.75, "p90": 0.95},
            "neck_length": {"p33": 8, "p67": 10},
            "thigh_calf_ratio": {"p33": 0.8, "p67": 1.1},
            "torso_leg_ratio": {"p33": 0.63, "p67": 0.69},
        }} for sex in ("M", "F")
    },
}


@patch("apps.recommend.services.body_profile.load_body_shape_thresholds", return_value=BODY_THRESHOLDS)
class BodyProfileTests(unittest.TestCase):
    """실루엣은 **둘레끼리** 비교해야 한다.

    예전에는 어깨너비(44cm)와 엉덩이둘레(98cm)를 직접 뺐다. 둘은 애초에 비교할
    수 있는 값이 아니라 spread가 언제나 -0.4~-0.6이 나왔고, 실제 사용자는
    **전부 삼각형**으로 판정됐다. 체형을 바꿔도 추천이 그대로이던 원인 중
    하나다. 아래 값은 전부 실제 사람의 cm 둘레다.
    """

    def test_real_bodies_do_not_all_collapse_to_one_silhouette(self, _thresholds):
        """단위를 섞으면 여기서 걸린다 — 판정이 한 값으로 뭉친다."""
        bodies = [
            {"gender": "male", "chest": 102, "waist": 92, "hip": 98, "shoulder": 44},
            {"gender": "male", "chest": 100, "waist": 78, "hip": 88, "shoulder": 50},
            {"gender": "male", "chest": 110, "waist": 108, "hip": 100, "shoulder": 43},
            {"gender": "female", "chest": 84, "waist": 62, "hip": 100, "shoulder": 38},
        ]
        got = {build_profile(b).silhouette for b in bodies}
        self.assertGreaterEqual(len(got), 3, f"판정이 뭉쳤다: {got}")

    def test_chest_wider_than_hip_is_inverted(self, _thresholds):
        p = build_profile({"gender": "male", "height": 180, "weight": 70, "shoulder": 50, "chest": 110, "waist": 78, "hip": 80})
        self.assertEqual(p.silhouette, INVERTED_TRIANGLE)

    def test_hip_wider_than_chest_is_triangle(self, _thresholds):
        self.assertEqual(
            build_profile({"gender": "female", "shoulder": 35, "chest": 80, "waist": 62, "hip": 110}).silhouette, TRIANGLE
        )

    def test_balanced_with_small_waist_is_hourglass(self, _thresholds):
        self.assertEqual(
            build_profile({"gender": "female", "shoulder": 45, "chest": 100, "waist": 70, "hip": 100}).silhouette, HOURGLASS
        )

    def test_dominant_waist_is_round(self, _thresholds):
        self.assertEqual(
            build_profile({"gender": "female", "shoulder": 45, "chest": 100, "waist": 100, "hip": 100}).silhouette, ROUND
        )

    def test_round_wins_over_chest_hip_spread(self, _thresholds):
        """허리 우세는 상하 균형과 무관하게 성립한다.

        예전에는 가슴-엉덩이 균형을 먼저 봐서, 허리 108에 가슴 110/엉덩이 100인
        사람이 명백한 라운드형인데 역삼각형으로 빠졌다.
        """
        p = build_profile({"gender": "male", "height": 165, "weight": 95, "shoulder": 50, "chest": 110, "waist": 108, "hip": 100})
        self.assertEqual(p.silhouette, ROUND)

    def test_balanced_middle_waist_is_rectangle(self, _thresholds):
        self.assertEqual(
            build_profile({"gender": "male", "shoulder": 45, "chest": 100, "waist": 85, "hip": 100}).silhouette, RECTANGLE
        )

    def test_balanced_without_waist_stays_unknown(self, _thresholds):
        p = build_profile({"gender": "male", "shoulder": 45, "chest": 92, "hip": 92})
        self.assertEqual(p.silhouette, UNKNOWN)
        self.assertIn("waist", p.missing)

    def test_without_chest_there_is_no_silhouette(self, _thresholds):
        """모르는 값을 메우지 않는다. 틀린 추천보다 미판정이 낫다."""
        p = build_profile({"height": 170, "weight": 62, "waist": 70, "hip": 90, "shoulder": 44})
        self.assertEqual(p.silhouette, UNKNOWN)
        self.assertIn("chest", p.missing)

    def test_gender_is_mandatory_for_silhouette(self, _thresholds):
        """실루엣에서는 뺐지만 어깨 발달은 실제로 다른 축이다."""
        profile = build_profile({"chest": 100, "hip": 96, "waist": 80, "shoulder": 50})
        self.assertEqual(profile.silhouette, UNKNOWN)
        self.assertIn("gender", profile.missing)

    def test_bmi_bands(self, _thresholds):
        for w, band in ((50, UNDERWEIGHT), (62, NORMAL), (70, OVERWEIGHT), (85, OBESE)):
            p = build_profile({"height": 170, "weight": w})
            self.assertEqual(p.bmi_band, band, f"{w}kg bmi={p.bmi}")

    def test_no_measurement_is_empty(self, _thresholds):
        self.assertTrue(build_profile(None).is_empty)
        self.assertTrue(build_profile({}).is_empty)

    def test_legacy_ratio_axis_names_are_normalized(self, _thresholds):
        profile = BodyProfile(
            ratios={"leg_volume": "balanced", "vertical_balance": "long_torso"}
        )
        self.assertEqual(
            profile.ratios,
            {"thigh_calf_ratio": "balanced", "torso_leg_ratio": "long_torso"},
        )

    def test_thigh_calf_ratio(self, _thresholds):
        self.assertEqual(build_profile({"gender": "male", "thigh_calf_ratio": 1.2}).ratios["thigh_calf_ratio"], "thigh_dominant")
        self.assertEqual(build_profile({"gender": "male", "thigh_calf_ratio": 0.82}).ratios["thigh_calf_ratio"], "balanced")
        self.assertEqual(build_profile({"gender": "male", "thigh_calf_ratio": 0.70}).ratios["thigh_calf_ratio"], "calf_dominant")

    def test_torso_leg_ratio(self, _thresholds):
        self.assertEqual(build_profile({"gender": "female", "torso_leg_ratio": 0.72}).ratios["torso_leg_ratio"], "long_torso")
        self.assertEqual(build_profile({"gender": "female", "torso_leg_ratio": 0.66}).ratios["torso_leg_ratio"], "balanced")
        self.assertEqual(build_profile({"gender": "female", "torso_leg_ratio": 0.58}).ratios["torso_leg_ratio"], "short_torso")

    def test_neck_length_uses_sex_thresholds(self, _thresholds):
        self.assertEqual(build_profile({"gender": "female", "neck_length": 7}).ratios["neck_length"], "short")
        self.assertEqual(build_profile({"gender": "female", "neck_length": 9}).ratios["neck_length"], "average")
        self.assertEqual(build_profile({"gender": "female", "neck_length": 11}).ratios["neck_length"], "long")

    def test_ratio_threshold_boundaries_are_inclusive(self, _thresholds):
        self.assertEqual(build_profile({"gender": "female", "thigh_calf_ratio": 0.8}).ratios["thigh_calf_ratio"], "calf_dominant")
        self.assertEqual(build_profile({"gender": "female", "thigh_calf_ratio": 1.1}).ratios["thigh_calf_ratio"], "thigh_dominant")

    def test_empirical_percentile_matches_average_rank_for_ties(self, _thresholds):
        self.assertEqual(_empirical_percentile(40, [35, 40, 40, 50]), 0.625)

    def test_garbage_values_are_ignored(self, _thresholds):
        self.assertTrue(build_profile({"height": "abc", "weight": -5, "chest": None}).is_empty)

    def test_describe_is_human_readable(self, _thresholds):
        p = build_profile({"gender": "male", "height": 170, "weight": 85, "shoulder": 45, "chest": 100, "waist": 100, "hip": 100})
        self.assertIn("둥근체형", p.describe())
        self.assertIn("비만", p.describe())


class VocabularyTests(unittest.TestCase):
    def test_maps_to_tag_labels(self):
        t = translate({"top_fits":["oversized"],"styles":["minimal"]})
        self.assertEqual(t.labels("fit"), {"오버핏"}); self.assertEqual(t.labels("style"), {"미니멀"})
    def test_necklines_are_all_unmapped(self):
        t = translate({"necklines":["vneck","turtle"]})
        self.assertEqual(t.tags, {})
        self.assertEqual(set(t.unmapped), {("necklines","vneck"),("necklines","turtle")})
    def test_style_without_tag_equivalent_is_reported(self):
        t = translate({"styles":["business_casual","minimal"]})
        self.assertEqual(t.labels("style"), {"미니멀"})
        self.assertIn(("styles","business_casual"), t.unmapped)
    def test_approximate_is_flagged(self):
        t = translate({"top_fits":["loose"]})
        self.assertEqual(t.labels("fit"), {"오버핏"}); self.assertIn(("top_fits","loose"), t.approximate)
    def test_pants_fits_collapse_to_four_labels(self):
        t = translate({"pants_fits":["wide","semi_wide","slacks","skinny"]})
        self.assertEqual(t.labels("fit"), {"와이드핏","레귤러핏","슬림핏"})
    def test_empty_input(self):
        t = translate(None); self.assertEqual(t.tags, {}); self.assertEqual(t.unmapped, ())

class RulesTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_body_rules()
    def test_loads_clean(self):
        self.assertEqual(self.rules.schema_version, "body-fit-rules-v2")
    def test_preference_outweighs_rules(self):
        w = self.rules.weights
        self.assertGreater(abs(w.preference_avoid), abs(w.rule_avoid))
        self.assertGreater(w.preference_match, w.rule_prefer)
    def test_typo_in_rules_is_caught(self):
        bad = {"silhouette":{"triangle":{"prefer":[{"fit":"레귤귤핏","reason":"x"}],"avoid":[]}}}
        problems = validate_rules(bad)
        self.assertEqual(len(problems), 1); self.assertIn("레귤귤핏", problems[0])
    def test_unknown_field_is_caught(self):
        bad = {"bmi_band":{"obese":{"prefer":[{"neckline":"브이넥","reason":"x"}],"avoid":[]}}}
        self.assertIn("알 수 없는 태그 필드", validate_rules(bad)[0])
    def test_shipped_rules_have_no_problems(self):
        import json
        doc = json.loads(open(str(RULES_DIR / "body_fit_rules.json"), encoding="utf-8").read())
        self.assertEqual(validate_rules(doc), [])

    def test_golden_and_runtime_threshold_artifacts_are_identical(self):
        root = RULES_DIR.parents[3]
        golden = root / "golden-set/body/rules/body_shape_thresholds.json"
        runtime = RULES_DIR / "body_shape_thresholds.json"
        self.assertEqual(golden.read_bytes(), runtime.read_bytes())

    def test_runtime_rule_taxonomy_matches_golden_contract(self):
        root = RULES_DIR.parents[3]
        runtime = json.loads((RULES_DIR / "body_fit_rules.json").read_text(encoding="utf-8"))
        golden = json.loads((root / "golden-set/body/rules/body_fit_rules.json").read_text(encoding="utf-8"))
        self.assertEqual(runtime["golden_source_version"], golden["version"])
        self.assertEqual(set(runtime["silhouette"]), set(golden["axes"]["width"]["values"]))
    def test_unknown_axis_contributes_nothing(self):
        empty = self.rules.for_profile(BodyProfile())
        self.assertEqual(empty.prefer, ()); self.assertEqual(empty.avoid, ())
    def test_hard_rule_flag_survives_parsing(self):
        hard = [r for r in self.rules.for_profile(BodyProfile(silhouette=ROUND)).avoid if r.hard]
        self.assertTrue(hard)
        self.assertEqual(hard[0].match, {"category_large":"상의","length":"크롭"})
    def test_axes_combine(self):
        p = BodyProfile(
            silhouette=TRIANGLE,
            bmi_band=OBESE,
            ratios={"thigh_calf_ratio": "thigh_dominant", "torso_leg_ratio": "long_torso"},
        )
        axis = self.rules.for_profile(p)
        self.assertGreater(len(axis.avoid), 3)
    def test_rule_matches_list_payload(self):
        r = Rule(match={"style":"미니멀"}, reason="")
        self.assertTrue(r.matches({"style":["미니멀","시크"]}))
        self.assertFalse(r.matches({"style":["스트릿"]}))

class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_body_rules()
        self.w = self.rules.weights
    def test_avoided_preference_dominates_rule_bonus(self):
        axis = self.rules.for_profile(BodyProfile(silhouette=TRIANGLE))
        total, reasons = _score_items(
            [{"category_large":"상의","fit":"오버핏","color":"핑크"}],
            rules_prefer=axis.prefer, rules_avoid=axis.avoid,
            preferred_tags={}, avoided_tags={"color":{"핑크"}}, weights=self.w)
        self.assertLess(total, 0)
        self.assertTrue(any(r.source == "preference" for r in reasons))
    def test_rule_violation_lowers_score(self):
        axis = self.rules.for_profile(BodyProfile(silhouette=ROUND))
        total, _ = _score_items([{"category_large":"상의","length":"크롭"}],
            rules_prefer=axis.prefer, rules_avoid=axis.avoid,
            preferred_tags={}, avoided_tags={}, weights=self.w)
        self.assertEqual(total, self.w.rule_avoid)
    def test_reason_is_not_repeated(self):
        axis = self.rules.for_profile(BodyProfile(silhouette=TRIANGLE))
        _, reasons = _score_items([{"category_large":"상의","fit":"오버핏"}]*3,
            rules_prefer=axis.prefer, rules_avoid=axis.avoid,
            preferred_tags={}, avoided_tags={}, weights=self.w)
        self.assertEqual(len(reasons), len({r.text for r in reasons}))
    def test_same_rule_counts_once_per_outfit(self):
        """아이템 수가 순위를 정하면 안 된다.

        예전에는 점수만 아이템마다 누적하고 이유는 한 번만 남겼다. 상의가 셋인
        코디는 같은 규칙으로 +45를 받는데 설명에는 +15 한 줄만 보였다 — 점수와
        설명이 서로 다른 말을 했고, 순위가 '규칙에 얼마나 맞는가'가 아니라
        '아이템이 몇 개인가'로 정해졌다.
        """
        axis = self.rules.for_profile(BodyProfile(silhouette=TRIANGLE))
        one, _ = _score_items([{"category_large":"상의","fit":"오버핏"}],
            rules_prefer=axis.prefer, rules_avoid=(), preferred_tags={}, avoided_tags={}, weights=self.w)
        three, reasons = _score_items([{"category_large":"상의","fit":"오버핏"}]*3,
            rules_prefer=axis.prefer, rules_avoid=(), preferred_tags={}, avoided_tags={}, weights=self.w)
        self.assertEqual(three, one)
        # 점수와 설명이 같은 말을 하는가
        self.assertEqual(three, sum(r.delta for r in reasons))
    def test_empty_profile_scores_nothing(self):
        total, reasons = _score_items([{"category_large":"상의","fit":"슬림핏"}],
            rules_prefer=(), rules_avoid=(), preferred_tags={}, avoided_tags={}, weights=self.w)
        self.assertEqual(total, 0.0); self.assertEqual(reasons, [])

class WeatherTests(unittest.TestCase):
    def test_temperature_to_season(self):
        for t, s in ((28,"여름"),(23,"여름"),(19,"간절기"),(12,"가을"),(3,"겨울"),(-5,"겨울")):
            self.assertEqual(_season_from_weather({"temperature":t}), s, t)
    def test_missing_weather(self):
        self.assertEqual(_season_from_weather(None), "")
        self.assertEqual(_season_from_weather({}), "")
        self.assertEqual(_season_from_weather({"temperature":"n/a"}), "")



#: 지금 실제로 적재된 골든 코디의 모양. 분석 단계(analyses.jsonl)를 돌리지 않아
#: style/season/occasion이 전부 빈 배열이다.
UNTAGGED_OUTFIT = {
    "golden_id": "095",
    "style": [],
    "season": [],
    "occasion": [],
    "items": [{"category_large": "상의", "fit": "레귤러핏", "color": "화이트"}],
}
TAGGED_OUTFIT = dict(UNTAGGED_OUTFIT, season=["여름"], occasion=["출근"])


class ContextScoringTests(unittest.TestCase):
    """계절·상황은 가산이지 탈락 조건이 아니다.

    처음엔 날씨에서 뽑은 계절을 Qdrant의 must 조건으로 걸었다. 그러자 모든 추천이
    EMPTY로 끝났다 — 적재된 코디의 season이 전부 빈 배열이라 한 건도 안 걸린
    것이다. 있지도 않은 값에 must를 걸면 결과는 언제나 0건이다.
    """

    def setUp(self) -> None:
        self.weights = load_body_rules().weights

    def test_empty_season_is_neither_bonus_nor_penalty(self) -> None:
        total, reasons = _score_context(
            UNTAGGED_OUTFIT, season="여름", occasion="", weights=self.weights
        )
        self.assertEqual(total, 0.0)
        self.assertEqual(reasons, [])

    def test_matching_season_adds_bonus(self) -> None:
        total, reasons = _score_context(
            TAGGED_OUTFIT, season="여름", occasion="", weights=self.weights
        )
        self.assertEqual(total, self.weights.context_match)
        self.assertEqual(reasons[0].source, "context")

    def test_mismatching_season_is_not_penalised(self) -> None:
        """'안 맞음'을 감점하면 태그가 있는 코디가 없는 코디보다 불리해진다."""
        total, _ = _score_context(
            TAGGED_OUTFIT, season="겨울", occasion="", weights=self.weights
        )
        self.assertEqual(total, 0.0)

    def test_occasion_also_adds(self) -> None:
        total, _ = _score_context(
            TAGGED_OUTFIT, season="여름", occasion="출근", weights=self.weights
        )
        self.assertEqual(total, self.weights.context_match * 2)

    def test_context_never_outweighs_preference(self) -> None:
        """계절이 맞는다고 사용자가 기피한 항목을 이기면 안 된다 (가이드 Q2)."""
        self.assertLess(self.weights.context_match, abs(self.weights.preference_avoid))
        self.assertLess(self.weights.context_match, self.weights.preference_match)
        self.assertLessEqual(self.weights.context_match, self.weights.rule_prefer)

    def test_hard_season_filter_would_have_dropped_everything(self) -> None:
        """회귀 재현 — EMPTY의 정체."""
        points = [UNTAGGED_OUTFIT, dict(UNTAGGED_OUTFIT, golden_id="096")]
        survived = [p for p in points if "여름" in (p.get("season") or [])]
        self.assertEqual(survived, [], "must 조건이면 전부 탈락한다")
        # 소프트로 바꾼 뒤에는 전부 살아남고 가산만 0이다
        deltas = [
            _score_context(p, season="여름", occasion="", weights=self.weights)[0]
            for p in points
        ]
        self.assertEqual(deltas, [0.0, 0.0])


class BuildFilterTests(unittest.TestCase):
    """검색 필터에는 기피 요건만 들어간다 (가이드 6장)."""

    def test_weather_does_not_become_a_filter(self) -> None:
        from apps.recommend.services.retriever import RetrievalRequest, build_filter

        built = build_filter(RetrievalRequest(weather={"temperature": 28}))
        self.assertIsNone(built, "날씨만으로는 어떤 조건도 걸리지 않아야 한다")

    def test_occasion_does_not_become_a_filter(self) -> None:
        from apps.recommend.services.retriever import RetrievalRequest, build_filter

        self.assertIsNone(build_filter(RetrievalRequest(occasion="출근")))

    def test_avoided_style_becomes_must_not(self) -> None:
        from apps.recommend.services.retriever import RetrievalRequest, build_filter

        built = build_filter(
            RetrievalRequest(pursuit={"preferred": {}, "avoided": {"styles": ["street"]}})
        )
        self.assertIsNotNone(built)
        self.assertTrue(built.must_not)
        self.assertFalse(built.must)


OUTER = {"category_large": "아우터", "material": "코튼"}
SHORT_TEE = {"category_large": "상의", "sleeve": "반팔", "material": "코튼"}
KNIT_TOP = {"category_large": "상의", "sleeve": "긴팔", "material": "니트"}


class WeatherRuleTests(unittest.TestCase):
    """기온을 선택에 반영한다.

    실제 사고: 27도인데 아우터가 든 코디가 1위로 뽑혔고, LLM은 그걸 정당화하려고
    "선선한 날씨"라고 썼다. 모델이 온도를 잘못 읽은 게 아니라 모순을 봉합한
    것이다. 근본 원인은 기온이 선택에 전혀 관여하지 않았다는 것이었다.
    """

    def setUp(self) -> None:
        self.rules = load_weather_rules()
        self.weights = self.rules.weights

    def test_shipped_rules_validate(self) -> None:
        document = json.loads(
            (RULES_DIR / "weather_rules.json").read_text(encoding="utf-8")
        )
        self.assertEqual(validate_weather_rules(document), [])

    def test_band_boundaries(self) -> None:
        for celsius, label in (
            (27, "더움"), (23, "더움"), (22.9, "선선"), (17, "선선"),
            (16.9, "쌀쌀"), (9, "쌀쌀"), (8.9, "추움"), (-10, "추움"),
        ):
            self.assertEqual(self.rules.band_for(celsius).label, label, f"{celsius}도")

    def test_unknown_temperature_disables_the_rules(self) -> None:
        self.assertIsNone(self.rules.band_for(None))
        self.assertEqual(_score_weather([OUTER], None, self.weights), (0.0, []))

    def test_outer_at_27_is_penalised(self) -> None:
        band = self.rules.band_for(27.4)
        total, reasons = _score_weather([OUTER, SHORT_TEE], band, self.weights)
        self.assertLess(total, 0)
        self.assertTrue(any(r.source == "weather" for r in reasons))
        self.assertTrue(any("겉옷" in r.text for r in reasons))

    def test_outer_when_cool_is_rewarded(self) -> None:
        total, _ = _score_weather([OUTER], self.rules.band_for(12), self.weights)
        self.assertEqual(total, self.weights.encourage)

    def test_knit_flips_with_temperature(self) -> None:
        self.assertLess(
            _score_weather([KNIT_TOP], self.rules.band_for(28), self.weights)[0], 0
        )
        self.assertGreater(
            _score_weather([KNIT_TOP], self.rules.band_for(12), self.weights)[0], 0
        )

    def test_reason_not_repeated_across_items(self) -> None:
        band = self.rules.band_for(27.4)
        _, reasons = _score_weather([OUTER] * 3, band, self.weights)
        self.assertEqual(len(reasons), len({r.text for r in reasons}))

    def test_penalty_is_weaker_than_user_avoidance(self) -> None:
        """사용자가 직접 고른 기피가 날씨 추정보다 우선이어야 한다 (가이드 Q2)."""
        self.assertLess(
            abs(self.weights.discourage), abs(load_body_rules().weights.preference_avoid)
        )

    def test_gap_between_bands_is_reported(self) -> None:
        """구간 사이에 구멍이 있으면 그 기온에서 규칙이 통째로 빠진다."""
        bad = {"bands": [{"label": "a", "min": 20, "max": 25}, {"label": "b", "min": 30}]}
        self.assertTrue(any("틈" in p for p in validate_weather_rules(bad)))

    def test_unbounded_band_is_reported(self) -> None:
        self.assertTrue(
            any("모든 기온" in p for p in validate_weather_rules({"bands": [{"label": "x"}]}))
        )


class CelsiusTests(unittest.TestCase):
    def test_parses_number_and_string(self) -> None:
        self.assertEqual(celsius_of({"temperature": 27.4}), 27.4)
        self.assertEqual(celsius_of({"temperature": "27.4"}), 27.4)

    def test_missing_or_garbage_is_none(self) -> None:
        for value in (None, {}, {"temperature": None}, {"temperature": "n/a"}):
            self.assertIsNone(celsius_of(value))


class GenderHardFilterTests(unittest.TestCase):
    """성별은 하드 필터다.

    남성 사용자에게 여성 코디를 "순위만 낮춰" 보여주는 건 추천이 아니라
    오작동으로 읽힌다. 계절·기온과 달리 감점으로 둘 수 없는 축이다.
    """

    def _filter(self, **kwargs):
        from apps.recommend.services.retriever import RetrievalRequest, build_filter

        return build_filter(RetrievalRequest(**kwargs))

    def test_male_user_gets_men_and_unisex(self) -> None:
        built = self._filter(gender="male")
        self.assertIsNotNone(built)
        condition = built.must[0]
        self.assertEqual(condition.key, "presentation_group")
        self.assertEqual(sorted(condition.match.any), ["men", "unisex"])

    def test_female_user_gets_women_and_unisex(self) -> None:
        condition = self._filter(gender="female").must[0]
        self.assertEqual(sorted(condition.match.any), ["unisex", "women"])

    def test_unknown_gender_disables_the_filter(self) -> None:
        """성별 미등록 사용자에게까지 걸면 아무것도 못 본다."""
        for value in ("", "   ", "other"):
            self.assertIsNone(self._filter(gender=value), repr(value))

    def test_case_and_whitespace_tolerated(self) -> None:
        condition = self._filter(gender="  MALE  ").must[0]
        self.assertEqual(sorted(condition.match.any), ["men", "unisex"])

    def test_gender_is_a_must_not_a_penalty(self) -> None:
        """감점 경로로 새면 여성 코디가 순위만 밀린 채 노출된다."""
        built = self._filter(gender="male")
        self.assertTrue(built.must)
        self.assertFalse(built.must_not)


class PresentationGroupNormalizeTests(unittest.TestCase):
    """CSV 표기가 흔들리면 그대로 검색 누락이 된다.

    golden_set 쪽 정규화 함수와 리트리버의 매핑이 같은 어휘를 써야 한다.
    """

    def test_retriever_vocabulary_matches_golden_set(self) -> None:
        from apps.recommend.services.retriever import (
            GENDER_TO_PRESENTATION,
            PRESENTATION_UNISEX,
        )

        self.assertEqual(set(GENDER_TO_PRESENTATION.values()), {"men", "women"})
        self.assertEqual(PRESENTATION_UNISEX, "unisex")

    def test_body_measurement_choices_are_covered(self) -> None:
        """users.BodyMeasurement.Gender의 값이 전부 매핑돼야 한다."""
        from apps.recommend.services.retriever import GENDER_TO_PRESENTATION
        from apps.users.models import BodyMeasurement

        for value in BodyMeasurement.Gender.values:
            self.assertIn(value, GENDER_TO_PRESENTATION, value)


class GenderNormalizationTests(unittest.TestCase):
    """성별 표기 해석은 한 곳에서만 한다.

    이 클래스는 실제로 난 사고의 재발 방지선이다. 83kg 남성 사용자에게 "캉캉
    끈나시 탑"이 추천됐는데, 원인은 검색 로직이 아니라 **값의 배관**이었다:

        BodyMeasurement.gender = ""           (미입력 허용 컬럼)
          → _serialize_measurement 의 `value or None`  → None
          → daily_look 의 `str(...)`                    → "None"
          → GENDER_TO_PRESENTATION.get("none")          → None
          → 성별 하드 필터가 통째로 사라짐 (예외도 로그도 없음)

    필터가 "적용됐는데 틀린" 것이 아니라 "조용히 사라진" 것이라 겉으로는 그냥
    추천이 하나 나온 것처럼 보였다. 그래서 아래 두 가지를 못 박는다.
    """

    def test_str_of_none_is_not_a_gender(self) -> None:
        """실제 사고 값. 이것 하나가 필터 전체를 무력화했다."""
        self.assertEqual(normalize_gender("None"), "")
        self.assertEqual(normalize_gender(None), "")
        self.assertEqual(allowed_presentation_groups("None"), ())

    def test_known_spellings(self) -> None:
        for value in ("male", "MALE", "  Male ", "m", "남성", "남자"):
            self.assertEqual(normalize_gender(value), "male", repr(value))
        for value in ("female", "F", "여성", "여자"):
            self.assertEqual(normalize_gender(value), "female", repr(value))

    def test_blank_like_values_are_blank(self) -> None:
        for value in ("", "   ", "unknown", "미지정", "null", "-"):
            self.assertEqual(normalize_gender(value), "", repr(value))

    def test_allowed_groups_never_include_the_other_side(self) -> None:
        self.assertEqual(allowed_presentation_groups("male"), ("men", "unisex"))
        self.assertEqual(allowed_presentation_groups("female"), ("women", "unisex"))
        # 라벨 없는 코디("")는 어느 쪽에도 없다. unisex로 봐주면 여성 코디가
        # 그대로 남성에게 나간다.
        self.assertNotIn("", allowed_presentation_groups("male"))

    def test_empty_tuple_means_unknown_not_unrestricted(self) -> None:
        """빈 튜플을 '제한 없음'으로 읽는 호출부가 생기면 다시 같은 사고가 난다."""
        self.assertEqual(allowed_presentation_groups(""), ())


class _FakePoint:
    def __init__(self, pid: str, payload: dict) -> None:
        self.id = pid
        self.payload = payload


class _IgnoresFilterClient:
    """필터를 **무시하는** Qdrant. 인덱스 누락·구버전 배포를 흉내낸다.

    Qdrant의 must는 payload 인덱스가 없거나 키가 빠지면 기대와 다르게 동작할 수
    있고, 오래된 이미지가 돌면 애초에 필터가 붙지 않는다. 어느 쪽이든 예외가
    나지 않아 조용히 통과한다. 그래서 리트리버는 파이썬에서 한 번 더 막는다.
    """

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads
        self.last_filter = "unset"

    def scroll(self, *, scroll_filter=None, **kwargs):
        self.last_filter = scroll_filter
        return [_FakePoint(f"p{i}", p) for i, p in enumerate(self.payloads)], None


class GenderSecondLineOfDefenceTests(unittest.TestCase):
    def _run(self, gender: str, payloads: list[dict]):
        from apps.recommend.services.retriever import RetrievalRequest, retrieve_outfits

        client = _IgnoresFilterClient(payloads)
        got = retrieve_outfits(
            RetrievalRequest(gender=gender, limit=10), client=client
        )
        return client, got

    def test_womens_outfit_never_reaches_a_male_user(self) -> None:
        client, got = self._run(
            "male",
            [
                {"golden_id": "w1", "presentation_group": "women",
                 "items": [{"category": "탑", "name": "캉캉 끈나시 탑"}]},
                {"golden_id": "m1", "presentation_group": "men", "items": []},
                {"golden_id": "u1", "presentation_group": "unisex", "items": []},
            ],
        )
        # 검색 단계에도 조건이 붙어 있어야 한다 (왕복 낭비를 줄이는 1차 방어선)
        self.assertIsNotNone(client.last_filter)
        # 그리고 필터가 무시돼도 결과에는 없어야 한다 (2차 방어선)
        self.assertEqual(sorted(c.golden_id for c in got), ["m1", "u1"])

    def test_unlabelled_outfits_are_dropped_not_treated_as_unisex(self) -> None:
        _, got = self._run(
            "male",
            [
                {"golden_id": "x", "presentation_group": "", "items": []},
                {"golden_id": "y", "items": []},
                {"golden_id": "m1", "presentation_group": "men", "items": []},
            ],
        )
        self.assertEqual([c.golden_id for c in got], ["m1"])

    def test_str_none_gender_does_not_open_the_gate(self) -> None:
        """사고 재현. 예전 코드는 여기서 여성 코디를 그대로 돌려줬다."""
        client, got = self._run(
            "None",
            [{"golden_id": "w1", "presentation_group": "women", "items": []}],
        )
        # 성별을 모르면 리트리버는 제한하지 않는다 — 그 판단은 daily_look의 몫이다.
        # 다만 "None"이 성별로 해석되지 않는다는 점은 여기서 못 박는다.
        self.assertIsNone(client.last_filter)
        self.assertEqual([c.golden_id for c in got], ["w1"])


class RecentExclusionTests(unittest.TestCase):
    """최근에 이미 나간 코디는 top k에서 빠지고, 다음 순위가 그 자리를 채운다.

    오늘의 룩이 exclude_golden_ids로 최근 5일치 추천을 넘긴다. 골든셋과 규칙이
    그대로면 순위도 그대로라, 이 제외가 없으면 매일 같은 1위가 뽑힌다.
    """

    PAYLOADS = [
        {"golden_id": "a", "items": []},
        {"golden_id": "b", "items": []},
        {"golden_id": "c", "items": []},
    ]

    def _run(self, exclude: set[str], limit: int = 2):
        from apps.recommend.services.retriever import (
            RetrievalRequest,
            retrieve_outfits,
        )

        client = _IgnoresFilterClient(self.PAYLOADS)
        return retrieve_outfits(
            RetrievalRequest(limit=limit, exclude_golden_ids=frozenset(exclude)),
            client=client,
        )

    def test_without_exclusion_top_k_is_stable(self) -> None:
        """전제 확인 — 점수가 같으면 golden_id 순으로 a, b가 뽑힌다."""
        got = self._run(set())
        self.assertEqual([c.golden_id for c in got], ["a", "b"])

    def test_excluded_outfit_is_replaced_by_the_next_rank(self) -> None:
        """1위(a)가 최근 추천분이면 빠지고, top k는 다음 순위로 다시 채워진다."""
        got = self._run({"a"})
        self.assertEqual([c.golden_id for c in got], ["b", "c"])

    def test_all_excluded_falls_back_to_repeats_not_empty(self) -> None:
        """후보 전부가 최근 추천분이면 제외를 풀고 반복을 허용한다.

        골든셋이 작은 사용자 조건에서 조용히 EMPTY가 되면 "며칠 잘 나오다
        추천이 사라졌다"가 된다 — 반복 추천이 그보다 낫다.
        """
        got = self._run({"a", "b", "c"})
        self.assertEqual([c.golden_id for c in got], ["a", "b"])


class _Point:
    def __init__(self, pid, payload):
        self.id, self.payload = pid, payload


class _FakeQdrant:
    """코디 컬렉션은 scroll로, 아이템 컬렉션은 retrieve로 응답한다."""

    def __init__(self, outfits, item_tags=None, page=2):
        self.outfits = outfits
        self.item_tags = item_tags or {}
        self.page = page
        self.scroll_calls = 0
        self.retrieved: list[str] = []

    def scroll(self, *, collection_name, limit, offset=None, **kwargs):
        if collection_name != "outfit_goldenset":
            return [], None            # 하드 규칙용 아이템 조회
        self.scroll_calls += 1
        start = int(offset or 0)
        chunk = self.outfits[start : start + min(limit, self.page)]
        nxt = start + len(chunk)
        return (
            [_Point(f"p{i + start}", p) for i, p in enumerate(chunk)],
            nxt if nxt < len(self.outfits) else None,
        )

    def retrieve(self, *, collection_name, ids, **kwargs):
        self.retrieved.extend(ids)
        return [
            _Point(i, self.item_tags[i]) for i in ids if i in self.item_tags
        ]


def _outfit(golden_id, *items, **payload):
    return {
        "golden_id": golden_id,
        "presentation_group": "unisex",
        "items": [dict(i) for i in items],
        **payload,
    }


TOP_OVER = {"point_id": "t-over", "category_large": "상의", "item_name": "오버핏 티"}
TOP_SLIM = {"point_id": "t-slim", "category_large": "상의", "item_name": "슬림 니트"}
PANTS_WIDE = {"point_id": "b-wide", "category_large": "하의", "item_name": "와이드 팬츠"}
PANTS_REG = {"point_id": "b-reg", "category_large": "하의", "item_name": "레귤러 슬랙스"}

ITEM_TAGS = {
    "t-over": {"fit": "오버핏", "length": "기본"},
    "t-slim": {"fit": "슬림핏", "length": "기본"},
    "b-wide": {"fit": "와이드핏", "length": "롱"},
    "b-reg": {"fit": "레귤러핏", "length": "기본"},
}


class BodyChangesTheRecommendationTests(TestCase):
    """체형을 바꾸면 1등이 바뀌어야 한다.

    이 클래스가 없어서 다음 결함이 조용히 살아 있었다.

    1. 코디 payload의 아이템 요약에 fit·length·pattern이 없어서 체형 규칙이
       **하나도 매칭되지 않았다.** 모든 체형에서 규칙 점수가 0이라 순위가
       똑같았다. 규칙이 0점이어도 아무 테스트가 실패하지 않았다.
    2. scroll이 앞에서 20건만 끊어 후보 풀이 고정이었다.
    3. 동점일 때 안정 정렬이 스크롤 순서 1등을 그대로 1등으로 만들었다.

    셋 다 "추천이 하나로 고정된다"는 같은 증상을 낸다.
    """

    def setUp(self):
        outfit_render_cache = getattr(retriever, "clear_item_tag_cache", None)
        if outfit_render_cache:
            outfit_render_cache()

    def _top(self, profile, client):
        got = retrieve_outfits(
            RetrievalRequest(body=profile, gender="male", limit=3), client=client
        )
        return [c.golden_id for c in got]

    @override_settings(RETRIEVER_SCROLL_CAP=100, RETRIEVER_SCROLL_PAGE=2,
                       RETRIEVER_ITEM_TAG_JOIN=True,
                       RETRIEVER_ITEM_TAG_CACHE_SECONDS=0)
    def test_triangle_and_inverted_get_different_looks(self):
        client = _FakeQdrant(
            [
                _outfit("wide", TOP_SLIM, PANTS_WIDE),   # 역삼각형에 맞는 조합
                _outfit("over", TOP_OVER, PANTS_REG),    # 삼각형에 맞는 조합
            ],
            ITEM_TAGS,
        )
        triangle = self._top(BodyProfile(silhouette=TRIANGLE), client)
        inverted = self._top(BodyProfile(silhouette=INVERTED_TRIANGLE), client)

        self.assertEqual(triangle[0], "over", f"삼각형 1등: {triangle}")
        self.assertEqual(inverted[0], "wide", f"역삼각형 1등: {inverted}")

    @override_settings(RETRIEVER_SCROLL_CAP=100, RETRIEVER_SCROLL_PAGE=2,
                       RETRIEVER_ITEM_TAG_JOIN=True,
                       RETRIEVER_ITEM_TAG_CACHE_SECONDS=0)
    def test_without_the_tag_join_every_body_scores_the_same(self):
        """조인을 끄면 실제로 났던 증상이 그대로 재현된다 — 회귀 감시용."""
        client = _FakeQdrant(
            [_outfit("wide", TOP_SLIM, PANTS_WIDE), _outfit("over", TOP_OVER, PANTS_REG)],
            ITEM_TAGS,
        )
        with override_settings(RETRIEVER_ITEM_TAG_JOIN=False):
            triangle = retrieve_outfits(
                RetrievalRequest(body=BodyProfile(silhouette=TRIANGLE), gender="male"),
                client=client,
            )
            inverted = retrieve_outfits(
                RetrievalRequest(
                    body=BodyProfile(silhouette=INVERTED_TRIANGLE), gender="male"
                ),
                client=client,
            )
        self.assertEqual([c.score for c in triangle], [c.score for c in inverted])

    @override_settings(RETRIEVER_SCROLL_CAP=100, RETRIEVER_SCROLL_PAGE=2,
                       RETRIEVER_ITEM_TAG_JOIN=True,
                       RETRIEVER_ITEM_TAG_CACHE_SECONDS=0)
    def test_bmi_band_alone_changes_the_ranking(self):
        """실루엣을 모르는 사용자도 체중은 반영돼야 한다."""
        client = _FakeQdrant(
            [_outfit("slim", TOP_SLIM), _outfit("reg", dict(TOP_OVER, point_id="b-reg"))],
            ITEM_TAGS,
        )
        obese = self._top(BodyProfile(bmi_band=OBESE), client)
        self.assertEqual(obese[0], "reg", f"비만 1등: {obese}")


class ScrollCoverageTests(TestCase):
    """후보 풀이 골든셋 전체여야 한다."""

    @override_settings(RETRIEVER_SCROLL_CAP=1000, RETRIEVER_SCROLL_PAGE=7,
                       RETRIEVER_ITEM_TAG_JOIN=False)
    def test_every_matching_outfit_is_considered(self):
        outfits = [_outfit(f"g{n}") for n in range(50)]
        client = _FakeQdrant(outfits, page=7)
        got = retrieve_outfits(RetrievalRequest(gender="male", limit=50), client=client)
        self.assertEqual(len(got), 50)
        self.assertGreater(client.scroll_calls, 1, "페이지네이션을 돌지 않았다")

    @override_settings(RETRIEVER_SCROLL_CAP=10, RETRIEVER_SCROLL_PAGE=4,
                       RETRIEVER_ITEM_TAG_JOIN=False)
    def test_cap_is_honoured_and_warned(self):
        client = _FakeQdrant([_outfit(f"g{n}") for n in range(50)], page=4)
        with self.assertLogs("apps.recommend.services.retriever", "WARNING") as logs:
            got = retrieve_outfits(
                RetrievalRequest(gender="male", limit=50), client=client
            )
        self.assertEqual(len(got), 10)
        self.assertTrue(any("잘랐습니다" in line for line in logs.output))


class TieBreakTests(TestCase):
    @override_settings(RETRIEVER_SCROLL_CAP=100, RETRIEVER_SCROLL_PAGE=50,
                       RETRIEVER_ITEM_TAG_JOIN=False)
    def test_ties_do_not_depend_on_scroll_order(self):
        """동점이면 조회 순서가 1등을 정했다. 적재 순서가 바뀌면 추천도 바뀐다."""
        a = _outfit("aaa")
        b = _outfit("bbb")
        first = retrieve_outfits(
            RetrievalRequest(gender="male"), client=_FakeQdrant([a, b], page=50)
        )
        second = retrieve_outfits(
            RetrievalRequest(gender="male"), client=_FakeQdrant([b, a], page=50)
        )
        self.assertEqual([c.golden_id for c in first], [c.golden_id for c in second])

    @override_settings(RETRIEVER_SCROLL_CAP=100, RETRIEVER_SCROLL_PAGE=50,
                       RETRIEVER_ITEM_TAG_JOIN=False)
    def test_tag_confidence_breaks_ties_before_golden_id(self):
        low = _outfit("aaa", tag_confidence=1)
        high = _outfit("zzz", tag_confidence=9)
        got = retrieve_outfits(
            RetrievalRequest(gender="male"), client=_FakeQdrant([low, high], page=50)
        )
        self.assertEqual(got[0].golden_id, "zzz")


class ItemTagJoinTests(TestCase):
    def setUp(self):
        retriever.clear_item_tag_cache()

    @override_settings(RETRIEVER_ITEM_TAG_JOIN=True, RETRIEVER_ITEM_TAG_BATCH=256,
                       RETRIEVER_ITEM_TAG_CACHE_SECONDS=0)
    def test_tags_are_merged_into_the_outfit_payload(self):
        payload = _outfit("g1", TOP_OVER)
        records = [("p0", 0.0, payload)]
        client = _FakeQdrant([], ITEM_TAGS)
        self.assertEqual(retriever.attach_item_tags(client, records), 1)
        self.assertEqual(payload["items"][0]["fit"], "오버핏")

    @override_settings(RETRIEVER_ITEM_TAG_JOIN=True,
                       RETRIEVER_ITEM_TAG_CACHE_SECONDS=0)
    def test_existing_payload_values_win(self):
        """재적재로 코디 payload에 값이 생기면 그쪽이 그 시점의 진실이다."""
        payload = _outfit("g1", dict(TOP_OVER, fit="레귤러핏"))
        client = _FakeQdrant([], ITEM_TAGS)
        retriever.attach_item_tags(client, [("p0", 0.0, payload)])
        self.assertEqual(payload["items"][0]["fit"], "레귤러핏")
        self.assertEqual(client.retrieved, [], "이미 값이 있는데 조회했다")

    @override_settings(RETRIEVER_ITEM_TAG_JOIN=True,
                       RETRIEVER_ITEM_TAG_CACHE_SECONDS=600)
    def test_cache_avoids_refetching_the_same_items(self):
        """워커는 같은 코디를 사용자 수만큼 반복해서 본다."""
        client = _FakeQdrant([], ITEM_TAGS)
        for _ in range(3):
            retriever.attach_item_tags(client, [("p0", 0.0, _outfit("g1", TOP_OVER))])
        self.assertEqual(client.retrieved, ["t-over"])

    @override_settings(RETRIEVER_ITEM_TAG_JOIN=True,
                       RETRIEVER_ITEM_TAG_CACHE_SECONDS=0)
    def test_qdrant_failure_does_not_break_the_recommendation(self):
        class _Broken(_FakeQdrant):
            def retrieve(self, **kwargs):
                raise RuntimeError("item collection down")

        payload = _outfit("g1", TOP_OVER)
        with self.assertLogs("apps.recommend.services.retriever", "WARNING"):
            self.assertEqual(
                retriever.attach_item_tags(_Broken([]), [("p0", 0.0, payload)]), 0
            )
        self.assertNotIn("fit", payload["items"][0])


class ListValuedTagTests(unittest.TestCase):
    """아이템 태그는 스칼라일 수도 리스트일 수도 있다.

    아이템 컬렉션의 style·season은 리스트다. 태그 조인을 붙인 뒤 취향 매칭이
    `value in labels`로 리스트를 집합에 넣으려다 죽었다:

        TypeError: unhashable type: 'list'

    조인 이전에는 아이템에 style 키 자체가 없어(None) 조용히 넘어갔기 때문에
    드러나지 않았다.
    """

    def setUp(self):
        self.rules = load_body_rules()
        self.w = self.rules.weights

    def _score(self, item, **kwargs):
        return _score_items(
            [item], rules_prefer=(), rules_avoid=(),
            preferred_tags=kwargs.get("preferred", {}),
            avoided_tags=kwargs.get("avoided", {}),
            weights=self.w,
        )

    def test_list_valued_tag_does_not_crash(self):
        item = {"category_large": "상의", "style": ["미니멀", "캐주얼"]}
        total, reasons = self._score(item, preferred={"style": {"미니멀"}})
        self.assertEqual(total, self.w.preference_match)
        self.assertIn("미니멀", reasons[0].text)

    def test_scalar_tag_still_works(self):
        item = {"category_large": "상의", "fit": "오버핏"}
        total, _ = self._score(item, avoided={"fit": {"오버핏"}})
        self.assertEqual(total, self.w.preference_avoid)

    def test_multiple_matches_in_one_list_are_all_counted(self):
        item = {"style": ["미니멀", "캐주얼", "스트릿"]}
        total, reasons = self._score(item, preferred={"style": {"미니멀", "스트릿"}})
        self.assertEqual(total, self.w.preference_match * 2)
        self.assertEqual(len(reasons), 2)

    def test_no_overlap_scores_nothing(self):
        item = {"style": ["포멀"]}
        total, reasons = self._score(item, preferred={"style": {"미니멀"}})
        self.assertEqual(total, 0.0)
        self.assertEqual(reasons, [])

    def test_missing_or_empty_values_are_ignored(self):
        for value in (None, "", [], ["", None]):
            item = {"style": value} if value is not None else {}
            total, _ = self._score(item, preferred={"style": {"미니멀"}})
            self.assertEqual(total, 0.0, repr(value))

    def test_rules_and_preferences_read_tags_the_same_way(self):
        """Rule.matches()는 리스트를 다뤘는데 취향 매칭만 스칼라를 가정했다."""
        item = {"category_large": "상의", "style": ["미니멀"]}
        from apps.recommend.services.style_rules import Rule

        rule = Rule(match={"style": "미니멀"}, reason="테스트")
        self.assertTrue(rule.matches(item))
        total, _ = self._score(item, preferred={"style": {"미니멀"}})
        self.assertGreater(total, 0)


class ItemLevelGenderGuardTests(unittest.TestCase):
    """성별은 이 시스템에서 가장 강한 규칙이다.

    presentation_group은 LLM이 사진을 보고 붙인 라벨이라 틀릴 수 있고, 특히
    "unisex"는 애매한 코디의 도피처가 된다. 실제로 여성 코디가 unisex로
    태깅돼 남성 사용자에게 '캉캉 끈나시 탑'이 추천됐다. 라벨만 믿는 한 이
    사고는 반복되므로, 옷 자체를 아이템 단위로 한 번 더 본다.
    """

    def test_the_actual_incident_is_caught(self) -> None:
        """실제로 나갔던 아이템. 소분류가 '민소매'라 라벨로는 안 걸린다."""
        items = [{"item_name": "캉캉 끈나시 탑", "category_small": "민소매"}]
        self.assertTrue(conflicting_item(items, "male"))

    def test_female_only_categories_are_blocked_for_men(self) -> None:
        for category in ("스커트", "원피스", "브라"):
            items = [{"item_name": "무언가", "category_small": category}]
            self.assertTrue(conflicting_item(items, "male"), category)

    def test_female_only_keywords_are_blocked_for_men(self) -> None:
        for name in ("플로럴 원피스", "미디 스커트", "레이스 블라우스", "새틴 캐미솔"):
            items = [{"item_name": name, "category_small": "기타"}]
            self.assertTrue(conflicting_item(items, "male"), name)

    def test_the_same_items_are_fine_for_women(self) -> None:
        items = [{"item_name": "플로럴 원피스", "category_small": "원피스"}]
        self.assertEqual(conflicting_item(items, "female"), "")

    def test_ordinary_items_pass(self) -> None:
        items = [
            {"item_name": "옥스퍼드 셔츠", "category_small": "셔츠/블라우스"},
            {"item_name": "데님 팬츠", "category_small": "데님 팬츠"},
            {"item_name": "스니커즈", "category_small": "스니커즈"},
        ]
        self.assertEqual(conflicting_item(items, "male"), "")
        self.assertEqual(conflicting_item(items, "female"), "")

    def test_one_bad_item_sinks_the_whole_outfit(self) -> None:
        """코디는 한 벌이다. 치마가 하나 섞이면 그 코디는 남성용이 아니다."""
        items = [
            {"item_name": "옥스퍼드 셔츠", "category_small": "셔츠/블라우스"},
            {"item_name": "플리츠 스커트", "category_small": "스커트"},
        ]
        self.assertTrue(conflicting_item(items, "male"))

    def test_unknown_gender_is_not_checked_here(self) -> None:
        """성별을 모를 때 무엇을 할지는 호출부가 정한다 (오늘의 룩은 EMPTY)."""
        items = [{"item_name": "플로럴 원피스", "category_small": "원피스"}]
        self.assertEqual(conflicting_item(items, ""), "")

    def test_reason_is_human_readable(self) -> None:
        items = [{"item_name": "플리츠 스커트", "category_small": "스커트"}]
        reason = conflicting_item(items, "male")
        self.assertIn("여성 전용", reason)
        self.assertIn("스커트", reason)

    def test_rules_file_is_loaded(self) -> None:
        """파일이 없거나 깨지면 이 규칙이 통째로 꺼진다 — 조용히 넘어가면 안 된다."""
        rules = load_gender_rules()
        self.assertIn("women", rules)
        self.assertTrue(rules["women"]["categories"])
        self.assertTrue(rules["women"]["keywords"])


class MislabelledUnisexTests(TestCase):
    """라벨이 unisex여도 여성복이면 남성에게 나가지 않는다."""

    @override_settings(RETRIEVER_SCROLL_CAP=100, RETRIEVER_SCROLL_PAGE=50,
                       RETRIEVER_ITEM_TAG_JOIN=False)
    def test_womens_outfit_tagged_unisex_is_dropped(self) -> None:
        outfits = [
            {   # 태깅이 틀린 코디 — 라벨은 unisex인데 내용은 여성복
                "golden_id": "bad", "presentation_group": "unisex",
                "items": [{"item_name": "캉캉 끈나시 탑", "category_small": "민소매"},
                          {"item_name": "플리츠 스커트", "category_small": "스커트"}],
            },
            {   # 정상적인 남녀 공용 코디
                "golden_id": "ok", "presentation_group": "unisex",
                "items": [{"item_name": "옥스퍼드 셔츠", "category_small": "셔츠/블라우스"},
                          {"item_name": "데님 팬츠", "category_small": "데님 팬츠"}],
            },
        ]
        client = _FakeQdrant(outfits, page=50)
        got = retrieve_outfits(RetrievalRequest(gender="male", limit=10), client=client)
        self.assertEqual([c.golden_id for c in got], ["ok"])

from django.contrib.auth import get_user_model
from apps.wardrobe.models import SharedWardrobeItem, SharedWardrobeMember, SharedWardrobeRoom, WardrobeItem
from apps.recommend.services.wardrobe_link import accessible_item_ids
from apps.recommend.services.retriever import (
    retrieve_accessible_substitutes,
    retrieve_substitutes,
)
from apps.recommend.services.qdrant import WARDROBE_ITEM_COLLECTION, qm

User = get_user_model()


class SharedWardrobeAccessibleItemIdsTests(TestCase):
    """공유 옷장 추천 검색 접근 가능 아이템 ID (accessible_item_ids) 테스트."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(username="testuser", password="password")
        self.other_user = User.objects.create_user(username="otheruser", password="password")

    def test_accessible_item_ids_includes_own_and_shared_items(self) -> None:
        # 내 옷장 confirmed=True 아이템
        own_item = WardrobeItem.objects.create(
            user=self.user,
            item_name="내 티셔츠",
            category_large="상의",
            confirmed=True,
        )

        # 타인 옷장 confirmed=True 아이템
        other_item = WardrobeItem.objects.create(
            user=self.other_user,
            item_name="타인 자켓",
            category_large="아우터",
            confirmed=True,
        )

        # 방 생성 및 멤버 가입
        room = SharedWardrobeRoom.objects.create(title="공유방1", invite_code="ABC123")
        SharedWardrobeMember.objects.create(room=room, user=self.user, role=SharedWardrobeMember.Role.OWNER)
        SharedWardrobeMember.objects.create(room=room, user=self.other_user, role=SharedWardrobeMember.Role.MEMBER)

        # 공유 아이템 (AVAILABLE)
        shared_item = SharedWardrobeItem.objects.create(
            room=room,
            registered_by=self.other_user,
            wardrobe_item=other_item,
            status=SharedWardrobeItem.Status.AVAILABLE,
        )

        got = accessible_item_ids(self.user)
        self.assertIn(str(own_item.id), got)
        self.assertIn(str(other_item.id), got)
        self.assertEqual(len(got), 2)

    def test_accessible_item_ids_excludes_unjoined_room_items(self) -> None:
        # 가입하지 않은 방의 공유 아이템
        other_item = WardrobeItem.objects.create(
            user=self.other_user,
            item_name="외부 자켓",
            category_large="아우터",
            confirmed=True,
        )
        room = SharedWardrobeRoom.objects.create(title="외부방", invite_code="XYZ789")
        SharedWardrobeMember.objects.create(room=room, user=self.other_user, role=SharedWardrobeMember.Role.OWNER)
        SharedWardrobeItem.objects.create(
            room=room,
            registered_by=self.other_user,
            wardrobe_item=other_item,
            status=SharedWardrobeItem.Status.AVAILABLE,
        )

        got = accessible_item_ids(self.user)
        self.assertNotIn(str(other_item.id), got)

    def test_accessible_item_ids_excludes_unconfirmed_items(self) -> None:
        unconfirmed_own = WardrobeItem.objects.create(
            user=self.user,
            item_name="미확정 내 옷",
            category_large="상의",
            confirmed=False,
        )
        unconfirmed_shared_item = WardrobeItem.objects.create(
            user=self.other_user,
            item_name="미확정 공유 옷",
            category_large="하의",
            confirmed=False,
        )

        room = SharedWardrobeRoom.objects.create(title="공유방2", invite_code="DEF456")
        SharedWardrobeMember.objects.create(room=room, user=self.user, role=SharedWardrobeMember.Role.OWNER)
        SharedWardrobeMember.objects.create(room=room, user=self.other_user, role=SharedWardrobeMember.Role.MEMBER)
        SharedWardrobeItem.objects.create(
            room=room,
            registered_by=self.other_user,
            wardrobe_item=unconfirmed_shared_item,
            status=SharedWardrobeItem.Status.AVAILABLE,
        )

        got = accessible_item_ids(self.user)
        self.assertNotIn(str(unconfirmed_own.id), got)
        self.assertNotIn(str(unconfirmed_shared_item.id), got)

    def test_accessible_item_ids_excludes_borrowed_or_private_items(self) -> None:
        shared_item1 = WardrobeItem.objects.create(
            user=self.other_user,
            item_name="대여중 옷",
            category_large="아우터",
            confirmed=True,
        )
        shared_item2 = WardrobeItem.objects.create(
            user=self.other_user,
            item_name="비공개 옷",
            category_large="하의",
            confirmed=True,
        )

        room = SharedWardrobeRoom.objects.create(title="공유방3", invite_code="GHI789")
        SharedWardrobeMember.objects.create(room=room, user=self.user, role=SharedWardrobeMember.Role.OWNER)
        SharedWardrobeMember.objects.create(room=room, user=self.other_user, role=SharedWardrobeMember.Role.MEMBER)

        SharedWardrobeItem.objects.create(
            room=room,
            registered_by=self.other_user,
            wardrobe_item=shared_item1,
            status=SharedWardrobeItem.Status.BORROWED,
        )
        SharedWardrobeItem.objects.create(
            room=room,
            registered_by=self.other_user,
            wardrobe_item=shared_item2,
            status=SharedWardrobeItem.Status.PRIVATE,
        )

        got = accessible_item_ids(self.user)
        self.assertNotIn(str(shared_item1.id), got)
        self.assertNotIn(str(shared_item2.id), got)

    @override_settings(RETRIEVER_WARDROBE_ID_CAP=2)
    def test_accessible_item_ids_respects_cap_and_deterministic_order(self) -> None:
        item1 = WardrobeItem.objects.create(user=self.user, item_name="1", confirmed=True)
        item2 = WardrobeItem.objects.create(user=self.user, item_name="2", confirmed=True)
        item3 = WardrobeItem.objects.create(user=self.user, item_name="3", confirmed=True)

        got1 = accessible_item_ids(self.user)
        got2 = accessible_item_ids(self.user)

        self.assertEqual(len(got1), 2)
        self.assertEqual(got1, got2)


class _WhitelistIgnoresFilterClient:
    """retrieve_substitutes 화이트리스트 테스트용 Qdrant 스텁 클라이언트."""

    def __init__(self) -> None:
        self.last_collection_name = None
        self.last_filter = None
        self.scroll_called = False
        self.search_called = False

    def scroll(self, collection_name: str, scroll_filter=None, with_payload=True, with_vectors=False, limit=10):
        self.scroll_called = True
        self.last_collection_name = collection_name
        self.last_filter = scroll_filter
        class Point:
            id = "mock-id-1"
            payload = {"item_name": "스텁 아이템"}
        return [Point()], None

    def search(self, collection_name: str, query_vector=None, query_filter=None, limit=10, with_payload=True):
        self.search_called = True
        self.last_collection_name = collection_name
        self.last_filter = query_filter
        class Hit:
            id = "mock-id-2"
            score = 0.95
            payload = {"item_name": "스텁 아이템 2"}
        return [Hit()]


class RetrieveSubstitutesWhitelistTests(unittest.TestCase):
    """retrieve_substitutes 필터 및 격리 회귀 방지 테스트."""

    def test_empty_allowed_item_ids_returns_empty_immediately(self) -> None:
        client = _WhitelistIgnoresFilterClient()
        got = retrieve_substitutes({"category_large": "상의"}, allowed_item_ids=[], client=client)
        self.assertEqual(got, [])
        self.assertFalse(client.scroll_called)
        self.assertFalse(client.search_called)

    def test_default_collection_name_is_wardrobe_items(self) -> None:
        client = _WhitelistIgnoresFilterClient()
        retrieve_substitutes({"category_large": "상의"}, allowed_item_ids=["uuid-1"], client=client)
        self.assertEqual(client.last_collection_name, WARDROBE_ITEM_COLLECTION)
        self.assertTrue(client.scroll_called)


class RetrieveAccessibleSubstitutesTests(TestCase):
    """DB 권한 목록이 실제 Qdrant 검색 화이트리스트로 연결되는지 검증한다."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(username="recommend-user", password="password")
        self.other_user = User.objects.create_user(username="recommend-other", password="password")

    def test_passes_only_own_and_available_shared_confirmed_ids(self) -> None:
        own = WardrobeItem.objects.create(user=self.user, item_name="내 옷", confirmed=True)
        hidden_own = WardrobeItem.objects.create(
            user=self.user, item_name="미확정 내 옷", confirmed=False
        )
        shared = WardrobeItem.objects.create(
            user=self.other_user, item_name="공유 옷", confirmed=True
        )
        borrowed = WardrobeItem.objects.create(
            user=self.other_user, item_name="대여 중", confirmed=True
        )
        room = SharedWardrobeRoom.objects.create(title="추천 공유방", invite_code="REC123")
        SharedWardrobeMember.objects.create(
            room=room, user=self.user, role=SharedWardrobeMember.Role.MEMBER
        )
        SharedWardrobeItem.objects.create(
            room=room,
            registered_by=self.other_user,
            wardrobe_item=shared,
            status=SharedWardrobeItem.Status.AVAILABLE,
        )
        SharedWardrobeItem.objects.create(
            room=room,
            registered_by=self.other_user,
            wardrobe_item=borrowed,
            status=SharedWardrobeItem.Status.BORROWED,
        )

        client = _WhitelistIgnoresFilterClient()
        retrieve_accessible_substitutes(
            self.user, {"category_large": "상의"}, client=client
        )

        has_id_conditions = [
            condition
            for condition in client.last_filter.must
            if isinstance(condition, qm.HasIdCondition)
        ]
        self.assertEqual(len(has_id_conditions), 1)
        allowed = {str(item_id) for item_id in has_id_conditions[0].has_id}
        self.assertEqual(allowed, {str(own.id), str(shared.id)})
        self.assertNotIn(str(hidden_own.id), allowed)
        self.assertNotIn(str(borrowed.id), allowed)

    def test_no_accessible_items_skips_qdrant(self) -> None:
        client = _WhitelistIgnoresFilterClient()

        got = retrieve_accessible_substitutes(
            self.user, {"category_large": "상의"}, client=client
        )

        self.assertEqual(got, [])
        self.assertFalse(client.scroll_called)
        self.assertFalse(client.search_called)

