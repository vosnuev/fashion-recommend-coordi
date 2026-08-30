"""Redis 큐의 추천 코디 이미지 생성 작업을 처리한다."""

from __future__ import annotations

import json
import logging
import signal
import time
from datetime import timedelta
from typing import Any

import redis
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.recommend.models import OutfitRenderJob, VirtualTryOnJob
from apps.recommend.services import (
    render_execution,
    render_jobs,
    render_queue,
    virtual_try_on_jobs,
)
from apps.recommend.services.mixed_outfit_render import (
    OutfitRenderError,
    RenderDisabled,
    RenderInputError,
)
from apps.recommend.services.render_events import RenderEventStore

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "코디 이미지 생성 큐를 소비해 Qwen 이미지 렌더링을 수행한다."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help="작업 1건을 처리하거나 큐 대기가 끝나면 종료한다.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        self._running = True
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)
        self._recover_interrupted()
        self._recover_orphaned_pending()
        logger.info(
            "코디 이미지 워커 시작 (queue=%s)",
            settings.OUTFIT_RENDER_QUEUE_PENDING_KEY,
        )
        last_orphan_sweep = time.monotonic()
        while self._running:
            if (
                time.monotonic() - last_orphan_sweep
                >= settings.OUTFIT_RENDER_QUEUE_ORPHAN_SWEEP_SECONDS
            ):
                last_orphan_sweep = time.monotonic()
                self._recover_orphaned_pending()
            try:
                raw = render_queue.fetch()
            except redis.RedisError:
                logger.exception("코디 이미지 큐 읽기 실패")
                if options["once"]:
                    break
                time.sleep(1)
                continue
            if raw is None:
                if options["once"]:
                    break
                continue
            self._handle(raw)
            if options["once"]:
                break
        logger.info("코디 이미지 워커 종료")

    def _request_stop(self, *_args: Any) -> None:
        self._running = False

    def _recover_interrupted(self) -> None:
        for raw in render_queue.recover_processing():
            job_id = self._parse_job_id(raw)
            if job_id and render_jobs.reset_for_retry(job_id):
                self._publish(
                    job_id,
                    "retrying",
                    {"job_id": job_id, "status": OutfitRenderJob.Status.QUEUED},
                )

    @staticmethod
    def _recover_orphaned_pending() -> int:
        cutoff = timezone.now() - timedelta(
            seconds=settings.OUTFIT_RENDER_QUEUE_ORPHAN_AGE_SECONDS
        )
        jobs = list(
            OutfitRenderJob.objects.filter(
                status=OutfitRenderJob.Status.QUEUED,
                enqueued_at__isnull=True,
                created_at__lte=cutoff,
            ).order_by("created_at")[: settings.OUTFIT_RENDER_QUEUE_ORPHAN_SWEEP_LIMIT]
        )
        recovered = 0
        for job in jobs:
            try:
                render_queue.enqueue(job)
            except redis.RedisError:
                logger.warning(
                    "미적재 이미지 작업 복구 중 Redis 연결 실패", exc_info=True
                )
                break
            recovered_job = render_jobs.mark_enqueued(job.pk)
            recovered += int(recovered_job is not None)
        return recovered

    @staticmethod
    def _parse_job_id(raw: str) -> str | None:
        try:
            return str(json.loads(raw)["job_id"])
        except (ValueError, KeyError, TypeError):
            return None

    def _handle(self, raw: str) -> None:
        job_id = self._parse_job_id(raw)
        if job_id is None:
            logger.error("코디 이미지 큐 페이로드 해석 실패: %s", raw[:200])
            render_queue.ack(raw, "?")
            return

        # 같은 큐에 가상 피팅 작업도 들어온다. 둘 다 같은 이미지 모델을 부르는 긴
        # 작업이라 한 워커가 순서대로 처리한다 (큐를 나누면 컨테이너도 나뉜다).
        if render_queue.kind_of(raw) == render_queue.KIND_VIRTUAL_TRY_ON:
            self._handle_virtual_try_on(raw, job_id)
            return

        current = OutfitRenderJob.objects.filter(pk=job_id).first()
        if current is None:
            render_queue.ack(raw, job_id)
            return
        if current.status in OutfitRenderJob.TERMINAL_STATUSES:
            self._publish_terminal(current)
            render_queue.ack(raw, job_id)
            return

        job = render_jobs.start(job_id)
        if job is None:
            render_queue.ack(raw, job_id)
            return
        self._publish(
            job_id,
            "processing",
            {"job_id": job_id, "status": OutfitRenderJob.Status.PROCESSING},
        )
        try:
            completed = render_execution.execute(job)
        except Exception as exc:  # noqa: BLE001 — 작업 단위로 상태·큐 전이를 보장한다.
            self._handle_failure(raw, job, exc)
            return

        self._publish_terminal(completed)
        logger.info(
            "코디 이미지 처리 완료: job=%s card=%s status=%s",
            completed.pk,
            completed.composition_id,
            completed.status,
            extra={
                "job_id": str(completed.pk),
                "card_id": str(completed.composition_id),
                "status": completed.status,
                "cache_hit": completed.cache_hit,
            },
        )
        render_queue.ack(raw, job_id)

    def _handle_virtual_try_on(self, raw: str, job_id: str) -> None:
        """가상 피팅 한 건.

        SSE 이벤트는 발행하지 않는다 — 이 기능의 프론트는 폴링으로 본다
        (화면을 나갔다 와도 보이려면 어차피 조회가 필요하다).
        """
        current = VirtualTryOnJob.objects.filter(pk=job_id).first()
        if current is None or current.status in VirtualTryOnJob.TERMINAL_STATUSES:
            render_queue.ack(raw, job_id)
            return

        job = virtual_try_on_jobs.start(job_id)
        if job is None:
            render_queue.ack(raw, job_id)
            return

        try:
            completed = virtual_try_on_jobs.run(job)
        except Exception as exc:  # noqa: BLE001 — 한 건의 실패로 워커가 죽으면 안 된다
            code = self._error_code(exc)
            # 사용자에게 보여도 되는 문구만 남긴다. 내부 예외 메시지에는 버킷·키가
            # 섞여 나올 수 있다.
            safe = (
                str(exc)[:500]
                if isinstance(exc, (OutfitRenderError, ValueError))
                else "가상 착장 이미지를 만들지 못했습니다."
            )
            non_retryable = isinstance(exc, (RenderDisabled, RenderInputError, ValueError))
            try:
                if non_retryable:
                    render_queue.dead_letter(raw, job_id, code)
                    dead = True
                else:
                    dead = render_queue.retry_or_dead(raw, job_id, code)
            except redis.RedisError:
                logger.exception("가상 피팅 실패 작업의 큐 상태 전환 실패")
                dead = True
            if dead:
                virtual_try_on_jobs.mark_failed(
                    job_id, error_code=code, error_message=safe
                )
            else:
                # 재시도가 예약됐으니 다시 집을 수 있게 되돌린다.
                virtual_try_on_jobs.reset_for_retry(job_id)
            logger.warning("가상 피팅 실패: job=%s code=%s", job_id, code)
            return

        logger.info(
            "가상 피팅 완료: job=%s look=%s golden=%s cache_hit=%s",
            completed.pk, completed.look_id, completed.golden_id, completed.cache_hit,
        )
        render_queue.ack(raw, job_id)

    def _handle_failure(self, raw: str, job: OutfitRenderJob, exc: Exception) -> None:
        code = self._error_code(exc)
        safe_message = (
            str(exc)[:500]
            if isinstance(exc, OutfitRenderError)
            else "코디 이미지 생성 중 내부 오류가 발생했습니다."
        )
        non_retryable = isinstance(exc, (RenderDisabled, RenderInputError))
        try:
            if non_retryable:
                render_queue.dead_letter(raw, str(job.pk), code)
                dead = True
            else:
                dead = render_queue.retry_or_dead(raw, str(job.pk), code)
        except redis.RedisError:
            logger.exception("코디 이미지 실패 작업의 큐 상태 전환 실패")
            dead = True

        if dead:
            failed = render_jobs.mark_failed(
                job.pk,
                error_code=code,
                error_message=safe_message,
            )
            self._publish_terminal(failed)
        else:
            render_jobs.reset_for_retry(job.pk)
            self._publish(
                job.pk,
                "retrying",
                {"job_id": str(job.pk), "status": OutfitRenderJob.Status.QUEUED},
            )

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, RenderDisabled):
            return "OUTFIT_RENDER_DISABLED"
        if isinstance(exc, RenderInputError):
            return "OUTFIT_RENDER_INVALID"
        if isinstance(exc, OutfitRenderError):
            return "OUTFIT_RENDER_PROVIDER_FAILED"
        return "OUTFIT_RENDER_INTERNAL"

    def _publish_terminal(self, job: OutfitRenderJob) -> None:
        event = (
            "completed" if job.status == OutfitRenderJob.Status.SUCCEEDED else "failed"
        )
        data = {
            "job_id": str(job.pk),
            "card_id": str(job.composition_id),
            "status": job.status,
            "cache_hit": job.cache_hit,
            "error": (
                {"code": job.error_code, "message": job.error_message}
                if job.status == OutfitRenderJob.Status.FAILED
                else None
            ),
        }
        self._publish(job.pk, event, data)

    @staticmethod
    def _publish(job_id, event: str, data: dict) -> None:
        try:
            RenderEventStore().publish(job_id, event, data)
        except redis.RedisError:
            logger.warning(
                "코디 이미지 SSE 이벤트 기록 실패: job=%s event=%s",
                job_id,
                event,
                exc_info=True,
            )
