"""추천 카드 이미지 생성 작업의 상태 전이와 소유권 조회."""

from __future__ import annotations

import logging
import uuid

import redis
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.chat.models import ChatIdentity
from apps.recommend.models import OutfitComposition, OutfitRenderJob
from apps.recommend.services import render_artifacts

logger = logging.getLogger(__name__)


class RenderQueueUnavailable(RuntimeError):
    """DB 작업은 만들었지만 Redis 큐 적재에 실패함."""


def render_fingerprint(composition_fingerprint: str) -> str:
    return render_artifacts.fingerprint(composition_fingerprint)


def output_key(render_fingerprint_value: str) -> str:
    return render_artifacts.output_key(render_fingerprint_value)


def owned_job(*, identity: ChatIdentity, job_id: uuid.UUID) -> OutfitRenderJob | None:
    return (
        OutfitRenderJob.objects.select_related("composition__result")
        .filter(
            pk=job_id,
            composition__result__identity=identity,
            composition__status=OutfitComposition.Status.VALIDATED,
        )
        .first()
    )


@transaction.atomic
def prepare_job(composition: OutfitComposition) -> tuple[OutfitRenderJob, bool]:
    """카드당 한 작업을 만들고 큐 적재가 필요한지 반환한다."""
    locked = OutfitComposition.objects.select_for_update().get(pk=composition.pk)
    if locked.status != OutfitComposition.Status.VALIDATED:
        raise ValueError("검증 완료된 추천 카드만 이미지로 생성할 수 있습니다.")
    composition_fingerprint = locked.composition_fingerprint.strip().lower()
    current_render_fingerprint = render_fingerprint(composition_fingerprint)
    job = OutfitRenderJob.objects.filter(composition=locked).first()
    if job is None:
        return (
            OutfitRenderJob.objects.create(
                composition=locked,
                composition_fingerprint=composition_fingerprint,
                render_fingerprint=current_render_fingerprint,
            ),
            True,
        )

    same_contract = (
        job.composition_fingerprint == composition_fingerprint
        and job.render_fingerprint == current_render_fingerprint
    )
    if same_contract and job.status in {
        OutfitRenderJob.Status.PROCESSING,
        OutfitRenderJob.Status.SUCCEEDED,
    }:
        return job, False
    if same_contract and job.status == OutfitRenderJob.Status.QUEUED:
        return job, True

    job.status = OutfitRenderJob.Status.QUEUED
    job.composition_fingerprint = composition_fingerprint
    job.render_fingerprint = current_render_fingerprint
    job.output_s3_bucket = ""
    job.output_s3_key = ""
    job.output_media_type = ""
    job.output_bytes = None
    job.provider = ""
    job.model = ""
    job.prompt_version = ""
    job.reference_count = 0
    job.usage = {}
    job.cache_hit = False
    job.attempts = 0
    job.error_code = ""
    job.error_message = ""
    job.enqueued_at = None
    job.started_at = None
    job.finished_at = None
    job.save()
    return job, True


def mark_enqueued(job_id) -> OutfitRenderJob | None:
    now = timezone.now()
    OutfitRenderJob.objects.filter(
        pk=job_id,
        status=OutfitRenderJob.Status.QUEUED,
    ).update(enqueued_at=now, updated_at=now)
    return OutfitRenderJob.objects.filter(pk=job_id).first()


def mark_enqueue_failed(job_id) -> OutfitRenderJob | None:
    now = timezone.now()
    OutfitRenderJob.objects.filter(
        pk=job_id,
        status=OutfitRenderJob.Status.QUEUED,
    ).update(
        status=OutfitRenderJob.Status.FAILED,
        error_code="OUTFIT_RENDER_QUEUE_UNAVAILABLE",
        error_message="이미지 생성 작업을 접수하지 못했습니다. 잠시 후 다시 시도해 주세요.",
        finished_at=now,
        updated_at=now,
    )
    return OutfitRenderJob.objects.filter(pk=job_id).first()


