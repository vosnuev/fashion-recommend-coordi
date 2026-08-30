"""골든셋 원칙의 적용 조건을 코디 조합에 대조한다.

## 이 모듈이 푸는 문제

추천은 골든 코디를 고른 뒤 슬롯마다 실제 상품으로 치환한다. 이때 **골든 코디가 성립한
이유가 치환 단계에서 깨진다** — 무지 니트 자리에 패턴 니트가 들어와도 시각적으로
비슷하면 통과한다.

사람이 검수해 승인한 원칙에는 그 코디가 성립하는 조건이 적혀 있다. 그 조건을 조합에
대조하면 어느 슬롯이 어긋났는지 알 수 있다.

## 조건의 의미 — 판정 규칙

원칙의 조건 여러 개는 **하나의 일관된 착장**을 묘사한다. 예를 들어

    상의 명도=어두움 / 하의 명도=밝음 / 신발 명도=어두움

이 셋은 "어두운 상의 + 밝은 하의일 때 신발도 어둡게"라는 한 덩어리다. 그래서 조합이
일부만 맞으면 **그 원칙이 이 코디에 관여한다고 보고, 안 맞는 조건을 어긋남으로 센다.**

- 맞는 조건이 `ENGAGE_MIN` 미만이면 → 애초에 다른 코디에 대한 원칙이다. 무시한다.
- `ENGAGE_MIN` 이상 맞으면 → 관여한다. 안 맞는 조건이 고칠 슬롯을 가리킨다.

이 문턱이 없으면 3개 중 1개만 우연히 맞은 원칙까지 끌어와 엉뚱한 슬롯을 바꾸게 된다.

## 알 수 없음은 어긋남이 아니다

상품 태그는 대부분 비어 있다(pattern 11퍼센트, fit 14퍼센트). 속성을 못 읽었을 때
어긋난 것으로 세면, **태깅이 안 된 상품이 전부 벌점을 받는다.** 그래서 판정은
참·거짓·모름 세 값이고, 모름은 양쪽 어디에도 세지 않는다.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Literal

logger = logging.getLogger(__name__)

#: 조건이 이만큼 맞아야 그 원칙이 이 코디에 관여한다고 본다.
ENGAGE_MIN = 2


SLOTS = ("top", "bottom", "outer", "shoes", "bag", "belt", "accessory")

#: 골든 아이템의 category_large → 조건이 쓰는 슬롯 이름.
#: 액세서리는 벨트와 그 외가 섞여 있어 상품명으로 한 번 더 가른다.
CATEGORY_SLOT = {
    "상의": "top", "하의": "bottom", "아우터": "outer",
    "신발": "shoes", "가방": "bag", "액세서리": "accessory",
}


def slot_of(payload: dict[str, Any]) -> str:
    """아이템이 어느 슬롯인지. 알 수 없으면 빈 문자열."""
    category = payload.get("category_large") or ""
    if isinstance(category, list):
        category = category[0] if category else ""
    slot = CATEGORY_SLOT.get(str(category).strip(), "")
    if slot == "accessory" and "벨트" in _texts(payload):
        return "belt"
    return slot

#: 색 이름 → 명도. 상품 color 태그는 19퍼센트만 채워져 있어 상품명에서도 찾는다.
BRIGHTNESS: dict[str, str] = {
    "화이트": "밝음", "흰색": "밝음", "아이보리": "밝음", "크림": "밝음",
    "베이지": "밝음", "라이트": "밝음", "파스텔": "밝음", "실버": "밝음",
    "블랙": "어두움", "검정": "어두움", "검정색": "어두움", "차콜": "어두움",
    "네이비": "어두움", "다크": "어두움", "진청": "어두움", "버건디": "어두움",
    "브라운": "어두움", "카키": "어두움",
    "그레이": "중간", "회색": "중간", "블루": "중간", "청색": "중간",
    "그린": "중간", "레드": "중간", "핑크": "중간", "퍼플": "중간",
}

#: 관계 이름 → 그 관계가 보는 속성.
RELATION_ATTRIBUTE = {
    "명도대비": "명도", "명도통일": "명도", "색대비": "색",
    "색통일": "색", "기장대비": "기장", "볼륨대비": "핏",
}

ACHROMATIC = {"화이트", "흰색", "블랙", "검정", "검정색", "그레이", "회색", "차콜", "아이보리"}

_KEYWORDS: dict[str, dict[str, tuple[str, ...]]] = {
    "패턴": {
        "무지": ("무지", "솔리드", "베이직"),
        "스트라이프": ("스트라이프", "줄무늬"),
        "체크": ("체크", "깅엄", "타탄"),
        "플로럴": ("플로럴", "꽃무늬"),
        "그래픽": ("그래픽", "레터링", "프린팅", "프린트"),
    },
    "기장": {
        "크롭": ("크롭",),
        "숏": ("숏", "반팔", "미니"),
        "미디": ("미디",),
        "롱": ("롱", "맥시"),
    },
    "핏": {
        "슬림": ("슬림", "타이트", "스키니"),
        "레귤러": ("레귤러", "스탠다드"),
        "와이드": ("와이드",),
        "오버핏": ("오버핏", "오버사이즈"),
        "배기": ("배기",),
    },
    "허리": {
        "하이": ("하이웨스트", "하이웨이스트", "하이라이즈"),
        "미드": ("미드라이즈",),
        "로우": ("로우라이즈",),
    },
    # 한 글자 키워드(면·마·울)는 뺐다. "표면"·"파마"처럼 무관한 단어에 걸려 잘못된
    # 소재를 붙인다. 태그와 구조화된 text가 이미 51퍼센트를 덮으므로 손실이 작다.
    "소재": {
        "니트": ("니트",),
        "데님": ("데님", "청바지"),
        "코튼": ("코튼",),
        "울": ("모직",),
        "레더": ("레더", "가죽"),
        "린넨": ("린넨",),
    },
}


@dataclass(frozen=True)
class Condition:
    """사람이 입력한 적용 조건 하나."""

    kind: Literal["single", "relation"]
    slot: str = ""
    attribute: str = ""
    value: str = ""
    relation: str = ""
    slot_a: str = ""
    slot_b: str = ""


@dataclass(frozen=True)
class PrincipleRule:
    principle_key: str
    cluster_id: str
    statement: str
    conditions: tuple[Condition, ...] = ()


@dataclass(frozen=True)
class RuleOutcome:
    """한 원칙을 조합에 대조한 결과."""

    rule: PrincipleRule
    matched: int
    #: 어긋난 조건과 그것이 가리키는 슬롯.
    violations: tuple[tuple[Condition, str], ...] = ()

    @property
    def engaged(self) -> bool:
        return self.matched >= ENGAGE_MIN

    @property
    def violation_slots(self) -> tuple[str, ...]:
        seen: list[str] = []
        for _, slot in self.violations:
            if slot and slot not in seen:
                seen.append(slot)
        return tuple(seen)


def _texts(payload: dict[str, Any]) -> str:
    """태그와 상품명을 한 덩어리로. 태그가 비어도 이름에서 찾을 수 있게 한다."""
    parts: list[str] = []
    for key in ("title", "item_name", "product_name", "display_name", "text"):
        value = payload.get(key)
        if value:
            parts.append(str(value))
    return " ".join(parts)


def _contains(pool: str, word: str) -> bool:
    """짧은 키워드가 다른 단어 안에 묻혀 걸리는 것을 막는다.

    "꽈배기"의 "배기"를 핏으로 읽는 식의 오탐이 실제로 나왔다. 두 글자 이하 키워드는
    앞 글자가 한글이면 다른 단어의 일부로 보고 무시한다. "배기핏"처럼 뒤에 붙는 것은
    그대로 인정한다 — 앞이 아니라 뒤에 붙는 게 한국어 합성의 정상 형태다.
    """
    if len(word) > 2:
        return word in pool
    return re.search(r"(?<![가-힣])" + re.escape(word), pool) is not None


def _listed(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value if item]


def extract_attributes(payload: dict[str, Any]) -> dict[str, str]:
    """상품 payload에서 원칙 판정에 쓸 속성을 뽑는다. 못 읽은 속성은 담지 않는다."""
    attributes: dict[str, str] = {}
    haystack = _texts(payload)

    colors = _listed(payload, "color") or _listed(payload, "base_color")
    if not colors:
        colors = [name for name in BRIGHTNESS if name in haystack]
    levels = {BRIGHTNESS[name] for name in colors if name in BRIGHTNESS}
    if len(levels) == 1:
        attributes["명도"] = levels.pop()
    if colors and all(name in ACHROMATIC for name in colors):
        attributes["색"] = "무채색"

    for source, name in (("season", "계절"), ("sleeve", "소매")):
        values = _listed(payload, source)
        if values:
            attributes[name] = ";".join(sorted(values))

    for attribute, table in _KEYWORDS.items():
        tagged = _listed(payload, {"패턴": "pattern", "기장": "length", "핏": "fit",
                                   "소재": "material", "허리": "rise"}.get(attribute, ""))
        pool = " ".join(tagged) + " " + haystack
        for value, words in table.items():
            if any(_contains(pool, word) for word in words):
                attributes[attribute] = value
                break
    return attributes


def _check_single(attributes: dict[str, str], condition: Condition) -> bool | None:
    actual = attributes.get(condition.attribute)
    if actual is None:
        return None
    return actual == condition.value


def _check_relation(
    a: dict[str, str], b: dict[str, str], relation: str
) -> bool | None:
    if relation not in RELATION_ATTRIBUTE:
        return None
    attribute = RELATION_ATTRIBUTE[relation]
    want_same = relation in ("명도통일", "색통일")
    left, right = a.get(attribute), b.get(attribute)
    if left is None or right is None:
        return None
    return (left == right) if want_same else (left != right)


def evaluate_rule(
    rule: PrincipleRule, slot_attributes: dict[str, dict[str, str]]
) -> RuleOutcome:
    """원칙 하나를 조합에 대조한다. 모름은 맞음에도 어긋남에도 세지 않는다."""
    matched = 0
    violations: list[tuple[Condition, str]] = []
    for condition in rule.conditions:
        if condition.kind == "relation":
            result = _check_relation(
                slot_attributes.get(condition.slot_a, {}),
                slot_attributes.get(condition.slot_b, {}),
                condition.relation,
            )
            slot = condition.slot_b
        else:
            result = _check_single(
                slot_attributes.get(condition.slot, {}), condition
            )
            slot = condition.slot
        if result is True:
            matched += 1
        elif result is False:
            violations.append((condition, slot))
    return RuleOutcome(rule=rule, matched=matched, violations=tuple(violations))


def evaluate(
    rules: Iterable[PrincipleRule], slot_attributes: dict[str, dict[str, str]]
) -> tuple[RuleOutcome, ...]:
    """관여하는 원칙만 돌려준다. 어긋남이 없는 것도 포함된다."""
    outcomes = [evaluate_rule(rule, slot_attributes) for rule in rules]
    return tuple(outcome for outcome in outcomes if outcome.engaged)


def violation_count(
    rules: Iterable[PrincipleRule], slot_attributes: dict[str, dict[str, str]]
) -> int:
    """조합 정렬에 쓸 어긋남 수. 적을수록 좋은 조합이다."""
    return sum(len(outcome.violations) for outcome in evaluate(rules, slot_attributes))


def _conditions_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "principle_conditions.json"


@lru_cache(maxsize=1)
def load_principle_rules() -> tuple[PrincipleRule, ...]:
    """사람이 승인한 원칙의 적용 조건. 파일이 없거나 깨져 있으면 빈 튜플.

    조건은 골든셋 사이클마다 갱신되는 정적 데이터라 DB가 아니라 파일로 둔다.
    프로세스당 한 번만 읽는다 — 조합마다 파일을 여는 일이 없어야 한다.

    **읽기에 실패해도 예외를 올리지 않는다.** 원칙은 없어도 추천이 성립하는
    부가 정보이고, 데이터 파일 하나 때문에 추천 전체가 죽으면 안 된다.
    """
    path = _conditions_path()
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("원칙 조건 파일을 읽지 못했다: %s", path, exc_info=True)
        return ()

    rules: list[PrincipleRule] = []
    for row in rows:
        conditions: list[Condition] = []
        for raw in row.get("conditions", []):
            kind = raw.get("kind")
            if kind == "relation":
                if raw.get("relation") and raw.get("slot_a") and raw.get("slot_b"):
                    conditions.append(Condition(
                        kind="relation", relation=raw["relation"],
                        slot_a=raw["slot_a"], slot_b=raw["slot_b"],
                    ))
            elif kind == "single":
                if raw.get("slot") and raw.get("attribute") and raw.get("value"):
                    conditions.append(Condition(
                        kind="single", slot=raw["slot"],
                        attribute=raw["attribute"], value=raw["value"],
                    ))
        if conditions:
            rules.append(PrincipleRule(
                principle_key=str(row.get("principle_key", "")),
                cluster_id=str(row.get("cluster_id", "")),
                statement=str(row.get("statement", "")),
                conditions=tuple(conditions),
            ))
    return tuple(rules)


def rules_for_styles(styles: Iterable[str]) -> tuple[PrincipleRule, ...]:
    """해당 스타일의 원칙만. 스타일을 모르면 전부 돌려준다.

    골든 코디의 스타일과 무관한 원칙까지 대조하면, 다른 스타일에서만 참인 조건이
    이 코디를 어긋난 것으로 만든다.
    """
    wanted = {str(value).strip() for value in styles if str(value).strip()}
    rules = load_principle_rules()
    if not wanted:
        return rules
    return tuple(rule for rule in rules if rule.cluster_id in wanted)


def attributes_in_play(rules: Iterable[PrincipleRule], slot: str) -> frozenset[str]:
    """해당 슬롯에 대해 원칙들이 언급하는 속성 이름.

    원칙이 신경 쓰지 않는 속성까지 치환 변화를 벌점으로 세면, 원칙과 무관한 이유로
    후보가 밀린다. 원칙이 실제로 거론한 속성만 본다.
    """
    names: set[str] = set()
    for rule in rules:
        for condition in rule.conditions:
            if condition.kind == "single" and condition.slot == slot:
                names.add(condition.attribute)
            elif condition.kind == "relation" and slot in (
                condition.slot_a,
                condition.slot_b,
            ):
                names.add(RELATION_ATTRIBUTE.get(condition.relation, ""))
    names.discard("")
    return frozenset(names)


def drift_count(
    template: dict[str, str],
    candidate: dict[str, str],
    watched: frozenset[str],
) -> int:
    """치환이 골든 원본의 성질을 바꾼 정도.

    **읽을 수 있는 모든 속성을 본다.** 사례가 나올 때마다 감시 목록에 추가하는 방식은
    다음 사례를 못 막는다 — 무지가 그래픽이 되는 것도, 여름 옷이 가을 옷이 되는 것도
    "원본과 다른 옷이 들어왔다"는 한 가지 문제의 단면이다.

    원칙이 지목한 속성은 두 배로 센다. 그 축은 코디가 성립한 이유와 직접 닿아 있다.

    한쪽이라도 못 읽으면 세지 않는다. 상품 태그가 비어 있는 경우가 많아, 모름을
    벌점으로 주면 태깅 안 된 상품이 일괄로 밀린다.

    계절처럼 값이 여러 개인 속성은 겹치는 게 하나라도 있으면 같은 것으로 본다 —
    "봄;가을"과 "가을;겨울"은 가을에 함께 입을 수 있다.
    """
    changed = 0
    for name in set(template) & set(candidate):
        before, after = template[name], candidate[name]
        if before == after:
            continue
        if ";" in before or ";" in after:
            if set(before.split(";")) & set(after.split(";")):
                continue
        changed += 2 if name in watched else 1
    return changed
