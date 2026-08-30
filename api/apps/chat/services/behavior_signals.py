"""스타일리스트 추천용 사용자 행동 신호 통합 서비스."""

from __future__ import annotations

from datetime import date
from typing import Any

from apps.chat.models import ChatIdentity, ChatRun
from apps.chat.services.behavior_event_history import (
    load_product_click_history,
    load_saved_outfit_history,
)
from apps.chat.services.calendar_wear_history import load_calendar_wear_history
from apps.chat.services.recent_recommendations import load_recent_recommendations

BEHAVIOR_SIGNAL_SCHEMA_VERSION = "1.1"


def _feedback_signal(
    *,
    run: dict[str, Any],
    result: dict[str, Any],
    card: dict[str, Any],
) -> dict[str, Any] | None:
    feedback = card.get("feedback")
    if not isinstance(feedback, dict):
        return None
    reaction = feedback.get("reaction")
    if reaction == "LIKE":
        signal_type = "RECOMMENDATION_LIKE"
        strength = "WEAK"
        polarity = "POSITIVE"
    elif reaction == "DISLIKE":
        signal_type = "RECOMMENDATION_DISLIKE"
        strength = "NEGATIVE"
        polarity = "NEGATIVE"
    else:
        return None
    return {
        "signal_type": signal_type,
        "strength": strength,
        "polarity": polarity,
        "occurred_at": feedback.get("updated_at"),
        "run_id": run["run_id"],
        "result_id": result["result_id"],
        "composition_id": card["composition_id"],
        "persona_id": result.get("persona_id"),
        "reason_codes": list(feedback.get("reason_codes") or []),
        "comment": str(feedback.get("comment") or ""),
        "outfit": {
            "styles": list(card.get("styles") or []),
            "colors": list(card.get("colors") or []),
            "fits": list(card.get("fits") or []),
            "items": list(card.get("items") or []),
        },
    }


def _recommendation_feedback_signals(
    recent_recommendations: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    likes: list[dict[str, Any]] = []
    dislikes: list[dict[str, Any]] = []
    for run in recent_recommendations["runs"]:
        for result in run["results"]:
            for card in result["cards"]:
                signal = _feedback_signal(run=run, result=result, card=card)
                if signal is None:
                    continue
                if signal["polarity"] == "POSITIVE":
                    likes.append(signal)
                else:
                    dislikes.append(signal)
    return likes, dislikes


def _calendar_registration_signals(
    calendar_wear: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "signal_type": "CALENDAR_REGISTRATION",
            "strength": "STRONG",
            "polarity": "POSITIVE",
            "occurred_on": entry["worn_on"],
            "calendar_id": entry["calendar_id"],
            "status": entry["status"],
            "source_type": entry["source_type"],
            "tpo": list(entry.get("tpo") or []),
            "linked_item_count": entry["linked_item_count"],
            "items": list(entry.get("items") or []),
        }
        for entry in calendar_wear["recent_entries"]
    ]


def _saved_outfit_signals(
    saved_outfits: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "signal_type": "OUTFIT_SAVED",
            "strength": "WEAK",
            "polarity": "POSITIVE",
            "occurred_at": event["saved_at"],
            "saved_outfit_id": event["saved_outfit_id"],
            "result_id": event["result_id"],
            "composition_id": event["composition_id"],
            "persona_id": event.get("persona_id"),
            "outfit": event["outfit"],
        }
        for event in saved_outfits["events"]
    ]


def _product_click_signals(
    product_clicks: dict[str, Any],
    *,
    saved_composition_ids: set[str],
    liked_composition_ids: set[str],
) -> list[dict[str, Any]]:
    signals = []
    for event in product_clicks["events"]:
        composition_id = event["composition_id"]
        evidence = []
        if composition_id in saved_composition_ids:
            evidence.append("OUTFIT_SAVED")
        if composition_id in liked_composition_ids:
            evidence.append("RECOMMENDATION_LIKE")
        signals.append(
            {
                "signal_type": "PRODUCT_CLICK",
                "strength": "REFERENCE",
                "polarity": "NEUTRAL",
                "occurred_at": event["clicked_at"],
                "product_click_id": event["product_click_id"],
                "result_id": event["result_id"],
                "composition_id": composition_id,
                "persona_id": event.get("persona_id"),
                "engagement_duration_ms": event.get("engagement_duration_ms"),
                "engagement_recorded_at": event.get("engagement_recorded_at"),
                "preference_evidence": evidence,
                "corroborated_preference": bool(evidence),
                "item": event["item"],
            }
        )
    return signals


