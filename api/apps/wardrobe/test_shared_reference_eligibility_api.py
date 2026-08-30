from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.wardrobe.models import (
    SharedWardrobeItem,
    SharedWardrobeRoom,
    WardrobeItem,
)
from apps.wardrobe.services import shared_wardrobe as shared_service
from apps.wardrobe.services.vector_reconciliation import (
    WardrobeVectorStoreUnavailable,
)

User = get_user_model()


class SharedReferenceEligibilityApiTests(APITestCase):
    def setUp(self) -> None:
        self.owner = User.objects.create_user(username="reference-owner")
        self.room = shared_service.create_shared_room(self.owner, "참조 가능 테스트")
        self.client.force_authenticate(self.owner)
        self.url = f"/api/v1/shared-wardrobes/{self.room.pk}/items/"
        enqueue_patcher = patch(
            "apps.wardrobe.services.reference_eligibility."
            "vector_reindex_jobs.enqueue_many",
            return_value=1,
        )
        self.enqueue_many = enqueue_patcher.start()
        self.addCleanup(enqueue_patcher.stop)

    def _shared_item(
        self,
        *,
        name: str,
        status: str = SharedWardrobeItem.Status.AVAILABLE,
        confirmed: bool = True,
        embedding_version: str = "fashionsiglip-v1",
        s3_key: str = "wardrobe/reference.webp",
    ) -> SharedWardrobeItem:
        item = WardrobeItem.objects.create(
            user=self.owner,
            s3_key=s3_key,
            item_name=name,
            category_large="상의",
            confirmed=confirmed,
            added_to_closet_at=timezone.now(),
            embedding_version=embedding_version,
        )
        return SharedWardrobeItem.objects.create(
            room=self.room,
            registered_by=self.owner,
            wardrobe_item=item,
            status=status,
        )

    @patch(
        "apps.wardrobe.services.reference_eligibility.WardrobeVectorReconciler"
    )
    def test_shared_item_list_exposes_reference_eligibility_contract(
        self,
        reconciler_class,
    ) -> None:
        available = self._shared_item(name="사용 가능")
        borrowed = self._shared_item(
            name="대여 중",
            status=SharedWardrobeItem.Status.BORROWED,
        )
        legacy_private = self._shared_item(
            name="과거 나만 보기 값",
            status=SharedWardrobeItem.Status.PRIVATE,
        )
        unconfirmed = self._shared_item(name="미확정", confirmed=False)
        vector_not_ready = self._shared_item(
            name="벡터 준비 중",
            embedding_version="",
        )
        reconciler_class.return_value.audit.side_effect = lambda items: [
            SimpleNamespace(item_id=str(item.pk), vector_ready=True)
            for item in items
        ]

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        by_id = {str(row["id"]): row for row in response.data}
        self.assertEqual(
            (
                by_id[str(available.pk)]["reference_eligible"],
                by_id[str(available.pk)]["reference_unavailable_reason"],
            ),
            (True, None),
        )
        self.assertEqual(
            (
                by_id[str(borrowed.pk)]["reference_eligible"],
                by_id[str(borrowed.pk)]["reference_unavailable_reason"],
            ),
            (True, None),
        )
        self.assertEqual(
            (
                by_id[str(legacy_private.pk)]["reference_eligible"],
                by_id[str(legacy_private.pk)]["reference_unavailable_reason"],
            ),
            (True, None),
        )
        self.assertEqual(
            (
                by_id[str(unconfirmed.pk)]["reference_eligible"],
                by_id[str(unconfirmed.pk)]["reference_unavailable_reason"],
            ),
            (False, "NOT_CONFIRMED"),
        )
        self.assertEqual(
            (
                by_id[str(vector_not_ready.pk)]["reference_eligible"],
                by_id[str(vector_not_ready.pk)]["reference_unavailable_reason"],
            ),
            (False, "VECTOR_NOT_READY"),
        )

    @patch(
        "apps.wardrobe.services.reference_eligibility.WardrobeVectorReconciler"
    )
    def test_db_flag_does_not_claim_eligibility_when_qdrant_point_is_missing(
        self,
        reconciler_class,
    ) -> None:
        shared_item = self._shared_item(name="실제 벡터 없음")
        reconciler_class.return_value.audit.return_value = [
            SimpleNamespace(
                item_id=str(shared_item.wardrobe_item_id),
                vector_ready=False,
            )
        ]

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data[0]["reference_eligible"])
        self.assertEqual(
            response.data[0]["reference_unavailable_reason"],
            "VECTOR_NOT_READY",
        )
        queued_items = list(self.enqueue_many.call_args.args[0])
        self.assertEqual(
            [str(item.pk) for item in queued_items],
            [str(shared_item.wardrobe_item_id)],
        )

    @patch(
        "apps.wardrobe.services.reference_eligibility.WardrobeVectorReconciler"
    )
    def test_qdrant_outage_fails_closed_without_breaking_the_list_api(
        self,
        reconciler_class,
    ) -> None:
        self._shared_item(name="저장소 장애")
        reconciler_class.return_value.audit.side_effect = (
            WardrobeVectorStoreUnavailable("offline")
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data[0]["reference_eligible"])
        self.assertEqual(
            response.data[0]["reference_unavailable_reason"],
            "VECTOR_NOT_READY",
        )
        self.enqueue_many.assert_not_called()

    @patch(
        "apps.wardrobe.services.reference_eligibility.WardrobeVectorReconciler"
    )
    def test_missing_db_flag_is_automatically_queued_for_reindex(
        self,
        reconciler_class,
    ) -> None:
        shared_item = self._shared_item(
            name="자동 복구 대상",
            embedding_version="",
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data[0]["reference_eligible"])
        reconciler_class.assert_not_called()
        queued_items = list(self.enqueue_many.call_args.args[0])
        self.assertEqual(
            [str(item.pk) for item in queued_items],
            [str(shared_item.wardrobe_item_id)],
        )
