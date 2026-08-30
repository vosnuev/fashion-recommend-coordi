"""실험형 가설 LLM에 전달할 ID 없는 입력 요약을 만든다."""

from __future__ import annotations

from collections import Counter
from typing import Any

from apps.chat.services.experimental_hypotheses import (
    EXPERIMENT_AXIS_VALUES,
    EXPERIMENT_REASON_CODE_VALUES,
)
from apps.users.constants import category_keys

EXPERIMENTAL_HYPOTHESIS_INSTRUCTIONS = """
당신은 패션 추천 서비스의 실험형 검색 가설 생성기다.
현재 요청의 날씨·TPO·선호·기피 하드 조건은 그대로 유지한다.
최근 추천과 실제 착용에서 반복된 관계를 피하되, 강한 선호나 익숙한 축을
최소 하나 유지하는 서로 다른 검색 가설을 정확히 2개 만든다.
change_axes, preserve_axes, reason_code는 제공된 허용값만 사용한다.
입력에 근거가 있는 reason_code만 선택한다.
레트로, 강한 색, 트렌디 같은 고정 키워드를 습관적으로 선택하지 않는다.
상품·옷장·골든셋 아이템이나 ID를 만들거나 선택하지 않는다.
최종 코디를 정하지 않고 존재하지 않는 아이템 속성을 만들지 않는다.
날씨·TPO·예산·기피 조건과 Validator를 우회하거나 완화하지 않는다.
응답은 지정된 구조화 출력 스키마만 따른다.
""".strip()

_WEATHER_FIELDS = (
    "temperature",
    "feels_like",
    "apparent_temperature",
    "temp_min",
    "temp_max",
    "precipitation",
    "precipitation_probability",
    "rain_probability",
    "wind_speed",
    "sky_state",
    "humidity",
    "is_stale",
)
_CONDITION_FIELDS = (
    "occasion",
    "season",
    "styles",
    "colors",
    "fits",
    "avoided_styles",
    "avoided_colors",
    "avoided_fits",
    "activity",
    "activity_level",
    "movement",
    "tpo",
    "budget",
)
_SUMMARY_FIELDS = (
    "calendar_registrations_30d",
    "worn_item_occurrences_30d",
    "liked_recommendation_cards",
    "disliked_recommendation_cards",
    "saved_outfits",
    "product_clicks",
    "product_clicks_with_duration",
    "corroborated_product_clicks",
)
_COLLECTION_FIELDS = (
    "calendar_wear",
    "recommendation_feedback",
    "saved_outfits",
    "product_clicks",
)
_COLLECTION_DETAIL_FIELDS = (
    "available",
    "signal_strength",
    "like_strength",
    "dislike_strength",
    "history_scope",
    "preference_requires_corroboration",
    "reason",
)
_MAX_COUNT_VALUES = 20


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for row in value:
        normalized = str(row).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _safe_value(value: object) -> str | int | float | bool | None | list[str]:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return _strings(value)


def _select_fields(value: object, fields: tuple[str, ...]) -> dict[str, Any]:
    source = _mapping(value)
    return {field: _safe_value(source[field]) for field in fields if field in source}


def _add_values(counter: Counter[str], value: object) -> None:
    counter.update(_strings(value))


def _count_rows(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"value": value, "count": count}
        for value, count in sorted(counter.items(), key=lambda row: (-row[1], row[0]))[
            :_MAX_COUNT_VALUES
        ]
    ]


def _preferences(context: dict[str, Any]) -> dict[str, Any]:
    pursuit = _mapping(_mapping(context.get("profile")).get("pursuit"))
    allowed_categories = tuple(category_keys())
    return {
        polarity: {
            category: _strings(values)
            for category in allowed_categories
            if (values := _mapping(pursuit.get(polarity)).get(category))
        }
        for polarity in ("preferred", "avoided")
    }


def _recent_recommendation_summary(behavior: dict[str, Any]) -> dict[str, Any]:
    source_data = _mapping(behavior.get("source_data"))
    recent = _mapping(source_data.get("recent_recommendations"))
    runs = _rows(recent.get("runs"))[:10]
    counters = {
        "styles": Counter(),
        "colors": Counter(),
        "fits": Counter(),
        "major_slots": Counter(),
    }
    card_count = 0
    for run in runs:
        for result in _rows(run.get("results")):
            for card in _rows(result.get("cards")):
                card_count += 1
                for field, counter in counters.items():
                    _add_values(counter, card.get(field))

    repetitions = _mapping(recent.get("repetitions"))
    repeated_slots = [
        {
            "slot": str(row.get("slot") or "").strip(),
            "count": max(int(row.get("count") or 0), 0),
        }
        for row in _rows(repetitions.get("slots"))
        if str(row.get("slot") or "").strip()
    ]
    repeated_combination_counts = sorted(
        (
            max(int(row.get("count") or 0), 0)
            for row in _rows(repetitions.get("combinations"))
        ),
        reverse=True,
    )
    return {
        "history_scope": "RECENT_10_RUNS",
        "run_count": len(runs),
        "card_count": card_count,
        "style_counts": _count_rows(counters["styles"]),
        "color_counts": _count_rows(counters["colors"]),
        "fit_counts": _count_rows(counters["fits"]),
        "major_slot_counts": _count_rows(counters["major_slots"]),
        "repeated_slots": repeated_slots[:_MAX_COUNT_VALUES],
        "repeated_combination_counts": repeated_combination_counts[:_MAX_COUNT_VALUES],
    }


