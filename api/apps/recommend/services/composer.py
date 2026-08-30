"""모드별 Composer가 공유하는 결정적 조합 엔진.

이 모듈은 슬롯별 후보를 출처 정책에 따라 조합하는 기계적 책임만
담당한다. 옷장 기반과 추구미 기반의 상세 정책은 각 전용 Composer가
결정하고, 완성된 조합의 적합성은 OutfitValidator가 다시 검사한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from apps.recommend.services import principle_rules
from apps.recommend.services.item_retriever import (
    ItemCandidate,
    ItemRetrievalResult,
    ItemSource,
    TemplateItem,
)
from apps.recommend.services.outfit_types import (
    OutfitComposition,
    OutfitItem,
    OutfitSlot,
    RecommendationMode,
)
from apps.recommend.services.outfit_slots import is_required_outfit_slot
from apps.recommend.services.qdrant import GOLDEN_ITEM_COLLECTION


class CompositionError(RuntimeError):
    """코디 구성 요청을 안전하게 처리할 수 없는 경우."""


@dataclass(frozen=True)
class CompositionPolicy:
    mode: RecommendationMode
    source_priority: tuple[ItemSource, ...]
    composition_count: int = 3
    total_budget: int | None = None
    category_budgets: dict[str, int] | None = None
    require_image: bool = True
    candidates_per_slot: int = 6
    minimum_source_counts: tuple[tuple[ItemSource, int], ...] = ()
    #: 이 코디의 골든셋 스타일. 원칙 대조 범위를 그 스타일로 좁힌다.
    principle_styles: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompositionRequest:
    """기존 단일 조합 호출부를 위한 호환 요청."""

    mode: RecommendationMode
    slot_results: tuple[ItemRetrievalResult, ...]
    total_budget: int | None = None
    category_budgets: dict[str, int] | None = None
    require_image: bool = True


@dataclass(frozen=True)
class _PartialComposition:
    items: tuple[OutfitItem, ...] = ()
    used: frozenset[tuple[str, str, str]] = frozenset()
    missing_slot_ids: tuple[str, ...] = ()
    total_product_price: int = 0
    priority_cost: int = 0
    similarity_sum: float = 0.0
    #: 지금까지 담은 아이템으로 판정한 원칙 어긋남 수. 적을수록 좋다.
    principle_violations: int = 0
    #: 슬롯 -> 속성. 원칙 판정용이며 후보를 담을 때 한 번만 뽑는다.
    slot_attributes: dict[str, dict[str, str]] = field(default_factory=dict)
    #: 치환이 골든 원본의 속성을 바꾼 누적 횟수.
    principle_drift: int = 0
    #: 계절 판정에 쓸 후보 payload. 조합이 완성돼야 충돌을 셀 수 있다.
    season_payloads: tuple[dict[str, Any], ...] = ()


# 이전 호출부가 단계적 마이그레이션 할 수 있도록 이름을 유지한다.
ComposedItem = OutfitItem
CompositionResult = OutfitComposition


def _payload_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _image_ref(payload: dict[str, Any]) -> str:
    for key in ("s3_key", "image_s3_key", "image_url"):
        if value := payload.get(key):
            return str(value)
    return ""


def _slot_id(template: TemplateItem) -> str:
    explicit = _payload_text(template.payload, "slot_id")
    if explicit:
        return explicit
    layer_role = _payload_text(template.payload, "layer_role")
    return f"{layer_role}:{template.point_id}" if layer_role else template.point_id


def _outfit_slot(template: TemplateItem) -> OutfitSlot:
    category_large = _payload_text(template.payload, "category_large")
    layer_role = _payload_text(template.payload, "layer_role")
    return OutfitSlot(
        slot_id=_slot_id(template),
        template_point_id=template.point_id,
        category_large=category_large,
        category_small=_payload_text(template.payload, "category_small"),
        layer_role=layer_role,
        required=is_required_outfit_slot(category_large, layer_role),
    )


def _template_candidate(template: TemplateItem) -> ItemCandidate:
    """대체 후보가 없을 때만 사용할 원본 골든셋 아이템."""

    return ItemCandidate(
        point_id=template.point_id,
        source_type=ItemSource.GOLDENSET_ITEM,
        source_id=template.point_id,
        source_collection=GOLDEN_ITEM_COLLECTION,
        score=1.0,
        reasons=("선정된 골든 코디의 원본 아이템",),
        payload=template.payload,
    )


def _candidate_identity(candidate: ItemCandidate) -> tuple[str, str, str]:
    return (
        candidate.source_type.value,
        candidate.source_collection,
        candidate.source_id,
    )


def _selection_reason(mode: RecommendationMode, source: ItemSource) -> str:
    if mode is RecommendationMode.WARDROBE_BASED:
        return {
            ItemSource.WARDROBE: "옷장 기반: 보유 아이템 우선",
            ItemSource.GOLDENSET_ITEM: "옷장 부족: 골든셋 참고 아이템으로 보완",
            ItemSource.PRODUCT: "옷장 기반 정책 외 상품 선택",
        }[source]
    return {
        ItemSource.WARDROBE: "신규 아이템 추천: 기존 보유 아이템 활용",
        ItemSource.PRODUCT: "신규 아이템 추천: 구매 가능한 상품 추가",
        ItemSource.GOLDENSET_ITEM: "신규 아이템 정책 외 골든셋 선택",
    }[source]


class CompositionEngine:
    """슬롯 후보를 beam search로 조합해 서로 다른 코디를 반환한다."""

    def compose(
        self,
        slot_results: tuple[ItemRetrievalResult, ...],
        *,
        policy: CompositionPolicy,
    ) -> tuple[OutfitComposition, ...]:
        self._validate(slot_results, policy)
        source_rank = {
            source: rank for rank, source in enumerate(policy.source_priority)
        }
        slots = tuple(_outfit_slot(result.template) for result in slot_results)
        states = [_PartialComposition()]
        beam_width = max(policy.composition_count * 12, 24)

        for slot_result in slot_results:
            candidates = self._ordered_candidates(
                slot_result,
                source_rank=source_rank,
                limit=policy.candidates_per_slot,
            )
            expanded: list[_PartialComposition] = []
            for state in states:
                additions = self._add_slot_candidates(
                    state,
                    slot_result=slot_result,
                    candidates=candidates,
                    source_rank=source_rank,
                    policy=policy,
                )
                if additions:
                    expanded.extend(additions)
                elif slot_result.pinned_candidate is None:
                    expanded.append(
                        _PartialComposition(
                            items=state.items,
                            used=state.used,
                            missing_slot_ids=(
                                *state.missing_slot_ids,
                                _slot_id(slot_result.template),
                            ),
                            total_product_price=state.total_product_price,
                            priority_cost=state.priority_cost,
                            similarity_sum=state.similarity_sum,
                        )
                    )
            states = sorted(expanded, key=self._state_sort_key)[:beam_width]

        compositions: list[OutfitComposition] = []
        seen: set[tuple[tuple[str, str, str], ...]] = set()
        for state in sorted(states, key=self._state_sort_key):
            if not self._meets_minimum_source_counts(
                state,
                policy.minimum_source_counts,
            ):
                continue
            fingerprint = tuple(item.identity for item in state.items)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            compositions.append(
                OutfitComposition(
                    mode=policy.mode,
                    items=state.items,
                    missing_slot_ids=state.missing_slot_ids,
                    total_product_price=state.total_product_price,
                    warnings=tuple(
                        f"{slot_id}: 조건을 만족하는 후보 없음"
                        for slot_id in state.missing_slot_ids
                    ),
                    slots=slots,
                )
            )
            if len(compositions) >= policy.composition_count:
                break
        return tuple(compositions)

    @staticmethod
    def _validate(
        slot_results: tuple[ItemRetrievalResult, ...],
        policy: CompositionPolicy,
    ) -> None:
        if not slot_results:
            raise ValueError("최소 하나의 아이템 슬롯이 필요합니다.")
        if not 1 <= policy.composition_count <= 3:
            raise ValueError("composition_count는 1 이상 3 이하여야 합니다.")
        if not 1 <= policy.candidates_per_slot <= 20:
            raise ValueError("candidates_per_slot은 1 이상 20 이하여야 합니다.")
        if not policy.source_priority:
            raise ValueError("최소 하나의 아이템 출처가 필요합니다.")
        if len(policy.source_priority) != len(set(policy.source_priority)):
            raise ValueError("아이템 출처 우선순위가 중복되었습니다.")
        minimum_sources = [source for source, _ in policy.minimum_source_counts]
        if len(minimum_sources) != len(set(minimum_sources)):
            raise ValueError("최소 출처 개수 조건이 중복되었습니다.")
        for source, count in policy.minimum_source_counts:
            if source not in policy.source_priority:
                raise ValueError("최소 개수 조건의 출처가 허용 대상이 아닙니다.")
            if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                raise ValueError("최소 출처 개수는 1 이상의 정수여야 합니다.")
        if policy.total_budget is not None and (
            not isinstance(policy.total_budget, int)
            or isinstance(policy.total_budget, bool)
            or policy.total_budget < 0
        ):
            raise ValueError("total_budget은 0 이상의 정수여야 합니다.")
        if policy.category_budgets is not None and any(
            not isinstance(category, str)
            or not isinstance(amount, int)
            or isinstance(amount, bool)
            or amount < 0
            for category, amount in policy.category_budgets.items()
        ):
            raise ValueError("category_budgets는 대분류별 0 이상의 정수여야 합니다.")
        template_ids = [result.template.point_id for result in slot_results]
        if len(template_ids) != len(set(template_ids)):
            raise CompositionError("같은 템플릿 아이템 슬롯이 중복되었습니다.")

    @staticmethod
    def _ordered_candidates(
        slot_result: ItemRetrievalResult,
        *,
        source_rank: dict[ItemSource, int],
        limit: int,
    ) -> list[ItemCandidate]:
        if slot_result.pinned_candidate is not None:
            pinned = slot_result.pinned_candidate
            if pinned.source_type not in source_rank:
                raise CompositionError(
                    "고정 아이템의 출처가 현재 추천 모드에서 허용되지 않습니다."
                )
            return [pinned]

        unique: dict[tuple[str, str, str], ItemCandidate] = {}
        for candidate in (
            *slot_result.candidates,
            _template_candidate(slot_result.template),
        ):
            if candidate.source_type not in source_rank:
                continue
            identity = _candidate_identity(candidate)
            previous = unique.get(identity)
            previous_score = (
                previous.score
                if previous is not None and previous.score is not None
                else -1.0
            )
            current_score = candidate.score if candidate.score is not None else -1.0
            if previous is None or current_score > previous_score:
                unique[identity] = candidate
        return sorted(
            unique.values(),
            key=lambda candidate: (
                source_rank[candidate.source_type],
                -(candidate.score if candidate.score is not None else -1.0),
                candidate.source_collection,
                candidate.source_id,
            ),
        )[:limit]

    def _add_slot_candidates(
        self,
        state: _PartialComposition,
        *,
        slot_result: ItemRetrievalResult,
        candidates: list[ItemCandidate],
        source_rank: dict[ItemSource, int],
        policy: CompositionPolicy,
    ) -> list[_PartialComposition]:
        additions: list[_PartialComposition] = []
        for candidate in candidates:
            identity = _candidate_identity(candidate)
            if identity in state.used:
                continue
            image_ref = candidate.image_ref
            if policy.require_image and not image_ref:
                continue
            next_price = state.total_product_price
            if candidate.source_type is ItemSource.PRODUCT:
                category = _payload_text(slot_result.template.payload, "category_large")
                category_budget = (policy.category_budgets or {}).get(category)
                price = candidate.price
                if (
                    policy.total_budget is not None
                    or category_budget is not None
                ) and price is None:
                    continue
                if (
                    category_budget is not None
                    and price is not None
                    and price > category_budget
                ):
                    continue
                next_price += price or 0
                if policy.total_budget is not None and next_price > policy.total_budget:
                    continue
            slot_attributes, drift, payloads, violations = self._principle_state(
                state, slot_result.template, candidate, policy
            )
            additions.append(
                _PartialComposition(
                    items=(
                        *state.items,
                        self._to_item(
                            template=slot_result.template,
                            candidate=candidate,
                            mode=policy.mode,
                        ),
                    ),
                    used=state.used | {identity},
                    missing_slot_ids=state.missing_slot_ids,
                    total_product_price=next_price,
                    priority_cost=(
                        state.priority_cost + source_rank[candidate.source_type]
                    ),
                    similarity_sum=(
                        state.similarity_sum
                        + (candidate.score if candidate.score is not None else -1.0)
                    ),
                    principle_violations=violations,
                    principle_drift=drift,
                    season_payloads=payloads,
                    slot_attributes=slot_attributes,
                )
            )
        return additions

    @staticmethod
    def _to_item(
        *,
        template: TemplateItem,
        candidate: ItemCandidate,
        mode: RecommendationMode,
    ) -> OutfitItem:
        return OutfitItem(
            slot_id=_slot_id(template),
            template_point_id=template.point_id,
            category_large=_payload_text(template.payload, "category_large"),
            layer_role=_payload_text(template.payload, "layer_role"),
            source_type=candidate.source_type,
            source_id=candidate.source_id,
            source_collection=candidate.source_collection,
            point_id=candidate.point_id,
            image_ref=_image_ref(candidate.payload),
            price=candidate.price,
            score=candidate.score,
            reasons=(
                _selection_reason(mode, candidate.source_type),
                *candidate.reasons,
            ),
            payload=candidate.payload,
        )

    @staticmethod
    def _principle_state(
        state: _PartialComposition,
        template: TemplateItem,
        candidate: ItemCandidate,
        policy: CompositionPolicy,
    ) -> tuple[dict[str, dict[str, str]], int, tuple[dict[str, Any], ...], int]:
        """후보를 담았을 때의 (속성 맵, 누적 드리프트, 총 어긋남).

        속성 추출은 후보마다 한 번만 한다. 정렬 키에서 매번 상품명을 파싱하면
        beam 폭만큼 곱해져 느려진다.
        """
        if not getattr(settings, "PRINCIPLE_COMPOSITION_ENABLED", False):
            return state.slot_attributes, 0, (), 0
        slot = principle_rules.slot_of(template.payload)
        if not slot:
            return (
                state.slot_attributes,
                state.principle_drift,
                state.season_payloads,
                state.principle_violations,
            )
        rules = principle_rules.rules_for_styles(policy.principle_styles)
        attributes = principle_rules.extract_attributes(candidate.payload)
        merged = dict(state.slot_attributes)
        merged[slot] = attributes

        # 두 신호를 합산한다.
        #  1) 조합이 원칙 조건을 어겼는가 — 상품 태그가 비어 있어 잡히는 일이 적다.
        #  2) 치환이 골든 원본의 성질을 바꿨는가 — 원본은 그 원칙을 이미 만족하므로,
        #     원본과 달라진 것 자체가 원칙에서 멀어졌다는 신호다. 골든 아이템은 명도
        #     90퍼센트로 잘 읽혀서 실제 신호는 대부분 이쪽에서 나온다.
        payloads = (*state.season_payloads, candidate.payload)
        drift = state.principle_drift + principle_rules.drift_count(
            principle_rules.extract_attributes(template.payload),
            attributes,
            principle_rules.attributes_in_play(rules, slot),
        )
        return (
            merged,
            drift,
            payloads,
            principle_rules.violation_count(rules, merged)
            + drift,
        )

    @staticmethod
    def _state_sort_key(state: _PartialComposition) -> tuple:
        # 원칙 어긋남은 슬롯 누락 다음, 출처 우선순위 앞이다. 옷장/상품 우선순위보다
        # "코디가 성립하는가"가 앞선다고 본 것이다. 플래그가 꺼져 있으면 이 값이 늘
        # 0이라 정렬이 예전과 완전히 같다.
        return (
            len(state.missing_slot_ids),
            state.principle_violations,
            state.priority_cost,
            -state.similarity_sum,
            tuple(item.identity for item in state.items),
        )

    @staticmethod
    def _meets_minimum_source_counts(
        state: _PartialComposition,
        requirements: tuple[tuple[ItemSource, int], ...],
    ) -> bool:
        return all(
            sum(item.source_type is source for item in state.items) >= minimum
            for source, minimum in requirements
        )


class OutfitComposer:
    """기존 단일 조합 인터페이스. 신규 코드는 모드별 Composer를 사용한다."""

    def __init__(self, *, engine: CompositionEngine | None = None) -> None:
        self.engine = engine or CompositionEngine()

    def compose(self, request: CompositionRequest) -> OutfitComposition:
        if not isinstance(request.mode, RecommendationMode):
            raise TypeError("유효한 추천 모드가 필요합니다.")
        priority = {
            RecommendationMode.WARDROBE_BASED: (ItemSource.WARDROBE,),
            RecommendationMode.NEW_ITEM: (
                ItemSource.WARDROBE,
                ItemSource.PRODUCT,
            ),
        }[request.mode]
        compositions = self.engine.compose(
            request.slot_results,
            policy=CompositionPolicy(
                mode=request.mode,
                source_priority=priority,
                composition_count=1,
                total_budget=request.total_budget,
                category_budgets=request.category_budgets,
                require_image=request.require_image,
                minimum_source_counts=(
                    ((ItemSource.PRODUCT, 1),)
                    if request.mode is RecommendationMode.NEW_ITEM
                    else ()
                ),
            ),
        )
        if not compositions:
            raise CompositionError("신규 상품을 포함한 코디를 구성할 수 없습니다.")
        return compositions[0]
