"""쇼핑몰(source)별 S3 prefix·Qdrant 컬렉션 분리와 배치 병렬 처리 검증."""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

# indexer/ 를 import 루트로 잡아 product_indexer·util 패키지를 찾게 한다.
INDEXER_ROOT = Path(__file__).resolve().parents[2]
if str(INDEXER_ROOT) not in sys.path:
    sys.path.insert(0, str(INDEXER_ROOT))

from product_indexer import product_config, product_indexer
from product_indexer.product_assets import PreparedImage


def prepared_image(s3_key: str) -> PreparedImage:
    return PreparedImage(
        image=Image.new("RGB", (4, 4), "white"),
        checksum="a" * 64,
        s3_key=s3_key,
    )


class SourceConfigTests(unittest.TestCase):
    def test_default_prefix_matches_legacy_key_layout(self) -> None:
        """기본값은 기존 키 규칙(products/{source})과 동일해야 한다."""
        self.assertEqual(product_config.image_s3_prefix("naver"), "products/naver")
        self.assertEqual(product_config.image_s3_prefix("eleven"), "products/eleven")

    def test_collections_are_separated_per_source(self) -> None:
        naver = product_config.qdrant_collection("naver")
        eleven = product_config.qdrant_collection("eleven")
        self.assertNotEqual(naver, eleven)

    def test_unknown_source_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            product_config.image_s3_prefix("coupang")
        with self.assertRaises(ValueError):
            product_config.qdrant_collection("coupang")


class SourceRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.indexer = product_indexer.ProductIndexer.__new__(
            product_indexer.ProductIndexer
        )
        self.indexer.source = None
        self.indexer.catalog = Mock()
        self.indexer.s3 = Mock()
        self.indexer.http = Mock()
        self.indexer.qdrant = Mock()

    def test_new_image_uses_source_specific_prefix(self) -> None:
        job = {"id": 1, "source": "naver", "external_product_id": "100"}
        product = {"image_url": "https://example.com/p.jpg"}

        with patch.object(
            product_indexer,
            "download_and_store_image",
            return_value=prepared_image("products/naver/100/hash.jpg"),
        ) as download:
            image, downloaded = self.indexer._resolve_image(job, product)

        self.assertTrue(downloaded)
        self.assertEqual(
            download.call_args.kwargs["key_prefix"],
            product_config.image_s3_prefix("naver"),
        )
        image.image.close()

    def test_upsert_routes_points_to_per_source_collections(self) -> None:
        calls: list[tuple[str, int]] = []

        def fake_upsert(client, collection_name, points):
            calls.append((collection_name, len(points)))

        with patch.object(product_indexer, "upsert_points", side_effect=fake_upsert):
            self.indexer._upsert_by_source(
                {"naver": ["n1", "n2"], "eleven": ["e1"]}
            )

        self.assertEqual(
            dict(calls),
            {
                product_config.qdrant_collection("naver"): 2,
                product_config.qdrant_collection("eleven"): 1,
            },
        )

    def test_single_source_batch_skips_thread_pool(self) -> None:
        with patch.object(product_indexer, "upsert_points") as upsert:
            self.indexer._upsert_by_source({"eleven": ["e1"]})

        upsert.assert_called_once()
        self.assertEqual(
            upsert.call_args.args[1],
            product_config.qdrant_collection("eleven"),
        )


class BatchConcurrencyTests(unittest.TestCase):
    """한 배치의 naver·eleven 이미지 I/O가 실제로 겹쳐 실행되는지 확인한다."""

    def setUp(self) -> None:
        self.indexer = product_indexer.ProductIndexer.__new__(
            product_indexer.ProductIndexer
        )
        self.indexer.source = None
        self.indexer.catalog = Mock()
        self.indexer.catalog.mark_image_stored.return_value = True

    def _jobs(self) -> list[dict]:
        return [
            {
                "id": index,
                "source": source,
                "external_product_id": str(index),
                "target_version": product_config.EMBEDDING_VERSION,
                "generation": 1,
                "attempt_count": 1,
                "product": {"image_url": "https://example.com/p.jpg"},
            }
            for index, source in enumerate(("naver", "eleven"), start=1)
        ]

    def test_image_stage_runs_in_parallel(self) -> None:
        barrier = threading.Barrier(2, timeout=5)

        def slow_resolve(job, product):
            # 두 작업이 동시에 도달하지 못하면 BrokenBarrierError로 실패한다.
            barrier.wait()
            time.sleep(0.01)
            return prepared_image(f"products/{job['source']}/x/hash.jpg"), False

        self.indexer._resolve_image = slow_resolve
        prepared = self.indexer._prepare_batch(self._jobs())

        self.assertEqual(len(prepared), 2)
        self.assertEqual(
            sorted(item.job["source"] for item in prepared),
            ["eleven", "naver"],
        )
        for item in prepared:
            item.image.image.close()

    def test_one_failed_source_does_not_block_the_other(self) -> None:
        def resolve(job, product):
            if job["source"] == "naver":
                raise OSError("일시적 네트워크 오류")
            return prepared_image("products/eleven/2/hash.jpg"), False

        self.indexer._resolve_image = resolve
        self.indexer._fail = Mock()

        prepared = self.indexer._prepare_batch(self._jobs())

        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0].job["source"], "eleven")
        self.indexer._fail.assert_called_once()
        self.assertTrue(self.indexer._fail.call_args.kwargs["transient"])
        prepared[0].image.image.close()

    def test_unsupported_source_fails_without_image_work(self) -> None:
        self.indexer._fail = Mock()
        self.indexer._resolve_image = Mock()
        jobs = [
            {
                "id": 9,
                "source": "coupang",
                "external_product_id": "9",
                "target_version": product_config.EMBEDDING_VERSION,
                "generation": 1,
                "attempt_count": 1,
                "product": {"image_url": "https://example.com/p.jpg"},
            }
        ]

        self.assertEqual(self.indexer._prepare_batch(jobs), [])
        self.indexer._resolve_image.assert_not_called()
        self.assertFalse(self.indexer._fail.call_args.kwargs["transient"])


if __name__ == "__main__":
    unittest.main()
