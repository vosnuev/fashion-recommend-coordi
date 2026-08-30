"""'캘린더에도 기록하기'를 켠 룩북 등록."""

from datetime import date

from django.urls import reverse

from apps.lookbook.models import LookbookPost
from apps.lookbook.tests.base import LookbookApiTestCase, make_image_file
from apps.style_calendar.contracts import CalendarSourceType, CalendarStatus
from apps.style_calendar.models import CalendarEntry

ENTRY_DATE = "2026-08-08"


class LookbookCalendarLinkTests(LookbookApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.photo_url = reverse("lookbook:lookbook-photo-create")
        self.wardrobe_url = reverse("lookbook:lookbook-wardrobe-create")

    def _photo_payload(self, **overrides):
        payload = {
            "image": make_image_file(),
            "schedule": "친구 결혼식",
            "tpo": ["행사"],
            "hashtags": ["데이트"],
            "calendar_date": ENTRY_DATE,
        }
        payload.update(overrides)
        return payload

    def _existing_entry(self, *, status=CalendarStatus.COMPLETED.value) -> CalendarEntry:
        return CalendarEntry.objects.create(
            user=self.user,
            date=date(2026, 8, 8),
            source_type=CalendarSourceType.WARDROBE_SELECTED.value,
            image_s3_key="calendar/user/existing.png",
            status=status,
        )

    def test_photo_lookbook_and_calendar_share_one_processing_job(self) -> None:
        """같은 사진을 GPU가 두 번 처리하지 않도록 job을 공유한다."""

        response = self.client.post(
            self.photo_url,
            self._photo_payload(wardrobe_item_ids=[str(self.top.pk)]),
            format="multipart",
        )

        self.assertEqual(response.status_code, 202)
        post = LookbookPost.objects.get(pk=response.data["id"])
        entry = CalendarEntry.objects.get(user=self.user, date=date(2026, 8, 8))

        self.assertEqual(post.calendar_entry_id, entry.pk)
        self.assertEqual(post.wardrobe_upload_job_id, entry.wardrobe_upload_job_id)
        self.assertEqual(entry.source_type, CalendarSourceType.PHOTO_UPLOAD.value)
        self.assertEqual(entry.status, CalendarStatus.REGISTERED.value)
        self.assertEqual(entry.schedule, "친구 결혼식")
        self.assertEqual(entry.hashtags, ["데이트"])
        # 큐에는 한 번만 실린다.
        self.assertEqual(self.mocks["enqueue"].call_count, 1)

        # 캘린더는 자기 버킷 키만 presign하므로 원본이 캘린더 prefix로 복사돼야 한다.
        self.assertEqual(
            entry.image_s3_key,
            f"calendar/{self.user.pk}/{entry.pk}/original.jpg",
        )
        self.mocks["copy_original_to_calendar"].assert_called_once_with(
            post.image_s3_key,
            entry.image_s3_key,
        )
        self.assertEqual(
            response.data["calendar"],
            {"id": str(entry.pk), "date": ENTRY_DATE},
        )

    def test_existing_calendar_date_returns_conflict_without_creating_anything(self) -> None:
        self._existing_entry()

        response = self.client.post(self.photo_url, self._photo_payload(), format="multipart")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "CALENDAR_DATE_CONFLICT")
        self.assertEqual(response.data["date"], ENTRY_DATE)
        self.assertFalse(LookbookPost.objects.exists())
        # 충돌은 S3를 건드리기 전에 걸러야 한다.
        self.mocks["upload_fileobj"].assert_not_called()
        self.mocks["enqueue"].assert_not_called()

    def test_overwrite_replaces_the_existing_calendar(self) -> None:
        existing = self._existing_entry()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.photo_url,
                self._photo_payload(overwrite_calendar=True),
                format="multipart",
            )

        self.assertEqual(response.status_code, 202)
        self.assertFalse(CalendarEntry.objects.filter(pk=existing.pk).exists())
        entry = CalendarEntry.objects.get(user=self.user, date=date(2026, 8, 8))
        post = LookbookPost.objects.get(pk=response.data["id"])
        self.assertEqual(post.calendar_entry_id, entry.pk)
        self.mocks["calendar_delete_calendar"].assert_called_once()

    def test_overwrite_is_refused_while_the_existing_calendar_is_processing(self) -> None:
        self._existing_entry(status=CalendarStatus.REGISTERED.value)

        response = self.client.post(
            self.photo_url,
            self._photo_payload(overwrite_calendar=True),
            format="multipart",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "CALENDAR_BUSY")
        self.assertFalse(LookbookPost.objects.exists())
        self.assertEqual(CalendarEntry.objects.count(), 1)

    def test_wardrobe_lookbook_creates_a_completed_calendar(self) -> None:
        response = self.client.post(
            self.wardrobe_url,
            {
                "wardrobe_item_ids": [str(self.top.pk), str(self.bottom.pk)],
                "schedule": "팀 회의",
                "tpo": ["출근"],
                "hashtags": ["출근"],
                "calendar_date": ENTRY_DATE,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        post = LookbookPost.objects.get(pk=response.data["id"])
        entry = CalendarEntry.objects.get(user=self.user, date=date(2026, 8, 8))
        self.assertEqual(post.calendar_entry_id, entry.pk)
        self.assertEqual(entry.source_type, CalendarSourceType.WARDROBE_SELECTED.value)
        self.assertEqual(entry.status, CalendarStatus.COMPLETED.value)
        self.assertEqual(entry.wardrobe_links.count(), 2)
        self.assertEqual(post.wardrobe_links.count(), 2)

    def test_calendar_failure_rolls_the_lookbook_back(self) -> None:
        """캘린더 생성이 실패하면 룩북도 남지 않는다."""

        self.mocks["copy_original_to_calendar"].side_effect = RuntimeError("copy failed")

        response = self.client.post(self.photo_url, self._photo_payload(), format="multipart")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(LookbookPost.objects.exists())
        self.assertFalse(CalendarEntry.objects.exists())
        self.mocks["enqueue"].assert_not_called()

    def test_calendar_side_failure_cleans_the_calendar_bucket_copy(self) -> None:
        """캘린더 버킷으로 복사한 원본은 룩북 정리 경로가 손대지 못한다."""

        self.mocks["calendar_copy_wardrobe_item"].side_effect = RuntimeError("copy failed")

        response = self.client.post(
            self.photo_url,
            self._photo_payload(wardrobe_item_ids=[str(self.top.pk)]),
            format="multipart",
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(LookbookPost.objects.exists())
        self.assertFalse(CalendarEntry.objects.exists())
        self.mocks["calendar_delete_objects"].assert_called_once()
        cleaned = self.mocks["calendar_delete_objects"].call_args.args[0]
        self.assertEqual(len(cleaned), 1)
        self.assertTrue(cleaned[0].startswith(f"calendar/{self.user.pk}/"))
        self.assertTrue(cleaned[0].endswith("/original.jpg"))

    def test_deleting_the_lookbook_keeps_the_calendar(self) -> None:
        """룩북을 내려도 '그날 무엇을 입었는지'는 남는다."""

        created = self.client.post(
            self.wardrobe_url,
            {
                "wardrobe_item_ids": [str(self.top.pk)],
                "calendar_date": ENTRY_DATE,
            },
            format="json",
        )
        post_id = created.data["id"]
        entry_id = created.data["calendar"]["id"]

        response = self.client.delete(
            reverse("lookbook:lookbook-detail", args=[post_id])
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(LookbookPost.objects.filter(pk=post_id).exists())
        self.assertTrue(CalendarEntry.objects.filter(pk=entry_id).exists())
