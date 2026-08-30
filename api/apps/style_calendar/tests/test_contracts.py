from unittest import TestCase

from apps.style_calendar.contracts import CalendarSourceType, CalendarStatus


class CalendarContractTests(TestCase):
    def test_calendar_status_does_not_include_partial(self) -> None:
        self.assertEqual(
            {status.value for status in CalendarStatus},
            {"REGISTERED", "PROCESSING", "COMPLETED", "FAILED"},
        )

    def test_source_types_only_describe_registration_path(self) -> None:
        self.assertEqual(
            {source.value for source in CalendarSourceType},
            {"PHOTO_UPLOAD", "WARDROBE_SELECTED"},
        )
