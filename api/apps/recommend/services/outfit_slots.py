"""추천 전 경로가 공유하는 코디 슬롯 정규화 계약."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

TOP = "TOP"
BOTTOM = "BOTTOM"
OUTER = "OUTER"
DRESS = "DRESS"
FOOTWEAR = "FOOTWEAR"
ACCESSORY = "ACCESSORY"

CORE_DIVERSITY_SLOTS = frozenset({TOP, BOTTOM, OUTER, DRESS})
BODY_COVERING_SLOTS = frozenset({TOP, BOTTOM, DRESS})
OPTIONAL_OUTFIT_SLOTS = frozenset({OUTER, FOOTWEAR, ACCESSORY})
DUPLICATE_SLOT_ORDER = (TOP, BOTTOM, OUTER, DRESS, FOOTWEAR, ACCESSORY)

_ALIASES_BY_SLOT = {
    TOP: (
        "TOP",
        "UPPER",
        "INNER",
        "MID",
        "LAYER",
        "SHIRT",
        "BLOUSE",
        "KNIT",
        "SWEATER",
        "TEE",
        "상의",
        "기본상의",
        "기본_상의",
        "레이어드상의",
        "레이어드_상의",
        "이너",
        "셔츠",
        "블라우스",
        "니트",
    ),
    BOTTOM: (
        "BOTTOM",
        "LOWER",
        "PANTS",
        "TROUSER",
        "SKIRT",
        "하의",
        "바지",
        "치마",
    ),
    OUTER: (
        "OUTER",
        "OUTERWEAR",
        "JACKET",
        "COAT",
        "CARDIGAN",
        "아우터",
        "겉옷",
        "재킷",
        "코트",
        "가디건",
    ),
    DRESS: (
        "DRESS",
        "ONEPIECE",
        "ONE_PIECE",
        "SET",
        "원피스",
        "원피스/세트",
        "세트",
    ),
    FOOTWEAR: (
        "FOOTWEAR",
        "SHOE",
        "SHOES",
        "SNEAKER",
        "BOOT",
        "BOOTS",
        "신발",
        "슈즈",
        "운동화",
    ),
    ACCESSORY: (
        "ACCESSORY",
        "ACCESSORIES",
        "BAG",
        "액세서리",
        "가방",
        "모자",
        "주얼리",
    ),
}


def normalize_slot(value: str) -> str:
    return value.strip().replace("-", "_").replace(" ", "_").upper()


_SLOT_ALIASES = {
    normalize_slot(alias): slot
    for slot, aliases in _ALIASES_BY_SLOT.items()
    for alias in aliases
}


def canonical_slot(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = normalize_slot(value)
    if canonical := _SLOT_ALIASES.get(normalized):
        return canonical
    # 실제 slot_id는 ``TOP:<template-id>`` 형태가 많다.
    return _SLOT_ALIASES.get(normalized.split(":", 1)[0])


class SlotBearingItem(Protocol):
    slot_id: str
    layer_role: str
    category_large: str
    payload: Mapping[str, object]


def outfit_item_slot(item: SlotBearingItem) -> str | None:
    """레이어 역할·카테고리·slot_id를 같은 우선순위로 해석한다."""

    for value in (
        item.layer_role,
        item.payload.get("layer_role"),
        item.category_large,
        item.payload.get("category_large"),
        item.slot_id,
    ):
        if slot := canonical_slot(value):
            return slot
    return None


def canonical_slots(values: Iterable[object]) -> frozenset[str]:
    return frozenset(
        slot for value in values if (slot := canonical_slot(value)) is not None
    )


def is_required_outfit_slot(*values: object) -> bool:
    """신체를 구성하는 슬롯은 필수, 마무리 슬롯은 선택으로 판정한다.

    알 수 없는 값은 기존 계약처럼 보수적으로 필수로 둔다.
    """

    for value in values:
        if slot := canonical_slot(value):
            return slot not in OPTIONAL_OUTFIT_SLOTS
    return True
