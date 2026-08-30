import json
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import batch_tagger
import eleven_collector_db as collector
from util.tagging.openai_batch import parse_result_lines


class OpenAIBatchUtilTests(unittest.TestCase):
    def test_parse_result_lines_splits_success_and_failure(self):
        success = json.dumps(
            {
                "custom_id": "7",
                "response": {
                    "status_code": 200,
                    "body": {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        {"season": ["여름"]},
                                        ensure_ascii=False,
                                    )
                                }
                            }
                        ]
                    },
                },
            },
            ensure_ascii=False,
        )
        failed = json.dumps({"custom_id": "8", "error": {"code": "x"}})
        error_file_line = json.dumps({"custom_id": "9"})

        results, failed_ids = parse_result_lines(
            [success, failed],
            [error_file_line],
        )

        self.assertEqual(results, {7: {"season": ["여름"]}})
        self.assertEqual(failed_ids, [8, 9])


class ElevenBatchTaggerTests(unittest.TestCase):
    def _product(self):
        return {
            "id": 11,
            "title": "화이트 반팔 티셔츠",
            "image_url": "https://example.com/product.jpg",
            "eleven_category1": "여성의류",
            "eleven_category2": "티셔츠",
            "eleven_category3": "",
            "eleven_category4": "",
            "category_large": "상의",
            "category_small": "티셔츠",
        }

    def test_request_line_uses_shared_openai_batch_format(self):
        request = json.loads(batch_tagger._request_line(self._product()))

        self.assertEqual(request["custom_id"], "11")
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["url"], "/v1/chat/completions")
        self.assertEqual(request["body"]["model"], batch_tagger.OPENAI_MODEL)

    def test_submit_pending_tracks_batch_and_queues_products(self):
        client = SimpleNamespace(
            files=SimpleNamespace(
                create=Mock(return_value=SimpleNamespace(id="file-1"))
            ),
            batches=SimpleNamespace(
                create=Mock(return_value=SimpleNamespace(id="batch-1"))
            ),
        )
        conn = object()

        with (
            patch.object(batch_tagger, "_client", return_value=client),
            patch.object(
                batch_tagger.db,
                "fetch_pending_products",
                side_effect=[[self._product()], []],
            ),
            patch.object(batch_tagger.db, "insert_tagging_batch") as insert,
            patch.object(
                batch_tagger.db, "set_products_tagging_status"
            ) as set_status,
        ):
            submitted = batch_tagger.submit_pending(conn)

        self.assertEqual(submitted, 1)
        insert.assert_called_once()
        set_status.assert_called_once_with(conn, [11], "queued")

    def test_collect_job_uses_batch_submit_in_batch_mode(self):
        fake_batch_module = ModuleType("batch_tagger")
        fake_batch_module.submit_pending = Mock()
        conn = object()
        entries = [object()]

        with (
            patch.object(collector, "TAGGING_MODE", "batch"),
            patch.object(collector, "collect") as collect,
            patch.object(collector, "trigger_product_indexer") as trigger,
            patch.dict(sys.modules, {"batch_tagger": fake_batch_module}),
        ):
            collector.run_collect_job(
                conn,
                entries,
                limit_per_keyword=3,
                skip_llm=False,
                max_total_items=1000,
            )

        collect.assert_called_once_with(
            conn,
            entries,
            3,
            skip_llm=True,
            dry_run=False,
            max_total_items=1000,
        )
        fake_batch_module.submit_pending.assert_called_once_with(conn)
        trigger.assert_not_called()

    def test_sync_collect_triggers_remote_indexer_after_save(self):
        conn = object()
        entries = [object()]

        with (
            patch.object(collector, "TAGGING_MODE", "sync"),
            patch.object(collector, "collect", return_value=2),
            patch.object(collector, "trigger_product_indexer") as trigger,
        ):
            collector.run_collect_job(
                conn,
                entries,
                limit_per_keyword=3,
                skip_llm=False,
            )

        trigger.assert_called_once_with(
            source="eleven",
            reason="sync_completed",
            tagged_count=2,
        )

    def test_completed_batch_triggers_remote_indexer(self):
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
            patch.object(batch_tagger, "_apply_completed_batch", return_value=4),
            patch.object(batch_tagger.db, "update_tagging_batch"),
            patch.object(
                batch_tagger.db,
                "reset_orphan_queued_products",
                return_value=0,
            ),
            patch.object(batch_tagger, "trigger_product_indexer") as trigger,
        ):
            tagged = batch_tagger.poll_batches(conn)

        self.assertEqual(tagged, 4)
        trigger.assert_called_once_with(
            source="eleven",
            reason="batch_completed",
            tagged_count=4,
        )

    def test_cli_exposes_batch_jobs(self):
        self.assertEqual(
            collector.parse_args(["--job", "batch-submit"]).job,
            "batch-submit",
        )
        self.assertEqual(
            collector.parse_args(["--job", "batch-poll"]).job,
            "batch-poll",
        )


