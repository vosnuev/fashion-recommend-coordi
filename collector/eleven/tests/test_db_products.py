import unittest
from unittest.mock import MagicMock, patch

import db


class ElevenProductDbTests(unittest.TestCase):
    def test_insert_skips_conflicts_and_counts_only_returned_ids(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value

        with (
            patch.object(
                db,
                "execute_values",
                return_value=[("new-product",)],
            ) as execute_values,
            patch.object(db, "enqueue_new_products") as enqueue,
        ):
            inserted = db.upsert_products(
                conn,
                [
                    ("new-product",),
                    ("existing-product",),
                ],
            )

        self.assertEqual(inserted, 1)
        sql = execute_values.call_args.args[1]
        self.assertIn("ON CONFLICT (eleven_product_id) DO NOTHING", sql)
        self.assertTrue(execute_values.call_args.kwargs["fetch"])
        enqueue.assert_called_once_with(conn, "eleven", ["new-product"])
        conn.commit.assert_called_once()
        cursor.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
