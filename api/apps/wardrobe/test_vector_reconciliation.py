from __future__ import annotations

import math
import uuid
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from apps.recommend.services.qdrant import IMAGE_VECTOR, TEXT_VECTOR
from apps.users.models import User
from apps.wardrobe.models import WardrobeItem
from apps.wardrobe.services import vectors
from apps.wardrobe.services.vector_reconciliation import (
    EMBEDDING_VERSION_MISMATCH,
    POINT_MISSING,
    S3_KEY_MISMATCH,
    TEXT_VECTOR_INVALID,
    WardrobeVectorReconciler,
    WardrobeVectorStoreUnavailable,
)


def _item(*, embedding_version: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=7,
        s3_key="wardrobe/7/item.webp",
        embedding_version=embedding_version or vectors.EMBEDDING_VERSION,
        confirmed=True,
        category_large="상의",
    )


def _point(
    item,
    *,
    image_vector=None,
    text_vector=None,
    payload_overrides=None,
) -> SimpleNamespace:
    payload = {
        "item_id": str(item.id),
        "user_id": item.user_id,
        "s3_key": item.s3_key,
        "embedding_version": vectors.EMBEDDING_VERSION,
        "confirmed": item.confirmed,
        "category_large": item.category_large,
    }
    payload.update(payload_overrides or {})
    return SimpleNamespace(
        id=item.id,
        payload=payload,
        vector={
            IMAGE_VECTOR: image_vector if image_vector is not None else [1.0, 0.0],
            TEXT_VECTOR: text_vector if text_vector is not None else [0.0, 1.0, 0.0],
        },
    )


class WardrobeVectorReconcilerTests(SimpleTestCase):
    def _reconciler(self, client: Mock) -> WardrobeVectorReconciler:
        return WardrobeVectorReconciler(
            client=client,
            expected_vector_dimensions={IMAGE_VECTOR: 2, TEXT_VECTOR: 3},
        )

    def test_ready_point_matches_db_and_full_vector_contract(self) -> None:
        item = _item()
        client = Mock()
        client.retrieve.return_value = [_point(item)]

        result = self._reconciler(client).audit([item])[0]

        self.assertTrue(result.vector_ready)
        self.assertFalse(result.needs_flag_repair)

    def test_missing_point_requests_stale_db_flag_clear(self) -> None:
        item = _item()
        client = Mock()
        client.retrieve.return_value = []

        result = self._reconciler(client).audit([item])[0]

        self.assertEqual(result.issues, (POINT_MISSING,))
        self.assertEqual(result.desired_embedding_version, "")
        self.assertTrue(result.needs_flag_repair)

    def test_payload_and_vector_contract_mismatches_are_reported(self) -> None:
        item = _item()
        client = Mock()
        client.retrieve.return_value = [
            _point(
                item,
                text_vector=[0.0, math.nan, 0.0],
                payload_overrides={
                    "s3_key": "wardrobe/other.webp",
                    "embedding_version": "old-version",
                },
            )
        ]

        result = self._reconciler(client).audit([item])[0]

        self.assertIn(S3_KEY_MISMATCH, result.issues)
        self.assertIn(EMBEDDING_VERSION_MISMATCH, result.issues)
        self.assertIn(TEXT_VECTOR_INVALID, result.issues)

    def test_qdrant_failure_is_exposed_as_safe_domain_error(self) -> None:
        client = Mock()
        client.retrieve.side_effect = RuntimeError("connection failed")

        with self.assertRaises(WardrobeVectorStoreUnavailable):
            self._reconciler(client).audit([_item()])


class AuditWardrobeVectorsCommandTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="vector-audit-user")

    def _db_item(self, key: str, embedding_version: str) -> WardrobeItem:
        return WardrobeItem.objects.create(
            user=self.user,
            s3_key=f"wardrobe/vector-audit-user/{key}.webp",
            item_name=key,
            category_large="상의",
            embedding_version=embedding_version,
        )

    @staticmethod
    def _qdrant_point(item: WardrobeItem) -> SimpleNamespace:
        return _point(
            item,
            image_vector=[0.0] * vectors.IMAGE_DIM,
            text_vector=[0.0] * vectors.TEXT_DIM,
        )

    @patch("apps.wardrobe.services.vector_reconciliation.get_client")
    def test_dry_run_reports_but_does_not_change_db_flag(self, get_client) -> None:
        stale = self._db_item("stale", vectors.EMBEDDING_VERSION)
        get_client.return_value.retrieve.return_value = []

        output = StringIO()
        call_command("audit_wardrobe_vectors", stdout=output)

        stale.refresh_from_db()
        self.assertEqual(stale.embedding_version, vectors.EMBEDDING_VERSION)
        self.assertIn("mode=dry-run", output.getvalue())

    @patch("apps.wardrobe.services.vector_reconciliation.get_client")
    def test_repair_clears_stale_flag_and_backfills_valid_point(
        self,
        get_client,
    ) -> None:
        stale = self._db_item("stale", vectors.EMBEDDING_VERSION)
        valid = self._db_item("valid", "")
        point = self._qdrant_point(valid)
        get_client.return_value.retrieve.return_value = [point]

        call_command(
            "audit_wardrobe_vectors",
            repair_flags=True,
            stdout=StringIO(),
        )

        stale.refresh_from_db()
        valid.refresh_from_db()
        self.assertEqual(stale.embedding_version, "")
        self.assertEqual(valid.embedding_version, vectors.EMBEDDING_VERSION)

    @patch("apps.wardrobe.services.vector_reconciliation.get_client")
    def test_qdrant_failure_never_partially_updates_db(self, get_client) -> None:
        first = self._db_item("first", vectors.EMBEDDING_VERSION)
        second = self._db_item("second", vectors.EMBEDDING_VERSION)
        get_client.return_value.retrieve.side_effect = RuntimeError("offline")

        with self.assertRaises(CommandError):
            call_command(
                "audit_wardrobe_vectors",
                repair_flags=True,
                batch_size=1,
                stdout=StringIO(),
            )

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.embedding_version, vectors.EMBEDDING_VERSION)
        self.assertEqual(second.embedding_version, vectors.EMBEDDING_VERSION)
