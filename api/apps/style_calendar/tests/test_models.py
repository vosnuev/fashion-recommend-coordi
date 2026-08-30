from datetime import date

from django.db import IntegrityError, models, transaction
from django.test import SimpleTestCase, TestCase

from apps.style_calendar.contracts import CalendarSourceType, CalendarStatus
from apps.style_calendar.models import CalendarEntry, CalendarWardrobeItem
from apps.users.models import User
from apps.wardrobe.models import WardrobeItem, WardrobeUploadJob


class CalendarModelMetadataTests(SimpleTestCase):
    def test_explicit_table_names_and_comments(self) -> None:
        for model, table_name in (
            (CalendarEntry, "calendar_entry"),
            (CalendarWardrobeItem, "calendar_wardrobe_item"),
        ):
            with self.subTest(model=model.__name__):
                self.assertEqual(model._meta.db_table, table_name)
                self.assertTrue(model._meta.db_table_comment)

    def test_every_database_field_has_comment(self) -> None:
        for model in (CalendarEntry, CalendarWardrobeItem):
            for field in model._meta.local_fields:
                with self.subTest(model=model.__name__, field=field.name):
                    self.assertTrue(field.db_comment)

    def test_calendar_uses_explicit_many_to_many_relation(self) -> None:
        field = CalendarEntry._meta.get_field("wardrobe_items")

        self.assertTrue(field.many_to_many)
        self.assertIs(field.remote_field.through, CalendarWardrobeItem)

    def test_upload_job_relation_is_nullable_one_to_one(self) -> None:
        field = CalendarEntry._meta.get_field("wardrobe_upload_job")

        self.assertTrue(field.one_to_one)
        self.assertTrue(field.null)
        self.assertIs(field.remote_field.on_delete, models.SET_NULL)

    def test_calendar_schema_has_no_matching_or_processor_item_fields(self) -> None:
        field_names = {
            field.name
            for model in (CalendarEntry, CalendarWardrobeItem)
            for field in model._meta.local_fields
        }

        self.assertFalse(
            field_names
            & {
                "embedding",
                "image_embedding",
                "text_embedding",
                "match_score",
                "matched",
                "unmatched",
                "processor_item_id",
            }
        )

    def test_expected_database_constraints_are_declared(self) -> None:
        self.assertEqual(
            {constraint.name for constraint in CalendarEntry._meta.constraints},
            {"uq_calendar_user_date"},
        )
        self.assertEqual(
            {
                constraint.name
                for constraint in CalendarWardrobeItem._meta.constraints
            },
            {"uq_cal_wardrobe_link"},
        )


class CalendarModelConstraintTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create(username="calendar-model-user")
        self.other_user = User.objects.create(username="calendar-model-other")
        self.entry = self.create_entry(self.user, date(2026, 8, 4))

    @staticmethod
    def create_entry(user: User, entry_date: date) -> CalendarEntry:
        return CalendarEntry.objects.create(
            user=user,
            date=entry_date,
            source_type=CalendarSourceType.PHOTO_UPLOAD.value,
            image_s3_key=f"calendar/{user.pk}/{entry_date}/original.jpg",
            status=CalendarStatus.COMPLETED.value,
        )

    def test_user_can_have_only_one_calendar_per_date(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_entry(self.user, self.entry.date)

        other_entry = self.create_entry(self.other_user, self.entry.date)
        self.assertEqual(other_entry.date, self.entry.date)

    def test_one_upload_job_can_belong_to_only_one_calendar(self) -> None:
        job = WardrobeUploadJob.objects.create(
            user=self.user,
            source_s3_key="wardrobe/model/job/original.jpg",
        )
        self.entry.wardrobe_upload_job = job
        self.entry.save(update_fields=["wardrobe_upload_job"])

        with self.assertRaises(IntegrityError), transaction.atomic():
            CalendarEntry.objects.create(
                user=self.user,
                wardrobe_upload_job=job,
                date=date(2026, 8, 5),
                source_type=CalendarSourceType.PHOTO_UPLOAD.value,
                image_s3_key="calendar/model/second/original.jpg",
            )

    def test_wardrobe_link_is_unique_per_calendar_but_reusable(self) -> None:
        wardrobe_item = WardrobeItem.objects.create(
            user=self.user,
            job=None,
            s3_key="wardrobe/model/top.png",
            item_name="테스트 상의",
            category_large="상의",
        )
        CalendarWardrobeItem.objects.create(
            calendar=self.entry,
            wardrobe_item=wardrobe_item,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            CalendarWardrobeItem.objects.create(
                calendar=self.entry,
                wardrobe_item=wardrobe_item,
            )

        another_entry = self.create_entry(self.user, date(2026, 8, 5))
        reusable_link = CalendarWardrobeItem.objects.create(
            calendar=another_entry,
            wardrobe_item=wardrobe_item,
        )
        self.assertEqual(reusable_link.wardrobe_item, wardrobe_item)
