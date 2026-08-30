from __future__ import annotations

import sys
import unittest
from pathlib import Path

COLLECTOR_ROOT = Path(__file__).resolve().parents[2]
if str(COLLECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(COLLECTOR_ROOT))

from util.embedding_jobs import (
    enqueue_new_products,
    find_new_external_ids,
    requeue_existing_products,
)


class FakeCursor:
    def __init__(self, fetch_batches=None):
        self.fetch_batches = list(fetch_batches or [])
        self.executed = []
        self.executed_many = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def executemany(self, sql, params):
        self.executed_many.append((sql, list(params)))

    def fetchall(self):
        return self.fetch_batches.pop(0) if self.fetch_batches else []


class FakeConnection:
    def __init__(self, fetch_batches=None):
        self.cursor_instance = FakeCursor(fetch_batches)

    def cursor(self):
        return self.cursor_instance


class EmbeddingJobTests(unittest.TestCase):
    def test_find_new_external_ids_excludes_existing_and_duplicates(self) -> None:
        conn = FakeConnection(fetch_batches=[[("2",)]])

        result = find_new_external_ids(
            conn,
            "naver",
            ["1", "2", "1", "3"],
        )

        self.assertEqual(result, ["1", "3"])

    def test_enqueue_uses_idempotent_job_insert(self) -> None:
        conn = FakeConnection()

        count = enqueue_new_products(
            conn,
            "eleven",
            ["10", "11", "10"],
            target_version="test-v1",
        )

        self.assertEqual(count, 2)
        insert_sql, values = conn.cursor_instance.executed_many[0]
        self.assertIn(
            "ON CONFLICT (source, external_product_id) DO NOTHING", insert_sql
        )
        self.assertEqual(
            values,
            [
                ("eleven", "10", "test-v1"),
                ("eleven", "11", "test-v1"),
            ],
        )

    def test_requeue_only_updates_products_with_existing_jobs(self) -> None:
        conn = FakeConnection(fetch_batches=[[("10",)]])

        count = requeue_existing_products(
            conn,
            "eleven",
            ["10", "99"],
            target_version="test-v2",
        )

        self.assertEqual(count, 1)
        product_update_sql, product_update_params = conn.cursor_instance.executed[-1]
        self.assertIn("UPDATE eleven_product", product_update_sql)
        self.assertEqual(product_update_params, (["10"],))


if __name__ == "__main__":
    unittest.main()
