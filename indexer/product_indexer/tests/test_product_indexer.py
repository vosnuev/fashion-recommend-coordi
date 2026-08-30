from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

# indexer/ 를 import 루트로 잡아 product_indexer·util 패키지를 찾게 한다.
INDEXER_ROOT = Path(__file__).resolve().parents[2]
if str(INDEXER_ROOT) not in sys.path:
    sys.path.insert(0, str(INDEXER_ROOT))

from product_indexer import product_indexer
from product_indexer.product_assets import (
    PreparedImage,
    StoredProductImageUnavailable,
)


def prepared_image() -> PreparedImage:
    return PreparedImage(
        image=Image.new("RGB", (4, 4), "white"),
        checksum="a" * 64,
        s3_key="products/eleven/100/hash.jpg",
    )


class ProductIndexerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.indexer = product_indexer.ProductIndexer.__new__(
            product_indexer.ProductIndexer
        )
        self.indexer.catalog = Mock()
        self.indexer.s3 = Mock()
        self.indexer.http = Mock()

    def test_existing_s3_checkpoint_skips_external_download(self) -> None:
        stored = prepared_image()
        product = {
            "image_url": "https://example.com/product.jpg",
            "image_s3_key": stored.s3_key,
            "image_checksum": stored.checksum,
        }
        job = {"source": "eleven", "external_product_id": "100"}

        with (
            patch.object(
                product_indexer,
                "load_stored_image",
                return_value=stored,
            ) as load,
            patch.object(
                product_indexer,
                "download_and_store_image",
            ) as download,
        ):
            result = self.indexer._load_or_store_image(job, product)

        self.assertIs(result, stored)
        load.assert_called_once()
        download.assert_not_called()
        result.image.close()

    def test_invalid_s3_checkpoint_falls_back_and_saves_checkpoint(self) -> None:
        downloaded = prepared_image()
        product = {
            "image_url": "https://example.com/product.jpg",
            "image_s3_key": "products/eleven/100/old.jpg",
            "image_checksum": "b" * 64,
        }
        job = {
            "id": 1,
            "source": "eleven",
            "external_product_id": "100",
            "generation": 1,
        }

        with (
            patch.object(
                product_indexer,
                "load_stored_image",
                side_effect=StoredProductImageUnavailable("mismatch"),
            ),
            patch.object(
                product_indexer,
                "download_and_store_image",
                return_value=downloaded,
            ) as download,
        ):
            self.indexer.catalog.mark_image_stored.return_value = True
            result = self.indexer._load_or_store_image(job, product)

        self.assertIs(result, downloaded)
        download.assert_called_once()
        self.indexer.catalog.mark_image_stored.assert_called_once_with(
            job,
            image_s3_key=downloaded.s3_key,
            image_checksum=downloaded.checksum,
        )
        self.assertEqual(product["image_s3_key"], downloaded.s3_key)
        result.image.close()

    def test_drain_processes_batches_until_no_pending_job(self) -> None:
        self.indexer.process_once = Mock(side_effect=[32, 5, 0])
        self.indexer.catalog.status.return_value = {
            "next_available_in_seconds": None
        }

        claimed = self.indexer.drain(
            32,
            max_wait_seconds=120,
            max_runtime_minutes=5,
        )

        self.assertEqual(claimed, 37)
        self.assertEqual(self.indexer.process_once.call_count, 3)

    def test_drain_does_not_wait_when_retry_wait_is_disabled(self) -> None:
        self.indexer.process_once = Mock(side_effect=[1, 0])
        self.indexer.catalog.status.return_value = {
            "next_available_in_seconds": 30
        }

        with patch.object(product_indexer.time, "sleep") as sleep:
            claimed = self.indexer.drain(
                32,
                max_wait_seconds=0,
                max_runtime_minutes=5,
            )

        self.assertEqual(claimed, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
