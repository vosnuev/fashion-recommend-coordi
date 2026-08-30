from unittest.mock import patch

from django.test import SimpleTestCase

from apps.style_calendar.services import storage


class CalendarEnvironmentValidationTests(SimpleTestCase):
    def test_storage_rejects_whitespace_only_bucket(self) -> None:
        with self.assertRaises(storage.CalendarStorageConfigurationError):
            storage._require_bucket("   ", "CALENDAR_S3_BUCKET")

    def test_calendar_to_wardrobe_copy_requires_both_buckets(self) -> None:
        with (
            patch.object(storage, "BUCKET", "calendar-bucket"),
            patch.object(storage, "WARDROBE_BUCKET", ""),
            self.assertRaises(storage.CalendarStorageConfigurationError),
        ):
            storage.copy_calendar_original_to_wardrobe(
                "calendar/1/entry/original.jpg",
                "wardrobe/1/job/original.jpg",
            )
