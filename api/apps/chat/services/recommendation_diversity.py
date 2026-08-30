"""검증 완료 후보에서 사용자가 체감할 수 있는 코디 차이만 남긴다."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Protocol, TypeVar

from apps.recommend.services.outfit_types import (
    OutfitComposition,
    OutfitItem,
)
from apps.recommend.services.outfit_slots import (
    CORE_DIVERSITY_SLOTS,
    canonical_slots,
    outfit_item_slot,
)

DEFAULT_CORE_DIVERSITY_SLOTS = CORE_DIVERSITY_SLOTS


class CandidateWithComposition(Protocol):
    composition: OutfitComposition


CandidateT = TypeVar("CandidateT", bound=CandidateWithComposition)


def _item_slot(item: OutfitItem) -> str | None:
    return outfit_item_slot(item)


def _core_fingerprint(
    candidate: CandidateWithComposition,
    *,
    diversity_slots: frozenset[str],
) -> tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...]:
    identities: dict[str, list[tuple[str, str, str]]] = {
        slot: [] for slot in diversity_slots
    }
    for item in candidate.composition.items:
        slot = _item_slot(item)
        if slot in identities:
            identities[slot].append(item.identity)
    return tuple(
        (slot, tuple(sorted(identities[slot]))) for slot in sorted(diversity_slots)
    )


def select_diverse_candidates(
    candidates: Sequence[CandidateT],
    *,
    diversity_slots: Collection[str] = DEFAULT_CORE_DIVERSITY_SLOTS,
    limit: int = 3,
) -> tuple[CandidateT, ...]:
    """순위를 보존하며 핵심 슬롯 구성이 겹치지 않는 후보를 최대 ``limit``개 남긴다.

    첫 후보는 핵심 슬롯을 분류하지 못한 경우에도 항상 남긴다. ``diversity_slots``는
    호출자가 다른 다양성 정책을 쓰는 경우 슬롯 집합을 주입할 수 있다.
    """

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit은 1 이상의 정수여야 합니다.")
    if not candidates:
        return ()

    normalized_slots = canonical_slots(diversity_slots)
    if not normalized_slots:
        raise ValueError("다양성 판정 슬롯이 하나 이상 필요합니다.")

    selected = [candidates[0]]
    seen = {
        _core_fingerprint(
            candidates[0],
            diversity_slots=normalized_slots,
        )
    }
    for candidate in candidates[1:]:
        if len(selected) >= limit:
            break
        fingerprint = _core_fingerprint(
            candidate,
            diversity_slots=normalized_slots,
        )
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        selected.append(candidate)
    return tuple(selected)
