from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.style_calendar.contracts import CalendarSourceType, CalendarStatus
from apps.style_calendar.models import CalendarEntry, CalendarWardrobeItem
from apps.users.models import User
from apps.wardrobe.models import WardrobeItem, WardrobeUploadJob


class CalendarWardrobeCallbackBridgeTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create(username="calendar-callback-user")
        self.job = WardrobeUploadJob.objects.create(
            user=self.user,
            source_s3_key="wardrobe/callback/job/original.jpg",
        )
        self.entry = CalendarEntry.objects.create(
            user=self.user,
            wardrobe_upload_job=self.job,
            date=date(2026, 8, 12),
            source_type=CalendarSourceType.PHOTO_UPLOAD.value,
            image_s3_key="calendar/callback/original.jpg",
            status=CalendarStatus.REGISTERED.value,
        )
        self.manual_item = WardrobeItem.objects.create(
            user=self.user,
            job=None,
            s3_key="wardrobe/callback/manual.png",
            category_large="하의",
        )
        CalendarWardrobeItem.objects.create(
            calendar=self.entry,
            wardrobe_item=self.manual_item,
            sort_order=0,
            snapshot={"s3_key": "calendar/callback/selected/manual.png"},
        )
        self.client = APIClient()
        self.url = reverse("wardrobe:callback")

    @staticmethod
    def item_payload(index: int) -> dict[str, object]:
        return {
            "s3_key": f"wardrobe/callback/job/item_{index:03d}.png",
            "item_name": f"자동 등록 아이템 {index}",
            "category_large": "상의",
            "category_small": "티셔츠",
            "season": ["여름"],
            "style": ["캐주얼"],
            "color": "화이트",
            "pattern": "무지",
            "fit": "레귤러",
            "material": "면",
            "sleeve": "반팔",
            "length": "기본",
            "usage": ["일상"],
            "layer_role": "이너",
            "layer_order": 1,
            "seg_meta": {"index": index},
            "image_vector": [],
            "text_vector": [],
        }

    @patch("apps.style_calendar.services.calendar_service.storage.copy_wardrobe_item")
    @patch("apps.wardrobe.views.vectors.upsert_item", return_value=True)
    @patch.dict("os.environ", {"WARDROBE_INTERNAL_TOKEN": "test-token"})
    def test_success_creates_wardrobe_items_and_appends_calendar_links(
        self,
        _mock_upsert,
        mock_copy,
    ) -> None:
        payload = {
            "job_id": str(self.job.pk),
            "status": "success",
            "items": [self.item_payload(0), self.item_payload(1)],
        }

        response = self.client.post(
            self.url,
            payload,
            format="json",
            HTTP_X_INTERNAL_TOKEN="test-token",
        )

        self.assertEqual(response.status_code, 201)
        self.job.refresh_from_db()
        self.entry.refresh_from_db()
        self.assertEqual(self.job.status, WardrobeUploadJob.Status.DONE)
        self.assertEqual(self.entry.status, CalendarStatus.COMPLETED.value)
        self.assertIsNotNone(self.entry.callback_applied_at)
        created_items = list(self.job.items.order_by("created_at"))
        self.assertEqual(len(created_items), 2)
        links = list(self.entry.wardrobe_links.order_by("sort_order"))
        self.assertEqual(
            [link.wardrobe_item_id for link in links],
            [self.manual_item.pk, created_items[0].pk, created_items[1].pk],
        )
        self.assertEqual([link.sort_order for link in links], [0, 1, 2])
        self.assertEqual(mock_copy.call_count, 2)

        duplicate = self.client.post(
            self.url,
            payload,
            format="json",
            HTTP_X_INTERNAL_TOKEN="test-token",
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(self.entry.wardrobe_links.count(), 3)

    @patch.dict("os.environ", {"WARDROBE_INTERNAL_TOKEN": "test-token"})
    def test_failed_callback_marks_job_and_calendar_failed(self) -> None:
        response = self.client.post(
            self.url,
            {
                "job_id": str(self.job.pk),
                "status": "failed",
                "error": "처리 성공한 아이템이 없습니다",
                "items": [],
            },
            format="json",
            HTTP_X_INTERNAL_TOKEN="test-token",
        )

        self.assertEqual(response.status_code, 200)
        self.job.refresh_from_db()
        self.entry.refresh_from_db()
        self.assertEqual(self.job.status, WardrobeUploadJob.Status.FAILED)
        self.assertEqual(self.entry.status, CalendarStatus.FAILED.value)
        self.assertEqual(self.entry.processing_error_code, "IMAGE_PROCESSING_FAILED")
        self.assertEqual(
            self.entry.processing_error_message,
            "처리 성공한 아이템이 없습니다",
        )
