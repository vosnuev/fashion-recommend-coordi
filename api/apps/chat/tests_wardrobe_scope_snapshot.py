from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.chat.models import ChatRun, ChatSession
from apps.chat.services import identity as identity_service
from apps.chat.services import sessions as session_service
from apps.wardrobe.models import WardrobeHashtag, WardrobeItem
from apps.recommend.services.wardrobe_link import owned_closet_item_ids

User = get_user_model()


class WardrobeScopeSnapshotApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="scope-member")
        self.other = User.objects.create_user(username="scope-other")
        identity = identity_service.get_or_create_member_identity(self.user)
        self.session = session_service.create_session(
            identity=identity,
            mode=ChatSession.Mode.WARDROBE_BASED,
        )
        self.url = reverse("chat:session-messages", args=[self.session.pk])
        self.client.force_authenticate(self.user)
        self.hashtag = WardrobeHashtag.objects.create(user=self.user, name="출근룩")
        self.item = WardrobeItem.objects.create(
            user=self.user,
            s3_key="wardrobe/scope/member-shirt.png",
            item_name="출근 셔츠",
            category_large="상의",
            category_small="셔츠",
            color="화이트",
            confirmed=True,
            added_to_closet_at=timezone.now(),
        )
        self.item.wardrobe_hashtags.add(self.hashtag)

    def payload(self, hashtag_id):
        return {
            "content": "출근룩 해시태그 안에서 추천해줘",
            "client_message_id": f"scope-{hashtag_id}",
            "wardrobe_scope": {
                "system_categories": ["상의"],
                "hashtag_ids": [str(hashtag_id)],
                "match_mode": "REQUIRED",
            },
        }

    @patch("apps.chat.views.ChatEventStore.publish")
    @patch("apps.chat.views.chat_queue.enqueue")
    def test_scope_is_snapshotted_as_owned_candidate(self, _enqueue, _publish):
        response = self.client.post(self.url, self.payload(self.hashtag.pk), format="json")

        self.assertEqual(response.status_code, 202)
        run = ChatRun.objects.get(pk=response.data["run"]["id"])
        run.full_clean()
        self.assertEqual(run.wardrobe_scope_snapshot["candidate_item_ids"], [str(self.item.pk)])
        self.assertEqual(run.wardrobe_scope_snapshot["hashtags"][0]["name"], "출근룩")
        self.assertEqual(
            response.data["run"]["wardrobe_scope_snapshot"]["candidate_item_ids"],
            [str(self.item.pk)],
        )

    def test_foreign_hashtag_is_rejected_without_run(self):
        foreign = WardrobeHashtag.objects.create(user=self.other, name="비밀")
        response = self.client.post(self.url, self.payload(foreign.pk), format="json")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "WARDROBE_SCOPE_FORBIDDEN")
        self.assertFalse(ChatRun.objects.exists())

    def test_cross_dimension_filter_uses_and_and_empty_scope_is_rejected(self):
        response = self.client.post(
            self.url,
            {
                **self.payload(self.hashtag.pk),
                "client_message_id": "scope-empty",
                "wardrobe_scope": {
                    "system_categories": ["하의"],
                    "hashtag_ids": [str(self.hashtag.pk)],
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "WARDROBE_SCOPE_EMPTY")

    def test_owned_scope_pool_excludes_other_users_and_unadded_items(self):
        WardrobeItem.objects.create(
            user=self.other,
            s3_key="wardrobe/scope/friend.png",
            item_name="친구 옷",
            category_large="상의",
            confirmed=True,
            added_to_closet_at=timezone.now(),
        )
        WardrobeItem.objects.create(
            user=self.user,
            s3_key="wardrobe/scope/unadded.png",
            item_name="미편입 옷",
            category_large="상의",
            confirmed=True,
        )

        self.assertEqual(owned_closet_item_ids(self.user), [str(self.item.pk)])
