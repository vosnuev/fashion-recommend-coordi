from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.users.models import User
from apps.wardrobe.models import WardrobeItem
from apps.wardrobe.services import vector_reindex_jobs, vectors


class ReindexWardrobeVectorsCommandTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="reindex-command-user")
        self.missing = self._item("missing", "")
        self.ready = self._item("ready", vectors.EMBEDDING_VERSION)

    def _item(self, key: str, embedding_version: str) -> WardrobeItem:
        return WardrobeItem.objects.create(
            user=self.user,
            s3_key=f"wardrobe/reindex-command-user/{key}.webp",
            item_name=key,
            category_large="상의",
            embedding_version=embedding_version,
        )

    @patch("apps.wardrobe.services.vector_reindex_jobs.enqueue_many")
    def test_default_is_dry_run_and_selects_only_missing_flags(
        self,
        enqueue_many,
    ) -> None:
        output = StringIO()

        call_command("reindex_wardrobe_vectors", stdout=output)

        enqueue_many.assert_not_called()
        self.assertIn(str(self.missing.pk), output.getvalue())
        self.assertNotIn(str(self.ready.pk), output.getvalue())
        self.assertIn("mode=dry-run", output.getvalue())

    @patch("apps.wardrobe.services.vector_reindex_jobs.enqueue_many")
    def test_enqueue_and_force_are_explicit(self, enqueue_many) -> None:
        enqueue_many.return_value = 2

        call_command(
            "reindex_wardrobe_vectors",
            enqueue=True,
            force=True,
            stdout=StringIO(),
        )

        queued_items = enqueue_many.call_args.args[0]
        self.assertEqual(
            {item.pk for item in queued_items},
            {self.missing.pk, self.ready.pk},
        )

    @patch.dict(
        "os.environ",
        {
            "WARDROBE_REINDEX_CALLBACK_URL": "",
            "WARDROBE_CALLBACK_URL": (
                "https://api.example.com/api/v1/internal/wardrobe/callback/"
            ),
        },
    )
    def test_reindex_callback_falls_back_to_upload_callback_origin(self) -> None:
        self.assertEqual(
            vector_reindex_jobs._callback_url(),
            "https://api.example.com/api/v1/internal/wardrobe/reindex-callback/",
        )

    @patch("apps.wardrobe.services.vector_reindex_jobs._redis")
    @patch.object(
        vector_reindex_jobs.storage,
        "BUCKET",
        "wardrobe-test-bucket",
    )
    @patch.dict(
        "os.environ",
        {
            "WARDROBE_REINDEX_CALLBACK_URL": (
                "https://api.example.com/api/v1/internal/wardrobe/reindex-callback/"
            )
        },
    )
    def test_enqueue_many_deduplicates_items_in_redis(
        self,
        redis_client,
    ) -> None:
        pipeline = redis_client.return_value.pipeline.return_value
        pipeline.execute.return_value = [1, 0]

        enqueued = vector_reindex_jobs.enqueue_many([self.missing, self.ready])

        self.assertEqual(enqueued, 1)
        self.assertEqual(pipeline.eval.call_count, 2)
        for call in pipeline.eval.call_args_list:
            self.assertEqual(call.args[2], vector_reindex_jobs.DEDUP_KEY)
            self.assertEqual(call.args[3], vector_reindex_jobs.QUEUE_KEY)


class WardrobeReindexCallbackTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="reindex-callback-user")
        self.item = WardrobeItem.objects.create(
            user=self.user,
            s3_key="wardrobe/reindex-callback-user/item.webp",
            item_name="셔츠",
            category_large="상의",
            embedding_version="",
        )
        self.client = APIClient()
        self.url = "/api/v1/internal/wardrobe/reindex-callback/"

    def _payload(self, **overrides) -> dict:
        payload = {
            "item_id": str(self.item.pk),
            "status": "success",
            "source_updated_at": self.item.updated_at.isoformat(),
            "embedding_version": vectors.EMBEDDING_VERSION,
            "error": "",
            "image_vector": [0.0] * vectors.IMAGE_DIM,
            "text_vector": [0.0] * vectors.TEXT_DIM,
        }
        payload.update(overrides)
        return payload

    @patch.dict("os.environ", {"WARDROBE_INTERNAL_TOKEN": "test-token"})
    @patch("apps.wardrobe.views.vectors.upsert_item", return_value=True)
    def test_success_sets_db_flag_only_after_qdrant_upsert(self, upsert_item) -> None:
        response = self.client.post(
            self.url,
            self._payload(),
            format="json",
            HTTP_X_INTERNAL_TOKEN="test-token",
        )

        self.assertEqual(response.status_code, 200)
        upsert_item.assert_called_once()
        self.item.refresh_from_db()
        self.assertEqual(self.item.embedding_version, vectors.EMBEDDING_VERSION)

    @patch.dict("os.environ", {"WARDROBE_INTERNAL_TOKEN": "test-token"})
    @patch("apps.wardrobe.views.vectors.upsert_item", return_value=False)
    def test_qdrant_failure_keeps_db_flag_unset(self, _upsert_item) -> None:
        response = self.client.post(
            self.url,
            self._payload(),
            format="json",
            HTTP_X_INTERNAL_TOKEN="test-token",
        )

        self.assertEqual(response.status_code, 503)
        self.item.refresh_from_db()
        self.assertEqual(self.item.embedding_version, "")

    @patch.dict("os.environ", {"WARDROBE_INTERNAL_TOKEN": "test-token"})
    @patch("apps.wardrobe.views.vectors.upsert_item")
    def test_stale_or_wrong_version_result_is_rejected(self, upsert_item) -> None:
        stale_response = self.client.post(
            self.url,
            self._payload(
                source_updated_at=(
                    self.item.updated_at - timedelta(seconds=1)
                ).isoformat()
            ),
            format="json",
            HTTP_X_INTERNAL_TOKEN="test-token",
        )
        wrong_version_response = self.client.post(
            self.url,
            self._payload(embedding_version="old-version"),
            format="json",
            HTTP_X_INTERNAL_TOKEN="test-token",
        )

        self.assertEqual(stale_response.status_code, 409)
        self.assertEqual(wrong_version_response.status_code, 409)
        upsert_item.assert_not_called()

    @patch.dict("os.environ", {"WARDROBE_INTERNAL_TOKEN": "test-token"})
    @patch("apps.wardrobe.views.vectors.upsert_item")
    def test_failed_worker_callback_does_not_change_vector_state(
        self,
        upsert_item,
    ) -> None:
        response = self.client.post(
            self.url,
            self._payload(
                status="failed",
                error="embedding failed",
                image_vector=[],
                text_vector=[],
            ),
            format="json",
            HTTP_X_INTERNAL_TOKEN="test-token",
        )

        self.assertEqual(response.status_code, 200)
        upsert_item.assert_not_called()
        self.item.refresh_from_db()
        self.assertEqual(self.item.embedding_version, "")
