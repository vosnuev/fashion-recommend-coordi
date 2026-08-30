from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.style_calendar.contracts import (
    CalendarProcessingErrorCode,
    CalendarSourceType,
    CalendarStatus,
)
from apps.style_calendar.models import CalendarEntry, CalendarWardrobeItem
from apps.users.models import User
from apps.wardrobe.models import WardrobeItem, WardrobeUploadJob


class CalendarProcessingStatusApiTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create(username="status-user")
        self.other_user = User.objects.create(username="status-other")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.job = WardrobeUploadJob.objects.create(
            user=self.user,
            source_s3_key="wardrobe/status/job/original.jpg",
        )
        self.entry = CalendarEntry.objects.create(
            user=self.user,
            wardrobe_upload_job=self.job,
            date=date(2026, 8, 4),
            source_type=CalendarSourceType.PHOTO_UPLOAD.value,
            image_s3_key="calendar/status/original.jpg",
            status=CalendarStatus.REGISTERED.value,
        )

    def url_for(self, entry: CalendarEntry) -> str:
        return reverse(
            "style_calendar:calendar-processing-status",
            kwargs={"calendar_id": entry.pk},
        )

    def test_registered_status_response(self) -> None:
        response = self.client.get(self.url_for(self.entry))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["calendar_id"], str(self.entry.pk))
        self.assertEqual(response.data["status"], CalendarStatus.REGISTERED.value)
        self.assertTrue(response.data["processing_required"])
        self.assertFalse(response.data["is_terminal"])
        self.assertFalse(response.data["result_available"])
        self.assertIsNone(response.data["failure"])
        self.assertEqual(
            response.data["item_counts"],
            {"total": 0, "extracted": 0, "failed": 0},
        )

    def test_completed_status_counts_only_items_created_by_its_job(self) -> None:
        self.entry.status = CalendarStatus.COMPLETED.value
        self.entry.processing_completed_at = timezone.now()
        self.entry.save()
        extracted = WardrobeItem.objects.create(
            user=self.user,
            job=self.job,
            s3_key="wardrobe/status/job/item_000.png",
            category_large="상의",
        )
        manually_selected = WardrobeItem.objects.create(
            user=self.user,
            job=None,
            s3_key="wardrobe/status/manual.png",
            category_large="하의",
        )
        CalendarWardrobeItem.objects.create(
            calendar=self.entry,
            wardrobe_item=manually_selected,
            sort_order=0,
        )
        CalendarWardrobeItem.objects.create(
            calendar=self.entry,
            wardrobe_item=extracted,
            sort_order=1,
        )

        response = self.client.get(self.url_for(self.entry))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_terminal"])
        self.assertTrue(response.data["result_available"])
        self.assertEqual(
            response.data["item_counts"],
            {"total": 1, "extracted": 1, "failed": 0},
        )

    def test_failed_status_returns_safe_public_failure(self) -> None:
        self.entry.status = CalendarStatus.FAILED.value
        self.entry.processing_error_code = (
            CalendarProcessingErrorCode.QUEUE_ENQUEUE_FAILED.value
        )
        self.entry.processing_error_message = "redis host=private.internal 연결 실패"
        self.entry.processing_completed_at = timezone.now()
        self.entry.save()

        response = self.client.get(self.url_for(self.entry))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_terminal"])
        self.assertFalse(response.data["result_available"])
        self.assertEqual(
            response.data["failure"],
            {
                "code": "QUEUE_ENQUEUE_FAILED",
                "message": "처리 대기열 등록에 실패했습니다. 잠시 후 다시 시도해주세요.",
            },
        )
        self.assertNotIn("private.internal", str(response.data))

    def test_direct_wardrobe_calendar_does_not_require_processing(self) -> None:
        direct_entry = CalendarEntry.objects.create(
            user=self.user,
            date=date(2026, 8, 5),
            source_type=CalendarSourceType.WARDROBE_SELECTED.value,
            image_s3_key="calendar/direct/item.png",
            status=CalendarStatus.COMPLETED.value,
        )

        response = self.client.get(self.url_for(direct_entry))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["processing_required"])
        self.assertTrue(response.data["is_terminal"])
        self.assertTrue(response.data["result_available"])

    def test_status_requires_authentication_and_ownership(self) -> None:
        other_entry = CalendarEntry.objects.create(
            user=self.other_user,
            date=date(2026, 8, 6),
            source_type=CalendarSourceType.PHOTO_UPLOAD.value,
            image_s3_key="calendar/other/original.jpg",
        )

        unauthenticated = APIClient().get(self.url_for(self.entry))
        other_user_response = self.client.get(self.url_for(other_entry))

        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(other_user_response.status_code, 404)
