from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from apps.catalog.views import (
    ProductEmbeddingClaimView,
    ProductEmbeddingCompleteView,
    ProductEmbeddingStatusView,
)


class ProductEmbeddingApiTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.token_env = patch.dict(
            "os.environ",
            {"PRODUCT_INDEXER_INTERNAL_TOKEN": "catalog-secret"},
        )
        self.token_env.start()
        self.addCleanup(self.token_env.stop)

    def post(self, path: str, payload: dict, *, token: str | None = None):
        headers = {}
        if token is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.factory.post(path, payload, format="json", **headers)

    def test_claim_requires_internal_bearer_token(self) -> None:
        request = self.post(
            "/api/v1/internal/catalog/product-embeddings/claim/",
            {"target_version": "embed-v1", "limit": 2},
        )

        response = ProductEmbeddingClaimView.as_view()(request)

        self.assertEqual(response.status_code, 403)

    @patch("apps.catalog.views.product_embeddings.claim_jobs")
    def test_claim_returns_jobs_from_catalog_service(self, claim_jobs) -> None:
        claim_jobs.return_value = [{"id": 1, "product": {"title": "상품"}}]
        request = self.post(
            "/api/v1/internal/catalog/product-embeddings/claim/",
            {"target_version": "embed-v1", "limit": 2},
            token="catalog-secret",
        )

        response = ProductEmbeddingClaimView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["jobs"][0]["id"], 1)
        claim_jobs.assert_called_once_with(2, "embed-v1", source=None)

    @patch("apps.catalog.views.product_embeddings.claim_jobs")
    def test_claim_scopes_to_source_when_given(self, claim_jobs) -> None:
        """source를 주면 해당 쇼핑몰 작업만 선점한다 (naver/eleven 동시 drain용)."""
        claim_jobs.return_value = []
        request = self.post(
            "/api/v1/internal/catalog/product-embeddings/claim/",
            {"target_version": "embed-v1", "limit": 2, "source": "naver"},
            token="catalog-secret",
        )

        response = ProductEmbeddingClaimView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        claim_jobs.assert_called_once_with(2, "embed-v1", source="naver")

    @patch("apps.catalog.views.product_embeddings.claim_jobs")
    def test_claim_rejects_unknown_source(self, claim_jobs) -> None:
        request = self.post(
            "/api/v1/internal/catalog/product-embeddings/claim/",
            {"target_version": "embed-v1", "limit": 2, "source": "coupang"},
            token="catalog-secret",
        )

        response = ProductEmbeddingClaimView.as_view()(request)

        self.assertEqual(response.status_code, 400)
        claim_jobs.assert_not_called()

    @patch("apps.catalog.views.product_embeddings.get_status")
    def test_status_passes_source_to_service(self, get_status) -> None:
        get_status.return_value = {
            "has_pending_jobs": False,
            "next_available_in_seconds": None,
            "reset_stale_count": 0,
        }
        request = self.post(
            "/api/v1/internal/catalog/product-embeddings/status/",
            {
                "target_version": "embed-v1",
                "reset_stale": True,
                "stale_job_minutes": 30,
                "source": "eleven",
            },
            token="catalog-secret",
        )

        response = ProductEmbeddingStatusView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        get_status.assert_called_once_with(
            "embed-v1",
            reset_stale=True,
            stale_job_minutes=30,
            source="eleven",
        )

    @patch("apps.catalog.views.product_embeddings.mark_success")
    def test_complete_passes_attempt_identity_to_service(self, mark_success) -> None:
        mark_success.return_value = True
        request = self.post(
            "/api/v1/internal/catalog/product-embeddings/7/complete/",
            {
                "generation": 3,
                "attempt_count": 2,
                "embedding_version": "embed-v1",
                "image_s3_key": "products/naver/1/hash.jpg",
                "image_checksum": "a" * 64,
            },
            token="catalog-secret",
        )

        response = ProductEmbeddingCompleteView.as_view()(request, job_id=7)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["accepted"])
        mark_success.assert_called_once_with(
            7,
            generation=3,
            attempt_count=2,
            embedding_version="embed-v1",
            image_s3_key="products/naver/1/hash.jpg",
            image_checksum="a" * 64,
        )
