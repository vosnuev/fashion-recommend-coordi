"""상품 임베딩 작업의 DB 접근을 소유하는 catalog 서비스."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db import transaction
from django.db.models import Min, Q, QuerySet, TextField, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.catalog.models import ElevenProduct, NaverProduct, ProductEmbeddingJob

_PRODUCT_FIELDS = (
    "id",
    "title",
    "link",
    "image_url",
    "mall_name",
    "image_s3_key",
    "image_checksum",
    "category_large",
    "category_small",
    "season",
    "style",
    "color",
    "pattern",
    "fit",
    "material",
    "sleeve",
    "length",
    "usage",
    "layer_role",
    "layer_order",
    "tagging_status",
)

_SOURCE_CONFIG = {
    "naver": {
        "model": NaverProduct,
        "external_id": "naver_product_id",
    },
    "eleven": {
        "model": ElevenProduct,
        "external_id": "eleven_product_id",
    },
}


def _source_config(source: str) -> dict[str, Any]:
    try:
        return _SOURCE_CONFIG[source]
    except KeyError as exc:
        raise ValueError(f"지원하지 않는 상품 source: {source}") from exc


def _tagged_jobs(queryset: QuerySet, source: str | None = None) -> QuerySet:
    """태깅 완료 상품의 작업만 남긴다. source를 주면 해당 쇼핑몰로 좁힌다.

    source별로 좁히면 naver drain이 eleven 백로그를 선점하지 않아 두 쇼핑몰의
    임베딩이 서로를 기다리지 않고 동시에 진행된다. 테이블은 그대로 공용
    product_embedding_job 하나를 쓴다.
    """
    conditions = []
    if source in (None, "naver"):
        naver_ids = NaverProduct.objects.filter(tagging_status="tagged").values(
            "naver_product_id"
        )
        conditions.append(Q(source="naver", external_product_id__in=naver_ids))
    if source in (None, "eleven"):
        eleven_ids = ElevenProduct.objects.filter(tagging_status="tagged").values(
            "eleven_product_id"
        )
        conditions.append(Q(source="eleven", external_product_id__in=eleven_ids))
    if not conditions:
        raise ValueError(f"지원하지 않는 상품 source: {source}")

    combined = conditions[0]
    for condition in conditions[1:]:
        combined |= condition
    return queryset.filter(combined)


def _product_for_job(job: ProductEmbeddingJob):
    config = _source_config(job.source)
    return config["model"].objects.filter(
        **{config["external_id"]: job.external_product_id}
    ).first()


def _serialize_product(job: ProductEmbeddingJob, product) -> dict[str, Any]:
    data = {field: getattr(product, field) for field in _PRODUCT_FIELDS}
    data.update(
        {
            "source": job.source,
            "external_product_id": job.external_product_id,
            "brand": getattr(product, "brand", None),
            "price": (
                product.lprice
                if job.source == "naver"
                else (
                    product.sale_price
                    if product.sale_price is not None
                    else product.product_price
                )
            ),
        }
    )
    return data


def _serialize_job(job: ProductEmbeddingJob, product) -> dict[str, Any]:
    return {
        "id": job.pk,
        "source": job.source,
        "external_product_id": job.external_product_id,
        "target_version": job.target_version,
        "generation": job.generation,
        "attempt_count": job.attempt_count,
        "product": _serialize_product(job, product),
    }


def reset_stale_jobs(stale_job_minutes: int, source: str | None = None) -> int:
    """비정상 종료 후 오래 processing에 남은 작업을 pending으로 복구한다.

    source를 주면 다른 쇼핑몰 worker가 정상 처리 중인 작업을 건드리지 않는다.
    """
    now = timezone.now()
    queryset = ProductEmbeddingJob.objects.filter(
        status="processing",
        claimed_at__lt=now - timedelta(minutes=stale_job_minutes),
    )
    if source is not None:
        _source_config(source)
        queryset = queryset.filter(source=source)
    return queryset.update(
        status="pending",
        claimed_at=None,
        available_at=now,
        updated_at=now,
        last_error=Coalesce(
            "last_error",
            Value(
                "worker 종료로 인해 stale 작업을 재개함",
                output_field=TextField(),
            ),
        ),
    )


def get_status(
    target_version: str,
    *,
    reset_stale: bool,
    stale_job_minutes: int,
    source: str | None = None,
) -> dict[str, Any]:
    stale_count = (
        reset_stale_jobs(stale_job_minutes, source) if reset_stale else 0
    )
    queryset = ProductEmbeddingJob.objects.filter(
        status="pending",
        target_version=target_version,
    )
    if source is not None:
        queryset = queryset.filter(source=source)
    pending = _tagged_jobs(queryset, source)
    next_available_at = pending.aggregate(value=Min("available_at"))["value"]
    next_delay = None
    if next_available_at is not None:
        next_delay = max(
            0.0,
            (next_available_at - timezone.now()).total_seconds(),
        )
    return {
        "has_pending_jobs": next_available_at is not None,
        "next_available_in_seconds": next_delay,
        "reset_stale_count": stale_count,
    }


@transaction.atomic
def claim_jobs(
    limit: int,
    target_version: str,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """태깅 완료된 pending 작업을 원자적으로 선점하고 상품 데이터를 반환한다.

    source를 주면 해당 쇼핑몰 작업만 선점한다. skip_locked 덕분에 naver worker와
    eleven worker가 같은 테이블을 동시에 폴링해도 서로 블로킹되지 않는다.
    """
    now = timezone.now()
    queryset = ProductEmbeddingJob.objects.select_for_update(
        skip_locked=True
    ).filter(
        status="pending",
        target_version=target_version,
        available_at__lte=now,
    )
    if source is not None:
        queryset = queryset.filter(source=source)
    candidates = _tagged_jobs(queryset, source).order_by("id")[:limit]

    claimed: list[dict[str, Any]] = []
    for job in candidates:
        product = _product_for_job(job)
        if product is None:
            continue
        job.status = "processing"
        job.attempt_count += 1
        job.claimed_at = now
        job.updated_at = now
        job.save(
            update_fields=[
                "status",
                "attempt_count",
                "claimed_at",
                "updated_at",
            ]
        )
        if product.embedding_status == "pending":
            product.embedding_status = "processing"
            product.updated_at = now
            product.save(update_fields=["embedding_status", "updated_at"])
        claimed.append(_serialize_job(job, product))
    return claimed


def _locked_current_job(
    job_id: int,
    *,
    generation: int,
    attempt_count: int,
) -> ProductEmbeddingJob | None:
    return (
        ProductEmbeddingJob.objects.select_for_update()
        .filter(
            pk=job_id,
            generation=generation,
            attempt_count=attempt_count,
            status="processing",
        )
        .first()
    )


@transaction.atomic
def mark_image_stored(
    job_id: int,
    *,
    generation: int,
    attempt_count: int,
    image_s3_key: str,
    image_checksum: str,
) -> bool:
    job = _locked_current_job(
        job_id,
        generation=generation,
        attempt_count=attempt_count,
    )
    if job is None:
        return False
    product = _product_for_job(job)
    if product is None:
        return False
    product.image_s3_key = image_s3_key
    product.image_checksum = image_checksum.lower()
    product.updated_at = timezone.now()
    product.save(
        update_fields=["image_s3_key", "image_checksum", "updated_at"]
    )
    return True


@transaction.atomic
def mark_success(
    job_id: int,
    *,
    generation: int,
    attempt_count: int,
    embedding_version: str,
    image_s3_key: str,
    image_checksum: str,
) -> bool:
    job = _locked_current_job(
        job_id,
        generation=generation,
        attempt_count=attempt_count,
    )
    if job is None:
        return False
    if job.target_version != embedding_version:
        return False
    product = _product_for_job(job)

    now = timezone.now()
    job.status = "completed"
    job.last_error = None
    job.completed_at = now
    job.claimed_at = None
    job.updated_at = now
    job.save(
        update_fields=[
            "status",
            "last_error",
            "completed_at",
            "claimed_at",
            "updated_at",
        ]
    )

    if product is not None:
        product.embedding_status = "completed"
        product.embedding_version = embedding_version
        product.embedding_retry_count = max(0, attempt_count - 1)
        product.embedding_error = None
        product.image_s3_key = image_s3_key
        product.image_checksum = image_checksum.lower()
        product.image_embedded_at = now
        product.text_embedded_at = now
        product.embedded_at = now
        product.updated_at = now
        product.save(
            update_fields=[
                "embedding_status",
                "embedding_version",
                "embedding_retry_count",
                "embedding_error",
                "image_s3_key",
                "image_checksum",
                "image_embedded_at",
                "text_embedded_at",
                "embedded_at",
                "updated_at",
            ]
        )
    return True


@transaction.atomic
def mark_failure(
    job_id: int,
    *,
    generation: int,
    attempt_count: int,
    error: str,
    max_retries: int,
    retry_delay_seconds: int,
    transient: bool,
) -> str | None:
    job = _locked_current_job(
        job_id,
        generation=generation,
        attempt_count=attempt_count,
    )
    if job is None:
        return None
    product = _product_for_job(job)

    now = timezone.now()
    should_retry = transient and attempt_count < 1 + max_retries
    next_status = "pending" if should_retry else "failed"
    safe_error = error[:4000]
    job.status = next_status
    job.last_error = safe_error
    if should_retry:
        job.available_at = now + timedelta(seconds=retry_delay_seconds)
    job.claimed_at = None
    job.completed_at = None
    job.updated_at = now
    job.save(
        update_fields=[
            "status",
            "last_error",
            "available_at",
            "claimed_at",
            "completed_at",
            "updated_at",
        ]
    )

    if product is not None:
        product.embedding_status = next_status
        product.embedding_retry_count = max(0, attempt_count - 1)
        product.embedding_error = safe_error
        product.updated_at = now
        product.save(
            update_fields=[
                "embedding_status",
                "embedding_retry_count",
                "embedding_error",
                "updated_at",
            ]
        )
    return next_status
