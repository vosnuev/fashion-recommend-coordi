from datetime import date
from uuid import uuid4

from django.urls import reverse
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework.test import APIClient

from apps.lookbook.contracts import (
    LookbookLinkType,
    LookbookSourceType,
    LookbookStatus,
)
from apps.lookbook.models import LookbookPost
from apps.lookbook.serializers import MAX_LOOKBOOK_UPLOAD_MB
from apps.lookbook.tests.base import LookbookApiTestCase, make_image_file
from apps.wardrobe.models import WardrobeItem, WardrobeUploadJob


class LookbookPhotoCreateApiTests(LookbookApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("lookbook:lookbook-photo-create")

    def _payload(self, *, image=None, **overrides):
        payload = {
            "image": image or make_image_file(),
            "schedule": "주말 나들이",
            "tpo": ["데이트"],
            "hashtags": ["나들이", "캐주얼"],
        }
        payload.update(overrides)
        return payload

    def test_photo_upload_saves_s3_before_registered_lookbook(self) -> None:
        image = make_image_file("My Look.JPG")

        def assert_no_row_before_upload(*_args) -> None:
            self.assertFalse(LookbookPost.objects.filter(user=self.user).exists())
            self.assertFalse(WardrobeUploadJob.objects.filter(user=self.user).exists())

        self.mocks["upload_fileobj"].side_effect = assert_no_row_before_upload

        response = self.client.post(self.url, self._payload(image=image), format="multipart")

        self.assertEqual(response.status_code, 202)
        post = LookbookPost.objects.get(pk=response.data["id"])
        self.assertEqual(post.user, self.user)
        self.assertEqual(post.source_type, LookbookSourceType.PHOTO_UPLOAD.value)
        self.assertEqual(post.status, LookbookStatus.REGISTERED.value)
        self.assertIsNone(post.calendar_entry_id)
        self.assertIsNotNone(post.wardrobe_upload_job_id)
        self.assertEqual(
            post.image_s3_key,
            f"lookbook/{self.user.pk}/{post.pk}/original.jpg",
        )
        self.assertEqual(post.schedule, "주말 나들이")
        self.assertEqual(post.tpo, ["데이트"])
        self.assertEqual(post.hashtags, ["나들이", "캐주얼"])
        self.assertEqual(post.skipped_categories, [])

        job = post.wardrobe_upload_job
        self.assertEqual(
            job.source_s3_key,
            f"wardrobe/{self.user.pk}/{job.pk}/original.jpg",
        )
        self.mocks["copy_original_to_wardrobe"].assert_called_once_with(
            post.image_s3_key,
            job.source_s3_key,
        )
        self.assertEqual(
            response.data["image_url"],
            f"https://lookbook.example/{post.image_s3_key}",
        )
        self.assertIsNone(response.data["calendar"])

    def test_selected_categories_are_excluded_from_the_processing_queue(self) -> None:
        """입은 옷으로 지정한 부위는 큐 페이로드에서 제외된다."""

        payload = self._payload(
            wardrobe_item_ids=[str(self.top.pk), str(self.bottom.pk)],
        )

        response = self.client.post(self.url, payload, format="multipart")

        self.assertEqual(response.status_code, 202)
        post = LookbookPost.objects.get(pk=response.data["id"])
        self.assertEqual(post.skipped_categories, ["상의", "하의"])
        self.assertEqual(response.data["skipped_categories"], ["상의", "하의"])

        self.assertEqual(self.mocks["enqueue"].call_count, 1)
        enqueue_kwargs = self.mocks["enqueue"].call_args.kwargs
        self.assertEqual(enqueue_kwargs["exclude_categories"], ["상의", "하의"])
        self.assertEqual(
            self.mocks["enqueue"].call_args.args[0].pk,
            post.wardrobe_upload_job_id,
        )

        links = list(post.wardrobe_links.order_by("sort_order"))
        self.assertEqual(
            [link.wardrobe_item_id for link in links],
            [self.top.pk, self.bottom.pk],
        )
        self.assertEqual(
            [link.link_type for link in links],
            [LookbookLinkType.SELECTED.value] * 2,
        )
        self.assertEqual(self.mocks["copy_wardrobe_item"].call_count, 2)

    def test_same_category_twice_is_reported_once(self) -> None:
        """대분류가 겹치는 옷을 두 벌 골라도 제외 목록은 중복 없이 정렬된다."""

        second_top = WardrobeItem.objects.create(
            user=self.user,
            job=None,
            s3_key="wardrobe/user/top2.png",
            item_name="줄무늬 셔츠",
            category_large="상의",
            confirmed=True,
        )

        payload = self._payload(
            wardrobe_item_ids=[str(self.top.pk), str(second_top.pk)],
        )
        response = self.client.post(self.url, payload, format="multipart")

        self.assertEqual(response.status_code, 202)
        post = LookbookPost.objects.get(pk=response.data["id"])
        self.assertEqual(post.skipped_categories, ["상의"])

    def test_no_selected_items_sends_no_exclusion(self) -> None:
        response = self.client.post(self.url, self._payload(), format="multipart")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            self.mocks["enqueue"].call_args.kwargs["exclude_categories"],
            [],
        )

    def test_photo_upload_accepts_blank_swagger_inputs(self) -> None:
        payload = self._payload(wardrobe_item_ids="", calendar_date="")

        response = self.client.post(self.url, payload, format="multipart")

        self.assertEqual(response.status_code, 202)
        post = LookbookPost.objects.get(pk=response.data["id"])
        self.assertEqual(post.wardrobe_links.count(), 0)
        self.assertIsNone(post.calendar_entry_id)

    def test_rejects_missing_or_other_users_items_before_s3(self) -> None:
        other = self.client.post(
            self.url,
            self._payload(wardrobe_item_ids=[str(self.other_item.pk)]),
            format="multipart",
        )
        missing = self.client.post(
            self.url,
            self._payload(wardrobe_item_ids=[str(uuid4())]),
            format="multipart",
        )

        self.assertEqual(other.status_code, 400)
        self.assertEqual(missing.status_code, 400)
        self.mocks["upload_fileobj"].assert_not_called()
        self.assertFalse(LookbookPost.objects.exists())
        self.assertFalse(WardrobeUploadJob.objects.exists())

    def test_original_upload_failure_returns_503_without_database_row(self) -> None:
        self.mocks["upload_fileobj"].side_effect = RuntimeError("upload failed")

        response = self.client.post(self.url, self._payload(), format="multipart")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(LookbookPost.objects.exists())
        self.mocks["delete_objects"].assert_not_called()

    def test_selected_item_copy_failure_cleans_uploaded_objects(self) -> None:
        payload = self._payload(
            wardrobe_item_ids=[str(self.bottom.pk), str(self.top.pk)],
        )
        self.mocks["copy_wardrobe_item"].side_effect = [None, RuntimeError("copy failed")]

        response = self.client.post(self.url, payload, format="multipart")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(LookbookPost.objects.exists())
        self.assertFalse(WardrobeUploadJob.objects.exists())
        deleted_keys = self.mocks["delete_objects"].call_args.args[0]
        self.assertEqual(len(deleted_keys), 2)
        self.assertTrue(deleted_keys[0].endswith("/original.jpg"))
        self.assertIn("/items/", deleted_keys[1])
        self.mocks["wardrobe_delete_objects"].assert_called_once()
        self.mocks["enqueue"].assert_not_called()

    def test_queue_failure_marks_lookbook_failed_and_keeps_s3_objects(self) -> None:
        self.mocks["enqueue"].side_effect = RedisConnectionError("redis unavailable")

        response = self.client.post(self.url, self._payload(), format="multipart")

        self.assertEqual(response.status_code, 503)
        post = LookbookPost.objects.get(pk=response.data["id"])
        self.assertEqual(post.status, LookbookStatus.FAILED.value)
        self.assertEqual(post.processing_error_code, "QUEUE_ENQUEUE_FAILED")
        self.assertEqual(
            post.wardrobe_upload_job.status,
            WardrobeUploadJob.Status.FAILED,
        )
        self.mocks["delete_objects"].assert_not_called()
        self.mocks["logger_exception"].assert_called_once()

    def test_validates_image_and_item_id_inputs(self) -> None:
        invalid_type = self.client.post(
            self.url,
            self._payload(
                image=make_image_file(
                    name="look.gif",
                    content_type="image/gif",
                    image_format="GIF",
                )
            ),
            format="multipart",
        )
        duplicate = self.client.post(
            self.url,
            self._payload(wardrobe_item_ids=[str(self.top.pk), str(self.top.pk)]),
            format="multipart",
        )
        oversized = self.client.post(
            self.url,
            self._payload(
                image=make_image_file(
                    extra_size=(MAX_LOOKBOOK_UPLOAD_MB * 1024 * 1024) + 1
                )
            ),
            format="multipart",
        )
        missing_image = self.client.post(self.url, {"schedule": "x"}, format="multipart")

        for response in (invalid_type, duplicate, oversized, missing_image):
            self.assertEqual(response.status_code, 400)
        self.mocks["upload_fileobj"].assert_not_called()

    def test_overwrite_without_date_is_rejected(self) -> None:
        response = self.client.post(
            self.url,
            self._payload(overwrite_calendar=True),
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.mocks["upload_fileobj"].assert_not_called()

    def test_requires_authentication(self) -> None:
        response = APIClient().post(self.url, self._payload(), format="multipart")

        self.assertEqual(response.status_code, 401)
        self.mocks["upload_fileobj"].assert_not_called()

    def test_calendar_date_is_optional_and_absent_by_default(self) -> None:
        response = self.client.post(self.url, self._payload(), format="multipart")

        self.assertEqual(response.status_code, 202)
        post = LookbookPost.objects.get(pk=response.data["id"])
        self.assertIsNone(post.calendar_entry_id)
        self.assertNotEqual(post.created_at.date(), date.min)
