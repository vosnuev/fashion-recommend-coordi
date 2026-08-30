"""추구미(Pursuit) 코드 ↔ 의류 태그 라벨 사이의 번역표.

두 어휘가 따로 자랐다. 사용자 선호는 `users/migrations/0007_pursuit.py`가 시드한
영문 코드(`oversized`, `vneck`, ...)로 저장되고, 옷·코디 태그는 image-processor의
`pipeline/taxonomy.py`가 정한 한글 라벨(`오버핏`, `레귤러핏`, ...)로 저장된다.
Qdrant payload도 후자를 쓴다. 그래서 "사용자가 기피한 것"을 검색 필터로 바꾸려면
반드시 이 표를 거쳐야 한다.

설계 원칙 두 가지.

1) 한쪽에만 있는 값은 조용히 버리지 않는다. `translate()`가 번역하지 못한 코드를
   `unmapped`로 돌려주므로, 호출부가 "이 선호는 검색에 반영되지 않았다"를 사용자나
   로그에 남길 수 있다. 조용히 버리면 필터가 걸린 줄 알고 잘못된 결과를 신뢰하게 된다.
2) 근사 매핑은 근사임을 표시한다. `루즈핏`은 태그 어휘에 없어 `오버핏`으로 보내는데,
   이런 항목은 `APPROXIMATE`에 모아 두어 나중에 어휘를 정리할 때 근거가 되게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 계절 — 태그 쪽에만 '간절기'가 더 있다 (선호로는 고를 수 없음).
SEASON = {
    "spring": "봄",
    "summer": "여름",
    "autumn": "가을",
    "winter": "겨울",
}

#: 스타일 — 선호 16종 중 태그 어휘와 겹치는 8종만 번역된다.
#: 나머지(클래식·로맨틱·엘레강스·레트로·모던·비즈니스·비즈니스 캐주얼·보이시)는
#: 태그 쪽에 대응 라벨이 없어 unmapped로 떨어진다.
STYLE = {
    "minimal": "미니멀",
    "casual": "캐주얼",
    "street": "스트릿",
    "lovely": "러블리",
    "chic": "시크",
    "sporty": "스포티",
    "vintage": "빈티지",
    "americasual": "아메카지",
}

#: 색상 — 선호 29종, 태그 17종. 이름이 같은 것만 직역하고
#: 나머지는 가장 가까운 태그 색으로 보낸다(APPROXIMATE에 기록).
COLOR = {
    "black": "블랙",
    "white": "화이트",
    "ivory": "아이보리",
    "gray": "그레이",
    "navy": "네이비",
    "beige": "베이지",
    "brown": "브라운",
    "khaki": "카키",
    "blue": "블루",
    "green": "그린",
    "red": "레드",
    "pink": "핑크",
    "yellow": "옐로우",
    "purple": "퍼플",
    "orange": "오렌지",
    # ── 근사 ──
    "charcoal": "그레이",
    "olive": "카키",
    "carmel": "브라운",
    "denim_blue": "블루",
    "light_pink": "핑크",
    "rose": "핑크",
    "mauve": "퍼플",
    "peach": "오렌지",
    "coral": "오렌지",
    "light_blue": "스카이블루",
    "mint": "그린",
    "burgundy": "레드",
    "silver": "그레이",
    "gold": "베이지",
}

#: 상의 핏 — 태그의 '와이드핏'은 하의 전용이라 상의 선호에서는 안 나온다.
TOP_FIT = {
    "normal": "레귤러핏",
    "slim": "슬림핏",
    "oversized": "오버핏",
    "loose": "오버핏",       # 근사: 태그에 '루즈핏'이 없다
}

#: 팬츠 핏 — 선호는 7종으로 잘게 나뉘는데 태그는 4종뿐이라 많이 뭉친다.
PANTS_FIT = {
    "wide": "와이드핏",
    "semi_wide": "와이드핏",
    "straight": "레귤러핏",
    "slacks": "레귤러핏",
    "bootcut": "레귤러핏",
    "jogger": "레귤러핏",
    "skinny": "슬림핏",
}

#: 상의 기장 — 선호의 '숏'은 태그에 없어 '기본'으로 보낸다.
TOP_LENGTH = {
    "crop": "크롭",
    "regular": "기본",
    "long": "롱",
    "short": "기본",         # 근사
}

#: 소매 — 선호의 '7부소매'는 태그에 없다.
SLEEVE = {
    "long": "긴팔",
    "short": "반팔",
    "sleeveless": "민소매",
}

#: 넥라인 — **태그 체계에 넥라인 필드 자체가 없다.**
#: 골든 아이템/옷장/상품 어디에도 저장되지 않으므로 검색에 반영할 수단이 없다.
#: 빈 표로 두어 전부 unmapped로 떨어뜨린다 — 조용히 무시하지 않기 위해서다.
NECKLINE: dict[str, str] = {}

#: 카테고리별로 어느 표를 쓰는지. 없는 카테고리는 번역 대상이 아니다.
_TABLES: dict[str, dict[str, str]] = {
    "seasons": SEASON,
    "styles": STYLE,
    "colors": COLOR,
    "top_fits": TOP_FIT,
    "pants_fits": PANTS_FIT,
    "top_lengths": TOP_LENGTH,
    "sleeves": SLEEVE,
    # 채팅 분석기는 상·하의를 구분하지 않은 통합 핏 조건을 사용한다.
    "fits": {**TOP_FIT, **PANTS_FIT},
    "necklines": NECKLINE,
}

#: 번역이 정확하지 않은 항목 (카테고리, 코드). 어휘를 정리할 때의 작업 목록이다.
APPROXIMATE: frozenset[tuple[str, str]] = frozenset(
    {
        ("top_fits", "loose"),
        ("top_lengths", "short"),
        ("pants_fits", "semi_wide"),
        ("pants_fits", "slacks"),
        ("pants_fits", "bootcut"),
        ("pants_fits", "jogger"),
        *(
            ("colors", code)
            for code in (
                "charcoal", "olive", "carmel", "denim_blue", "light_pink",
                "rose", "mauve", "peach", "coral", "light_blue", "mint",
                "burgundy", "silver", "gold",
            )
        ),
    }
)

#: 추구미 카테고리 → 아이템 태그 필드명. 검색 필터를 만들 때 쓴다.
TAG_FIELD: dict[str, str] = {
    "seasons": "season",
    "styles": "style",
    "colors": "color",
    "top_fits": "fit",
    "pants_fits": "fit",
    "top_lengths": "length",
    "sleeves": "sleeve",
    "fits": "fit",
}

_DIRECT_VALUES: dict[str, frozenset[str]] = {
    "season": frozenset({"봄", "여름", "가을", "겨울", "간절기"}),
    "style": frozenset(
        {
            "캐주얼", "포멀", "미니멀", "스트릿", "스포티", "러블리",
            "페미닌", "시크", "빈티지", "아웃도어", "댄디", "아메카지",
            "트렌디", "리조트", "베이직",
        }
    ),
    "color": frozenset(
        {
            "화이트", "블랙", "그레이", "네이비", "블루", "스카이블루",
            "레드", "핑크", "오렌지", "옐로우", "그린", "카키",
            "브라운", "베이지", "아이보리", "퍼플", "멀티",
        }
    ),
    "fit": frozenset({"오버핏", "레귤러핏", "슬림핏", "와이드핏"}),
    "length": frozenset({"크롭", "기본", "롱"}),
    "sleeve": frozenset({"반팔", "긴팔", "민소매"}),
}

#: 상의/하의 구분이 있는 카테고리는 그 대분류에만 적용해야 한다.
#: (상의 선호 '슬림핏'을 하의에까지 걸면 사용자가 고르지 않은 제약이 생긴다)
CATEGORY_SCOPE: dict[str, str] = {
    "top_fits": "상의",
    "top_lengths": "상의",
    "sleeves": "상의",
    "pants_fits": "하의",
    "pants_lengths": "하의",
    "skirt_lengths": "하의",
    "skirt_types": "하의",
}


@dataclass(frozen=True)
class Translation:
    """번역 결과. 반영된 값과 반영되지 못한 값을 함께 들고 다닌다."""

    #: 태그 필드명 → 라벨 집합 (검색 필터에 그대로 쓸 수 있는 형태)
    tags: dict[str, set[str]] = field(default_factory=dict)
    #: 대응 라벨이 없어 검색에 반영하지 못한 (카테고리, 코드)
    unmapped: tuple[tuple[str, str], ...] = ()
    #: 근사 매핑으로 반영한 (카테고리, 코드)
    approximate: tuple[tuple[str, str], ...] = ()

    def labels(self, tag_field: str) -> set[str]:
        return self.tags.get(tag_field, set())


def translate(selection: dict[str, list[str]] | None) -> Translation:
    """추구미 한쪽(preferred 또는 avoided)을 태그 라벨로 옮긴다.

    입력은 `{"styles": ["minimal"], "top_fits": ["slim"], ...}` 형태
    (users/services/pursuit.py의 payload 한 겹).
    """
    tags: dict[str, set[str]] = {}
    unmapped: list[tuple[str, str]] = []
    approximate: list[tuple[str, str]] = []

    for category, codes in (selection or {}).items():
        table = _TABLES.get(category)
        tag_field = TAG_FIELD.get(category)
        for code in codes or []:
            label = table.get(code) if table is not None else None
            if (
                label is None
                and tag_field is not None
                and code in _DIRECT_VALUES.get(tag_field, frozenset())
            ):
                label = code
            if table is None or tag_field is None or label is None:
                # 번역할 표가 없거나(스커트타입 등) 표에 그 코드가 없다.
                unmapped.append((category, code))
                continue
            tags.setdefault(tag_field, set()).add(label)
            if (category, code) in APPROXIMATE:
                approximate.append((category, code))

    return Translation(
        tags=tags,
        unmapped=tuple(unmapped),
        approximate=tuple(approximate),
    )