def enqueue_prepared(job: OutfitRenderJob) -> OutfitRenderJob:
    """준비된 작업을 큐에 넣고 queued 이벤트를 best-effort로 기록한다."""
    from apps.recommend.services import render_queue
    from apps.recommend.services.render_events import RenderEventStore

    try:
        render_queue.enqueue(job)
    except redis.RedisError as exc:
        failed = mark_enqueue_failed(job.pk) or job
        raise RenderQueueUnavailable(str(failed.pk)) from exc
    enqueued = mark_enqueued(job.pk) or job
    try:
        RenderEventStore().publish(
            enqueued.pk,
            "queued",
            {"job_id": str(enqueued.pk), "status": OutfitRenderJob.Status.QUEUED},
        )
    except redis.RedisError:
        logger.warning(
            "코디 이미지 queued SSE 이벤트 기록 실패: job=%s",
            enqueued.pk,
            exc_info=True,
        )
    return enqueued


def schedule_result(result_id) -> list[OutfitRenderJob]:
    """확정 추천 결과의 모든 검증 카드를 이미지 큐에 자동 접수한다.

    이미지 큐 장애가 채팅 추천 성공을 되돌리지는 않는다. 실패 작업은 DB에 남아
    카드 render API를 다시 호출하면 같은 작업 ID로 재접수할 수 있다.
    """
    jobs: list[OutfitRenderJob] = []
    compositions = OutfitComposition.objects.filter(
        result_id=result_id,
        status=OutfitComposition.Status.VALIDATED,
    ).order_by("rank")
    for composition in compositions:
        try:
            job, should_enqueue = prepare_job(composition)
            if should_enqueue:
                job = enqueue_prepared(job)
        except RenderQueueUnavailable:
            job = OutfitRenderJob.objects.filter(composition=composition).first()
            if job is not None:
                job.refresh_from_db()
                jobs.append(job)
            logger.warning(
                "추천 결과 이미지 자동 접수 실패: result=%s card=%s",
                result_id,
                composition.pk,
            )
            continue
        except Exception:  # 이미지 접수가 채팅 추천 성공을 되돌리지 않는다.
            logger.exception(
                "추천 결과 이미지 자동 접수 내부 오류: result=%s card=%s",
                result_id,
                composition.pk,
            )
            continue
        jobs.append(job)
    return jobs


def start(job_id) -> OutfitRenderJob | None:
    now = timezone.now()
    updated = OutfitRenderJob.objects.filter(
        pk=job_id,
        status=OutfitRenderJob.Status.QUEUED,
    ).update(
        status=OutfitRenderJob.Status.PROCESSING,
        attempts=F("attempts") + 1,
        error_code="",
        error_message="",
        started_at=now,
        finished_at=None,
        updated_at=now,
    )
    if not updated:
        return None
    return (
        OutfitRenderJob.objects.select_related("composition__result")
        .prefetch_related("composition__items")
        .get(pk=job_id)
    )


def reset_for_retry(job_id) -> bool:
    now = timezone.now()
    return bool(
        OutfitRenderJob.objects.filter(
            pk=job_id,
            status=OutfitRenderJob.Status.PROCESSING,
        ).update(
            status=OutfitRenderJob.Status.QUEUED,
            enqueued_at=now,
            error_code="",
            error_message="",
            finished_at=None,
            updated_at=now,
        )
    )


def mark_succeeded(job_id, *, values: dict, cache_hit: bool) -> OutfitRenderJob:
    now = timezone.now()
    OutfitRenderJob.objects.filter(pk=job_id).update(
        status=OutfitRenderJob.Status.SUCCEEDED,
        output_s3_bucket=values["output_s3_bucket"],
        output_s3_key=values["output_s3_key"],
        output_media_type=values["output_media_type"],
        output_bytes=values["output_bytes"],
        provider=values["provider"],
        model=values["model"],
        prompt_version=values["prompt_version"],
        reference_count=values["reference_count"],
        usage=values.get("usage") or {},
        cache_hit=cache_hit,
        error_code="",
        error_message="",
        finished_at=now,
        updated_at=now,
    )
    return OutfitRenderJob.objects.get(pk=job_id)


def mark_failed(job_id, *, error_code: str, error_message: str) -> OutfitRenderJob:
    now = timezone.now()
    OutfitRenderJob.objects.filter(pk=job_id).update(
        status=OutfitRenderJob.Status.FAILED,
        error_code=error_code[:64],
        error_message=error_message[:500],
        finished_at=now,
        updated_at=now,
    )
    return OutfitRenderJob.objects.get(pk=job_id)
