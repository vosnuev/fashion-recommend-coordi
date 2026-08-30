from django.urls import path

from apps.catalog.views import (
    ProductEmbeddingClaimView,
    ProductEmbeddingCompleteView,
    ProductEmbeddingFailureView,
    ProductEmbeddingImageView,
    ProductEmbeddingStatusView,
)

app_name = "catalog"

_PREFIX = "internal/catalog/product-embeddings/"

urlpatterns = [
    path(
        f"{_PREFIX}status/",
        ProductEmbeddingStatusView.as_view(),
        name="product-embedding-status",
    ),
    path(
        f"{_PREFIX}claim/",
        ProductEmbeddingClaimView.as_view(),
        name="product-embedding-claim",
    ),
    path(
        f"{_PREFIX}<int:job_id>/image/",
        ProductEmbeddingImageView.as_view(),
        name="product-embedding-image",
    ),
    path(
        f"{_PREFIX}<int:job_id>/complete/",
        ProductEmbeddingCompleteView.as_view(),
        name="product-embedding-complete",
    ),
    path(
        f"{_PREFIX}<int:job_id>/fail/",
        ProductEmbeddingFailureView.as_view(),
        name="product-embedding-fail",
    ),
]
