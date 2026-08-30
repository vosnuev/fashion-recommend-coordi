from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class WardrobeViewPreferenceApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="wardrobe-view-owner")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_default_is_created_and_partial_updates_are_restored(self):
        initial = self.client.get("/api/v1/wardrobe/view-preferences/")
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.data["group_mode"], "SYSTEM_CATEGORY")
        self.assertEqual(initial.data["item_sort"], "ADDED_DESC")

        saved = self.client.patch(
            "/api/v1/wardrobe/view-preferences/",
            {"group_mode": "HASHTAG", "item_sort": "COLOR_NAME_ASC"},
            format="json",
        )
        restored = self.client.get("/api/v1/wardrobe/view-preferences/")

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(restored.data["group_mode"], "HASHTAG")
        self.assertEqual(restored.data["item_sort"], "COLOR_NAME_ASC")

    def test_invalid_enum_is_rejected(self):
        response = self.client.patch(
            "/api/v1/wardrobe/view-preferences/",
            {"group_mode": "SHARED_CATEGORY"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
