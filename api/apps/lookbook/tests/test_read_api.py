"""룩북 조회·수정·삭제 API."""

from django.urls import reverse
from rest_framework.test import APIClient

from apps.lookbook.contracts import LookbookStatus
from apps.lookbook.models import LookbookPost
from apps.lookbook.tests.base import LookbookApiTestCase


class LookbookReadApiTests(LookbookApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.list_url = reverse("lookbook:lookbook-list")
        self.first = self._create(hashtags=["출근", "미니멀"])
        self.second = self._create(hashtags=["데이트"])
        self.foreign = LookbookPost.objects.create(
            user=self.other_user,
            source_type="WARDROBE_SELECTED",
            image_s3_key="lookbook/other/cover.png",
            status=LookbookStatus.COMPLETED.value,
        )

    def _create(self, **overrides) -> LookbookPost:
        response = self.client.post(
            reverse("lookbook:lookbook-wardrobe-create"),
            {
                "wardrobe_item_ids": [str(self.top.pk)],
                "schedule": "기록",
                "tpo": ["출근"],
                "hashtags": overrides.get("hashtags", []),
            },
            format="json",
        )
        assert response.status_code == 201, response.data
        return LookbookPost.objects.get(pk=response.data["id"])

    def test_list_returns_only_my_posts_newest_first(self) -> None:
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)
        self.assertIsNone(response.data["next_offset"])
        ids = [row["id"] for row in response.data["results"]]
        self.assertEqual(ids, [str(self.second.pk), str(self.first.pk)])

    def test_list_filters_by_hashtag(self) -> None:
        response = self.client.get(self.list_url, {"hashtag": "데이트"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(self.second.pk))

    def test_list_paginates_and_reports_the_next_offset(self) -> None:
        first_page = self.client.get(self.list_url, {"limit": 1})
        second_page = self.client.get(self.list_url, {"limit": 1, "offset": 1})

        self.assertEqual(first_page.data["count"], 2)
        self.assertEqual(first_page.data["next_offset"], 1)
        self.assertEqual(len(first_page.data["results"]), 1)
        self.assertIsNone(second_page.data["next_offset"])
        self.assertEqual(second_page.data["results"][0]["id"], str(self.first.pk))

    def test_list_rejects_an_oversized_page(self) -> None:
        response = self.client.get(self.list_url, {"limit": 1000})

        self.assertEqual(response.status_code, 400)

    def test_detail_exposes_items_and_presigned_urls(self) -> None:
        response = self.client.get(
            reverse("lookbook:lookbook-detail", args=[self.first.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], LookbookStatus.COMPLETED.value)
        self.assertEqual(len(response.data["wardrobe_items"]), 1)
        item = response.data["wardrobe_items"][0]
        self.assertEqual(str(item["wardrobe_item_id"]), str(self.top.pk))
        self.assertTrue(item["image_url"].startswith("https://lookbook.example/"))
        self.assertEqual(item["snapshot"]["item_name"], "흰색 반팔")

    def test_detail_hides_other_users_posts(self) -> None:
        response = self.client.get(
            reverse("lookbook:lookbook-detail", args=[self.foreign.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_patch_updates_metadata_only(self) -> None:
        response = self.client.patch(
            reverse("lookbook:lookbook-detail", args=[self.first.pk]),
            {"schedule": "수정된 일정", "hashtags": ["여행"]},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.first.refresh_from_db()
        self.assertEqual(self.first.schedule, "수정된 일정")
        self.assertEqual(self.first.hashtags, ["여행"])
        self.assertEqual(self.first.tpo, ["출근"])

    def test_patch_rejects_unknown_fields(self) -> None:
        response = self.client.patch(
            reverse("lookbook:lookbook-detail", args=[self.first.pk]),
            {"image_s3_key": "hacked"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_delete_removes_the_post_and_cleans_s3(self) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(
                reverse("lookbook:lookbook-detail", args=[self.first.pk])
            )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(LookbookPost.objects.filter(pk=self.first.pk).exists())
        self.mocks["delete_lookbook"].assert_called_once_with(
            self.user.pk,
            self.first.pk,
        )

    def test_delete_is_refused_while_processing(self) -> None:
        LookbookPost.objects.filter(pk=self.first.pk).update(
            status=LookbookStatus.REGISTERED.value
        )

        response = self.client.delete(
            reverse("lookbook:lookbook-detail", args=[self.first.pk])
        )

        self.assertEqual(response.status_code, 409)
        self.assertTrue(LookbookPost.objects.filter(pk=self.first.pk).exists())

    def test_processing_status_counts_selected_and_extracted(self) -> None:
        response = self.client.get(
            reverse("lookbook:lookbook-processing-status", args=[self.first.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(response.data["lookbook_id"]), str(self.first.pk))
        self.assertFalse(response.data["processing_required"])
        self.assertTrue(response.data["is_terminal"])
        self.assertTrue(response.data["result_available"])
        self.assertEqual(
            response.data["item_counts"],
            {"total": 1, "selected": 1, "extracted": 0},
        )
        self.assertIsNone(response.data["failure"])

    def test_read_endpoints_require_authentication(self) -> None:
        anonymous = APIClient()

        self.assertEqual(anonymous.get(self.list_url).status_code, 401)
        self.assertEqual(
            anonymous.get(
                reverse("lookbook:lookbook-detail", args=[self.first.pk])
            ).status_code,
            401,
        )
