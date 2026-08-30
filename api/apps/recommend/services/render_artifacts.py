"""채팅 추천과 오늘의 룩이 함께 쓰는 렌더 결과 생성·저장·캐시 계층."""

from __future__ import annotations

import hashlib

from django.conf import settings

from apps.recommend.models import OutfitRenderJob
from apps.recommend.services import storage
from apps.recommend.services.mixed_outfit_render import (
    PROMPT_VERSION,
    OutfitRenderRequest,
    OutfitRenderService,
    RenderInputError,
    active_model,
)
from apps.recommend.services.render_cache import RenderCacheEntry, RenderResultCache


def fingerprint(composition_fingerprint: str, subject_presentation: str = "") -> str:
    contract = "|".join(
        (
            composition_fingerprint.strip().lower(),
            subject_presentation.strip().lower(),
            # 백엔드를 바꾸면 모델 id가 바뀌고 지문도 갈린다 — 예전 모델로 만든
            # 이미지를 새 모델 결과로 재사용하지 않기 위해서다.
            active_model(),
            PROMPT_VERSION,
            settings.OUTFIT_RENDER_ASPECT_RATIO,
            settings.OUTFIT_RENDER_RESOLUTION,
        )
    )
    return hashlib.sha256(contract.encode("utf-8")).hexdigest()


def output_key(render_fingerprint: str) -> str:
    prefix = settings.OUTFIT_RENDER_RESULT_PREFIX.strip("/")
    leaf = f"{render_fingerprint[:2]}/{render_fingerprint}/render"
    return f"{prefix}/{leaf}" if prefix else leaf


def _job_entry(job: OutfitRenderJob) -> RenderCacheEntry:
    return RenderCacheEntry(
        render_fingerprint=job.render_fingerprint,
        output_s3_bucket=job.output_s3_bucket,
        output_s3_key=job.output_s3_key,
        output_media_type=job.output_media_type,
        output_bytes=job.output_bytes or 0,
        provider=job.provider,
        model=job.model,
        prompt_version=job.prompt_version,
        reference_count=job.reference_count,
        usage=job.usage or {},
    )


def _usable(entry: RenderCacheEntry) -> bool:
    return bool(
        entry.output_s3_bucket
        and entry.output_s3_key
        and entry.output_media_type
        and storage.exists_for(entry.output_s3_bucket, entry.output_s3_key)
    )


def find_cached(
    render_fingerprint: str,
    *,
    cache: RenderResultCache | None = None,
) -> RenderCacheEntry | None:
    """Redis가 비어도 채팅 작업 DB를 durable cache로 재사용한다."""
    result_cache = cache or RenderResultCache()
    cached = result_cache.get(render_fingerprint)
    if cached is not None and _usable(cached):
        return cached

    durable = (
        OutfitRenderJob.objects.filter(
            render_fingerprint=render_fingerprint,
            status=OutfitRenderJob.Status.SUCCEEDED,
        )
        .order_by("-finished_at")
        .first()
    )
    if durable is None:
        bucket = settings.OUTFIT_RENDER_RESULT_BUCKET
        key = output_key(render_fingerprint)
        metadata = storage.metadata_for(bucket, key) if bucket else None
        if metadata is None:
            return None
        entry = RenderCacheEntry(
            render_fingerprint=render_fingerprint,
            output_s3_bucket=bucket,
            output_s3_key=key,
            output_media_type=metadata["content_type"],
            output_bytes=metadata["content_length"],
            provider=settings.OUTFIT_RENDER_BACKEND,
            model=active_model(),
            prompt_version=PROMPT_VERSION,
            reference_count=0,
            usage={},
        )
    else:
        entry = _job_entry(durable)
        if not _usable(entry):
            return None
    result_cache.set(entry)
    return entry


def get_or_render(
    request: OutfitRenderRequest,
    *,
    renderer: OutfitRenderService | None = None,
    cache: RenderResultCache | None = None,
) -> tuple[RenderCacheEntry, bool]:
    """공통 계약으로 캐시를 조회하고, 없을 때만 생성하여 비공개 S3에 저장한다."""
    render_fingerprint = fingerprint(
        request.composition_fingerprint,
        request.subject_presentation,
    )
    result_cache = cache or RenderResultCache()
    if cached := find_cached(render_fingerprint, cache=result_cache):
        return cached, True

    bucket = settings.OUTFIT_RENDER_RESULT_BUCKET
    if not bucket:
        raise RenderInputError("OUTFIT_RENDER_RESULT_BUCKET이 설정되지 않았습니다.")
    rendered = (renderer or OutfitRenderService()).render_request(request)
    key = output_key(render_fingerprint)
    storage.put_bytes_for(bucket, key, rendered.content, rendered.media_type)
    entry = RenderCacheEntry(
        render_fingerprint=render_fingerprint,
        output_s3_bucket=bucket,
        output_s3_key=key,
        output_media_type=rendered.media_type,
        output_bytes=len(rendered.content),
        provider=rendered.provider,
        model=rendered.model,
        prompt_version=rendered.prompt_version,
        reference_count=rendered.reference_count,
        usage=rendered.usage,
    )
    result_cache.set(entry)
    return entry, False
