"""GPU product-indexer가 호출하는 내부 상품 임베딩 API."""

from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.permissions import HasProductIndexerToken
from apps.catalog.serializers import (
    ProductEmbeddingClaimSerializer,
    ProductEmbeddingCompleteSerializer,
    ProductEmbeddingFailureSerializer,
    ProductEmbeddingImageSerializer,
    ProductEmbeddingStatusSerializer,
)
from apps.catalog.services import product_embeddings


class ProductEmbeddingBaseView(APIView):
    authentication_classes = ()
    permission_classes = (HasProductIndexerToken,)


class ProductEmbeddingStatusView(ProductEmbeddingBaseView):
    def post(self, request):
        serializer = ProductEmbeddingStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = product_embeddings.get_status(
            data["target_version"],
            reset_stale=data["reset_stale"],
            stale_job_minutes=data["stale_job_minutes"],
            source=data.get("source"),
        )
        return Response(result)


class ProductEmbeddingClaimView(ProductEmbeddingBaseView):
    def post(self, request):
        serializer = ProductEmbeddingClaimSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        jobs = product_embeddings.claim_jobs(
            data["limit"],
            data["target_version"],
            source=data.get("source"),
        )
        return Response({"jobs": jobs})


class ProductEmbeddingImageView(ProductEmbeddingBaseView):
    def post(self, request, job_id: int):
        serializer = ProductEmbeddingImageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        accepted = product_embeddings.mark_image_stored(
            job_id,
            **serializer.validated_data,
        )
        return Response({"accepted": accepted})


class ProductEmbeddingCompleteView(ProductEmbeddingBaseView):
    def post(self, request, job_id: int):
        serializer = ProductEmbeddingCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        accepted = product_embeddings.mark_success(
            job_id,
            **serializer.validated_data,
        )
        return Response({"accepted": accepted})


class ProductEmbeddingFailureView(ProductEmbeddingBaseView):
    def post(self, request, job_id: int):
        serializer = ProductEmbeddingFailureSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        next_status = product_embeddings.mark_failure(
            job_id,
            **serializer.validated_data,
        )
        return Response(
            {
                "accepted": next_status is not None,
                "status": next_status,
            }
        )
