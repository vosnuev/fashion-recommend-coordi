"""회원의 저장 코디와 상품 클릭 이벤트를 제한된 행동 이력으로 구조화한다."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from django.db.models import Prefetch

from apps.chat.models import ChatIdentity
from apps.recommend.models import (
    GoldenTemplateSnapshot,
    OutfitComposition,
    OutfitCompositionItem,
    ProductClickEvent,
    SavedOutfit,
)

MEMBER_BEHAVIOR_HISTORY_LIMIT = 100

_STYLE_KEYS = ("style", "styles", "style_tags")
_COLOR_KEYS = ("color", "colors", "base_color", "color_family")
_FIT_KEYS = ("fit", "fits", "silhouette")


class MemberBehaviorHistoryRequired(RuntimeError):
    """회원 identity가 아닌 행동 이력 조회를 거부한다."""


def _values(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    if not isinstance(value, Iterable) or isinstance(value, (bytes, dict)):
        normalized = str(value).strip()
        return [normalized] if normalized else []
    result: list[str] = []
    for row in value:
        normalized = str(row).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _snapshot_values(snapshot: object, keys: tuple[str, ...]) -> list[str]:
    if not isinstance(snapshot, dict):
        return []
    sources = [snapshot]
    if isinstance(snapshot.get("tags"), dict):
        sources.append(snapshot["tags"])
    return list(
        dict.fromkeys(
            value
            for source in sources
            for key in keys
            for value in _values(source.get(key))
        )
    )


def _merge(*groups: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for group in groups for value in group))


def _item_payload(item: OutfitCompositionItem) -> dict[str, Any]:
    snapshot = item.item_snapshot
    return {
        "item_id": str(item.id),
        "slot": item.slot,
        "source_type": item.source_type,
        "source_collection": item.source_collection,
        "source_id": item.source_id,
        "styles": _snapshot_values(snapshot, _STYLE_KEYS),
        "colors": _snapshot_values(snapshot, _COLOR_KEYS),
        "fits": _snapshot_values(snapshot, _FIT_KEYS),
    }


def _outfit_payload(composition: OutfitComposition) -> dict[str, Any]:
    try:
        template = composition.result.golden_template.payload_snapshot
    except GoldenTemplateSnapshot.DoesNotExist:
        template = {}
    items = [_item_payload(item) for item in composition.behavior_items]
    return {
        "styles": _merge(
            _snapshot_values(template, _STYLE_KEYS),
            *(item["styles"] for item in items),
        ),
        "colors": _merge(
            _snapshot_values(template, _COLOR_KEYS),
            *(item["colors"] for item in items),
        ),
        "fits": _merge(
            _snapshot_values(template, _FIT_KEYS),
            *(item["fits"] for item in items),
        ),
        "slots": list(dict.fromkeys(item["slot"] for item in items)),
        "items": items,
    }


def _counter_rows(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"value": value, "count": count}
        for value, count in sorted(counter.items(), key=lambda row: (-row[1], row[0]))
    ]


def _item_counter_rows(
    counter: Counter[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    return [
        {
            "source_type": key[0],
            "source_collection": key[1],
            "source_id": key[2],
            "count": count,
        }
        for key, count in sorted(
            counter.items(),
            key=lambda row: (-row[1], row[0]),
        )
    ]


def summarize_behavior_features(
    *,
    outfits: Iterable[dict[str, Any]] = (),
    items: Iterable[dict[str, Any]] = (),
) -> dict[str, list[dict[str, Any]]]:
    """행동 이력의 태그·슬롯·원본 아이템 빈도를 결정적으로 집계한다."""

    counters = {
        "styles": Counter(),
        "colors": Counter(),
        "fits": Counter(),
        "slots": Counter(),
    }
    item_counter: Counter[tuple[str, str, str]] = Counter()
    normalized_items = list(items)
    outfit_items: list[dict[str, Any]] = []
    for outfit in outfits:
        for field in ("styles", "colors", "fits", "slots"):
            counters[field].update(_values(outfit.get(field)))
        outfit_items.extend(
            row for row in outfit.get("items", []) if isinstance(row, dict)
        )
    for item in normalized_items:
        for field in ("styles", "colors", "fits"):
            counters[field].update(_values(item.get(field)))
        counters["slots"].update(_values(item.get("slot")))
        key = (
            str(item.get("source_type") or ""),
            str(item.get("source_collection") or ""),
            str(item.get("source_id") or ""),
        )
        if all(key):
            item_counter[key] += 1
    for item in outfit_items:
        key = (
            str(item.get("source_type") or ""),
            str(item.get("source_collection") or ""),
            str(item.get("source_id") or ""),
        )
        if all(key):
            item_counter[key] += 1
    return {
        **{name: _counter_rows(counter) for name, counter in counters.items()},
        "items": _item_counter_rows(item_counter),
    }


def load_saved_outfit_history(*, identity: ChatIdentity) -> dict[str, Any]:
    """회원이 현재 저장 중인 최신 코디 최대 100건을 약한 선호 원천으로 읽는다."""

    if identity.user_id is None:
        raise MemberBehaviorHistoryRequired(
            "저장 코디 행동 이력은 로그인한 회원만 조회할 수 있습니다."
        )
    item_queryset = OutfitCompositionItem.objects.order_by("position", "created_at")
    rows = list(
        SavedOutfit.objects.filter(user_id=identity.user_id)
        .select_related(
            "composition__result",
            "composition__result__golden_template",
        )
        .prefetch_related(
            Prefetch(
                "composition__items",
                queryset=item_queryset,
                to_attr="behavior_items",
            )
        )
        .order_by("-created_at", "-id")[:MEMBER_BEHAVIOR_HISTORY_LIMIT]
    )
    events = []
    for row in rows:
        result = row.composition.result
        events.append(
            {
                "saved_outfit_id": str(row.id),
                "saved_at": row.created_at.isoformat(),
                "result_id": str(result.id),
                "composition_id": str(row.composition_id),
                "persona_id": result.persona_id or None,
                "outfit": _outfit_payload(row.composition),
            }
        )
    return {
        "history_limit": MEMBER_BEHAVIOR_HISTORY_LIMIT,
        "history_scope": "LATEST_100_SAVED_OUTFITS",
        "events": events,
        "feature_counts": summarize_behavior_features(
            outfits=(event["outfit"] for event in events)
        ),
    }


def load_product_click_history(*, identity: ChatIdentity) -> dict[str, Any]:
    """회원의 최신 상품 클릭 최대 100건을 참고 정보 원천으로 읽는다."""

    if identity.user_id is None:
        raise MemberBehaviorHistoryRequired(
            "상품 클릭 행동 이력은 로그인한 회원만 조회할 수 있습니다."
        )
    rows = list(
        ProductClickEvent.objects.filter(user_id=identity.user_id)
        .select_related("item")
        .order_by("-created_at", "-id")[:MEMBER_BEHAVIOR_HISTORY_LIMIT]
    )
    events = [
        {
            "product_click_id": str(row.id),
            "clicked_at": row.created_at.isoformat(),
            "engagement_duration_ms": row.engagement_duration_ms,
            "engagement_recorded_at": (
                row.engagement_recorded_at.isoformat()
                if row.engagement_recorded_at is not None
                else None
            ),
            "result_id": str(row.result_id_snapshot),
            "composition_id": str(row.composition_id_snapshot),
            "persona_id": row.persona_id or None,
            "item": _item_payload(row.item),
        }
        for row in rows
    ]
    return {
        "history_limit": MEMBER_BEHAVIOR_HISTORY_LIMIT,
        "history_scope": "LATEST_100_PRODUCT_CLICKS",
        "events": events,
        "feature_counts": summarize_behavior_features(
            items=(event["item"] for event in events)
        ),
    }
