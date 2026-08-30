"""페르소나별 유효 Top-K에서 중복을 줄인 최종 후보를 고른다."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from apps.chat.services.stylist_personas import EXPECTED_PERSONA_ORDER
from apps.chat.services.stylist_recommendation_pipeline import (
    PersonaRecommendationCandidates,
    RankedValidatedCandidate,
)
from apps.recommend.services.outfit_types import OutfitComposition, OutfitItem
from apps.recommend.services.outfit_slots import (
    DRESS,
    DUPLICATE_SLOT_ORDER,
    canonical_slot,
    normalize_slot,
    outfit_item_slot,
)

# 기본 실행은 validator를 통과한 서로 다른 후보가 있으면 persona 차이를 우선한다.
# 명시적으로 더 작은 값을 주입하는 테스트·운영 정책에서는 품질 guard를 쓸 수 있다.
MAX_DIVERSITY_SCORE_DROP = 1_000.0
HIGH_ITEM_OVERLAP_THRESHOLD = 0.75
HIGH_ITEM_OVERLAP_MIN_SHARED = 3
_MAJOR_SLOT_ORDER = DUPLICATE_SLOT_ORDER


class StylistDuplicateResolutionError(ValueError):
    """중복 검사 입력이나 선택 범위가 계약과 맞지 않을 때 발생한다."""


class DuplicateKind(StrEnum):
    EXACT = "EXACT"
    HIGH_ITEM_OVERLAP = "HIGH_ITEM_OVERLAP"
    MAJOR_SLOTS = "MAJOR_SLOTS"


class DiversityReasonCode(StrEnum):
    DUPLICATE_REPLACED = "STYLIST_DUPLICATE_REPLACED"
    DUPLICATE_ALLOWED_QUALITY_GUARD = "STYLIST_DUPLICATE_ALLOWED_QUALITY_GUARD"
    DUPLICATE_ALLOWED_CANDIDATE_EXHAUSTED = (
        "STYLIST_DUPLICATE_ALLOWED_CANDIDATE_EXHAUSTED"
    )
    DUPLICATE_ALLOWED_NO_DISTINCT_CANDIDATE = (
        "STYLIST_DUPLICATE_ALLOWED_NO_DISTINCT_CANDIDATE"
    )


@dataclass(frozen=True)
class DuplicateMatch:
    """앞서 확정된 스타일리스트 한 명과의 중복 판정."""

    persona_id: str
    kind: DuplicateKind
    matching_major_slots: tuple[str, ...] = ()


@dataclass(frozen=True)
class StylistCandidateSelection:
    """중복·품질 검토를 마친 스타일리스트 한 명의 최종 저장 후보."""

    source: PersonaRecommendationCandidates
    selected: RankedValidatedCandidate
    selected_rank: int
    reason_code: DiversityReasonCode | None
    duplicate_matches: tuple[DuplicateMatch, ...]
    score_drop: float
    allowed_duplicate_slots: tuple[str, ...]

    @property
    def persona_id(self) -> str:
        return self.source.persona_id

    @property
    def validated_reason_codes(self) -> tuple[str, ...]:
        codes = list(self.selected.reason_codes)
        if self.reason_code is not None:
            codes.append(self.reason_code.value)
        return tuple(dict.fromkeys(codes))

    def snapshot(self) -> dict[str, object]:
        """아이템 ID 없이 결과 전략 스냅샷에 합칠 수 있는 선택 근거."""

        return {
            "selected_rank": self.selected_rank,
            "reason_code": self.reason_code.value if self.reason_code else None,
            "score_drop": self.score_drop,
            "allowed_duplicate_slots": list(self.allowed_duplicate_slots),
            "duplicate_matches": [
                {
                    "persona_id": match.persona_id,
                    "kind": match.kind.value,
                    "matching_major_slots": list(match.matching_major_slots),
                }
                for match in self.duplicate_matches
            ],
        }


@dataclass(frozen=True)
class StylistDuplicateResolution:
    run_id: str
    selections: tuple[StylistCandidateSelection, ...]
    max_score_drop: float
    allowed_duplicate_slots: tuple[str, ...]

    def get(self, persona_id: str) -> StylistCandidateSelection:
        return next(
            selection
            for selection in self.selections
            if selection.persona_id == persona_id
        )


@dataclass(frozen=True)
class _AllowedSlots:
    normalized: frozenset[str]
    major: frozenset[str]
    display: tuple[str, ...]

    def contains(self, slot: str) -> bool:
        normalized = _normalize_slot(slot)
        major = _major_slot(slot)
        return normalized in self.normalized or (
            major is not None and major in self.major
        )

    def contains_item(self, item: OutfitItem) -> bool:
        canonical = outfit_item_slot(item)
        return self.contains(item.slot_id) or (
            canonical is not None and canonical in self.major
        )


def _normalize_slot(value: str) -> str:
    return normalize_slot(value)


def _major_slot(value: str) -> str | None:
    return canonical_slot(value)


def _allowed_slots(values: tuple[str, ...]) -> _AllowedSlots:
    normalized: list[str] = []
    major: list[str] = []
    display: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise StylistDuplicateResolutionError(
                "중복 허용 슬롯은 비어 있지 않은 문자열이어야 합니다."
            )
        raw = _normalize_slot(value)
        canonical = _major_slot(value)
        key = canonical or raw
        if key in display:
            raise StylistDuplicateResolutionError(
                "중복 허용 슬롯은 중복될 수 없습니다."
            )
        normalized.append(raw)
        if canonical:
            major.append(canonical)
        display.append(key)
    return _AllowedSlots(
        normalized=frozenset(normalized),
        major=frozenset(major),
        display=tuple(display),
    )


def _identity(item: OutfitItem) -> tuple[str, str, str]:
    return item.source_type.value, item.source_collection, item.source_id


def _all_item_ids(
    composition: OutfitComposition,
    allowed: _AllowedSlots,
) -> frozenset[tuple[str, str, str]]:
    return frozenset(
        _identity(item)
        for item in composition.items
        if not allowed.contains_item(item)
    )


def _major_items(
    composition: OutfitComposition,
    allowed: _AllowedSlots,
) -> dict[str, set[tuple[str, str, str]]]:
    result: dict[str, set[tuple[str, str, str]]] = {}
    for item in composition.items:
        if allowed.contains_item(item):
            continue
        major = outfit_item_slot(item)
        if major is not None:
            result.setdefault(major, set()).add(_identity(item))
    return result


def classify_duplicate(
    left: OutfitComposition,
    right: OutfitComposition,
    *,
    allowed_duplicate_slots: tuple[str, ...] = (),
) -> tuple[DuplicateKind, tuple[str, ...]] | None:
    """고정 슬롯을 제외한 완전 중복과 체감상 핵심 슬롯 중복을 판정한다."""

    allowed = _allowed_slots(allowed_duplicate_slots)
    left_ids = _all_item_ids(left, allowed)
    right_ids = _all_item_ids(right, allowed)
    if left_ids and left_ids == right_ids:
        return DuplicateKind.EXACT, ()

    left_major = _major_items(left, allowed)
    right_major = _major_items(right, allowed)
    matching = tuple(
        slot
        for slot in _MAJOR_SLOT_ORDER
        if left_major.get(slot, set()) & right_major.get(slot, set())
    )
    # 원피스는 상·하의를 함께 대체하므로 같은 원피스 자체가 핵심 중복이다.
    if len(matching) >= 3 or DRESS in matching:
        return DuplicateKind.MAJOR_SLOTS, matching

    # 상의 하나만 달라지고 하의·신발·가방·액세서리가 같은 코디처럼 사용자가
    # 사실상 동일하게 느끼는 결과도 중복으로 본다. 작은 코디의 75% 이상이 겹치고
    # 공통 아이템이 최소 3개일 때만 적용해 1~2개 우연한 공유는 허용한다.
    shared_count = len(left_ids & right_ids)
    smaller_size = min(len(left_ids), len(right_ids))
    if (
        shared_count >= HIGH_ITEM_OVERLAP_MIN_SHARED
        and smaller_size > 0
        and shared_count / smaller_size >= HIGH_ITEM_OVERLAP_THRESHOLD
    ):
        return DuplicateKind.HIGH_ITEM_OVERLAP, ()
    return None


class StylistDuplicateResolver:
    """미니멀→실험형→실용형 순서로 중복 없는 첫 품질 후보를 선택한다."""

    def __init__(self, *, max_score_drop: float = MAX_DIVERSITY_SCORE_DROP) -> None:
        if (
            isinstance(max_score_drop, bool)
            or not isinstance(max_score_drop, (int, float))
            or not math.isfinite(max_score_drop)
            or max_score_drop < 0
        ):
            raise StylistDuplicateResolutionError(
                "중복 교체 허용 점수 하락폭은 0 이상의 유한한 숫자여야 합니다."
            )
        self.max_score_drop = float(max_score_drop)

    def resolve(
        self,
        results: tuple[PersonaRecommendationCandidates, ...],
        *,
        allowed_duplicate_slots: tuple[str, ...] = (),
    ) -> StylistDuplicateResolution:
        ordered, run_id = self._validate_results(results)
        allowed = _allowed_slots(allowed_duplicate_slots)
        selections: list[StylistCandidateSelection] = []

        for result in ordered:
            first = result.ranked_candidates[0]
            first_matches = self._matches(first, selections, allowed)
            if not first_matches:
                selections.append(
                    self._selection(
                        source=result,
                        selected=first,
                        selected_rank=1,
                        reason_code=None,
                        duplicate_matches=(),
                        score_drop=0.0,
                        allowed=allowed,
                    )
                )
                continue

            replacement: tuple[int, RankedValidatedCandidate, float] | None = None
            distinct_but_too_low = False
            smallest_blocked_drop: float | None = None
            for rank, candidate in enumerate(result.ranked_candidates[1:], start=2):
                if self._matches(candidate, selections, allowed):
                    continue
                score_drop = max(
                    first.evaluation.total_score - candidate.evaluation.total_score,
                    0.0,
                )
                if score_drop <= self.max_score_drop:
                    replacement = rank, candidate, score_drop
                    break
                distinct_but_too_low = True
                smallest_blocked_drop = (
                    score_drop
                    if smallest_blocked_drop is None
                    else min(smallest_blocked_drop, score_drop)
                )

            if replacement is not None:
                rank, candidate, score_drop = replacement
                selections.append(
                    self._selection(
                        source=result,
                        selected=candidate,
                        selected_rank=rank,
                        reason_code=DiversityReasonCode.DUPLICATE_REPLACED,
                        duplicate_matches=first_matches,
                        score_drop=score_drop,
                        allowed=allowed,
                    )
                )
                continue

            if distinct_but_too_low:
                reason_code = DiversityReasonCode.DUPLICATE_ALLOWED_QUALITY_GUARD
            elif len(result.ranked_candidates) == 1:
                reason_code = DiversityReasonCode.DUPLICATE_ALLOWED_CANDIDATE_EXHAUSTED
            else:
                reason_code = (
                    DiversityReasonCode.DUPLICATE_ALLOWED_NO_DISTINCT_CANDIDATE
                )
            selections.append(
                self._selection(
                    source=result,
                    selected=first,
                    selected_rank=1,
                    reason_code=reason_code,
                    duplicate_matches=first_matches,
                    score_drop=(
                        smallest_blocked_drop
                        if reason_code
                        is DiversityReasonCode.DUPLICATE_ALLOWED_QUALITY_GUARD
                        and smallest_blocked_drop is not None
                        else 0.0
                    ),
                    allowed=allowed,
                )
            )

        return StylistDuplicateResolution(
            run_id=run_id,
            selections=tuple(selections),
            max_score_drop=self.max_score_drop,
            allowed_duplicate_slots=allowed.display,
        )

    @staticmethod
    def _selection(
        *,
        source: PersonaRecommendationCandidates,
        selected: RankedValidatedCandidate,
        selected_rank: int,
        reason_code: DiversityReasonCode | None,
        duplicate_matches: tuple[DuplicateMatch, ...],
        score_drop: float,
        allowed: _AllowedSlots,
    ) -> StylistCandidateSelection:
        return StylistCandidateSelection(
            source=source,
            selected=selected,
            selected_rank=selected_rank,
            reason_code=reason_code,
            duplicate_matches=duplicate_matches,
            score_drop=round(score_drop, 6),
            allowed_duplicate_slots=allowed.display,
        )

    @staticmethod
    def _matches(
        candidate: RankedValidatedCandidate,
        selections: list[StylistCandidateSelection],
        allowed: _AllowedSlots,
    ) -> tuple[DuplicateMatch, ...]:
        matches: list[DuplicateMatch] = []
        for selection in selections:
            classified = classify_duplicate(
                candidate.candidate.composition,
                selection.selected.candidate.composition,
                allowed_duplicate_slots=allowed.display,
            )
            if classified is None:
                continue
            kind, matching_slots = classified
            matches.append(
                DuplicateMatch(
                    persona_id=selection.persona_id,
                    kind=kind,
                    matching_major_slots=matching_slots,
                )
            )
        return tuple(matches)

    @staticmethod
    def _validate_results(
        results: tuple[PersonaRecommendationCandidates, ...],
    ) -> tuple[tuple[PersonaRecommendationCandidates, ...], str]:
        if not results:
            raise StylistDuplicateResolutionError(
                "중복 검사할 스타일리스트 결과가 없습니다."
            )
        if len(results) > len(EXPECTED_PERSONA_ORDER):
            raise StylistDuplicateResolutionError(
                "스타일리스트 결과는 최대 3개까지 검사할 수 있습니다."
            )
        persona_ids = [result.persona_id for result in results]
        if len(persona_ids) != len(set(persona_ids)):
            raise StylistDuplicateResolutionError(
                "같은 스타일리스트 결과를 중복 검사할 수 없습니다."
            )
        if any(persona_id not in EXPECTED_PERSONA_ORDER for persona_id in persona_ids):
            raise StylistDuplicateResolutionError(
                "지원하지 않는 스타일리스트 결과가 포함됐습니다."
            )
        scopes = {
            (
                result.generated.run_id,
                result.generated.session_id,
                result.generated.identity_id,
                result.generated.response_mode,
                result.generated.mode,
            )
            for result in results
        }
        if len(scopes) != 1:
            raise StylistDuplicateResolutionError(
                "서로 다른 ChatRun·세션·사용자의 결과를 함께 검사할 수 없습니다."
            )
        execution_ids = [result.persona_execution_id for result in results]
        if len(execution_ids) != len(set(execution_ids)):
            raise StylistDuplicateResolutionError(
                "같은 스타일리스트 실행을 중복 검사할 수 없습니다."
            )
        for result in results:
            if not result.ranked_candidates:
                raise StylistDuplicateResolutionError(
                    "스타일리스트 유효 후보가 비어 있습니다."
                )
            if len(result.ranked_candidates) > 3:
                raise StylistDuplicateResolutionError(
                    "스타일리스트 중복 검사는 유효 Top-3만 지원합니다."
                )
            if result.strategy_result.persona_id != result.persona_id:
                raise StylistDuplicateResolutionError(
                    "전략 실행 결과의 스타일리스트 ID가 다릅니다."
                )
            ordinals = [row.candidate.ordinal for row in result.ranked_candidates]
            if len(ordinals) != len(set(ordinals)):
                raise StylistDuplicateResolutionError(
                    "스타일리스트 Top-K 후보 ordinal은 중복될 수 없습니다."
                )
            if any(
                not row.candidate.validation.valid for row in result.ranked_candidates
            ):
                raise StylistDuplicateResolutionError(
                    "Validator를 통과하지 않은 후보는 중복 검사에 사용할 수 없습니다."
                )
            available = {
                candidate.ordinal: candidate
                for candidate in result.generated.candidates
            }
            if any(
                available.get(row.candidate.ordinal) != row.candidate
                or row.evaluation.candidate_ordinal != row.candidate.ordinal
                for row in result.ranked_candidates
            ):
                raise StylistDuplicateResolutionError(
                    "페르소나 생성 결과에 속하지 않은 후보는 중복 검사할 수 없습니다."
                )
        order = {
            persona_id: index for index, persona_id in enumerate(EXPECTED_PERSONA_ORDER)
        }
        run_id = next(iter(scopes))[0]
        return tuple(sorted(results, key=lambda row: order[row.persona_id])), run_id
