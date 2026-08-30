"""채팅 개인화를 위한 회원 캘린더 실제 착용 이력 집계."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

from django.db.models import Count, Max, Prefetch, Q
from django.utils import timezone

from apps.chat.models import ChatIdentity
from apps.style_calendar.models import CalendarEntry, CalendarWardrobeItem
from apps.wardrobe.models import WardrobeItem

WEAR_WINDOWS = (7, 14, 30)
RECENT_WEAR_WINDOW_DAYS = 30


class CalendarWearHistoryError(RuntimeError):
    """캘린더 착용 이력을 안전하게 조회할 수 없을 때 발생한다."""


class MemberCalendarWearHistoryRequired(CalendarWearHistoryError):
    """회원 identity가 아닌 호출을 거부한다."""


def _window_start(as_of: date, days: int) -> date:
    return as_of - timedelta(days=days - 1)


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    if isinstance(value, set):
        value = sorted(value, key=str)
    if isinstance(value, (list, tuple)):
        return [normalized for item in value if (normalized := str(item).strip())]
    return []


def _snapshot_value(
    snapshot: object,
    key: str,
    fallback: object,
) -> object:
    if isinstance(snapshot, dict) and snapshot.get(key) not in (None, "", []):
        return snapshot[key]
    if (
        isinstance(snapshot, dict)
        and isinstance(snapshot.get("tags"), dict)
        and snapshot["tags"].get(key) not in (None, "", [])
    ):
        return snapshot["tags"][key]
    return fallback


def _wardrobe_item_payload(
    item: WardrobeItem,
    *,
    snapshot: object | None = None,
) -> dict[str, Any]:
    return {
        "wardrobe_item_id": str(item.id),
        "item_name": str(_snapshot_value(snapshot, "item_name", item.item_name)),
        "category_large": str(
            _snapshot_value(snapshot, "category_large", item.category_large)
        ),
        "category_small": str(
            _snapshot_value(snapshot, "category_small", item.category_small)
        ),
        "styles": _string_list(_snapshot_value(snapshot, "style", item.style)),
        "color": str(_snapshot_value(snapshot, "color", item.color)),
        "fit": str(_snapshot_value(snapshot, "fit", item.fit)),
    }


def _wear_count_payload(row: dict[str, Any] | None) -> dict[str, int]:
    row = row or {}
    return {f"{days}d": int(row.get(f"count_{days}d") or 0) for days in WEAR_WINDOWS}


def _entry_payload(entry: CalendarEntry) -> dict[str, Any]:
    links = list(entry.recent_wardrobe_links)
    return {
        "calendar_id": str(entry.id),
        "worn_on": entry.date.isoformat(),
        "status": entry.status,
        "source_type": entry.source_type,
        "tpo": list(entry.tpo) if isinstance(entry.tpo, list) else [],
        "linked_item_count": len(links),
        "items": [
            _wardrobe_item_payload(link.wardrobe_item, snapshot=link.snapshot)
            for link in links
        ],
    }


def _repeated_combinations(
    entries: list[CalendarEntry],
) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, ...]] = Counter()
    worn_dates: defaultdict[tuple[str, ...], list[str]] = defaultdict(list)
    for entry in entries:
        signature = tuple(
            sorted(str(link.wardrobe_item_id) for link in entry.recent_wardrobe_links)
        )
        if len(signature) < 2:
            continue
        counts[signature] += 1
        worn_dates[signature].append(entry.date.isoformat())

    combinations = [
        {
            "wardrobe_item_ids": list(signature),
            "count": count,
            "worn_dates": sorted(worn_dates[signature], reverse=True),
        }
        for signature, count in counts.items()
        if count >= 2
    ]
    combinations.sort(key=lambda row: (-row["count"], row["wardrobe_item_ids"]))
    return combinations


def load_calendar_wear_history(
    *,
    identity: ChatIdentity,
    as_of: date | None = None,
) -> dict[str, Any]:
    """회원의 캘린더 등록과 명시적 옷장 연결을 실제 착용으로 집계한다.

    날짜 구간은 기준일을 포함한다. 연결이 없는 캘린더도 등록 이벤트로는
    보존하지만 아이템을 추측하거나 아이템별 착용 횟수를 올리지는 않는다.
    """

    if identity.user_id is None:
        raise MemberCalendarWearHistoryRequired(
            "캘린더 실제 착용 이력은 로그인한 회원만 조회할 수 있습니다."
        )
    as_of = as_of or timezone.localdate()
    starts = {days: _window_start(as_of, days) for days in WEAR_WINDOWS}

    item_rows = list(
        CalendarWardrobeItem.objects.filter(
            calendar__user_id=identity.user_id,
            calendar__date__lte=as_of,
            wardrobe_item__user_id=identity.user_id,
        )
        .values("wardrobe_item_id")
        .annotate(
            count_7d=Count(
                "id",
                filter=Q(calendar__date__gte=starts[7]),
            ),
            count_14d=Count(
                "id",
                filter=Q(calendar__date__gte=starts[14]),
            ),
            count_30d=Count(
                "id",
                filter=Q(calendar__date__gte=starts[30]),
            ),
            last_worn_on=Max("calendar__date"),
        )
    )
    item_stats = {row["wardrobe_item_id"]: row for row in item_rows}

    link_queryset = (
        CalendarWardrobeItem.objects.filter(
            wardrobe_item__user_id=identity.user_id,
        )
        .select_related("wardrobe_item")
        .order_by("sort_order", "created_at")
    )
    calendar_summary = CalendarEntry.objects.filter(
        user_id=identity.user_id,
        date__lte=as_of,
    ).aggregate(
        count_7d=Count("id", filter=Q(date__gte=starts[7])),
        count_14d=Count("id", filter=Q(date__gte=starts[14])),
        count_30d=Count("id", filter=Q(date__gte=starts[30])),
        last_calendar_entry_on=Max("date"),
    )
    recent_entries = list(
        CalendarEntry.objects.filter(
            user_id=identity.user_id,
            date__gte=starts[RECENT_WEAR_WINDOW_DAYS],
            date__lte=as_of,
        )
        .prefetch_related(
            Prefetch(
                "wardrobe_links",
                queryset=link_queryset,
                to_attr="recent_wardrobe_links",
            )
        )
        .order_by("-date", "-created_at")
    )

    linked_items: dict[object, WardrobeItem] = {}
    for entry in recent_entries:
        for link in entry.recent_wardrobe_links:
            linked_items[link.wardrobe_item_id] = link.wardrobe_item

    historically_worn_ids = set(item_stats) - set(linked_items)
    if historically_worn_ids:
        for item in WardrobeItem.objects.filter(
            user_id=identity.user_id,
            id__in=historically_worn_ids,
        ):
            linked_items[item.id] = item

    worn_items = []
    for item_id, row in item_stats.items():
        item = linked_items.get(item_id)
        if item is None:
            continue
        worn_items.append(
            {
                **_wardrobe_item_payload(item),
                "wear_counts": _wear_count_payload(row),
                "last_worn_on": row["last_worn_on"].isoformat(),
            }
        )
    worn_items.sort(
        key=lambda row: (
            -date.fromisoformat(row["last_worn_on"]).toordinal(),
            row["wardrobe_item_id"],
        )
    )

    recently_worn_ids = {
        item_id
        for item_id, row in item_stats.items()
        if int(row.get("count_30d") or 0) > 0
    }
    current_closet_items = WardrobeItem.objects.filter(
        user_id=identity.user_id,
        confirmed=True,
        added_to_closet_at__isnull=False,
    ).order_by("category_large", "category_small", "item_name", "id")
    not_recently_worn_items = []
    for item in current_closet_items:
        if item.id in recently_worn_ids:
            continue
        row = item_stats.get(item.id)
        not_recently_worn_items.append(
            {
                **_wardrobe_item_payload(item),
                "last_worn_on": (
                    row["last_worn_on"].isoformat() if row is not None else None
                ),
            }
        )

    entry_counts = {
        f"{days}d": int(calendar_summary.get(f"count_{days}d") or 0)
        for days in WEAR_WINDOWS
    }
    linked_item_occurrence_counts = {
        f"{days}d": sum(int(row.get(f"count_{days}d") or 0) for row in item_rows)
        for days in WEAR_WINDOWS
    }
    last_calendar_entry_on = calendar_summary["last_calendar_entry_on"]
    last_linked_wear_on = max(
        (row["last_worn_on"] for row in item_rows),
        default=None,
    )

    return {
        "as_of_date": as_of.isoformat(),
        "signal": {
            "type": "ACTUAL_WEAR",
            "strength": "STRONG",
            "source": "CALENDAR",
        },
        "windows_days": list(WEAR_WINDOWS),
        "entry_counts": entry_counts,
        "linked_item_occurrence_counts": linked_item_occurrence_counts,
        "last_calendar_entry_on": (
            last_calendar_entry_on.isoformat()
            if last_calendar_entry_on is not None
            else None
        ),
        "last_linked_wear_on": (
            last_linked_wear_on.isoformat() if last_linked_wear_on is not None else None
        ),
        "recent_entries": [_entry_payload(entry) for entry in recent_entries],
        "worn_items": worn_items,
        "repeated_combinations_30d": _repeated_combinations(recent_entries),
        "not_worn_in_30d_items": not_recently_worn_items,
    }
