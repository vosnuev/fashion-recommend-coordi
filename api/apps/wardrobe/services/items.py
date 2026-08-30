"""옷장 아이템 묶음에서 뽑아내는 파생 값.

룩북과 캘린더가 같은 판단을 해야 해서 여기 둔다. 두 앱 모두 옷장을 import 하지만
서로를 import 하지는 않는다(룩북 → 캘린더 한 방향뿐이라 반대편에 두면 순환된다).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.wardrobe.models import WardrobeItem


def skipped_categories_for(items: Sequence[WardrobeItem]) -> list[str]:
    """입은 옷이 이미 덮고 있는 대분류 — 사진 등록에서 제외할 부위.

    판정 단위를 대분류(상의/하의/아우터…)로 둔 것은 사용자가 말한 "겹치는 부위"가
    소분류(티셔츠/셔츠)가 아니라 부위이기 때문이다. 상의를 하나 골라 뒀으면 사진 속
    다른 상의도 등록하지 않는다.
    """

    return sorted({item.category_large for item in items if item.category_large})
