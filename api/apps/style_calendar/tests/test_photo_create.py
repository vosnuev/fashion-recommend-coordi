from datetime import date
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework.test import APIClient

from apps.style_calendar.contracts import CalendarSourceType, CalendarStatus
from apps.style_calendar.models import CalendarEntry
from apps.style_calendar.serializers import MAX_CALENDAR_UPLOAD_MB
from apps.users.models import User
from apps.wardrobe.models import WardrobeItem, WardrobeUploadJob


def make_image_file(
    name: str = "outfit.jpg",
    *,
    content_type: str = "image/jpeg",
    image_format: str = "JPEG",
    extra_size: int = 0,
) -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", (10, 10), "white").save(buffer, format=image_format)
    content = buffer.getvalue() + (b"\0" * extra_size)
    return SimpleUploadedFile(name, content, content_type=content_type)


class CalendarPhotoCreateApiTests(TestCase):
    def setUp(self) -> None:
        upload_patcher = patch(
            "apps.style_calendar.services.calendar_service.storage.upload_fileobj"
        )
        copy_patcher = patch(
            "apps.style_calendar.services.calendar_service.storage.copy_wardrobe_item"
        )
        copy_original_patcher = patch(
            "apps.style_calendar.services.calendar_service.storage."
            "copy_calendar_original_to_wardrobe"
        )
        delete_patcher = patch(
            "apps.style_calendar.services.calendar_service.storage.delete_objects"
        )
        wardrobe_delete_patcher = patch(
            "apps.style_calendar.services.calendar_service.wardrobe_storage."
            "delete_objects"
        )
        presigned_patcher = patch(
            "apps.style_calendar.serializers.storage.presigned_get",
            side_effect=lambda key: f"https://calendar.example/{key}" if key else "",
        )
        enqueue_patcher = patch("apps.style_calendar.views.wardrobe_jobs.enqueue")
        logger_patcher = patch("apps.style_calendar.views.logger.exception")
        self.mock_upload_fileobj = upload_patcher.start()
        self.mock_copy_wardrobe_item = copy_patcher.start()
        self.mock_copy_calendar_original = copy_original_patcher.start()
        self.mock_delete_objects = delete_patcher.start()
        self.mock_delete_wardrobe_objects = wardrobe_delete_patcher.start()
        self.mock_presigned_get = presigned_patcher.start()
        self.mock_enqueue = enqueue_patcher.start()
        self.mock_logger_exception = logger_patcher.start()
        self.addCleanup(upload_patcher.stop)
        self.addCleanup(copy_patcher.stop)
        self.addCleanup(copy_original_patcher.stop)
        self.addCleanup(delete_patcher.stop)
        self.addCleanup(wardrobe_delete_patcher.stop)
        self.addCleanup(presigned_patcher.stop)
        self.addCleanup(enqueue_patcher.stop)
        self.addCleanup(logger_patcher.stop)

        self.client = APIClient()
        self.user = User.objects.create(username="photo-calendar-user")
        self.other_user = User.objects.create(username="photo-calendar-other")
        self.client.force_authenticate(self.user)
        self.url = reverse("style_calendar:calendar-photo-create")

        self.top = WardrobeItem.objects.create(
            user=self.user,
            job=None,
            s3_key="wardrobe/user/top.png",
            item_name="흰색 반팔",
            category_large="상의",
            confirmed=True,
        )
        self.bottom = WardrobeItem.objects.create(
            user=self.user,
            job=None,
            s3_key="wardrobe/user/bottom.png",
            item_name="검정 바지",
            category_large="하의",
            confirmed=True,
        )
        self.other_item = WardrobeItem.objects.create(
            user=self.other_user,
            job=None,
            s3_key="wardrobe/other/item.png",
            item_name="다른 사용자 옷",
            category_large="상의",
            confirmed=True,
        )

    def _payload(self, *, image=None, entry_date: str = "2026-08-08"):
        return {
            "image": image or make_image_file(),
            "date": entry_date,
            "schedule": "주말 나들이",
            "tpo": ["데이트"],
            "hashtags": ["여름", "캐주얼"],
        }

    def test_photo_upload_saves_s3_before_registered_calendar(self) -> None:
        image = make_image_file("My Outfit.JPG")

        def assert_database_row_is_not_created_before_upload(*_args) -> None:
            self.assertFalse(CalendarEntry.objects.filter(user=self.user).exists())
            self.assertFalse(WardrobeUploadJob.objects.filter(user=self.user).exists())

        self.mock_upload_fileobj.side_effect = assert_database_row_is_not_created_before_upload
        response = self.client.post(
            self.url,
            self._payload(image=image),
            format="multipart",
        )

        self.assertEqual(response.status_code, 202)
        entry = CalendarEntry.objects.get(pk=response.data["id"])
        self.assertEqual(entry.user, self.user)
        self.assertEqual(entry.date, date(2026, 8, 8))
        self.assertEqual(entry.source_type, CalendarSourceType.PHOTO_UPLOAD.value)
        self.assertEqual(entry.status, CalendarStatus.REGISTERED.value)
        self.assertIsNotNone(entry.wardrobe_upload_job_id)
        self.assertEqual(
            entry.image_s3_key,
            f"calendar/{self.user.pk}/{entry.pk}/original.jpg",
        )
        self.assertEqual(entry.schedule, "주말 나들이")
        self.assertEqual(entry.tpo, ["데이트"])
        self.assertEqual(entry.hashtags, ["여름", "캐주얼"])
        self.assertIsNone(entry.weather_snapshot)
        self.assertEqual(entry.wardrobe_links.count(), 0)

        uploaded_file, uploaded_key, uploaded_type = self.mock_upload_fileobj.call_args.args
        self.assertEqual(uploaded_file.name, image.name)
        self.assertEqual(uploaded_key, entry.image_s3_key)
        self.assertEqual(uploaded_type, "image/jpeg")
        self.mock_copy_wardrobe_item.assert_not_called()
        job = entry.wardrobe_upload_job
        self.assertEqual(
            job.source_s3_key,
            f"wardrobe/{self.user.pk}/{job.pk}/original.jpg",
        )
        self.mock_copy_calendar_original.assert_called_once_with(
            entry.image_s3_key,
            job.source_s3_key,
        )
        self.assertEqual(
            response.data["image_url"],
            f"https://calendar.example/{entry.image_s3_key}",
        )
        self.assertEqual(self.mock_enqueue.call_count, 1)
        self.assertEqual(self.mock_enqueue.call_args.args[0].pk, job.pk)

    def test_user_photo_remains_representative_with_selected_wardrobe_items(self) -> None:
        payload = self._payload()
        payload["wardrobe_item_ids"] = [str(self.bottom.pk), str(self.top.pk)]

        response = self.client.post(self.url, payload, format="multipart")

        self.assertEqual(response.status_code, 202)
        entry = CalendarEntry.objects.get(pk=response.data["id"])
        self.assertTrue(entry.image_s3_key.endswith("/original.jpg"))
        links = list(entry.wardrobe_links.order_by("sort_order"))
        self.assertEqual(
            [link.wardrobe_item_id for link in links],
            [self.bottom.pk, self.top.pk],
        )
        self.assertEqual(self.mock_copy_wardrobe_item.call_count, 2)
        for link in links:
            self.assertIn(f"calendar/{self.user.pk}/{entry.pk}/selected/", link.snapshot["s3_key"])
            self.assertNotEqual(entry.image_s3_key, link.snapshot["s3_key"])

    def test_selected_categories_are_excluded_from_photo_extraction(self) -> None:
        """입은 옷으로 지정한 부위는 사진에서 다시 뽑지 않는다.

        뽑으면 사용자가 이미 고른 그 옷이 옷장에 한 벌 더 생긴다 — 크롭·태깅이
        달라 다른 옷처럼 보이기까지 한다. 워커가 열거 직후에 걸러 내도록
        큐 페이로드로 넘긴다(룩북 등록과 같은 규칙).
        """

        payload = self._payload()
        payload["wardrobe_item_ids"] = [str(self.bottom.pk), str(self.top.pk)]

        response = self.client.post(self.url, payload, format="multipart")

        self.assertEqual(response.status_code, 202)
        entry = CalendarEntry.objects.get(pk=response.data["id"])
        self.assertEqual(entry.skipped_categories, ["상의", "하의"])
        self.assertEqual(
            self.mock_enqueue.call_args.kwargs["exclude_categories"],
            ["상의", "하의"],
        )

    def test_photo_upload_without_selection_excludes_nothing(self) -> None:
        response = self.client.post(self.url, self._payload(), format="multipart")

        self.assertEqual(response.status_code, 202)
        entry = CalendarEntry.objects.get(pk=response.data["id"])
        self.assertEqual(entry.skipped_categories, [])
        self.assertEqual(self.mock_enqueue.call_args.kwargs["exclude_categories"], [])

    def test_photo_upload_accepts_blank_swagger_wardrobe_item_input(self) -> None:
        payload = self._payload()
        payload["wardrobe_item_ids"] = ""

        response = self.client.post(self.url, payload, format="multipart")

        self.assertEqual(response.status_code, 202)
        entry = CalendarEntry.objects.get(pk=response.data["id"])
        self.assertEqual(entry.wardrobe_links.count(), 0)
        self.assertIsNotNone(entry.wardrobe_upload_job_id)
        self.mock_enqueue.assert_called_once()

    def test_photo_upload_rejects_missing_or_other_users_items_before_s3(self) -> None:
        other_payload = self._payload()
        other_payload["wardrobe_item_ids"] = [str(self.other_item.pk)]
        other_response = self.client.post(
            self.url,
            other_payload,
            format="multipart",
        )

        missing_payload = self._payload(entry_date="2026-08-09")
        missing_payload["wardrobe_item_ids"] = [str(uuid4())]
        missing_response = self.client.post(
            self.url,
            missing_payload,
            format="multipart",
        )

        self.assertEqual(other_response.status_code, 400)
        self.assertEqual(missing_response.status_code, 400)
        self.mock_upload_fileobj.assert_not_called()
        self.assertFalse(CalendarEntry.objects.filter(user=self.user).exists())
        self.assertFalse(WardrobeUploadJob.objects.filter(user=self.user).exists())

    def test_photo_upload_returns_conflict_before_s3(self) -> None:
        CalendarEntry.objects.create(
            user=self.user,
            date=date(2026, 8, 8),
            source_type=CalendarSourceType.WARDROBE_SELECTED.value,
            image_s3_key="calendar/user/existing.png",
            status=CalendarStatus.COMPLETED.value,
        )

        response = self.client.post(self.url, self._payload(), format="multipart")

        self.assertEqual(response.status_code, 409)
        self.mock_upload_fileobj.assert_not_called()
        self.mock_copy_wardrobe_item.assert_not_called()
        self.mock_delete_objects.assert_not_called()

    def test_original_upload_failure_returns_503_without_database_row(self) -> None:
        self.mock_upload_fileobj.side_effect = RuntimeError("upload failed")

        response = self.client.post(self.url, self._payload(), format="multipart")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(CalendarEntry.objects.filter(user=self.user).exists())
        self.mock_delete_objects.assert_not_called()
        self.mock_delete_wardrobe_objects.assert_not_called()

    def test_wardrobe_original_copy_failure_cleans_calendar_original(self) -> None:
        self.mock_copy_calendar_original.side_effect = RuntimeError("copy failed")

        response = self.client.post(self.url, self._payload(), format="multipart")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(CalendarEntry.objects.filter(user=self.user).exists())
        self.assertFalse(WardrobeUploadJob.objects.filter(user=self.user).exists())
        self.mock_delete_objects.assert_called_once()
        self.mock_delete_wardrobe_objects.assert_not_called()

    def test_selected_item_copy_failure_cleans_uploaded_objects(self) -> None:
        payload = self._payload()
        payload["wardrobe_item_ids"] = [str(self.bottom.pk), str(self.top.pk)]
        self.mock_copy_wardrobe_item.side_effect = [None, RuntimeError("copy failed")]

        response = self.client.post(self.url, payload, format="multipart")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(CalendarEntry.objects.filter(user=self.user).exists())
        self.assertFalse(WardrobeUploadJob.objects.filter(user=self.user).exists())
        self.mock_delete_objects.assert_called_once()
        deleted_keys = self.mock_delete_objects.call_args.args[0]
        self.assertEqual(len(deleted_keys), 2)
        self.assertTrue(deleted_keys[0].endswith("/original.jpg"))
        self.assertIn("/selected/", deleted_keys[1])
        self.mock_delete_wardrobe_objects.assert_called_once()
        self.assertEqual(
            len(self.mock_delete_wardrobe_objects.call_args.args[0]),
            1,
        )
        self.mock_enqueue.assert_not_called()

    def test_queue_failure_marks_calendar_failed_and_keeps_s3_objects(self) -> None:
        self.mock_enqueue.side_effect = RedisConnectionError("redis unavailable")

        response = self.client.post(self.url, self._payload(), format="multipart")

        self.assertEqual(response.status_code, 503)
        entry = CalendarEntry.objects.get(pk=response.data["id"])
        self.assertEqual(entry.status, CalendarStatus.FAILED.value)
        self.assertEqual(entry.processing_error_code, "QUEUE_ENQUEUE_FAILED")
        self.assertEqual(
            entry.processing_error_message,
            "옷장 이미지 처리 큐 적재 실패",
        )
        self.assertIsNotNone(entry.processing_completed_at)
        self.assertTrue(entry.image_s3_key.endswith("/original.jpg"))
        self.assertEqual(
            entry.wardrobe_upload_job.status,
            WardrobeUploadJob.Status.FAILED,
        )
        self.mock_delete_objects.assert_not_called()
        self.mock_logger_exception.assert_called_once()

    def test_photo_upload_validates_image_and_item_id_inputs(self) -> None:
        missing_image = self.client.post(
            self.url,
            {"date": "2026-08-08"},
            format="multipart",
        )
        invalid_type = self.client.post(
            self.url,
            self._payload(
                image=make_image_file(
                    name="outfit.gif",
                    content_type="image/gif",
                    image_format="GIF",
                )
            ),
            format="multipart",
        )
        duplicate_items = self._payload()
        duplicate_items["wardrobe_item_ids"] = [str(self.top.pk), str(self.top.pk)]
        duplicate_response = self.client.post(
            self.url,
            duplicate_items,
            format="multipart",
        )

        self.assertEqual(missing_image.status_code, 400)
        self.assertEqual(invalid_type.status_code, 400)
        self.assertEqual(duplicate_response.status_code, 400)
        self.mock_upload_fileobj.assert_not_called()

    def test_photo_upload_rejects_oversized_image(self) -> None:
        oversized = make_image_file(
            extra_size=(MAX_CALENDAR_UPLOAD_MB * 1024 * 1024) + 1,
        )

        response = self.client.post(
            self.url,
            self._payload(image=oversized),
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.mock_upload_fileobj.assert_not_called()

    def test_photo_upload_requires_authentication(self) -> None:
        response = APIClient().post(
            self.url,
            self._payload(),
            format="multipart",
        )

        self.assertEqual(response.status_code, 401)
        self.mock_upload_fileobj.assert_not_called()
        self.mock_enqueue.assert_not_called()
