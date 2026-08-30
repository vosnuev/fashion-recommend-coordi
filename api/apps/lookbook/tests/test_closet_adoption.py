"""룩 사진에서 뽑힌 옷은 사용자가 넣기 전까지 옷장에 들어가지 않는다.

예전에는 사진을 올리면 추출된 옷이 곧바로 옷장 아이템이 되어, 사용자가 고른 적 없는
옷이 옷장에 쌓였다. 지금은 행은 만들되 `added_to_closet_at` 을 비워 두고,
룩 상세에서 '옷장에 추가'를 눌러야 옷장에 든다.
"""

from __future__ import annotations

from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from apps.lookbook.models import LookbookPost
from apps.lookbook.tests.base import LookbookApiTestCase, make_image_file
from apps.wardrobe.models import WardrobeItem, WardrobeUploadJob


class ClosetAdoptionTests(LookbookApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        # 이 테스트는 옷장 callback 과 옷장 목록까지 태운다 — 룩북 base 가 막지 않는
        # 옷장 쪽 벡터 적재와 presigned URL 생성을 여기서 함께 막는다.
        for target, kwargs in (
            ("apps.wardrobe.views.vectors.upsert_item", {"return_value": True}),
            (
                "apps.wardrobe.serializers.storage.presigned_get",
                {"side_effect": lambda key: f"https://wardrobe.example/{key}" if key else ""},
            ),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _closet_item_ids(self) -> set[str]:
        response = self.client.get(reverse("wardrobe:items"))
        self.assertEqual(response.status_code, 200)
        return {row["id"] for row in response.json()}

    @patch.dict("os.environ", {"WARDROBE_INTERNAL_TOKEN": "test-token"})
    def _extracted_item_for_lookbook(self) -> WardrobeItem:
        """룩 사진 등록 → 워커 callback 까지 태워 추출 아이템 하나를 만든다."""

        create = self.client.post(
            reverse("lookbook:lookbook-photo-create"),
            {"image": make_image_file()},
            format="multipart",
        )
        self.assertEqual(create.status_code, 202)
        post = LookbookPost.objects.get(pk=create.json()["id"])
        job = post.wardrobe_upload_job

        callback = self.client.post(
            reverse("wardrobe:callback"),
            {
                "job_id": str(job.pk),
                "status": "success",
                "items": [
                    {
                        "s3_key": "wardrobe/user/extracted.png",
                        "item_name": "사진에서 뽑은 상의",
                        "category_large": "상의",
                    }
                ],
            },
            format="json",
            HTTP_X_INTERNAL_TOKEN="test-token",
        )
        self.assertEqual(callback.status_code, 201, callback.content)
        return WardrobeItem.objects.get(job=job)

    def test_extracted_item_stays_out_of_closet_until_added(self):
        item = self._extracted_item_for_lookbook()

        self.assertIsNone(item.added_to_closet_at)
        self.assertNotIn(str(item.pk), self._closet_item_ids())

    def test_add_to_closet_puts_it_in_the_list(self):
        item = self._extracted_item_for_lookbook()

        response = self.client.post(
            reverse("wardrobe:item-add-to-closet", args=[item.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.json()["added_to_closet_at"])
        self.assertIn(str(item.pk), self._closet_item_ids())

    def test_add_to_closet_is_idempotent(self):
        """두 번 눌러도 들인 시각이 바뀌지 않는다 — 언제 들였는지가 흔들리면 안 된다."""

        item = self._extracted_item_for_lookbook()
        first = self.client.post(reverse("wardrobe:item-add-to-closet", args=[item.pk]))
        second = self.client.post(reverse("wardrobe:item-add-to-closet", args=[item.pk]))

        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["added_to_closet_at"], second.json()["added_to_closet_at"])

    @patch.dict("os.environ", {"WARDROBE_INTERNAL_TOKEN": "test-token"})
    def test_calendar_linked_look_puts_items_in_closet(self):
        """캘린더 기록과 job 을 공유하는 룩은 뽑힌 옷이 바로 옷장에 든다.

        그날 입었다고 적은 옷이라 사용자 것이 확실하고, 캘린더 상세에는 옷장에
        넣는 버튼이 없다. 여기서 막으면 그 옷을 어디서도 꺼낼 수 없다.
        """

        create = self.client.post(
            reverse("lookbook:lookbook-photo-create"),
            {"image": make_image_file(), "calendar_date": "2026-08-14"},
            format="multipart",
        )
        self.assertEqual(create.status_code, 202, create.content)
        job = LookbookPost.objects.get(pk=create.json()["id"]).wardrobe_upload_job

        callback = self.client.post(
            reverse("wardrobe:callback"),
            {
                "job_id": str(job.pk),
                "status": "success",
                "items": [
                    {
                        "s3_key": "wardrobe/user/worn.png",
                        "item_name": "그날 입은 상의",
                        "category_large": "상의",
                    }
                ],
            },
            format="json",
            HTTP_X_INTERNAL_TOKEN="test-token",
        )

        self.assertEqual(callback.status_code, 201, callback.content)
        item = WardrobeItem.objects.get(job=job)
        self.assertIsNotNone(item.added_to_closet_at)
        self.assertIn(str(item.pk), self._closet_item_ids())

    @patch.dict("os.environ", {"WARDROBE_INTERNAL_TOKEN": "test-token"})
    def test_plain_wardrobe_upload_still_lands_in_closet(self):
        """룩북과 무관한 옷장 업로드는 종전대로 바로 옷장에 든다."""

        job = WardrobeUploadJob.objects.create(
            user=self.user,
            source_s3_key="wardrobe/user/original.jpg",
        )
        response = self.client.post(
            reverse("wardrobe:callback"),
            {
                "job_id": str(job.pk),
                "status": "success",
                "items": [
                    {
                        "s3_key": "wardrobe/user/plain.png",
                        "item_name": "직접 올린 옷",
                        "category_large": "하의",
                    }
                ],
            },
            format="json",
            HTTP_X_INTERNAL_TOKEN="test-token",
        )

        self.assertEqual(response.status_code, 201, response.content)
        item = WardrobeItem.objects.get(job=job)
        self.assertIsNotNone(item.added_to_closet_at)
        self.assertIn(str(item.pk), self._closet_item_ids())

    def test_other_users_item_cannot_be_added(self):
        response = self.client.post(
            reverse("wardrobe:item-add-to-closet", args=[self.other_item.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_lookbook_detail_reports_whether_item_is_in_closet(self):
        """룩 상세는 아이템마다 옷장 편입 여부를 함께 준다 — 버튼을 그릴지 판단할 근거다."""

        item = self._extracted_item_for_lookbook()
        post = LookbookPost.objects.get(wardrobe_links__wardrobe_item=item)

        before = self.client.get(
            reverse("lookbook:lookbook-detail", args=[post.pk])
        ).json()
        self.assertIsNone(before["wardrobe_items"][0]["added_to_closet_at"])

        item.added_to_closet_at = timezone.now()
        item.save(update_fields=["added_to_closet_at"])

        after = self.client.get(
            reverse("lookbook:lookbook-detail", args=[post.pk])
        ).json()
        self.assertIsNotNone(after["wardrobe_items"][0]["added_to_closet_at"])
