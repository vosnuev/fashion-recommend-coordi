from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from apps.recommend.services.qdrant import GOLDEN_OUTFIT_COLLECTION


class SetGoldensetQdrantStatusCommandTests(SimpleTestCase):
    @patch(
        "apps.recommend.management.commands.set_goldenset_qdrant_status.get_client"
    )
    def test_updates_matching_outfit_payloads(self, get_client) -> None:
        client = Mock()
        client.count.return_value = SimpleNamespace(count=7)
        get_client.return_value = client
        stdout = StringIO()

        call_command(
            "set_goldenset_qdrant_status",
            dataset_version="v1",
            from_status="PILOT",
            status="ACTIVE",
            stdout=stdout,
        )

        client.count.assert_called_once()
        kwargs = client.set_payload.call_args.kwargs
        self.assertEqual(kwargs["collection_name"], GOLDEN_OUTFIT_COLLECTION)
        self.assertEqual(
            kwargs["payload"],
            {"status": "ACTIVE", "dataset_status": "ACTIVE"},
        )
        self.assertTrue(kwargs["wait"])
        self.assertIn("7건 갱신 완료", stdout.getvalue())

    @patch(
        "apps.recommend.management.commands.set_goldenset_qdrant_status.get_client"
    )
    def test_dry_run_only_counts_targets(self, get_client) -> None:
        client = Mock()
        client.count.return_value = SimpleNamespace(count=3)
        get_client.return_value = client

        call_command(
            "set_goldenset_qdrant_status",
            dataset_version="v1",
            from_status="PILOT",
            status="ACTIVE",
            dry_run=True,
            stdout=StringIO(),
        )

        client.set_payload.assert_not_called()

    def test_rejects_status_outside_model_contract(self) -> None:
        with self.assertRaisesRegex(CommandError, "지원하지 않는 골든셋 상태"):
            call_command(
                "set_goldenset_qdrant_status",
                dataset_version="v1",
                status="PUBLISHED",
                stdout=StringIO(),
            )
