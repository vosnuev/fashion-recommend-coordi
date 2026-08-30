"""상품 임베딩 내부 API 요청 검증."""

from rest_framework import serializers

# product_embedding_job은 source 컬럼을 가진 공용 테이블이다. worker가 source를
# 지정하면 해당 쇼핑몰 작업만 선점해 naver/eleven drain이 서로를 기다리지 않는다.
# 생략하면 기존과 동일하게 전체 source를 대상으로 한다.
PRODUCT_SOURCES = ("naver", "eleven")


class SourceScopedSerializer(serializers.Serializer):
    source = serializers.ChoiceField(
        choices=PRODUCT_SOURCES,
        required=False,
        allow_null=True,
        default=None,
    )


class ProductEmbeddingStatusSerializer(SourceScopedSerializer):
    target_version = serializers.CharField(max_length=200)
    reset_stale = serializers.BooleanField(default=False)
    stale_job_minutes = serializers.IntegerField(
        min_value=1,
        max_value=24 * 60,
        default=30,
    )


class ProductEmbeddingClaimSerializer(SourceScopedSerializer):
    target_version = serializers.CharField(max_length=200)
    limit = serializers.IntegerField(min_value=1, max_value=256)


class ProductEmbeddingActionSerializer(serializers.Serializer):
    generation = serializers.IntegerField(min_value=1)
    attempt_count = serializers.IntegerField(min_value=1)


class ProductEmbeddingImageSerializer(ProductEmbeddingActionSerializer):
    image_s3_key = serializers.CharField(max_length=2048)
    image_checksum = serializers.RegexField(r"^[0-9a-fA-F]{64}$")


class ProductEmbeddingCompleteSerializer(ProductEmbeddingImageSerializer):
    embedding_version = serializers.CharField(max_length=200)


class ProductEmbeddingFailureSerializer(ProductEmbeddingActionSerializer):
    error = serializers.CharField(max_length=4000)
    max_retries = serializers.IntegerField(min_value=0, max_value=20)
    retry_delay_seconds = serializers.IntegerField(
        min_value=1,
        max_value=24 * 60 * 60,
    )
    transient = serializers.BooleanField(default=True)
