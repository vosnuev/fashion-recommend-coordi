from __future__ import annotations

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


class CalendarDeleteApiTests(TestCase):
    def setUp(self) -> None:
        self.delete_s3_patcher = patch(
            "apps.style_calendar.services.calendar_service.storage.delete_calendar"
        )
        self.logger_patcher = patch(
            "apps.style_calendar.services.calendar_service.logger.exception"
        )
        self.mock_delete_s3 = self.delete_s3_patcher.start()
        self.mock_logger = self.logger_patcher.start()
        self.addCleanup(self.delete_s3_patcher.stop)
        self.addCleanup(self.logger_patcher.stop)

        self.user = User.objects.create(username="delete-user")
        self.other_user = User.objects.create(username="delete-other")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def create_entry(
        self,
        *,
        status: str = CalendarStatus.COMPLETED.value,
        entry_date: date = date(2026, 8, 4),
        user=None,
    ) -> CalendarEntry:
        owner = user or self.user
        return CalendarEntry.objects.create(
            user=owner,
            date=entry_date,
            source_type=CalendarSourceType.PHOTO_UPLOAD.value,
            image_s3_key=f"calendar/{owner.pk}/original.jpg",
            status=status,
        )

    @staticmethod
    def url_for(entry: CalendarEntry) -> str:
        return reverse(
            "style_calendar:calendar-detail",
            kwargs={"calendar_id": entry.pk},
        )

    def test_delete_completed_calendar_cascades_relations_and_cleans_s3(self) -> None:
        entry = self.create_entry()
        calendar_id = entry.pk
        wardrobe_item = WardrobeItem.objects.create(
            user=self.user,
            job=None,
            s3_key="wardrobe/delete/top.png",
            item_name="삭제되지 않을 옷장 아이템",
            category_large="상의",
        )
        link = CalendarWardrobeItem.objects.create(
            calendar=entry,
            wardrobe_item=wardrobe_item,
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(self.url_for(entry))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(CalendarEntry.objects.filter(pk=calendar_id).exists())
        self.assertFalse(CalendarWardrobeItem.objects.filter(pk=link.pk).exists())
        self.assertTrue(WardrobeItem.objects.filter(pk=wardrobe_item.pk).exists())
        self.mock_delete_s3.assert_called_once_with(self.user.pk, calendar_id)

    def test_delete_failed_calendar_is_allowed(self) -> None:
        entry = self.create_entry(status=CalendarStatus.FAILED.value)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(self.url_for(entry))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(CalendarEntry.objects.filter(pk=entry.pk).exists())
        self.mock_delete_s3.assert_called_once()

    def test_delete_rejects_registered_and_processing_calendar(self) -> None:
        registered = self.create_entry(status=CalendarStatus.REGISTERED.value)
        processing = self.create_entry(
            status=CalendarStatus.PROCESSING.value,
            entry_date=date(2026, 8, 5),
        )

        for entry in (registered, processing):
            with self.subTest(status=entry.status):
                response = self.client.delete(self.url_for(entry))
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.data["status"], entry.status)
                self.assertTrue(CalendarEntry.objects.filter(pk=entry.pk).exists())
        self.mock_delete_s3.assert_not_called()

    def test_delete_requires_authentication_and_ownership(self) -> None:
        own_entry = self.create_entry()
        other_entry = self.create_entry(
            user=self.other_user,
            entry_date=date(2026, 8, 5),
        )

        unauthenticated = APIClient().delete(self.url_for(own_entry))
        other_response = self.client.delete(self.url_for(other_entry))

        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(other_response.status_code, 404)
        self.assertTrue(CalendarEntry.objects.filter(pk=own_entry.pk).exists())
        self.assertTrue(CalendarEntry.objects.filter(pk=other_entry.pk).exists())
        self.mock_delete_s3.assert_not_called()

    def test_s3_cleanup_failure_does_not_restore_deleted_database_row(self) -> None:
        entry = self.create_entry()
        calendar_id = entry.pk
        self.mock_delete_s3.side_effect = RuntimeError("s3 unavailable")

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(self.url_for(entry))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(CalendarEntry.objects.filter(pk=calendar_id).exists())
        self.mock_logger.assert_called_once()
