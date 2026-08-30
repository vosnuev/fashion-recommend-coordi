from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

# indexer/ 를 import 루트로 잡아 product_indexer·util 패키지를 찾게 한다.
INDEXER_ROOT = Path(__file__).resolve().parents[2]
if str(INDEXER_ROOT) not in sys.path:
    sys.path.insert(0, str(INDEXER_ROOT))

from product_indexer.product_catalog_api import (
    ProductCatalogApiClient,
    ProductCatalogApiError,
)


class ProductCatalogApiClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = Mock()
        self.session.headers = {}
        self.client = ProductCatalogApiClient(
            "https://catalog.example/api/v1/internal/catalog/product-embeddings",
            "secret-token",
            12,
            session=self.session,
        )

    def response(self, payload):
        response = Mock()
        response.json.return_value = payload
        self.session.post.return_value = response
        return response

    def test_claim_sends_version_and_limit_with_bearer_token(self) -> None:
        self.response(
            {
                "jobs": [
                    {
                        "id": 1,
                        "source": "naver",
                        "external_product_id": "100",
                        "target_version": "embed-v1",
                        "generation": 2,
                        "attempt_count": 1,
                        "product": {"title": "상품"},
                    }
                ]
            }
        )

        jobs = self.client.claim_jobs(32, "embed-v1")

        self.assertEqual(jobs[0]["id"], 1)
        self.assertEqual(
            self.session.headers["Authorization"],
            "Bearer secret-token",
        )
        self.session.post.assert_called_once_with(
            "https://catalog.example/api/v1/internal/catalog/"
            "product-embeddings/claim/",
            json={"limit": 32, "target_version": "embed-v1"},
            timeout=12,
        )

    def test_completion_includes_generation_and_attempt(self) -> None:
        self.response({"accepted": True})
        job = {"id": 7, "generation": 3, "attempt_count": 2}

        accepted = self.client.mark_success(
            job,
            embedding_version="embed-v1",
            image_s3_key="products/naver/1/hash.jpg",
            image_checksum="a" * 64,
        )

        self.assertTrue(accepted)
        payload = self.session.post.call_args.kwargs["json"]
        self.assertEqual(payload["generation"], 3)
        self.assertEqual(payload["attempt_count"], 2)

    def test_claim_scopes_to_source_when_given(self) -> None:
        self.response({"jobs": []})

        self.client.claim_jobs(8, "embed-v1", source="naver")

        payload = self.session.post.call_args.kwargs["json"]
        self.assertEqual(payload["source"], "naver")

    def test_claim_omits_source_when_not_given(self) -> None:
        self.response({"jobs": []})

        self.client.claim_jobs(8, "embed-v1")

        self.assertNotIn("source", self.session.post.call_args.kwargs["json"])

    def test_status_scopes_to_source_when_given(self) -> None:
        self.response(
            {
                "has_pending_jobs": False,
                "next_available_in_seconds": None,
                "reset_stale_count": 0,
            }
        )

        self.client.status("embed-v1", source="eleven")

        payload = self.session.post.call_args.kwargs["json"]
        self.assertEqual(payload["source"], "eleven")

    def test_invalid_claim_response_is_rejected(self) -> None:
        self.response({"jobs": "not-an-array"})

        with self.assertRaises(ProductCatalogApiError):
            self.client.claim_jobs(2, "embed-v1")

    def test_stale_callback_is_reported_as_not_accepted(self) -> None:
        self.response({"accepted": False})
        job = {"id": 7, "generation": 1, "attempt_count": 1}

        accepted = self.client.mark_image_stored(
            job,
            image_s3_key="products/eleven/1/hash.jpg",
            image_checksum="b" * 64,
        )

        self.assertFalse(accepted)


if __name__ == "__main__":
    unittest.main()
