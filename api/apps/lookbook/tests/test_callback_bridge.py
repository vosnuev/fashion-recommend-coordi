"""옷장 callback → 룩북 자동 연결."""

from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.lookbook.contracts import LookbookLinkType, LookbookStatus
from apps.lookbook.models import LookbookPost, LookbookWardrobeItem
from apps.lookbook.services import lookbook_service
from apps.style_calendar.contracts import CalendarSourceType, CalendarStatus
from apps.style_calendar.models import CalendarEntry
from apps.users.models import User
from apps.wardrobe.models import WardrobeItem, WardrobeUploadJob


def item_payload(index: int, category_large: str = "신발") -> dict[str, object]:
    return {
        "s3_key": f"wardrobe/callback/job/item_{index:03d}.png",
        "item_name": f"자동 등록 아이템 {index}",
        "category_large": category_large,
        "category_small": "",
        "season": ["여름"],
        "style": ["캐주얼"],
        "color": "화이트",
        "pattern": "무지",
        "fit": "",
        "material": "",
        "sleeve": "",
        "length": "",
        "usage": ["일상"],
        "layer_role": "",
        "layer_order": None,
        "seg_meta": {"index": index},
        "image_vector": [],
        "text_vector": [],
    }


class LookbookCallbackBridgeTests(TestCase):
    def setUp(self) -> None:
        copy_patcher = patch(
            "apps.lookbook.services.lookbook_service.storage.copy_wardrobe_item"
        )
        calendar_copy_patcher = patch(
            "apps.style_calendar.services.calendar_service.storage.copy_wardrobe_item"
        )
        vectors_patcher = patch("apps.wardrobe.views.vectors.upsert_item", return_value=True)
        self.mock_copy = copy_patcher.start()
        self.mock_calendar_copy = calendar_copy_patcher.start()
        vectors_patcher.start()
        self.addCleanup(copy_patcher.stop)
        self.addCleanup(calendar_copy_patcher.stop)
        self.addCleanup(vectors_patcher.stop)

        self.user = User.objects.create(username="lookbook-callback-user")
        self.job = WardrobeUploadJob.objects.create(
            user=self.user,
            source_s3_key="wardrobe/callback/job/original.jpg",
        )
        self.selected = WardrobeItem.objects.create(
            user=self.user,
            job=None,
            s3_key="wardrobe/callback/selected.png",
            category_large="상의",
        )
        self.post = LookbookPost.objects.create(
            user=self.user,
            wardrobe_upload_job=self.job,
            source_type="PHOTO_UPLOAD",
            image_s3_key="lookbook/callback/original.jpg",
            skipped_categories=["상의"],
            status=LookbookStatus.REGISTERED.value,
        )
        LookbookWardrobeItem.objects.create(
            lookbook=self.post,
            wardrobe_item=self.selected,
            link_type=LookbookLinkType.SELECTED.value,
            sort_order=0,
            snapshot={"s3_key": "lookbook/callback/items/selected.png"},
        )
        self.client = APIClient()
        self.url = reverse("wardrobe:callback")

    def _post_callback(self, payload):
        return self.client.post(
            self.url,
            payload,
            format="json",
            HTTP_X_INTERNAL_TOKEN="test-token",
        )

    @patch.dict("os.environ", {"WARDROBE_INTERNAL_TOKEN": "test-token"})
    def test_success_appends_extracted_links_and_completes(self) -> None:
        payload = {
            "job_id": str(self.job.pk),
            "status": "success",
            "items": [item_payload(0), item_payload(1, "가방")],
        }

        response = self._post_callback(payload)

        self.assertEqual(response.status_code, 201)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, LookbookStatus.COMPLETED.value)
        self.assertIsNotNone(self.post.callback_applied_at)

        links = list(self.post.wardrobe_links.order_by("sort_order"))
        self.assertEqual([link.sort_order for link in links], [0, 1, 2])
        self.assertEqual(
            [link.link_type for link in links],
            [
                LookbookLinkType.SELECTED.value,
                LookbookLinkType.EXTRACTED.value,
                LookbookLinkType.EXTRACTED.value,
            ],
        )
        self.assertEqual(self.mock_copy.call_count, 2)

        # 중복 callback은 아무것도 더 만들지 않는다.
        self.assertEqual(self._post_callback(payload).status_code, 200)
        self.assertEqual(self.post.wardrobe_links.count(), 3)

    @patch.dict("os.environ", {"WARDROBE_INTERNAL_TOKEN": "test-token"})
    def test_items_in_a_skipped_category_are_not_linked(self) -> None:
        """구버전 워커가 제외를 무시해도 룩북에 같은 부위가 두 번 걸리지 않는다."""

        response = self._post_callback(
            {
                "job_id": str(self.job.pk),
                "status": "success",
                "items": [item_payload(0, "상의"), item_payload(1, "신발")],
            }
        )

        self.assertEqual(response.status_code, 201)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, LookbookStatus.COMPLETED.value)
        linked_categories = [
            link.snapshot["category_large"]
            for link in self.post.wardrobe_links.filter(
                link_type=LookbookLinkType.EXTRACTED.value
            )
        ]
        self.assertEqual(linked_categories, ["신발"])
        # 이미 만들어진 옷장 아이템 자체는 지우지 않는다.
        self.assertEqual(self.job.items.count(), 2)

    @patch.dict("os.environ", {"WARDROBE_INTERNAL_TOKEN": "test-token"})
    def test_success_with_no_items_still_completes(self) -> None:
        """입은 옷으로 사진 속 부위를 전부 지정하면 뽑을 것이 없는 게 정상이다."""

        response = self._post_callback(
            {"job_id": str(self.job.pk), "status": "success", "items": []}
        )

        self.assertEqual(response.status_code, 201)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, LookbookStatus.COMPLETED.value)
        self.assertEqual(self.post.wardrobe_links.count(), 1)

    @patch.dict("os.environ", {"WARDROBE_INTERNAL_TOKEN": "test-token"})
    def test_failed_callback_marks_lookbook_failed(self) -> None:
        response = self._post_callback(
            {
                "job_id": str(self.job.pk),
                "status": "failed",
                "error": "처리 성공한 아이템이 없습니다",
                "items": [],
            }
        )

        self.assertEqual(response.status_code, 200)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, LookbookStatus.FAILED.value)
        self.assertEqual(self.post.processing_error_code, "IMAGE_PROCESSING_FAILED")

    @patch.dict("os.environ", {"WARDROBE_INTERNAL_TOKEN": "test-token"})
    def test_shared_job_updates_both_lookbook_and_calendar(self) -> None:
        entry = CalendarEntry.objects.create(
            user=self.user,
            wardrobe_upload_job=self.job,
            date=date(2026, 8, 12),
            source_type=CalendarSourceType.PHOTO_UPLOAD.value,
            image_s3_key="calendar/callback/original.jpg",
            status=CalendarStatus.REGISTERED.value,
        )
        self.post.calendar_entry = entry
        self.post.save(update_fields=["calendar_entry"])

        response = self._post_callback(
            {
                "job_id": str(self.job.pk),
                "status": "success",
                "items": [item_payload(0)],
            }
        )

        self.assertEqual(response.status_code, 201)
        self.post.refresh_from_db()
        entry.refresh_from_db()
        self.assertEqual(self.post.status, LookbookStatus.COMPLETED.value)
        self.assertEqual(entry.status, CalendarStatus.COMPLETED.value)
        self.assertEqual(entry.wardrobe_links.count(), 1)
        self.assertEqual(
            self.post.wardrobe_links.filter(
                link_type=LookbookLinkType.EXTRACTED.value
            ).count(),
            1,
        )

    def test_apply_success_is_a_no_op_for_unrelated_jobs(self) -> None:
        other_job = WardrobeUploadJob.objects.create(
            user=self.user,
            source_s3_key="wardrobe/other/original.jpg",
        )

        lookbook_service.apply_wardrobe_job_success(job=other_job, created_items=[])
        lookbook_service.apply_wardrobe_job_failure(job=other_job)

        self.post.refresh_from_db()
        self.assertEqual(self.post.status, LookbookStatus.REGISTERED.value)
