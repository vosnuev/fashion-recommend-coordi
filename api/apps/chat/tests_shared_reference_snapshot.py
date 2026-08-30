from __future__ import annotations

from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import ChatMessage, ChatRun, ChatSession
from apps.chat.services import identity as identity_service
from apps.chat.services import sessions as session_service
from apps.wardrobe.models import (
    SharedWardrobeItem,
    SharedWardrobeMember,
    SharedWardrobeRoom,
    WardrobeItem,
)
from apps.wardrobe.services.vector_reconciliation import (
    POINT_MISSING,
    WardrobeVectorAuditResult,
)

User = get_user_model()


class SharedReferenceSnapshotApiTests(APITestCase):
    def setUp(self) -> None:
        reconciler_patcher = patch(
            "apps.wardrobe.services.reference_eligibility.WardrobeVectorReconciler"
        )
        self.addCleanup(reconciler_patcher.stop)
        reconciler_class = reconciler_patcher.start()
        self.audit_mock = reconciler_class.return_value.audit
        self.audit_mock.side_effect = self._ready_audits

        enqueue_patcher = patch(
            "apps.wardrobe.services.reference_eligibility.vector_reindex_jobs.enqueue_many",
            return_value=0,
        )
        self.addCleanup(enqueue_patcher.stop)
        self.enqueue_mock = enqueue_patcher.start()

        self.user = User.objects.create_user(username="reference-member")
        self.friend = User.objects.create_user(
            username="reference-friend",
            nickname="하영",
        )
        self.identity = identity_service.get_or_create_member_identity(self.user)
        self.session = session_service.create_session(
            identity=self.identity,
            mode=ChatSession.Mode.WARDROBE_BASED,
        )
        self.url = reverse("chat:session-messages", args=[self.session.pk])
        self.client.force_authenticate(self.user)

        self.room = SharedWardrobeRoom.objects.create(title="친구 옷장")
        SharedWardrobeMember.objects.create(
            room=self.room,
            user=self.user,
            role=SharedWardrobeMember.Role.MEMBER,
        )
        SharedWardrobeMember.objects.create(
            room=self.room,
            user=self.friend,
            role=SharedWardrobeMember.Role.OWNER,
        )
        self.wardrobe_item = WardrobeItem.objects.create(
            user=self.friend,
            s3_key="wardrobe/reference-jacket.webp",
            item_name="친구의 검정 재킷",
            category_large="아우터",
            category_small="재킷",
            season=["봄", "가을"],
            style=["미니멀"],
            color="검정",
            pattern="무지",
            fit="오버핏",
            material="울",
            usage=["데이트"],
            layer_role="아우터",
            layer_order=3,
            confirmed=True,
            added_to_closet_at=timezone.now(),
            embedding_version="fashionsiglip-v1",
        )
        self.own_item = WardrobeItem.objects.create(
            user=self.user,
            s3_key="wardrobe/my-shirt.webp",
            item_name="내 파란 셔츠",
            category_large="상의",
            category_small="셔츠",
            season=["봄", "여름"],
            style=["캐주얼"],
            color="파랑",
            pattern="무지",
            fit="레귤러핏",
            material="면",
            usage=["출근"],
            layer_role="상의",
            layer_order=1,
            confirmed=True,
            added_to_closet_at=timezone.now(),
            embedding_version="fashionsiglip-v1",
        )
        self.shared_item = SharedWardrobeItem.objects.create(
            room=self.room,
            registered_by=self.friend,
            wardrobe_item=self.wardrobe_item,
            status=SharedWardrobeItem.Status.AVAILABLE,
        )

    @staticmethod
    def _ready_audits(items) -> list[WardrobeVectorAuditResult]:
        return [
            WardrobeVectorAuditResult(
                item_id=str(item.pk),
                user_id=item.user_id,
                db_embedding_version=item.embedding_version,
                indexed_embedding_version="fashionsiglip-v1",
                expected_embedding_version="fashionsiglip-v1",
                issues=(),
            )
            for item in items
        ]

    def _payload(self, client_message_id: str = "shared-reference-1") -> dict:
        return {
            "content": "이 옷과 비슷한 느낌으로 추천해줘",
            "client_message_id": client_message_id,
            "reference": {
                "type": "SHARED_WARDROBE_ITEM",
                "shared_item_id": str(self.shared_item.pk),
            },
        }

    def _own_payload(self, client_message_id: str = "owned-reference-1") -> dict:
        return {
            "content": "이 옷과 비슷한 느낌으로 추천해줘",
            "client_message_id": client_message_id,
            "reference": {
                "type": "WARDROBE_ITEM",
                "wardrobe_item_id": str(self.own_item.pk),
            },
        }

    @patch("apps.chat.views.ChatEventStore.publish")
    @patch("apps.chat.views.chat_queue.enqueue")
    def test_own_wardrobe_reference_is_accepted_in_both_recommendation_modes(
        self,
        _enqueue_mock,
        _publish_mock,
    ) -> None:
        wardrobe_response = self.client.post(
            self.url,
            self._own_payload("owned-reference-wardrobe"),
            format="json",
        )
        self.assertEqual(wardrobe_response.status_code, status.HTTP_202_ACCEPTED)
        wardrobe_run = ChatRun.objects.get(pk=wardrobe_response.data["run"]["id"])
        wardrobe_run.full_clean()
        self.assertEqual(wardrobe_run.reference_snapshot["type"], "WARDROBE_ITEM")
        self.assertEqual(
            wardrobe_run.reference_snapshot["wardrobe_item_id"],
            str(self.own_item.pk),
        )
        self.assertNotIn("shared_item_id", wardrobe_run.reference_snapshot)

        new_item_session = session_service.create_session(
            identity=self.identity,
            mode=ChatSession.Mode.NEW_ITEM,
        )
        new_item_response = self.client.post(
            reverse("chat:session-messages", args=[new_item_session.pk]),
            self._own_payload("owned-reference-product"),
            format="json",
        )
        self.assertEqual(new_item_response.status_code, status.HTTP_202_ACCEPTED)
        new_item_run = ChatRun.objects.get(pk=new_item_response.data["run"]["id"])
        self.assertEqual(new_item_run.reference_snapshot["type"], "WARDROBE_ITEM")

    def test_another_users_wardrobe_item_cannot_be_referenced_directly(self) -> None:
        payload = self._own_payload("owned-reference-forbidden")
        payload["reference"]["wardrobe_item_id"] = str(self.wardrobe_item.pk)

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["code"], "REFERENCE_ITEM_NOT_FOUND")

    @patch(
        "apps.chat.serializers.wardrobe_storage.presigned_get",
        return_value="https://images.example/my-shirt.webp",
    )
    @patch("apps.chat.views.ChatEventStore.publish")
    @patch("apps.chat.views.chat_queue.enqueue")
    def test_own_wardrobe_reference_is_restored_in_message_history(
        self,
        _enqueue_mock,
        _publish_mock,
        _presigned_get_mock,
    ) -> None:
        created = self.client.post(
            self.url,
            self._own_payload("owned-reference-history"),
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_202_ACCEPTED)

        response = self.client.get(self.url)
        user_message = next(
            item for item in response.data if item["role"] == ChatMessage.Role.USER
        )
        self.assertEqual(
            user_message["reference_summary"],
            {
                "schema_version": "1.0",
                "type": "WARDROBE_ITEM",
                "wardrobe_item_id": str(self.own_item.pk),
                "item_name": "내 파란 셔츠",
                "category_large": "상의",
                "owner_name": "내 옷",
                "room_name": "",
                "image_url": "https://images.example/my-shirt.webp",
            },
        )

    @patch("apps.chat.views.ChatEventStore.publish")
    @patch("apps.chat.views.chat_queue.enqueue")
    def test_message_request_persists_shared_item_reference_snapshot(
        self,
        _enqueue_mock,
        _publish_mock,
    ) -> None:
        response = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        run = ChatRun.objects.get(pk=response.data["run"]["id"])
        run.full_clean()
        snapshot = run.reference_snapshot
        self.assertEqual(snapshot["schema_version"], "1.0")
        self.assertEqual(snapshot["type"], "SHARED_WARDROBE_ITEM")
        self.assertEqual(snapshot["shared_item_id"], str(self.shared_item.pk))
        self.assertEqual(snapshot["room_id"], str(self.room.pk))
        self.assertEqual(
            snapshot["wardrobe_item_id"],
            str(self.wardrobe_item.pk),
        )
        self.assertEqual(
            snapshot["qdrant_point_id"],
            str(self.wardrobe_item.pk),
        )
        self.assertEqual(snapshot["item"]["category_large"], "아우터")
        self.assertEqual(snapshot["item"]["style"], ["미니멀"])
        self.assertEqual(snapshot["owner_name"], "하영")
        self.assertEqual(snapshot["room_name"], "친구 옷장")
        self.assertNotIn("vector", snapshot)

    @patch(
        "apps.chat.serializers.wardrobe_storage.presigned_get",
        return_value="https://images.example/reference-jacket.webp",
    )
    @patch("apps.chat.views.ChatEventStore.publish")
    @patch("apps.chat.views.chat_queue.enqueue")
    def test_message_history_returns_safe_reference_summary(
        self,
        _enqueue_mock,
        _publish_mock,
        _presigned_get_mock,
    ) -> None:
        created = self.client.post(
            self.url,
            self._payload("shared-reference-history"),
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_202_ACCEPTED)
        shared_item_id = str(self.shared_item.pk)
        self.shared_item.delete()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user_message = next(
            item
            for item in response.data
            if item["role"] == ChatMessage.Role.USER
        )
        summary = user_message["reference_summary"]
        self.assertEqual(
            summary,
            {
                "schema_version": "1.0",
                "type": "SHARED_WARDROBE_ITEM",
                "shared_item_id": shared_item_id,
                "item_name": "친구의 검정 재킷",
                "category_large": "아우터",
                "owner_name": "하영",
                "room_name": "친구 옷장",
                "image_url": "https://images.example/reference-jacket.webp",
            },
        )
        self.assertNotIn("qdrant_collection", summary)
        self.assertNotIn("qdrant_point_id", summary)
        self.assertNotIn("image_s3_key", summary)
        self.assertNotIn("wardrobe_item_id", summary)

    @patch("apps.chat.views.ChatEventStore.publish")
    @patch("apps.chat.views.chat_queue.enqueue")
    def test_message_without_reference_returns_null_summary(
        self,
        _enqueue_mock,
        _publish_mock,
    ) -> None:
        response = self.client.post(
            self.url,
            {
                "content": "일반 추천",
                "client_message_id": "without-shared-reference",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertIsNone(response.data["message"]["reference_summary"])

    @patch("apps.chat.views.ChatEventStore.publish")
    @patch("apps.chat.views.chat_queue.enqueue")
    def test_duplicate_client_message_keeps_original_reference_snapshot(
        self,
        _enqueue_mock,
        _publish_mock,
    ) -> None:
        payload = self._payload("shared-reference-idempotent")
        first = self.client.post(self.url, payload, format="json")
        run = ChatRun.objects.get(pk=first.data["run"]["id"])
        original_snapshot = run.reference_snapshot
        self.shared_item.status = SharedWardrobeItem.Status.PRIVATE
        self.shared_item.save(update_fields=["status"])

        duplicate = self.client.post(self.url, payload, format="json")

        self.assertEqual(duplicate.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(duplicate.data["run"]["id"], first.data["run"]["id"])
        run.refresh_from_db()
        self.assertEqual(run.reference_snapshot, original_snapshot)

    @patch("apps.chat.views.ChatEventStore.publish")
    @patch("apps.chat.views.chat_queue.enqueue")
    def test_legacy_private_shared_item_can_still_be_used_as_reference(
        self,
        _enqueue_mock,
        _publish_mock,
    ) -> None:
        self.shared_item.status = SharedWardrobeItem.Status.PRIVATE
        self.shared_item.save(update_fields=["status"])

        response = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        run = ChatRun.objects.get(pk=response.data["run"]["id"])
        self.assertEqual(
            run.reference_snapshot["source_status"],
            SharedWardrobeItem.Status.PRIVATE,
        )

    @patch("apps.chat.views.ChatEventStore.publish")
    @patch("apps.chat.views.chat_queue.enqueue")
    def test_legacy_status_change_after_selection_does_not_block_reference(
        self,
        _enqueue_mock,
        _publish_mock,
    ) -> None:
        listed = self.client.get(
            f"/api/v1/shared-wardrobes/{self.room.pk}/items/"
        )
        selected = next(
            row
            for row in listed.data
            if str(row["id"]) == str(self.shared_item.pk)
        )
        self.assertTrue(selected["reference_eligible"])

        self.shared_item.status = SharedWardrobeItem.Status.PRIVATE
        self.shared_item.save(update_fields=["status"])
        response = self.client.post(
            self.url,
            self._payload("shared-reference-race"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        run = ChatRun.objects.get(pk=response.data["run"]["id"])
        self.assertEqual(
            run.reference_snapshot["source_status"],
            SharedWardrobeItem.Status.PRIVATE,
        )

    def test_non_member_cannot_reference_shared_item(self) -> None:
        SharedWardrobeMember.objects.filter(
            room=self.room,
            user=self.user,
        ).delete()

        response = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "REFERENCE_ITEM_FORBIDDEN")

    @patch("apps.chat.views.ChatEventStore.publish")
    @patch("apps.chat.views.chat_queue.enqueue")
    def test_borrowed_shared_item_can_still_be_used_as_reference(
        self,
        _enqueue_mock,
        _publish_mock,
    ) -> None:
        self.shared_item.status = SharedWardrobeItem.Status.BORROWED
        self.shared_item.save(update_fields=["status"])

        response = self.client.post(
            self.url,
            self._payload("shared-reference-borrowed"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        run = ChatRun.objects.get(pk=response.data["run"]["id"])
        self.assertEqual(
            run.reference_snapshot["source_status"],
            SharedWardrobeItem.Status.BORROWED,
        )

    def test_reference_without_embedding_is_not_ready(self) -> None:
        self.wardrobe_item.embedding_version = ""
        self.wardrobe_item.save(update_fields=["embedding_version"])

        response = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "REFERENCE_ITEM_NOT_READY")

    def test_own_reference_without_embedding_is_not_ready_and_requeued(self) -> None:
        self.own_item.embedding_version = ""
        self.own_item.save(update_fields=["embedding_version"])
        self.enqueue_mock.return_value = 1

        response = self.client.post(
            self.url,
            self._own_payload("owned-reference-not-ready"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "REFERENCE_ITEM_NOT_READY")
        queued_items = list(self.enqueue_mock.call_args.args[0])
        self.assertEqual(queued_items, [self.own_item])

    def test_reference_with_missing_qdrant_point_is_not_ready_and_requeued(
        self,
    ) -> None:
        self.audit_mock.side_effect = None
        self.audit_mock.return_value = [
            WardrobeVectorAuditResult(
                item_id=str(self.wardrobe_item.pk),
                user_id=self.wardrobe_item.user_id,
                db_embedding_version="fashionsiglip-v1",
                indexed_embedding_version="",
                expected_embedding_version="fashionsiglip-v1",
                issues=(POINT_MISSING,),
            )
        ]
        self.enqueue_mock.return_value = 1

        response = self.client.post(
            self.url,
            self._payload("shared-reference-missing-vector"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "REFERENCE_ITEM_NOT_READY")
        self.enqueue_mock.assert_called_once()
        queued_items = list(self.enqueue_mock.call_args.args[0])
        self.assertEqual(queued_items, [self.wardrobe_item])
        self.assertFalse(
            ChatRun.objects.filter(session=self.session).exists()
        )

    def test_reference_contract_rejects_unknown_type(self) -> None:
        payload = self._payload()
        payload["reference"]["type"] = "WARDROBE_ITEM"

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("reference", response.data)
