import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import batch_tagger
import naver_collector_db as collector


class NaverProductIndexerTriggerTests(unittest.TestCase):
    def test_batch_collect_does_not_trigger_before_tagging(self) -> None:
        fake_batch_module = ModuleType("batch_tagger")
        fake_batch_module.submit_pending = Mock()
        conn = object()
        entries = [object()]

        with (
            patch.object(collector, "TAGGING_MODE", "batch"),
            patch.object(collector, "collect"),
            patch.object(collector, "trigger_product_indexer") as trigger,
            patch.dict(sys.modules, {"batch_tagger": fake_batch_module}),
        ):
            collector.run_collect_job(
                conn,
                entries,
                limit_per_keyword=3,
                skip_llm=False,
            )

        trigger.assert_not_called()
        fake_batch_module.submit_pending.assert_called_once_with(conn)

    def test_sync_collect_triggers_after_save(self) -> None:
        conn = object()
        entries = [object()]

        with (
            patch.object(collector, "TAGGING_MODE", "sync"),
            patch.object(collector, "collect", return_value=3),
            patch.object(collector, "trigger_product_indexer") as trigger,
        ):
            collector.run_collect_job(
                conn,
                entries,
                limit_per_keyword=3,
                skip_llm=False,
            )

        trigger.assert_called_once_with(
            source="naver",
            reason="sync_completed",
            tagged_count=3,
        )

    def test_completed_batch_triggers_after_results_are_applied(self) -> None:
        batch = SimpleNamespace(
            id="batch-1",
            status="completed",
            output_file_id="output-1",
            error_file_id=None,
        )
        client = SimpleNamespace(
            batches=SimpleNamespace(retrieve=Mock(return_value=batch))
        )
        conn = object()

        with (
            patch.object(
                batch_tagger.db,
                "fetch_open_tagging_batches",
                return_value=[{"batch_id": "batch-1"}],
            ),
            patch.object(batch_tagger, "_client", return_value=client),
            patch.object(batch_tagger, "_apply_completed_batch", return_value=5),
            patch.object(batch_tagger.db, "update_tagging_batch"),
            patch.object(
                batch_tagger.db,
                "reset_orphan_queued_products",
                return_value=0,
            ),
            patch.object(batch_tagger, "trigger_product_indexer") as trigger,
        ):
            tagged = batch_tagger.poll_batches(conn)

        self.assertEqual(tagged, 5)
        trigger.assert_called_once_with(
            source="naver",
            reason="batch_completed",
            tagged_count=5,
        )


if __name__ == "__main__":
    unittest.main()
