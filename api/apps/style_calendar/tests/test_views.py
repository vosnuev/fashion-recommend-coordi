from datetime import date
from unittest.mock import patch

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


class CalendarApiTests(TestCase):
    def setUp(self) -> None:
        presigned_patcher = patch(
            "apps.style_calendar.serializers.storage.presigned_get",
            side_effect=lambda key: f"https://calendar.example/{key}" if key else "",
        )
        self.mock_presigned_get = presigned_patcher.start()
        self.addCleanup(presigned_patcher.stop)

        self.client = APIClient()
        self.user = User.objects.create(username="calendar-user", nickname="캘린더 사용자")
        self.other_user = User.objects.create(username="other-user", nickname="다른 사용자")
        self.client.force_authenticate(self.user)

        self.entry = CalendarEntry.objects.create(
            user=self.user,
            date=date(2026, 8, 4),
            source_type=CalendarSourceType.PHOTO_UPLOAD.value,
            image_s3_key="calendar/user/entry/original.jpg",
            schedule="친구와 저녁 약속",
            tpo=["데이트"],
            hashtags=["여름", "캐주얼"],
            status=CalendarStatus.COMPLETED.value,
        )
        self.older_entry = CalendarEntry.objects.create(
            user=self.user,
            date=date(2026, 8, 1),
            source_type=CalendarSourceType.WARDROBE_SELECTED.value,
            image_s3_key="calendar/user/older/original.jpg",
            status=CalendarStatus.COMPLETED.value,
        )
        self.other_entry = CalendarEntry.objects.create(
            user=self.other_user,
            date=date(2026, 8, 5),
            source_type=CalendarSourceType.PHOTO_UPLOAD.value,
            image_s3_key="calendar/other/entry/original.jpg",
        )

        wardrobe_item = WardrobeItem.objects.create(
            user=self.user,
            job=None,
            s3_key="wardrobe/user/top.png",
            item_name="흰색 반팔",
            category_large="상의",
            confirmed=True,
        )
        self.wardrobe_link = CalendarWardrobeItem.objects.create(
            calendar=self.entry,
            wardrobe_item=wardrobe_item,
            sort_order=0,
            snapshot={"item_name": "흰색 반팔", "s3_key": wardrobe_item.s3_key},
        )

    def test_read_endpoints_require_authentication(self) -> None:
        client = APIClient()
        urls = [
            reverse("style_calendar:calendar-list")
            + "?start_date=2026-08-01&end_date=2026-08-31",
            reverse("style_calendar:calendar-by-date") + "?date=2026-08-04",
            reverse(
                "style_calendar:calendar-detail",
                kwargs={"calendar_id": self.entry.pk},
            ),
        ]

        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 401)

    def test_period_list_returns_only_my_entries_in_range(self) -> None:
        response = self.client.get(
            reverse("style_calendar:calendar-list"),
            {"start_date": "2026-08-02", "end_date": "2026-08-31"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], str(self.entry.pk))

    def test_period_list_includes_both_boundary_dates(self) -> None:
        response = self.client.get(
            reverse("style_calendar:calendar-list"),
            {"start_date": "2026-08-01", "end_date": "2026-08-04"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["date"] for item in response.data],
            ["2026-08-04", "2026-08-01"],
        )

    def test_period_list_validates_required_dates_and_order(self) -> None:
        url = reverse("style_calendar:calendar-list")

        missing = self.client.get(url, {"start_date": "2026-08-01"})
        reversed_period = self.client.get(
            url,
            {"start_date": "2026-08-05", "end_date": "2026-08-01"},
        )
        invalid = self.client.get(
            url,
            {"start_date": "not-a-date", "end_date": "2026-08-31"},
        )

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(reversed_period.status_code, 400)
        self.assertEqual(invalid.status_code, 400)

    def test_by_date_returns_calendar_with_linked_wardrobe_items(self) -> None:
        response = self.client.get(
            reverse("style_calendar:calendar-by-date"),
            {"date": "2026-08-04"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], str(self.entry.pk))
        self.assertEqual(
            response.data["wardrobe_items"][0]["link_id"],
            str(self.wardrobe_link.pk),
        )
        self.assertEqual(
            response.data["wardrobe_items"][0]["wardrobe_item_id"],
            str(self.wardrobe_link.wardrobe_item_id),
        )
        self.assertNotIn("items", response.data)

    def test_by_date_returns_404_for_missing_or_other_users_entry(self) -> None:
        missing = self.client.get(
            reverse("style_calendar:calendar-by-date"),
            {"date": "2026-08-10"},
        )
        other_only = self.client.get(
            reverse("style_calendar:calendar-by-date"),
            {"date": str(self.other_entry.date)},
        )

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(other_only.status_code, 404)

    def test_detail_returns_only_owned_calendar(self) -> None:
        own_response = self.client.get(
            reverse(
                "style_calendar:calendar-detail",
                kwargs={"calendar_id": self.entry.pk},
            )
        )
        other_response = self.client.get(
            reverse(
                "style_calendar:calendar-detail",
                kwargs={"calendar_id": self.other_entry.pk},
            )
        )

        self.assertEqual(own_response.status_code, 200)
        self.assertEqual(own_response.data["schedule"], "친구와 저녁 약속")
        self.assertEqual(other_response.status_code, 404)

    def test_patch_updates_calendar_metadata(self) -> None:
        response = self.client.patch(
            reverse(
                "style_calendar:calendar-detail",
                kwargs={"calendar_id": self.entry.pk},
            ),
            {
                "schedule": "회사 회식",
                "tpo": ["출근", "모임"],
                "hashtags": ["포멀", "여름"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.schedule, "회사 회식")
        self.assertEqual(self.entry.tpo, ["출근", "모임"])
        self.assertEqual(self.entry.hashtags, ["포멀", "여름"])
        self.assertEqual(response.data["schedule"], "회사 회식")
        self.assertEqual(response.data["wardrobe_items"][0]["link_id"], str(self.wardrobe_link.pk))

    def test_patch_supports_partial_metadata_update(self) -> None:
        response = self.client.patch(
            reverse(
                "style_calendar:calendar-detail",
                kwargs={"calendar_id": self.entry.pk},
            ),
            {"schedule": "점심 약속"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.schedule, "점심 약속")
        self.assertEqual(self.entry.tpo, ["데이트"])
        self.assertEqual(self.entry.hashtags, ["여름", "캐주얼"])

    def test_patch_rejects_non_metadata_fields(self) -> None:
        original_date = self.entry.date
        original_status = self.entry.status
        response = self.client.patch(
            reverse(
                "style_calendar:calendar-detail",
                kwargs={"calendar_id": self.entry.pk},
            ),
            {"date": "2026-08-10", "status": CalendarStatus.FAILED.value},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.date, original_date)
        self.assertEqual(self.entry.status, original_status)

    def test_patch_validates_tpo_and_hashtags_as_string_lists(self) -> None:
        url = reverse(
            "style_calendar:calendar-detail",
            kwargs={"calendar_id": self.entry.pk},
        )

        non_list = self.client.patch(url, {"tpo": "데이트"}, format="json")
        non_string_item = self.client.patch(
            url,
            {"hashtags": ["여름", 1]},
            format="json",
        )

        self.assertEqual(non_list.status_code, 400)
        self.assertEqual(non_string_item.status_code, 400)

    def test_patch_rejects_non_object_request_body(self) -> None:
        response = self.client.patch(
            reverse(
                "style_calendar:calendar-detail",
                kwargs={"calendar_id": self.entry.pk},
            ),
            ["schedule", "잘못된 본문"],
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_patch_requires_authentication_and_ownership(self) -> None:
        own_url = reverse(
            "style_calendar:calendar-detail",
            kwargs={"calendar_id": self.entry.pk},
        )
        other_url = reverse(
            "style_calendar:calendar-detail",
            kwargs={"calendar_id": self.other_entry.pk},
        )

        unauthenticated = APIClient().patch(
            own_url,
            {"schedule": "변경 시도"},
            format="json",
        )
        other_user_entry = self.client.patch(
            other_url,
            {"schedule": "변경 시도"},
            format="json",
        )

        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(other_user_entry.status_code, 404)
