"""렌더 작업 한 건의 캐시 조회·이미지 생성·S3 저장 실행 계층."""

from __future__ import annotations

from apps.recommend.models import OutfitRenderJob
from apps.recommend.services import render_artifacts, render_jobs
from apps.recommend.services.mixed_outfit_render import (
    OutfitRenderService,
    RenderInputError,
)
from apps.recommend.services.render_cache import RenderCacheEntry, RenderResultCache


def _entry_values(entry: RenderCacheEntry) -> dict:
    return {
        "output_s3_bucket": entry.output_s3_bucket,
        "output_s3_key": entry.output_s3_key,
        "output_media_type": entry.output_media_type,
        "output_bytes": entry.output_bytes,
        "provider": entry.provider,
        "model": entry.model,
        "prompt_version": entry.prompt_version,
        "reference_count": entry.reference_count,
        "usage": entry.usage,
    }


def execute(
    job: OutfitRenderJob,
    *,
    renderer: OutfitRenderService | None = None,
    cache: RenderResultCache | None = None,
) -> OutfitRenderJob:
    """PROCESSING 작업을 캐시 재사용 또는 실제 생성으로 완료한다."""
    if job.status != OutfitRenderJob.Status.PROCESSING:
        raise RenderInputError("PROCESSING 상태의 이미지 작업만 실행할 수 있습니다.")
    if job.composition.composition_fingerprint.strip().lower() != (
        job.composition_fingerprint
    ):
        raise RenderInputError("작업 접수 후 코디 구성이 변경되었습니다.")

    request = OutfitRenderService().build_request(job.composition)
    service = renderer or OutfitRenderService()
    entry, cache_hit = render_artifacts.get_or_render(
        request,
        renderer=service,
        cache=cache,
    )
    if entry.render_fingerprint != job.render_fingerprint:
        raise RenderInputError("작업의 렌더 계약 지문이 현재 공통 계약과 다릅니다.")
    completed = render_jobs.mark_succeeded(
        job.pk,
        values=_entry_values(entry),
        cache_hit=cache_hit,
    )
    return completed
