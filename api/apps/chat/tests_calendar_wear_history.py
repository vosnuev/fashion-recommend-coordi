from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.chat.services import identity as identity_service
from apps.chat.services.calendar_wear_history import (
    MemberCalendarWearHistoryRequired,
    load_calendar_wear_history,
)
from apps.style_calendar.contracts import CalendarSourceType, CalendarStatus
from apps.style_calendar.models import CalendarEntry, CalendarWardrobeItem
from apps.wardrobe.models import WardrobeItem

User = get_user_model()


class CalendarWearHistoryLoaderTests(TestCase):
    AS_OF = date(2026, 8, 15)

    def setUp(self) -> None:
        self.user = User.objects.create_user(username="calendar-wear-member")
        self.identity = identity_service.get_or_create_member_identity(self.user)
        self.other_user = User.objects.create_user(username="calendar-wear-other")

    def _item(
        self,
        name: str,
        *,
        user=None,
        confirmed: bool = True,
        in_closet: bool = True,
        category: str = "상의",
    ) -> WardrobeItem:
        return WardrobeItem.objects.create(
            user=user or self.user,
            job=None,
            s3_key=f"wardrobe/{name}.png",
            item_name=name,
            category_large=category,
            category_small="테스트",
            style=["현재스타일"],
            color="현재색상",
            fit="현재핏",
            confirmed=confirmed,
            added_to_closet_at=timezone.now() if in_closet else None,
        )

    def _entry(
        self,
        days_ago: int,
        *items: WardrobeItem,
        user=None,
        status: str = CalendarStatus.COMPLETED.value,
        snapshot_first: bool = False,
    ) -> CalendarEntry:
        owner = user or self.user
        worn_on = self.AS_OF - timedelta(days=days_ago)
        entry = CalendarEntry.objects.create(
            user=owner,
            date=worn_on,
            source_type=CalendarSourceType.WARDROBE_SELECTED.value,
            image_s3_key=f"calendar/{owner.pk}/{worn_on}.jpg",
            tpo=["DAILY"],
            status=status,
        )
        for index, item in enumerate(items):
            snapshot = {}
            if snapshot_first and index == 0:
                snapshot = {
                    "item_name": "착용 당시 상의",
                    "category_large": "상의",
                    "category_small": "셔츠",
                    "tags": {
                        "style": ["착용당시스타일"],
                        "color": "착용당시색상",
                        "fit": "착용당시핏",
                    },
                }
            CalendarWardrobeItem.objects.create(
                calendar=entry,
                wardrobe_item=item,
                sort_order=index,
                snapshot=snapshot,
            )
        return entry

    def test_aggregates_inclusive_7_14_30_day_windows_and_last_worn_date(
        self,
    ) -> None:
        top = self._item("상의")
        bottom = self._item("하의", category="하의")
        old_item = self._item("오래 안 입은 옷")
        never_worn = self._item("한 번도 안 입은 옷")
        self._item("옷장 밖 옷", in_closet=False)
        self._item("미확정 옷", confirmed=False)

        self._entry(
            0,
            top,
            status=CalendarStatus.FAILED.value,
            snapshot_first=True,
        )
        self._entry(1)  # 아이템 연결 없는 캘린더도 등록 횟수만 보존한다.
        self._entry(6, top, bottom)
        self._entry(7, top, bottom)
        self._entry(13, top, bottom)
        self._entry(14, top)
        self._entry(29, top)
        self._entry(30, top, old_item)
        self._entry(-1, top)  # 미래 기록은 집계하지 않는다.

        other_item = self._item("다른 회원 옷", user=self.other_user)
        self._entry(0, other_item, user=self.other_user)

        history = load_calendar_wear_history(
            identity=self.identity,
            as_of=self.AS_OF,
        )

        self.assertEqual(history["entry_counts"], {"7d": 3, "14d": 5, "30d": 7})
        self.assertEqual(
            history["linked_item_occurrence_counts"],
            {"7d": 3, "14d": 7, "30d": 9},
        )
        self.assertEqual(history["last_calendar_entry_on"], "2026-08-15")
        self.assertEqual(history["last_linked_wear_on"], "2026-08-15")
        self.assertEqual(history["signal"]["strength"], "STRONG")

        worn_by_id = {row["wardrobe_item_id"]: row for row in history["worn_items"]}
        self.assertEqual(
            worn_by_id[str(top.id)]["wear_counts"],
            {"7d": 2, "14d": 4, "30d": 6},
        )
        self.assertEqual(
            worn_by_id[str(bottom.id)]["wear_counts"],
            {"7d": 1, "14d": 3, "30d": 3},
        )
        self.assertEqual(
            worn_by_id[str(old_item.id)]["wear_counts"],
            {"7d": 0, "14d": 0, "30d": 0},
        )
        self.assertEqual(worn_by_id[str(old_item.id)]["last_worn_on"], "2026-07-16")

        recent_entry = history["recent_entries"][0]
        self.assertEqual(recent_entry["status"], CalendarStatus.FAILED.value)
        self.assertEqual(recent_entry["items"][0]["item_name"], "착용 당시 상의")
        self.assertEqual(
            recent_entry["items"][0]["styles"],
            ["착용당시스타일"],
        )

        repeated = history["repeated_combinations_30d"]
        self.assertEqual(len(repeated), 1)
        self.assertEqual(repeated[0]["count"], 3)
        self.assertEqual(
            set(repeated[0]["wardrobe_item_ids"]),
            {str(top.id), str(bottom.id)},
        )

        not_recent_by_id = {
            row["wardrobe_item_id"]: row for row in history["not_worn_in_30d_items"]
        }
        self.assertEqual(
            set(not_recent_by_id),
            {str(old_item.id), str(never_worn.id)},
        )
        self.assertEqual(
            not_recent_by_id[str(old_item.id)]["last_worn_on"],
            "2026-07-16",
        )
        self.assertIsNone(not_recent_by_id[str(never_worn.id)]["last_worn_on"])

    def test_unlinked_calendar_does_not_infer_worn_items(self) -> None:
        closet_item = self._item("추측하면 안 되는 옷")
        self._entry(0)

        history = load_calendar_wear_history(
            identity=self.identity,
            as_of=self.AS_OF,
        )

        self.assertEqual(history["entry_counts"]["7d"], 1)
        self.assertEqual(history["linked_item_occurrence_counts"]["7d"], 0)
        self.assertEqual(history["recent_entries"][0]["items"], [])
        self.assertEqual(history["worn_items"], [])
        self.assertEqual(
            history["not_worn_in_30d_items"][0]["wardrobe_item_id"],
            str(closet_item.id),
        )

    def test_ignores_cross_owner_links_defensively(self) -> None:
        other_item = self._item("다른 회원 옷", user=self.other_user)
        entry = self._entry(0)
        CalendarWardrobeItem.objects.create(
            calendar=entry,
            wardrobe_item=other_item,
        )

        history = load_calendar_wear_history(
            identity=self.identity,
            as_of=self.AS_OF,
        )

        self.assertEqual(history["recent_entries"][0]["items"], [])
        self.assertEqual(history["worn_items"], [])

    def test_requires_member_identity(self) -> None:
        guest_identity = identity_service.issue_guest_identity().identity

        with self.assertRaises(MemberCalendarWearHistoryRequired):
            load_calendar_wear_history(
                identity=guest_identity,
                as_of=self.AS_OF,
            )