def _item_feature_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counters = {
        "categories": Counter(),
        "styles": Counter(),
        "colors": Counter(),
        "fits": Counter(),
    }
    for row in rows:
        _add_values(counters["categories"], row.get("category_large"))
        _add_values(counters["styles"], row.get("styles"))
        _add_values(counters["colors"], row.get("color"))
        _add_values(counters["fits"], row.get("fit"))
    return {
        f"{name}_counts": _count_rows(counter) for name, counter in counters.items()
    }


def _calendar_summary(behavior: dict[str, Any]) -> dict[str, Any]:
    source_data = _mapping(behavior.get("source_data"))
    calendar = _mapping(source_data.get("calendar_wear"))
    tpo_counts: Counter[str] = Counter()
    for entry in _rows(calendar.get("recent_entries")):
        _add_values(tpo_counts, entry.get("tpo"))

    repeated_combination_counts = sorted(
        (
            max(int(row.get("count") or 0), 0)
            for row in _rows(calendar.get("repeated_combinations_30d"))
        ),
        reverse=True,
    )
    return {
        "as_of_date": _safe_value(calendar.get("as_of_date")),
        "entry_counts": _select_fields(
            calendar.get("entry_counts"),
            ("7d", "14d", "30d"),
        ),
        "linked_item_occurrence_counts": _select_fields(
            calendar.get("linked_item_occurrence_counts"),
            ("7d", "14d", "30d"),
        ),
        "recent_tpo_counts": _count_rows(tpo_counts),
        "worn_item_features": _item_feature_counts(_rows(calendar.get("worn_items"))),
        "underused_item_features": _item_feature_counts(
            _rows(calendar.get("not_worn_in_30d_items"))
        ),
        "repeated_combination_counts": repeated_combination_counts[:_MAX_COUNT_VALUES],
    }


def _feedback_summary(behavior: dict[str, Any]) -> dict[str, Any]:
    signals = _mapping(behavior.get("signals"))
    weak = _mapping(signals.get("weak_preferences"))
    negative = _mapping(signals.get("negative_preferences"))
    groups = {
        "liked": _rows(weak.get("liked_recommendation_cards")),
        "saved": _rows(weak.get("saved_outfits")),
        "disliked": _rows(negative.get("disliked_recommendation_cards")),
    }
    result: dict[str, Any] = {}
    for label, rows in groups.items():
        counters = {
            "styles": Counter(),
            "colors": Counter(),
            "fits": Counter(),
            "reason_codes": Counter(),
        }
        for row in rows:
            outfit = _mapping(row.get("outfit"))
            for field in ("styles", "colors", "fits"):
                _add_values(counters[field], outfit.get(field))
            _add_values(counters["reason_codes"], row.get("reason_codes"))
        result[label] = {
            "card_count": len(rows),
            **{
                f"{name}_counts": _count_rows(counter)
                for name, counter in counters.items()
            },
        }
    click_rows = _rows(
        _mapping(signals.get("reference_information")).get("product_clicks")
    )
    click_counters = {"styles": Counter(), "colors": Counter(), "fits": Counter()}
    for row in click_rows:
        item = _mapping(row.get("item"))
        for field, counter in click_counters.items():
            _add_values(counter, item.get(field))
    result["product_click_reference"] = {
        "event_count": len(click_rows),
        "duration_observed_count": sum(
            row.get("engagement_duration_ms") is not None for row in click_rows
        ),
        "corroborated_count": sum(
            bool(row.get("corroborated_preference")) for row in click_rows
        ),
        **{
            f"{name}_counts": _count_rows(counter)
            for name, counter in click_counters.items()
        },
    }
    return result


def _collection_status(behavior: dict[str, Any]) -> dict[str, Any]:
    statuses = _mapping(behavior.get("collection_status"))
    return {
        name: _select_fields(statuses.get(name), _COLLECTION_DETAIL_FIELDS)
        for name in _COLLECTION_FIELDS
        if name in statuses
    }


def build_experimental_hypothesis_payload(context: dict[str, Any]) -> dict[str, Any]:
    """원본 ID·아이템을 제외하고 가설 근거로 허용된 요약만 반환한다."""

    if not isinstance(context, dict):
        raise TypeError("실험형 가설 컨텍스트는 JSON 객체여야 합니다.")
    session = _mapping(context.get("session"))
    behavior = _mapping(context.get("behavior_signals"))
    return {
        "task": "generate_experimental_hypotheses",
        "allowed_values": {
            "axes": list(EXPERIMENT_AXIS_VALUES),
            "reason_codes": list(EXPERIMENT_REASON_CODE_VALUES),
        },
        "current_request": str(context.get("current_request") or "").strip(),
        "recommendation_mode": str(session.get("mode") or "").strip(),
        "weather": _select_fields(context.get("weather"), _WEATHER_FIELDS),
        "tpo_conditions": _select_fields(session.get("conditions"), _CONDITION_FIELDS),
        "preferences": _preferences(context),
        "behavior": {
            "available": bool(behavior),
            "collection_status": _collection_status(behavior),
            "summary": _select_fields(behavior.get("summary"), _SUMMARY_FIELDS),
            "recent_recommendations": _recent_recommendation_summary(behavior),
            "calendar_wear": _calendar_summary(behavior),
            "feedback": _feedback_summary(behavior),
        },
    }
