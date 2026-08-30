"""일반 추천 설명의 ID 계약 검증, 규칙 폴백, 영속화를 담당한다."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.chat.services.openai_adapter import RecommendationExplanation
from apps.chat.services.response_text import normalize_assistant_text
from apps.recommend.models import (
    OutfitComposition,
    OutfitCompositionItem,
    RecommendationResult,
)

logger = logging.getLogger(__name__)


class RecommendationExplanationContractError(ValueError):
    """설명 출력이 저장된 추천 카드·아이템 계약과 다르다."""


@dataclass(frozen=True)
class AppliedRecommendationExplanation:
    opening: str
    fallback_used: bool
    fallback_reason: str = ""


def _required_text(value: str, *, field: str) -> str:
    normalized = normalize_assistant_text(value)
    if not normalized:
        raise RecommendationExplanationContractError(f"{field} 값이 비어 있습니다.")
    return normalized


def _condition_values(conditions: dict[str, Any], key: str) -> list[str]:
    value = conditions.get(key)
    if not isinstance(value, list):
        return []
    return [entry.strip() for entry in value if isinstance(entry, str) and entry.strip()]


def _fallback_opening(
    conditions: dict[str, Any],
    recent_messages: list[dict[str, Any]],
) -> str:
    styles = _condition_values(conditions, "styles")
    occasion = str(conditions.get("occasion") or "").strip()
    if styles and occasion:
        opening = f"{styles[0]} 스타일로 {occasion}에 입기 좋은 코디를 준비했어요."
    elif styles:
        opening = f"요청한 {styles[0]} 스타일에 맞춰 코디를 준비했어요."
    elif occasion:
        opening = f"{occasion}에 입을 수 있도록 코디를 준비했어요."
    else:
        opening = "요청한 조건에 맞춰 코디를 준비했어요."
    has_prior_user_message = any(
        message.get("role") == "user" for message in recent_messages
    )
    return opening if has_prior_user_message else f"안녕하세요! {opening}"


def _has_weather_facts(weather: dict[str, Any]) -> bool:
    if weather.get("is_stale") is True:
        return False
    return any(
        weather.get(key) not in (None, "", [], {})
        for key in (
            "temperature",
            "feels_like_temperature",
            "sky_state",
            "precipitation_type",
            "wind_speed",
        )
    )


def _fallback_rationale(
    card: OutfitComposition,
    *,
    mode: str,
    budget: int | None,
    conditions: dict[str, Any],
    weather: dict[str, Any],
) -> str:
    criteria: list[str] = []
    styles = _condition_values(conditions, "styles")
    if styles:
        criteria.append(f"{', '.join(styles[:2])} 스타일")
    occasion = str(conditions.get("occasion") or "").strip()
    if occasion:
        criteria.append(occasion)
    if _has_weather_facts(weather):
        criteria.append("현재 날씨 정보")
    if budget is not None and card.total_product_price <= budget:
        criteria.append(f"{budget:,}원 예산")

    if criteria:
        first = f"{'·'.join(criteria)} 기준으로 검증을 통과한 룩이에요."
    else:
        first = "요청 조건을 기준으로 검증을 통과한 룩이에요."
    if mode == RecommendationResult.Mode.WARDROBE_BASED:
        return f"{first} 보유 아이템을 활용해 구성했어요."
    if card.total_product_price:
        return f"{first} 새 상품 가격 합계는 {card.total_product_price:,}원이에요."
    return first


def _fallback_note(item: OutfitCompositionItem) -> str:
    reasons = [str(reason) for reason in item.reasons]
    if item.source_type == OutfitCompositionItem.SourceType.WARDROBE:
        return "보유 중인 아이템을 활용할 수 있도록 골랐어요."
    if any("레이어 역할 일치" in reason for reason in reasons):
        return "코디에 필요한 레이어 역할을 채우는 아이템으로 골랐어요."
    if any("대분류 일치" in reason for reason in reasons):
        return "코디에 필요한 카테고리를 채우는 아이템으로 골랐어요."
    if item.price_snapshot is not None:
        return f"구매 가능한 {item.price_snapshot:,}원 상품 후보 중에서 골랐어요."
    return "검증된 코디 조건에 맞는 아이템 후보로 골랐어요."


def _fallback_values(
    cards: list[OutfitComposition],
    *,
    mode: str,
    budget: int | None,
    conditions: dict[str, Any],
    weather: dict[str, Any],
    recent_messages: list[dict[str, Any]],
) -> tuple[str, dict[str, tuple[str, dict[str, str]]]]:
    values: dict[str, tuple[str, dict[str, str]]] = {}
    for card in cards:
        values[str(card.id)] = (
            _fallback_rationale(
                card,
                mode=mode,
                budget=budget,
                conditions=conditions,
                weather=weather,
            ),
            {str(item.id): _fallback_note(item) for item in card.items.all()},
        )
    return _fallback_opening(conditions, recent_messages), values


def _validated_values(
    cards: list[OutfitComposition],
    explanation: RecommendationExplanation,
) -> tuple[str, dict[str, tuple[str, dict[str, str]]]]:
    # 계약은 순번이다 — payload에 실린 순서(1부터)를 그대로 되돌려받는지만 본다.
    # 순번은 _approved_payload가 같은 쿼리·같은 정렬로 매기므로 서버가 카드로
    # 되돌릴 수 있고, 모델이 긴 UUID를 옮겨 적다 틀릴 여지가 없다.
    expected_outfit_indexes = list(range(1, len(cards) + 1))
    actual_outfit_indexes = [outfit.outfit_index for outfit in explanation.outfits]
    if actual_outfit_indexes != expected_outfit_indexes:
        raise RecommendationExplanationContractError(
            "설명 출력의 코디 순번 또는 순서가 저장된 추천과 다릅니다."
        )

    values: dict[str, tuple[str, dict[str, str]]] = {}
    for card, outfit in zip(cards, explanation.outfits, strict=True):
        card_items = list(card.items.all())
        expected_item_indexes = list(range(1, len(card_items) + 1))
        actual_item_indexes = [item.item_index for item in outfit.items]
        if actual_item_indexes != expected_item_indexes:
            raise RecommendationExplanationContractError(
                f"코디 {card.id}의 아이템 순번 또는 순서가 다릅니다."
            )
        if any(item.attribute_claims for item in outfit.items):
            raise RecommendationExplanationContractError(
                "입력에 없는 아이템 속성 주장을 저장할 수 없습니다."
            )
        values[str(card.id)] = (
            _required_text(outfit.rationale, field="outfit.rationale"),
            {
                str(card_item.id): _required_text(item.note, field="item.note")
                for card_item, item in zip(card_items, outfit.items, strict=True)
            },
        )
    return _required_text(explanation.opening, field="opening"), values


@transaction.atomic
def apply_recommendation_explanation(
    *,
    result: RecommendationResult,
    explanation: RecommendationExplanation | None,
    mode: str,
    budget: int | None,
    conditions: dict[str, Any],
    weather: dict[str, Any],
    recent_messages: list[dict[str, Any]],
    fallback_reason: str = "",
) -> AppliedRecommendationExplanation:
    cards = list(
        result.compositions.filter(status=OutfitComposition.Status.VALIDATED)
        .prefetch_related("items")
        .order_by("rank", "created_at")
    )
    if not cards:
        raise RecommendationExplanationContractError(
            "설명을 연결할 검증 완료 코디가 없습니다."
        )

    used_fallback = explanation is None
    reason = fallback_reason
    if explanation is not None:
        try:
            opening, values = _validated_values(cards, explanation)
        except RecommendationExplanationContractError as exc:
            used_fallback = True
            reason = "RECOMMENDATION_EXPLANATION_CONTRACT_FAILED"
            logger.warning("추천 설명 계약 검증 실패, 규칙 폴백 사용: %s", exc)
    if used_fallback:
        opening, values = _fallback_values(
            cards,
            mode=mode,
            budget=budget,
            conditions=conditions,
            weather=weather,
            recent_messages=recent_messages,
        )

    for card in cards:
        rationale, notes = values[str(card.id)]
        OutfitComposition.objects.filter(pk=card.pk).update(
            rationale=rationale,
            updated_at=timezone.now(),
        )
        for item in card.items.all():
            OutfitCompositionItem.objects.filter(pk=item.pk).update(
                note=notes[str(item.id)]
            )

    return AppliedRecommendationExplanation(
        opening=opening,
        fallback_used=used_fallback,
        fallback_reason=reason,
    )
