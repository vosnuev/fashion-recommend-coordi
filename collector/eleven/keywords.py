"""11번가용 추천 분류 검색어와 날짜별 균형 순환."""

from __future__ import annotations

import sys
from collections import defaultdict, deque
from collections.abc import Iterator
from datetime import date
from pathlib import Path

_COLLECTOR_ROOT = str(Path(__file__).resolve().parent.parent)
if _COLLECTOR_ROOT not in sys.path:
    sys.path.insert(0, _COLLECTOR_ROOT)

from naver.keywords import KEYWORD_MAP, KeywordEntry, count_keywords, iter_keywords

_ROTATION_EPOCH = date(2026, 1, 1)


def _balanced_keyword_list() -> list[KeywordEntry]:
    """대분류별 큐를 번갈아 꺼내 특정 분류가 연속되지 않게 한다."""
    category_queues: dict[str, deque[KeywordEntry]] = defaultdict(deque)
    category_order: list[str] = []
    for entry in iter_keywords():
        if entry.category_large not in category_queues:
            category_order.append(entry.category_large)
        category_queues[entry.category_large].append(entry)

    balanced: list[KeywordEntry] = []
    while any(category_queues.values()):
        for category_large in category_order:
            queue = category_queues[category_large]
            if queue:
                balanced.append(queue.popleft())
    return balanced


def iter_daily_keywords(
    run_date: date,
    *,
    expected_keywords_per_day: int,
) -> Iterator[KeywordEntry]:
    """날짜마다 시작점을 이동한 대분류 균형 키워드 순서를 반환한다.

    전체 목록을 반환하므로 중복 상품으로 일일 목표량이 부족하면 다음 키워드까지
    계속 진행할 수 있다. 시작점은 예상 일일 키워드 수만큼 이동한다.
    """
    keywords = _balanced_keyword_list()
    if not keywords:
        return

    step = max(1, expected_keywords_per_day)
    elapsed_days = (run_date - _ROTATION_EPOCH).days
    start = (elapsed_days * step) % len(keywords)
    yield from keywords[start:]
    yield from keywords[:start]


__all__ = [
    "KEYWORD_MAP",
    "KeywordEntry",
    "count_keywords",
    "iter_daily_keywords",
    "iter_keywords",
]
