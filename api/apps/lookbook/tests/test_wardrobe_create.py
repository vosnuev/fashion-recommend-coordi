from uuid import uuid4

from django.urls import reverse
from rest_framework.test import APIClient

from apps.lookbook.contracts import LookbookSourceType, LookbookStatus
from apps.lookbook.models import LookbookPost
from apps.lookbook.tests.base import LookbookApiTestCase


class LookbookWardrobeCreateApiTests(LookbookApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("lookbook:lookbook-wardrobe-create")

    def _payload(self, **overrides):
        payload = {
            "wardrobe_item_ids": [str(self.top.pk), str(self.bottom.pk)],
            "schedule": "팀 회의",
            "tpo": ["출근"],
            "hashtags": ["출근", "미니멀"],
        }
        payload.update(overrides)
        return payload

    def test_creates_completed_lookbook_without_processing(self) -> None:
        response = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(response.status_code, 201)
        post = LookbookPost.objects.get(pk=response.data["id"])
        self.assertEqual(post.source_type, LookbookSourceType.WARDROBE_SELECTED.value)
        self.assertEqual(post.status, LookbookStatus.COMPLETED.value)
        self.assertIsNone(post.wardrobe_upload_job_id)
        self.assertEqual(post.skipped_categories, [])
        self.mocks["upload_fileobj"].assert_not_called()
        self.mocks["enqueue"].assert_not_called()

    def test_first_selected_item_becomes_the_cover(self) -> None:
        """룩 사진이 없으면 고른 옷의 첫 장이 표지가 된다 (프론트와 같은 규칙)."""

        response = self.client.post(
            self.url,
            self._payload(wardrobe_item_ids=[str(self.bottom.pk), str(self.top.pk)]),
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        post = LookbookPost.objects.get(pk=response.data["id"])
        first_link = post.wardrobe_links.order_by("sort_order").first()
        self.assertEqual(first_link.wardrobe_item_id, self.bottom.pk)
        self.assertEqual(post.image_s3_key, first_link.snapshot["s3_key"])
        self.assertIn(f"lookbook/{self.user.pk}/{post.pk}/items/", post.image_s3_key)

    def test_snapshot_survives_wardrobe_item_rename(self) -> None:
        response = self.client.post(self.url, self._payload(), format="json")
        post = LookbookPost.objects.get(pk=response.data["id"])

        self.top.item_name = "이름이 바뀐 옷"
        self.top.save(update_fields=["item_name"])

        link = post.wardrobe_links.get(wardrobe_item=self.top)
        self.assertEqual(link.snapshot["item_name"], "흰색 반팔")
        self.assertEqual(link.snapshot["category_large"], "상의")
        self.assertEqual(link.snapshot["source_wardrobe_s3_key"], "wardrobe/user/top.png")

    def test_rejects_empty_duplicate_or_foreign_items(self) -> None:
        empty = self.client.post(self.url, self._payload(wardrobe_item_ids=[]), format="json")
        duplicate = self.client.post(
            self.url,
            self._payload(wardrobe_item_ids=[str(self.top.pk), str(self.top.pk)]),
            format="json",
        )
        foreign = self.client.post(
            self.url,
            self._payload(wardrobe_item_ids=[str(self.other_item.pk)]),
            format="json",
        )
        missing = self.client.post(
            self.url,
            self._payload(wardrobe_item_ids=[str(uuid4())]),
            format="json",
        )

        for response in (empty, duplicate, foreign, missing):
            self.assertEqual(response.status_code, 400)
        self.assertFalse(LookbookPost.objects.exists())

    def test_rejects_unknown_fields(self) -> None:
        response = self.client.post(self.url, self._payload(shared=True), format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("shared", response.data)

    def test_copy_failure_cleans_up_and_returns_503(self) -> None:
        self.mocks["copy_wardrobe_item"].side_effect = [None, RuntimeError("copy failed")]

        response = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(LookbookPost.objects.exists())
        self.mocks["delete_objects"].assert_called_once()

    def test_requires_authentication(self) -> None:
        response = APIClient().post(self.url, self._payload(), format="json")

        self.assertEqual(response.status_code, 401)
