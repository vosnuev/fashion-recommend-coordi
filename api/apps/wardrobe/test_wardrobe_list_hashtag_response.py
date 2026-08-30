from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.wardrobe.models import WardrobeHashtag, WardrobeItem

User = get_user_model()


class WardrobeListHashtagResponseTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="hashtag-list-owner")
        self.other_user = User.objects.create_user(username="hashtag-list-other")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_list_prefetches_hashtags_without_n_plus_one_queries(self):
        hashtags = [
            WardrobeHashtag.objects.create(
                user=self.user,
                name=f"태그 {index}",
                position=index,
            )
            for index in range(2)
        ]
        items = [
            WardrobeItem.objects.create(
                user=self.user,
                s3_key=f"wardrobe/hashtag-list-owner/{index}.png",
                item_name=f"옷 {index}",
                category_large="상의",
                confirmed=True,
                added_to_closet_at=timezone.now(),
            )
            for index in range(50)
        ]
        for index, item in enumerate(items):
            item.wardrobe_hashtags.add(hashtags[index % 2])

        with self.assertNumQueries(2):
            response = self.client.get("/api/v1/wardrobe/items/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 50)
        self.assertEqual(len(response.data[0]["wardrobe_hashtags"]), 1)

    def test_shared_item_response_does_not_expose_owner_hashtags(self):
        # 공유 옷장 관계를 만들지 않은 다른 사용자의 아이템은 상세 조회 자체가 거부된다.
        foreign = WardrobeItem.objects.create(
            user=self.other_user,
            s3_key="wardrobe/hashtag-list-other/private.png",
            item_name="비공개 옷",
            category_large="상의",
            confirmed=True,
            added_to_closet_at=timezone.now(),
        )
        private = WardrobeHashtag.objects.create(user=self.other_user, name="비밀")
        foreign.wardrobe_hashtags.add(private)

        response = self.client.get(f"/api/v1/wardrobe/items/{foreign.pk}/")

        self.assertEqual(response.status_code, 404)
