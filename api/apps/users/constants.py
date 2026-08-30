"""사용자 개인화 설정 상수.

화면 카테고리 목록 + 정렬 순서가 여기서 단일 진실 공급원(Single Source of Truth).
- `PREFERENCE_CATEGORIES`: 화면에 보일 11개 카테고리 (key, label)
- DB 마스터, 시리얼라이저, 서비스, 응답 직렬화 모두 이 상수를 참조.

새 카테고리 추가 시: (1) 여기에 추가 (2) PreferenceOption 마이그레이션에 시드 추가.
"""

from __future__ import annotations

BUDGET_CATEGORIES: tuple[str, ...] = (
    "상의",
    "하의",
    "아우터",
    "원피스/세트",
    "신발",
    "가방",
    "액세서리",
)

DEFAULT_CATEGORY_BUDGETS: dict[str, int] = {
    "상의": 50_000,
    "하의": 50_000,
    "아우터": 150_000,
    "원피스/세트": 50_000,
    "신발": 100_000,
    "가방": 200_000,
    "액세서리": 50_000,
}


def effective_category_budgets(overrides: dict[str, int] | None) -> dict[str, int]:
    """시스템 기본값 위에 사용자가 직접 설정한 값만 덮어쓴다."""
    return DEFAULT_CATEGORY_BUDGETS | (overrides or {})

# 11개 카테고리 — 프론트 화면 순서와 1:1 매핑
# key: JSON payload에 쓰일 키, label: 화면/관리자에 보일 한글 이름
PREFERENCE_CATEGORIES: list[tuple[str, str]] = [
    ("seasons",       "계절"),
    ("styles",        "스타일"),
    ("colors",        "색상"),
    ("necklines",     "넥라인"),
    ("top_fits",      "상의핏"),
    ("top_lengths",   "상의기장"),
    ("sleeves",       "소매길이"),
    ("pants_fits",    "팬츠핏"),
    ("pants_lengths", "팬츠기장"),
    ("skirt_lengths", "스커트기장"),
    ("skirt_types",   "스커트타입"),
]


def category_keys() -> list[str]:
    """카테고리 key만 추출 (preferred/avoided dict 키 만들 때 사용)."""
    return [k for k, _ in PREFERENCE_CATEGORIES]