def load_user_behavior_signals(
    *,
    identity: ChatIdentity,
    current_run: ChatRun,
    as_of: date | None = None,
) -> dict[str, Any]:
    """사용 가능한 행동 데이터를 의미와 강도를 보존해 한 번씩 로드한다.

    추천 노출은 선호로 승격하지 않고 반복 회피 자료로만 둔다. 상품 클릭과 체류
    시간은 중립 참고 정보이며, 저장 또는 LIKE가 같은 카드에 있을 때만 보강 근거를
    표시한다. 클릭 자체는 이 경우에도 약한 선호로 승격하지 않는다.
    """

    recent_recommendations = load_recent_recommendations(
        identity=identity,
        current_run=current_run,
    )
    calendar_wear = load_calendar_wear_history(
        identity=identity,
        as_of=as_of,
    )
    saved_outfits = load_saved_outfit_history(identity=identity)
    product_clicks = load_product_click_history(identity=identity)
    likes, dislikes = _recommendation_feedback_signals(recent_recommendations)
    calendar_registrations = _calendar_registration_signals(calendar_wear)
    saved_signals = _saved_outfit_signals(saved_outfits)
    click_signals = _product_click_signals(
        product_clicks,
        saved_composition_ids={row["composition_id"] for row in saved_signals},
        liked_composition_ids={row["composition_id"] for row in likes},
    )

    return {
        "schema_version": BEHAVIOR_SIGNAL_SCHEMA_VERSION,
        "as_of_date": calendar_wear["as_of_date"],
        "collection_status": {
            "calendar_wear": {
                "available": True,
                "signal_strength": "STRONG",
            },
            "recommendation_feedback": {
                "available": True,
                "like_strength": "WEAK",
                "dislike_strength": "NEGATIVE",
                "history_scope": "RECENT_10_RUNS",
            },
            "saved_outfits": {
                "available": True,
                "signal_strength": "WEAK",
                "history_scope": saved_outfits["history_scope"],
            },
            "product_clicks": {
                "available": True,
                "signal_strength": "REFERENCE",
                "history_scope": product_clicks["history_scope"],
                "preference_requires_corroboration": True,
            },
        },
        "summary": {
            "calendar_registrations_30d": calendar_wear["entry_counts"]["30d"],
            "worn_item_occurrences_30d": calendar_wear["linked_item_occurrence_counts"][
                "30d"
            ],
            "liked_recommendation_cards": len(likes),
            "disliked_recommendation_cards": len(dislikes),
            "saved_outfits": len(saved_signals),
            "product_clicks": len(click_signals),
            "product_clicks_with_duration": sum(
                signal["engagement_duration_ms"] is not None
                for signal in click_signals
            ),
            "corroborated_product_clicks": sum(
                signal["corroborated_preference"] for signal in click_signals
            ),
        },
        "signals": {
            "strong_preferences": {
                "calendar_registrations": calendar_registrations,
                "worn_items": calendar_wear["worn_items"],
            },
            "weak_preferences": {
                "liked_recommendation_cards": likes,
                "saved_outfits": saved_signals,
            },
            "negative_preferences": {
                "disliked_recommendation_cards": dislikes,
            },
            "reference_information": {
                "product_clicks": click_signals,
            },
        },
        "repetition_avoidance": {
            "recent_recommendations": recent_recommendations["repetitions"],
            "recent_calendar_combinations": calendar_wear["repeated_combinations_30d"],
            "not_worn_in_30d_items": calendar_wear["not_worn_in_30d_items"],
        },
        "source_data": {
            "recent_recommendations": recent_recommendations,
            "calendar_wear": calendar_wear,
            "saved_outfits": saved_outfits,
            "product_clicks": product_clicks,
        },
    }
