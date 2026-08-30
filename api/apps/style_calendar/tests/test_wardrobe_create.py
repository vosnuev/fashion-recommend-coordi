from datetime import date
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.style_calendar.contracts import CalendarSourceType, CalendarStatus
from apps.style_calendar.models import (
    CalendarEntry,
    CalendarWardrobeItem,
)
from apps.users.models import User
from apps.wardrobe.models import WardrobeItem


class CalendarWardrobeCreateApiTests(TestCase):
    def setUp(self) -> None:
        copy_patcher = patch(
            "apps.style_calendar.services.calendar_service.storage.copy_wardrobe_item"
        )
        delete_patcher = patch(
            "apps.style_calendar.services.calendar_service.storage.delete_objects"
        )
        presigned_patcher = patch(
            "apps.style_calendar.serializers.storage.presigned_get",
            side_effect=lambda key: f"https://calendar.example/{key}" if key else "",
        )
        self.mock_copy_wardrobe_item = copy_patcher.start()
        self.mock_delete_objects = delete_patcher.start()
        self.mock_presigned_get = presigned_patcher.start()
        self.addCleanup(copy_patcher.stop)
        self.addCleanup(delete_patcher.stop)
        self.addCleanup(presigned_patcher.stop)

        self.client = APIClient()
        self.user = User.objects.create(username="wardrobe-calendar-user")
        self.other_user = User.objects.create(username="wardrobe-calendar-other")
        self.client.force_authenticate(self.user)
        self.url = reverse("style_calendar:calendar-wardrobe-create")

        self.top = WardrobeItem.objects.create(
            user=self.user,
            job=None,
            s3_key="wardrobe/user/top.png",
            item_name="흰색 반팔",
            category_large="상의",
            category_small="티셔츠",
            season=["여름"],
            style=["캐주얼"],
            color="화이트",
            confirmed=True,
        )
        self.bottom = WardrobeItem.objects.create(
            user=self.user,
            job=None,
            s3_key="wardrobe/user/bottom.png",
            item_name="검정 슬랙스",
            category_large="하의",
            category_small="슬랙스",
            color="블랙",
            confirmed=True,
        )
        self.other_item = WardrobeItem.objects.create(
            user=self.other_user,
            job=None,
            s3_key="wardrobe/other/outer.png",
            item_name="다른 사용자의 재킷",
            category_large="아우터",
            confirmed=True,
        )

    def _payload(self, *, entry_date: str = "2026-08-06") -> dict[str, object]:
        return {
            "date": entry_date,
            "wardrobe_item_ids": [str(self.bottom.pk), str(self.top.pk)],
            "schedule": "출근 후 저녁 약속",
            "tpo": ["출근", "모임"],
            "hashtags": ["포멀", "여름"],
        }

    def test_create_from_wardrobe_creates_completed_calendar_and_links(self) -> None:
        response = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(response.status_code, 201)
        entry = CalendarEntry.objects.get(pk=response.data["id"])
        self.assertEqual(entry.user, self.user)
        self.assertEqual(entry.date, date(2026, 8, 6))
        self.assertEqual(entry.source_type, CalendarSourceType.WARDROBE_SELECTED.value)
        self.assertEqual(entry.status, CalendarStatus.COMPLETED.value)
        self.assertTrue(
            entry.image_s3_key.startswith(
                f"calendar/{self.user.pk}/{entry.pk}/selected/"
            )
        )
        self.assertTrue(entry.image_s3_key.endswith(".png"))
        self.assertEqual(entry.schedule, "출근 후 저녁 약속")
        self.assertEqual(entry.tpo, ["출근", "모임"])
        self.assertEqual(entry.hashtags, ["포멀", "여름"])

        links = list(entry.wardrobe_links.order_by("sort_order"))
        self.assertEqual(
            [link.wardrobe_item_id for link in links],
            [self.bottom.pk, self.top.pk],
        )
        self.assertEqual([link.sort_order for link in links], [0, 1])
        self.assertEqual(links[0].snapshot["item_name"], self.bottom.item_name)
        self.assertEqual(links[0].snapshot["s3_key"], entry.image_s3_key)
        self.assertEqual(
            links[0].snapshot["source_wardrobe_s3_key"],
            self.bottom.s3_key,
        )
        self.assertEqual(links[1].snapshot["tags"]["season"], ["여름"])
        self.assertEqual(
            [item["wardrobe_item_id"] for item in response.data["wardrobe_items"]],
            [str(self.bottom.pk), str(self.top.pk)],
        )
        self.assertNotIn("items", response.data)
        self.assertEqual(
            response.data["image_url"],
            f"https://calendar.example/{entry.image_s3_key}",
        )
        self.assertEqual(self.mock_copy_wardrobe_item.call_count, 2)
        self.mock_copy_wardrobe_item.assert_any_call(
            self.bottom.s3_key,
            links[0].snapshot["s3_key"],
        )
        self.mock_copy_wardrobe_item.assert_any_call(
            self.top.s3_key,
            links[1].snapshot["s3_key"],
        )

    def test_create_uses_defaults_for_optional_metadata(self) -> None:
        response = self.client.post(
            self.url,
            {
                "date": "2026-08-07",
                "wardrobe_item_ids": [str(self.top.pk)],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["schedule"], "")
        self.assertEqual(response.data["tpo"], [])
        self.assertEqual(response.data["hashtags"], [])

    def test_create_rejects_empty_or_duplicate_item_ids(self) -> None:
        empty = self.client.post(
            self.url,
            {"date": "2026-08-06", "wardrobe_item_ids": []},
            format="json",
        )
        duplicate = self.client.post(
            self.url,
            {
                "date": "2026-08-06",
                "wardrobe_item_ids": [str(self.top.pk), str(self.top.pk)],
            },
            format="json",
        )

        self.assertEqual(empty.status_code, 400)
        self.assertEqual(duplicate.status_code, 400)
        self.assertFalse(CalendarEntry.objects.filter(user=self.user).exists())

    def test_create_rejects_missing_or_other_users_items(self) -> None:
        other_user_item = self.client.post(
            self.url,
            {
                "date": "2026-08-06",
                "wardrobe_item_ids": [str(self.other_item.pk)],
            },
            format="json",
        )
        missing_item = self.client.post(
            self.url,
            {
                "date": "2026-08-06",
                "wardrobe_item_ids": [str(uuid4())],
            },
            format="json",
        )

        self.assertEqual(other_user_item.status_code, 400)
        self.assertEqual(missing_item.status_code, 400)
        self.assertFalse(CalendarEntry.objects.filter(user=self.user).exists())

    def test_create_returns_conflict_when_date_already_has_calendar(self) -> None:
        CalendarEntry.objects.create(
            user=self.user,
            date=date(2026, 8, 6),
            source_type=CalendarSourceType.PHOTO_UPLOAD.value,
            image_s3_key="calendar/user/existing.jpg",
        )

        response = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            CalendarEntry.objects.filter(user=self.user, date=date(2026, 8, 6)).count(),
            1,
        )
        self.assertEqual(CalendarWardrobeItem.objects.count(), 0)
        self.mock_copy_wardrobe_item.assert_not_called()
        self.mock_delete_objects.assert_not_called()

    def test_create_rejects_photo_fields_on_wardrobe_only_endpoint(self) -> None:
        payload = self._payload()
        payload["image_s3_key"] = "user-upload/photo.jpg"

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(CalendarEntry.objects.filter(user=self.user).exists())

    def test_same_wardrobe_item_can_be_used_by_multiple_calendars(self) -> None:
        first = self.client.post(
            self.url,
            {
                "date": "2026-08-06",
                "wardrobe_item_ids": [str(self.top.pk)],
            },
            format="json",
        )
        second = self.client.post(
            self.url,
            {
                "date": "2026-08-07",
                "wardrobe_item_ids": [str(self.top.pk)],
            },
            format="json",
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(
            CalendarWardrobeItem.objects.filter(wardrobe_item=self.top).count(),
            2,
        )

    def test_storage_failure_returns_503_and_rolls_back_copied_objects(self) -> None:
        self.mock_copy_wardrobe_item.side_effect = [None, RuntimeError("copy failed")]

        response = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(CalendarEntry.objects.filter(user=self.user).exists())
        self.assertEqual(CalendarWardrobeItem.objects.count(), 0)
        self.mock_delete_objects.assert_called_once()
        deleted_keys = self.mock_delete_objects.call_args.args[0]
        self.assertEqual(len(deleted_keys), 1)

    def test_create_requires_authentication(self) -> None:
        response = APIClient().post(self.url, self._payload(), format="json")

        self.assertEqual(response.status_code, 401)
        self.assertFalse(CalendarEntry.objects.filter(user=self.user).exists())