class ElevenDailyCollectionLimitTests(unittest.TestCase):
    def test_collect_caps_keyword_request_at_fifty(self):
        conn = object()
        entries = [
            SimpleNamespace(
                keyword="keyword-1",
                category_large="상의",
                category_small="티셔츠",
            )
        ]

        with (
            patch.object(
                collector,
                "fetch_keyword_products",
                return_value=[],
            ) as fetch,
            patch.object(collector.db, "load_category_paths", return_value={}),
            patch.object(collector.db, "upsert_products", return_value=0),
        ):
            collector.collect(
                conn,
                entries,
                limit_per_keyword=300,
                skip_llm=True,
                max_total_items=1000,
            )

        fetch.assert_called_once_with(conn, "keyword-1", 50)

    def test_collect_stops_at_total_unique_product_limit(self):
        conn = object()
        entries = [
            SimpleNamespace(
                keyword=f"keyword-{index}",
                category_large="상의",
                category_small="티셔츠",
            )
            for index in range(1, 4)
        ]
        first_items = [
            {"ProductCode": str(index), "ProductName": f"상품 {index}"}
            for index in range(1, 4)
        ]
        second_items = [
            {"ProductCode": str(index), "ProductName": f"상품 {index}"}
            for index in range(4, 6)
        ]

        with (
            patch.object(
                collector,
                "fetch_keyword_products",
                side_effect=[first_items, second_items],
            ) as fetch,
            patch.object(collector.db, "load_category_paths", return_value={}),
            patch.object(
                collector.db,
                "find_new_product_ids",
                side_effect=lambda _conn, external_ids: external_ids,
            ),
            patch.object(
                collector.db,
                "upsert_products",
                side_effect=lambda _conn, rows: len(rows),
            ) as upsert,
            patch.object(collector, "extract_attributes", return_value={}),
            patch.object(collector, "extract_category_disp_no", return_value=None),
            patch.object(collector, "extract_category_path", return_value=[]),
            patch.object(
                collector,
                "map_eleven_category",
                return_value=(None, None, None),
            ),
            patch.object(collector, "build_row", return_value=("row",)),
        ):
            saved = collector.collect(
                conn,
                entries,
                limit_per_keyword=300,
                skip_llm=True,
                max_total_items=5,
            )

        self.assertEqual(saved, 5)
        self.assertEqual(
            fetch.call_args_list,
            [
                unittest.mock.call(conn, "keyword-1", 5),
                unittest.mock.call(conn, "keyword-2", 2),
            ],
        )
        self.assertEqual([len(call.args[1]) for call in upsert.call_args_list], [3, 2])

    def test_existing_and_same_run_duplicates_do_not_count(self):
        conn = object()
        entries = [
            SimpleNamespace(
                keyword=f"keyword-{index}",
                category_large="상의",
                category_small="티셔츠",
            )
            for index in range(1, 4)
        ]
        existing_and_new = [
            {"ProductCode": "existing", "ProductName": "기존 상품"},
            {"ProductCode": "new-1", "ProductName": "신규 상품 1"},
        ]
        repeated = [
            {"ProductCode": "new-1", "ProductName": "신규 상품 1"},
        ]
        final_new = [
            {"ProductCode": "new-2", "ProductName": "신규 상품 2"},
        ]

        with (
            patch.object(
                collector,
                "fetch_keyword_products",
                side_effect=[existing_and_new, repeated, final_new],
            ) as fetch,
            patch.object(collector.db, "load_category_paths", return_value={}),
            patch.object(
                collector.db,
                "find_new_product_ids",
                side_effect=[["new-1"], [], ["new-2"]],
            ),
            patch.object(
                collector.db,
                "upsert_products",
                side_effect=lambda _conn, rows: len(rows),
            ) as upsert,
            patch.object(collector, "extract_attributes", return_value={}),
            patch.object(collector, "extract_category_disp_no", return_value=None),
            patch.object(collector, "extract_category_path", return_value=[]),
            patch.object(
                collector,
                "map_eleven_category",
                return_value=(None, None, None),
            ),
            patch.object(collector, "build_row", return_value=("row",)) as build,
        ):
            saved = collector.collect(
                conn,
                entries,
                limit_per_keyword=50,
                skip_llm=True,
                max_total_items=2,
            )

        self.assertEqual(saved, 2)
        self.assertEqual(build.call_count, 2)
        self.assertEqual(
            [call.args[2] for call in fetch.call_args_list],
            [2, 1, 1],
        )
        self.assertEqual([len(call.args[1]) for call in upsert.call_args_list], [1, 0, 1])


if __name__ == "__main__":
    unittest.main()
