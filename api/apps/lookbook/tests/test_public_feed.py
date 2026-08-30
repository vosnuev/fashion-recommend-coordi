"""전체 공개 룩 피드.

룩북은 친구 단위 공유를 두지 않는다 — 내 것이거나 모두에게 공개거나 둘 중 하나다.
공개한 룩만, 그리고 처리가 끝난 룩만 남에게 보인다.
"""

from __future__ import annotations

from django.urls import reverse
from rest_framework.test import APIClient

from apps.lookbook.contracts import LookbookSourceType, LookbookStatus
from apps.lookbook.models import LookbookPost
from apps.lookbook.tests.base import LookbookApiTestCase


class PublicFeedTests(LookbookApiTestCase):
    def _make_post(self, *, user, is_public: bool, status: str) -> LookbookPost:
        return LookbookPost.objects.create(
            user=user,
            source_type=LookbookSourceType.WARDROBE_SELECTED.value,
            image_s3_key=f"lookbook/{user.pk}/{is_public}-{status}.jpg",
            is_public=is_public,
            status=status,
        )

    def _feed_ids(self, client=None) -> list[str]:
        response = (client or self.client).get(reverse("lookbook:lookbook-public"))
        self.assertEqual(response.status_code, 200)
        return [row["id"] for row in response.json()["results"]]

    def test_only_public_and_completed_posts_are_listed(self):
        public = self._make_post(
            user=self.other_user,
            is_public=True,
            status=LookbookStatus.COMPLETED.value,
        )
        private = self._make_post(
            user=self.other_user,
            is_public=False,
            status=LookbookStatus.COMPLETED.value,
        )
        # 처리 중인 룩은 표지도 아이템도 아직 제자리가 아니라 남에게 보이면 깨져 보인다.
        still_processing = self._make_post(
            user=self.other_user,
            is_public=True,
            status=LookbookStatus.PROCESSING.value,
        )

        ids = self._feed_ids()

        self.assertIn(str(public.pk), ids)
        self.assertNotIn(str(private.pk), ids)
        self.assertNotIn(str(still_processing.pk), ids)

    def test_my_own_public_post_is_in_the_feed_too(self):
        """둘러보기는 '남의 룩'이 아니라 '모두가 보는 룩'이다 — 내가 공개한 것도 포함된다."""

        mine = self._make_post(
            user=self.user,
            is_public=True,
            status=LookbookStatus.COMPLETED.value,
        )

        self.assertIn(str(mine.pk), self._feed_ids())

    def test_guests_can_read_the_feed(self):
        public = self._make_post(
            user=self.other_user,
            is_public=True,
            status=LookbookStatus.COMPLETED.value,
        )

        self.assertIn(str(public.pk), self._feed_ids(client=APIClient()))

    def test_my_lookbook_list_still_shows_only_my_posts(self):
        """공개 피드가 생겨도 '내 룩북'은 내 것만이어야 한다."""

        others_public = self._make_post(
            user=self.other_user,
            is_public=True,
            status=LookbookStatus.COMPLETED.value,
        )

        response = self.client.get(reverse("lookbook:lookbook-list"))

        self.assertEqual(response.status_code, 200)
        ids = [row["id"] for row in response.json()["results"]]
        self.assertNotIn(str(others_public.pk), ids)

    def test_registering_with_is_public_marks_the_post(self):
        response = self.client.post(
            reverse("lookbook:lookbook-wardrobe-create"),
            {
                "wardrobe_item_ids": [str(self.top.pk)],
                "is_public": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response.json()["is_public"])
        self.assertIn(response.json()["id"], self._feed_ids())

    def test_visibility_can_be_flipped_afterwards(self):
        post = self._make_post(
            user=self.user,
            is_public=False,
            status=LookbookStatus.COMPLETED.value,
        )

        response = self.client.patch(
            reverse("lookbook:lookbook-detail", args=[post.pk]),
            {"is_public": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["is_public"])
        self.assertIn(str(post.pk), self._feed_ids())
