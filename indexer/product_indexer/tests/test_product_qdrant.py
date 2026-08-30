from __future__ import annotations

import sys
import unittest
from pathlib import Path

from qdrant_client import QdrantClient

# indexer/ 를 import 루트로 잡아 product_indexer·util 패키지를 찾게 한다.
INDEXER_ROOT = Path(__file__).resolve().parents[2]
if str(INDEXER_ROOT) not in sys.path:
    sys.path.insert(0, str(INDEXER_ROOT))

from product_indexer.product_qdrant import (
    build_point,
    ensure_collection,
    make_client,
    product_point_id,
)


class MakeClientUrlTests(unittest.TestCase):
    """QDRANT_URL을 qdrant-client가 변형하지 않는지 확인한다.

    port 기본값(6333) 때문에 포트 없는 https URL에 :6333이 붙으면
    리버스 프록시 뒤의 Qdrant에 도달할 수 없다 (Errno 101).
    """

    def rest_uri(self, url: str) -> str:
        client = make_client(url, None)
        try:
            return client._client.rest_uri
        finally:
            client.close()

    def test_portless_https_url_is_left_alone(self) -> None:
        url = "https://qdrant.example.com"
        self.assertEqual(self.rest_uri(url), url)

    def test_portless_http_url_is_left_alone(self) -> None:
        url = "http://qdrant.example.com"
        self.assertEqual(self.rest_uri(url), url)

    def test_explicit_port_in_url_is_preserved(self) -> None:
        url = "http://qdrant-host:6333"
        self.assertEqual(self.rest_uri(url), url)


class ProductQdrantTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = QdrantClient(":memory:")

    def test_collection_and_named_vectors(self) -> None:
        ensure_collection(
            self.client,
            collection_name="products_test",
            image_dim=3,
            text_dim=4,
        )

        collection = self.client.get_collection("products_test")
        vectors = collection.config.params.vectors

        self.assertEqual(vectors["image"].size, 3)
        self.assertEqual(vectors["text"].size, 4)

    def test_point_id_is_stable_and_point_can_be_upserted(self) -> None:
        point_id = product_point_id("eleven", "123")
        self.assertEqual(point_id, product_point_id("eleven", "123"))
        self.assertNotEqual(point_id, product_point_id("naver", "123"))

        ensure_collection(
            self.client,
            collection_name="products_test",
            image_dim=3,
            text_dim=4,
        )
        point = build_point(
            source="eleven",
            external_product_id="123",
            image_vector=[0.1, 0.2, 0.3],
            text_vector=[0.1, 0.2, 0.3, 0.4],
            payload={"title": "테스트 상품"},
        )
        self.client.upsert(collection_name="products_test", points=[point])

        self.assertEqual(
            self.client.count(
                collection_name="products_test",
                exact=True,
            ).count,
            1,
        )


if __name__ == "__main__":
    unittest.main()
