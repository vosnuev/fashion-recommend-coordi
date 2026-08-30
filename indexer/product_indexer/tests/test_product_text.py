from __future__ import annotations

import sys
import unittest
from pathlib import Path

# indexer/ 를 import 루트로 잡아 product_indexer·util 패키지를 찾게 한다.
INDEXER_ROOT = Path(__file__).resolve().parents[2]
if str(INDEXER_ROOT) not in sys.path:
    sys.path.insert(0, str(INDEXER_ROOT))

from product_indexer.product_text import (
    build_product_payload,
    serialize_product_text,
)


class ProductTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.product = {
            "id": 17,
            "source": "eleven",
            "external_product_id": "10001",
            "title": "오버핏 싱글 재킷",
            "link": "https://example.com/product/10001",
            "image_url": "https://example.com/product/10001.jpg",
            "price": 59000,
            "mall_name": "테스트몰",
            "brand": "테스트브랜드",
            "category_large": "아우터",
            "category_small": "재킷",
            "season": ["봄", "가을"],
            "style": ["미니멀"],
            "color": ["베이지"],
            "pattern": ["무지"],
            "fit": "오버핏",
            "material": ["코튼"],
            "sleeve": "긴팔",
            "length": "기본",
            "usage": ["출근"],
            "layer_role": "아우터",
            "layer_order": 3,
            "tagging_status": "tagged",
        }

    def test_serialization_uses_stable_tag_first_order(self) -> None:
        text = serialize_product_text(self.product)

        self.assertTrue(text.startswith("카테고리: 아우터 > 재킷"))
        self.assertIn("스타일: 미니멀", text)
        self.assertIn("상품명: 오버핏 싱글 재킷", text)
        self.assertLess(text.index("카테고리:"), text.index("상품명:"))

    def test_serialization_works_before_llm_tagging(self) -> None:
        product = {
            **self.product,
            "season": [],
            "style": [],
            "usage": [],
            "tagging_status": "pending",
        }

        text = serialize_product_text(product)

        self.assertIn("카테고리: 아우터 > 재킷", text)
        self.assertIn("상품명: 오버핏 싱글 재킷", text)
        self.assertNotIn("계절:", text)

    def test_payload_keeps_source_and_embedding_version(self) -> None:
        payload = build_product_payload(
            self.product,
            embedding_version="test-v1",
            image_s3_bucket="product-bucket",
            image_s3_key="products/eleven/10001/hash.jpg",
        )

        self.assertEqual(payload["source"], "eleven")
        self.assertEqual(payload["external_product_id"], "10001")
        self.assertEqual(payload["embedding_version"], "test-v1")
        self.assertEqual(payload["image_s3_bucket"], "product-bucket")


if __name__ == "__main__":
    unittest.main()
