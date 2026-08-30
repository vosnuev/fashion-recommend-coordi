from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.wardrobe.models import WardrobeHashtag, WardrobeItem

User = get_user_model()


class PersonalWardrobeHashtagAssignmentApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="hashtag-assignment-owner")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.first_item = self._item("first.png", "첫 옷")
        self.second_item = self._item("second.png", "둘째 옷")
        self.hashtag = WardrobeHashtag.objects.create(
            user=self.user,
            name="출근룩",
            position=0,
        )
        self.first_item.wardrobe_hashtags.add(self.hashtag)

    def _item(self, key: str, name: str) -> WardrobeItem:
        return WardrobeItem.objects.create(
            user=self.user,
            s3_key=f"wardrobe/hashtag-assignment-owner/{key}",
            item_name=name,
            category_large="상의",
            confirmed=True,
            added_to_closet_at=timezone.now(),
        )

    def test_patch_adds_and_removes_items(self):
        response = self.client.patch(
            f"/api/v1/wardrobe/hashtags/{self.hashtag.pk}/items/",
            {
                "add_item_ids": [str(self.second_item.pk)],
                "remove_item_ids": [str(self.first_item.pk)],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["deleted"])
        self.assertEqual(
            set(self.hashtag.wardrobe_items.values_list("pk", flat=True)),
            {self.second_item.pk},
        )

    def test_removing_last_item_deletes_hashtag(self):
        response = self.client.patch(
            f"/api/v1/wardrobe/hashtags/{self.hashtag.pk}/items/",
            {"remove_item_ids": [str(self.first_item.pk)]},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["deleted"])
        self.assertFalse(WardrobeHashtag.objects.filter(pk=self.hashtag.pk).exists())

    def test_item_put_normalizes_replaces_and_prunes_hashtags(self):
        response = self.client.put(
            f"/api/v1/wardrobe/items/{self.first_item.pk}/hashtags/",
            {"names": [" # 주말 ", "주말", "여름"]},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["name"] for row in response.data["wardrobe_hashtags"]],
            ["주말", "여름"],
        )
        self.assertFalse(WardrobeHashtag.objects.filter(pk=self.hashtag.pk).exists())
        self.assertEqual(
            set(self.first_item.wardrobe_hashtags.values_list("name", flat=True)),
            {"주말", "여름"},
        )

    def test_item_delete_prunes_last_orphan_hashtag(self):
        response = self.client.delete(
            f"/api/v1/wardrobe/items/{self.first_item.pk}/"
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(WardrobeHashtag.objects.filter(pk=self.hashtag.pk).exists())
