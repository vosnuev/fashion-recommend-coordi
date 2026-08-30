from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.wardrobe.models import WardrobeHashtag, WardrobeItem

User = get_user_model()


class PersonalWardrobeHashtagApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="hashtag-api-owner")
        self.other_user = User.objects.create_user(username="hashtag-api-other")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.item = WardrobeItem.objects.create(
            user=self.user,
            s3_key="wardrobe/hashtag-api-owner/shirt.png",
            item_name="셔츠",
            category_large="상의",
            confirmed=True,
            added_to_closet_at=timezone.now(),
        )

    def test_filter_list_contains_system_categories_and_hashtags(self):
        hashtag = WardrobeHashtag.objects.create(user=self.user, name="출근룩")
        self.item.wardrobe_hashtags.add(hashtag)

        response = self.client.get("/api/v1/wardrobe/categories/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["system_categories"][0]["type"], "SYSTEM")
        self.assertEqual(response.data["hashtags"][0]["name"], "출근룩")
        self.assertEqual(response.data["hashtags"][0]["item_count"], 1)

    def test_create_normalizes_name_and_requires_an_item(self):
        response = self.client.post(
            "/api/v1/wardrobe/hashtags/",
            {"name": " #  Work   Look ", "item_ids": [str(self.item.pk)]},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["name"], "Work Look")
        self.assertTrue(self.item.wardrobe_hashtags.filter(name="Work Look").exists())

        empty = self.client.post(
            "/api/v1/wardrobe/hashtags/",
            {"name": "빈 태그", "item_ids": []},
            format="json",
        )
        self.assertEqual(empty.status_code, 400)

    def test_create_reuses_existing_normalized_hashtag(self):
        existing = WardrobeHashtag.objects.create(user=self.user, name="출근룩")

        response = self.client.post(
            "/api/v1/wardrobe/hashtags/",
            {"name": "# 출근룩", "item_ids": [str(self.item.pk)]},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(response.data["id"]), str(existing.pk))
        self.assertEqual(WardrobeHashtag.objects.filter(user=self.user).count(), 1)

    def test_owner_can_rename_hashtag_without_losing_item_links(self):
        hashtag = WardrobeHashtag.objects.create(user=self.user, name="출근룩")
        self.item.wardrobe_hashtags.add(hashtag)

        response = self.client.patch(
            f"/api/v1/wardrobe/hashtags/{hashtag.pk}/",
            {"name": " # 데일리   출근 "},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["name"], "데일리 출근")
        hashtag.refresh_from_db()
        self.assertEqual(hashtag.name, "데일리 출근")
        self.assertTrue(self.item.wardrobe_hashtags.filter(pk=hashtag.pk).exists())

    def test_rename_rejects_duplicate_and_foreign_hashtag(self):
        first = WardrobeHashtag.objects.create(user=self.user, name="출근룩")
        second = WardrobeHashtag.objects.create(user=self.user, name="주말룩")
        foreign = WardrobeHashtag.objects.create(user=self.other_user, name="다른 사람")

        duplicate = self.client.patch(
            f"/api/v1/wardrobe/hashtags/{second.pk}/",
            {"name": "# 출근룩"},
            format="json",
        )
        forbidden = self.client.patch(
            f"/api/v1/wardrobe/hashtags/{foreign.pk}/",
            {"name": "침범"},
            format="json",
        )

        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(duplicate.data["code"], "HASHTAG_NAME_DUPLICATE")
        self.assertEqual(forbidden.status_code, 403)
        first.refresh_from_db()
        self.assertEqual(first.name, "출근룩")

    def test_delete_removes_only_hashtag_and_compacts_positions(self):
        first = WardrobeHashtag.objects.create(user=self.user, name="첫째", position=0)
        second = WardrobeHashtag.objects.create(user=self.user, name="둘째", position=1)
        self.item.wardrobe_hashtags.add(first)

        response = self.client.delete(f"/api/v1/wardrobe/hashtags/{first.pk}/")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(WardrobeHashtag.objects.filter(pk=first.pk).exists())
        self.assertTrue(WardrobeItem.objects.filter(pk=self.item.pk).exists())
        second.refresh_from_db()
        self.assertEqual(second.position, 0)

    def test_create_rejects_foreign_and_unadded_items(self):
        foreign = WardrobeItem.objects.create(
            user=self.other_user,
            s3_key="wardrobe/hashtag-api-other/item.png",
            item_name="다른 옷",
            category_large="상의",
            confirmed=True,
            added_to_closet_at=timezone.now(),
        )
        unadded = WardrobeItem.objects.create(
            user=self.user,
            s3_key="wardrobe/hashtag-api-owner/unadded.png",
            item_name="미등록 옷",
            category_large="상의",
            confirmed=True,
        )

        foreign_response = self.client.post(
            "/api/v1/wardrobe/hashtags/",
            {"name": "금지", "item_ids": [str(foreign.pk)]},
            format="json",
        )
        unadded_response = self.client.post(
            "/api/v1/wardrobe/hashtags/",
            {"name": "미등록", "item_ids": [str(unadded.pk)]},
            format="json",
        )

        self.assertEqual(foreign_response.status_code, 403)
        self.assertEqual(unadded_response.status_code, 404)

    def test_order_is_saved_immediately_and_requires_complete_set(self):
        first = WardrobeHashtag.objects.create(user=self.user, name="첫째", position=0)
        second = WardrobeHashtag.objects.create(user=self.user, name="둘째", position=1)

        response = self.client.put(
            "/api/v1/wardrobe/hashtags/order/",
            {"hashtag_ids": [str(second.pk), str(first.pk)]},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["name"] for row in response.data["hashtags"]], ["둘째", "첫째"])
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((second.position, first.position), (0, 1))

        invalid = self.client.put(
            "/api/v1/wardrobe/hashtags/order/",
            {"hashtag_ids": [str(first.pk)]},
            format="json",
        )
        self.assertEqual(invalid.status_code, 400)
