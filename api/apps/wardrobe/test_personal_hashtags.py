from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.wardrobe.models import WardrobeHashtag, WardrobeItem, WardrobeItemHashtag

User = get_user_model()


class PersonalWardrobeHashtagModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="hashtag-owner")
        self.other_user = User.objects.create_user(username="hashtag-other")
        self.item = WardrobeItem.objects.create(
            user=self.user,
            s3_key="wardrobe/hashtag-owner/shirt.png",
            item_name="셔츠",
            category_large="상의",
            confirmed=True,
            added_to_closet_at=timezone.now(),
        )

    def test_name_is_normalized_without_hash_prefix(self):
        hashtag = WardrobeHashtag.objects.create(
            user=self.user,
            name="  #  Work   Look  ",
        )

        self.assertEqual(hashtag.name, "Work Look")
        self.assertEqual(hashtag.normalized_name, "work look")

    def test_normalized_name_is_unique_per_user(self):
        WardrobeHashtag.objects.create(user=self.user, name="출근룩")

        with self.assertRaises(IntegrityError), transaction.atomic():
            WardrobeHashtag.objects.create(user=self.user, name="# 출근룩")

        other = WardrobeHashtag.objects.create(user=self.other_user, name="출근룩")
        self.assertEqual(other.user, self.other_user)

    def test_item_can_have_multiple_wardrobe_hashtags(self):
        work = WardrobeHashtag.objects.create(user=self.user, name="출근룩", position=0)
        summer = WardrobeHashtag.objects.create(user=self.user, name="여름", position=1)
        WardrobeItemHashtag.objects.create(wardrobe_item=self.item, hashtag=work)
        WardrobeItemHashtag.objects.create(wardrobe_item=self.item, hashtag=summer)

        self.assertEqual(
            list(self.item.wardrobe_hashtags.values_list("name", flat=True)),
            ["출근룩", "여름"],
        )

    def test_cross_user_link_is_rejected(self):
        foreign = WardrobeHashtag.objects.create(user=self.other_user, name="비공개")

        with self.assertRaises(ValidationError):
            WardrobeItemHashtag.objects.create(
                wardrobe_item=self.item,
                hashtag=foreign,
            )

