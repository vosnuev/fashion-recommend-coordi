import io
import os
from datetime import timedelta
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.users.models import User
from . import taxonomy as T
from .models import WardrobeItem, WardrobeItemBatch, WardrobeUploadJob
from .services import jobs, storage
from .views import _merge_metadata


@patch("apps.wardrobe.views.storage.BUCKET", "test-bucket")
class BatchSmokeTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create(username="batch-test")
        self.client.force_authenticate(self.user)

    @patch("apps.wardrobe.views.jobs.enqueue_item")
    @patch("apps.wardrobe.views.storage.upload_fileobj")
    @patch("apps.wardrobe.views.storage.fetch_remote_image")
    def test_two_links_become_two_qwen_jobs(self, fetch, upload, enqueue):
        fetch.return_value = (io.BytesIO(b"image"), "image/jpeg", ".jpg", 5)
        response = self.client.post(
            reverse("wardrobe:batch-list-create"),
            {
                "items": [
                    {"image_link": "https://cdn.example.com/one.jpg", "item_name": "상품명"},
                    {"image_link": "https://cdn.example.com/two.jpg"},
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 202)
        batch = WardrobeItemBatch.objects.get(pk=response.data["batch_id"])
        self.assertEqual(batch.jobs.count(), 2)
        self.assertEqual(set(batch.jobs.values_list("pipeline", flat=True)), {"qwen-tag"})
        self.assertEqual(batch.jobs.get(original_file_name="one.jpg").input_metadata["item_name"], "상품명")
        self.assertEqual((fetch.call_count, upload.call_count, enqueue.call_count), (2, 2, 2))

    def test_empty_batch_is_rejected(self):
        response = self.client.post(
            reverse("wardrobe:batch-list-create"), {"items": []}, format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_rollup_distinguishes_partial_failure(self):
        batch = WardrobeItemBatch.objects.create(user=self.user, total_count=2)
        for job_status in ("DONE", "FAILED"):
            WardrobeUploadJob.objects.create(
                user=self.user,
                batch=batch,
                source_s3_key=f"{job_status}.jpg",
                status=job_status,
            )
        batch.refresh_status()
        self.assertEqual((batch.status, batch.done_count, batch.failed_count), ("PARTIAL", 1, 1))

    def test_browser_metadata_wins_over_qwen_result(self):
        generated = {
            "item_name": "Qwen 이름",
            "category_large": T.CATEGORY_LARGE[0],
            "category_small": T.CATEGORY_SMALL[T.CATEGORY_LARGE[0]][0],
            "color": T.COLORS[0],
        }
        merged = _merge_metadata(
            generated,
            {"item_name": "구매 상품명", "color": T.COLORS[1], "confirmed": False},
        )
        self.assertEqual((merged["item_name"], merged["color"], merged["confirmed"]),
                         ("구매 상품명", T.COLORS[1], False))

    def test_pending_job_expires_when_polled_after_twenty_minutes(self):
        batch = WardrobeItemBatch.objects.create(user=self.user, total_count=1)
        job = WardrobeUploadJob.objects.create(
            user=self.user, batch=batch, source_s3_key="stale.jpg", pipeline="qwen-tag",
        )
        WardrobeUploadJob.objects.filter(pk=job.pk).update(
            created_at=timezone.now() - timedelta(minutes=21),
        )

        with (
            patch.dict(os.environ, {"WARDROBE_BATCH_STALE_AFTER_MINUTES": "20"}),
            patch("apps.wardrobe.views.jobs.cancel_pending") as cancel_pending,
        ):
            response = self.client.get(
                reverse("wardrobe:batch-detail", kwargs={"batch_id": batch.pk})
            )

        self.assertEqual(response.status_code, 200)
        cancel_pending.assert_called_once()
        self.assertEqual(response.data["status"], "FAILED")
        self.assertEqual(response.data["jobs"][0]["error_message"], "processing_timeout")

    def test_worker_processing_and_failure_callbacks_are_visible(self):
        batch = WardrobeItemBatch.objects.create(user=self.user, total_count=1)
        job = WardrobeUploadJob.objects.create(
            user=self.user, batch=batch, source_s3_key="failed.jpg", pipeline="qwen-tag",
        )
        url = reverse("wardrobe:callback")
        headers = {"HTTP_X_INTERNAL_TOKEN": "test-token"}

        with patch.dict(os.environ, {"WARDROBE_INTERNAL_TOKEN": "test-token"}):
            processing = self.client.post(
                url,
                {"job_id": str(job.pk), "status": "processing", "items": []},
                format="json",
                **headers,
            )
            failed = self.client.post(
                url,
                {
                    "job_id": str(job.pk),
                    "status": "failed",
                    "error": "RuntimeError: GPU failure",
                    "items": [],
                },
                format="json",
                **headers,
            )

        self.assertEqual((processing.status_code, processing.data["status"]), (200, "PROCESSING"))
        self.assertEqual((failed.status_code, failed.data["status"]), (200, "FAILED"))
        job.refresh_from_db()
        self.assertEqual(job.error_message, "RuntimeError: GPU failure")

    @patch("apps.wardrobe.services.jobs._redis")
    def test_cancel_pending_removes_matching_item_queue_payload(self, redis_factory):
        job = WardrobeUploadJob.objects.create(
            user=self.user, source_s3_key="pending.jpg", pipeline="qwen-tag",
        )
        redis_client = redis_factory.return_value
        redis_client.lrange.return_value = [
            '{"job_id":"other"}',
            f'{{"job_id":"{job.pk}"}}',
        ]
        redis_client.lrem.return_value = 1

        self.assertTrue(jobs.cancel_pending(job))
        redis_client.lrem.assert_called_once_with(
            jobs.ITEM_QUEUE_KEY, 1, f'{{"job_id":"{job.pk}"}}'
        )


class RemoteImageSecurityTest(TestCase):
    @patch("apps.wardrobe.services.storage.socket.getaddrinfo")
    def test_private_network_image_url_is_rejected(self, getaddrinfo):
        getaddrinfo.return_value = [(None, None, None, None, ("127.0.0.1", 80))]

        with self.assertRaises(storage.RemoteImageError):
            storage._validate_public_url("http://localhost/image.jpg")


@patch("apps.wardrobe.views.storage.BUCKET", "test-bucket")
class WardrobeUploadFlowTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create(username="upload-flow-test")
        self.client.force_authenticate(self.user)

    @patch("apps.wardrobe.views.jobs.enqueue")
    @patch("apps.wardrobe.views.storage.upload_fileobj")
    def test_upload_preserves_file_name_and_enqueues_pending_job(self, upload, enqueue):
        image_bytes = io.BytesIO()
        Image.new("RGB", (2, 2), color="beige").save(image_bytes, format="PNG")
        image = SimpleUploadedFile(
            "coat.png",
            image_bytes.getvalue(),
            content_type="image/png",
        )

        response = self.client.post(reverse("wardrobe:upload"), {"image": image})

        self.assertEqual(response.status_code, 202)
        job = WardrobeUploadJob.objects.get(pk=response.data["job_id"])
        self.assertEqual((job.original_file_name, job.status), ("coat.png", "PENDING"))
        upload.assert_called_once()
        enqueue.assert_called_once_with(job)

        polled = self.client.get(
            reverse("wardrobe:upload-job", kwargs={"job_id": job.pk})
        )
        self.assertEqual(polled.data["file_name"], "coat.png")

    def test_success_callback_creates_item_and_done_job(self):
        job = WardrobeUploadJob.objects.create(
            user=self.user,
            source_s3_key="wardrobe/source.jpg",
            original_file_name="source.jpg",
        )
        payload = {
            "job_id": str(job.pk),
            "status": "success",
            "items": [{
                "s3_key": "wardrobe/cropped.jpg",
                "item_name": "코트",
                "category_large": "아우터",
            }],
        }

        with (
            patch.dict(os.environ, {"WARDROBE_INTERNAL_TOKEN": "test-token"}),
            patch("apps.wardrobe.views.vectors.upsert_item", return_value=True),
        ):
            response = self.client.post(
                reverse("wardrobe:callback"), payload, format="json",
                HTTP_X_INTERNAL_TOKEN="test-token",
            )

        self.assertEqual((response.status_code, response.data["status"]), (201, "DONE"))
        job.refresh_from_db()
        item = WardrobeItem.objects.get(job=job)
        self.assertEqual(job.status, "DONE")
        self.assertIsNotNone(job.finished_at)
        self.assertEqual((item.user, item.item_name, item.added_to_closet_at is not None),
                         (self.user, "코트", True))

    def test_failed_callback_without_error_uses_fallback_message(self):
        job = WardrobeUploadJob.objects.create(
            user=self.user,
            source_s3_key="wardrobe/source.jpg",
        )

        with patch.dict(os.environ, {"WARDROBE_INTERNAL_TOKEN": "test-token"}):
            response = self.client.post(
                reverse("wardrobe:callback"),
                {"job_id": str(job.pk), "status": "failed", "items": []},
                format="json",
                HTTP_X_INTERNAL_TOKEN="test-token",
            )

        self.assertEqual((response.status_code, response.data["status"]), (200, "FAILED"))
        job.refresh_from_db()
        self.assertEqual(job.error_message, "image_processor_failed")
        self.assertIsNotNone(job.finished_at)


@patch("apps.wardrobe.serializers.storage.presigned_get", return_value="https://img.example/x.jpg")
@patch("apps.wardrobe.views.vectors.update_payload", return_value=True)
class WardrobeFavoriteTest(TestCase):
    """즐겨찾기(별)는 아이템 수정과 같은 문으로 드나든다 — 새 엔드포인트를 만들지 않았다.

    (S3 프리사인·벡터 동기화는 이 기능과 무관해 막아 둔다)
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create(username="favorite-test")
        self.other = User.objects.create(username="favorite-other")
        self.job = WardrobeUploadJob.objects.create(
            user=self.user,
            source_s3_key="wardrobe/source.jpg",
        )
        self.item = WardrobeItem.objects.create(
            user=self.user,
            job=self.job,
            s3_key="wardrobe/cropped.jpg",
            item_name="코트",
            category_large="아우터",
            confirmed=True,
            # 목록은 옷장에 들어온 옷만 준다 — 이 값이 없으면 목록에서 빠진다.
            added_to_closet_at=timezone.now(),
        )

    def _item_url(self, item=None):
        return reverse(
            "wardrobe:item-detail",
            kwargs={"item_id": (item or self.item).pk},
        )

    def test_favorite_toggles_and_is_visible_in_list(self, vectors_mock, url_mock):
        self.client.force_authenticate(self.user)

        turned_on = self.client.patch(self._item_url(), {"is_favorite": True}, format="json")
        listed = self.client.get(reverse("wardrobe:items"))
        turned_off = self.client.patch(
            self._item_url(), {"is_favorite": False}, format="json"
        )

        self.assertEqual(turned_on.status_code, 200)
        self.assertTrue(turned_on.data["is_favorite"])
        self.assertTrue(listed.data[0]["is_favorite"])
        self.assertFalse(turned_off.data["is_favorite"])
        self.item.refresh_from_db()
        self.assertFalse(self.item.is_favorite)

    def test_default_is_off_and_other_user_cannot_toggle(self, vectors_mock, url_mock):
        self.assertFalse(self.item.is_favorite)

        self.client.force_authenticate(self.other)
        response = self.client.patch(self._item_url(), {"is_favorite": True}, format="json")

        self.assertEqual(response.status_code, 404)
        self.item.refresh_from_db()
        self.assertFalse(self.item.is_favorite)
