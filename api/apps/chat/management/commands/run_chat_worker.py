"""Redis 큐에서 ChatRun을 소비해 OpenAI 오케스트레이터를 실행한다."""

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

from apps.chat.models import ChatRun, ChatRunPersona
from apps.chat.serializers import ChatMessageSerializer
from apps.chat.services import queue
from apps.chat.services.alternative_recommendations import (
    finalize_persisted_alternative,
    mark_alternative_enqueue_failed,
    mark_alternative_processing_failed,
    reset_interrupted_alternative,
)
from apps.chat.services.events import ChatEventStore
from apps.chat.services.mood_analysis import (
    ChatMoodAnalysisStateInvalid,
    ChatMoodImageInvalid,
)
from apps.chat.services.openai_adapter import ChatLLMConfigurationError
from apps.chat.services.orchestrator import (
    ChatOrchestrator,
    ChatRunAlreadyProcessing,
    ChatRunInvalid,
    reset_run_for_retry,
)
from apps.chat.services.persona_retry import reset_interrupted_persona_retry
from apps.chat.services.recommendation_pipeline import ChatRecommendationError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "채팅 실행 큐를 소비해 OpenAI 오케스트레이터를 수행한다."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help="작업 1건을 처리하거나 큐 대기 시간이 끝나면 종료한다.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        self._running = True
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)

        self._recover_interrupted()
        self._recover_orphaned_pending()
        logger.info("채팅 워커 시작 (queue=%s)", settings.CHAT_QUEUE_PENDING_KEY)
        last_orphan_sweep = time.monotonic()
        while self._running:
            if (
                time.monotonic() - last_orphan_sweep
                >= settings.CHAT_QUEUE_ORPHAN_SWEEP_SECONDS
            ):
                last_orphan_sweep = time.monotonic()
                self._recover_orphaned_pending()
            try:
                raw = queue.fetch()
            except redis.RedisError:
                logger.exception("채팅 큐 읽기 실패")
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
        logger.info("채팅 워커 종료")

    def _request_stop(self, *_args: Any) -> None:
        logger.info("종료 신호 수신 — 현재 채팅 실행을 마치고 종료한다")
        self._running = False

    def _recover_interrupted(self) -> None:
        for raw in queue.recover_processing():
            payload = self._parse_payload(raw)
            if payload is None:
                continue
            run_id = str(payload["run_id"])
            if payload.get("task") == queue.PERSONA_RETRY_TASK:
                reset = reset_interrupted_persona_retry(
                    run_id=run_id,
                    persona_id=str(payload.get("persona_id", "")),
                )
            elif payload.get("task") == queue.PERSONA_ALTERNATIVE_TASK:
                reset = reset_interrupted_alternative(
                    run_id=run_id,
                    persona_id=str(payload.get("persona_id", "")),
                )
            else:
                reset = reset_run_for_retry(run_id)
            if reset:
                self._publish(
                    run_id,
                    "retrying",
                    {
                        "run_id": run_id,
                        "status": ChatRun.Status.PENDING,
                        "persona_id": payload.get("persona_id"),
                    },
                )

    @staticmethod
    def _recover_orphaned_pending() -> int:
        """DB 접수 후 Redis 적재 확인 전에 중단된 실행을 다시 큐에 넣는다."""
        cutoff = timezone.now() - timedelta(
            seconds=settings.CHAT_QUEUE_ORPHAN_AGE_SECONDS
        )
        runs = list(
            ChatRun.objects.filter(
                status=ChatRun.Status.PENDING,
                enqueued_at__isnull=True,
                created_at__lte=cutoff,
            ).order_by("created_at")[: settings.CHAT_QUEUE_ORPHAN_SWEEP_LIMIT]
        )
        recovered = 0
        for run in runs:
            try:
                alternative_execution = (
                    run.persona_executions.filter(
                        alternative_status=ChatRunPersona.AlternativeStatus.PENDING,
                    )
                    .order_by("display_order")
                    .first()
                )
                retry_execution = (
                    run.persona_executions.filter(
                        status=ChatRunPersona.Status.PENDING,
                        retry_count__gt=0,
                    )
                    .order_by("display_order")
                    .first()
                )
                if alternative_execution is not None:
                    current = alternative_execution.recommendation_result
                    queue.enqueue_persona_alternative(
                        run_id=run.pk,
                        persona_id=alternative_execution.persona_id,
                        source_result_id=str(current.pk),
                        generation=current.generation + 1,
                    )
                elif retry_execution is None:
                    queue.enqueue(run)
                else:
                    queue.enqueue_persona_retry(
                        run_id=run.pk,
                        persona_id=retry_execution.persona_id,
                        retry_count=retry_execution.retry_count,
                    )
            except redis.RedisError:
                logger.warning("미적재 ChatRun 복구 중 Redis 연결 실패", exc_info=True)
                break
            now = timezone.now()
            updated = ChatRun.objects.filter(
                pk=run.pk,
                status=ChatRun.Status.PENDING,
                enqueued_at__isnull=True,
            ).update(enqueued_at=now, updated_at=now)
            recovered += int(bool(updated))
        if recovered:
            logger.warning("DB 미적재 ChatRun %d건을 Redis 큐에 복구", recovered)
        return recovered

    @staticmethod
    def _parse_payload(raw: str) -> dict[str, object] | None:
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not payload.get("run_id"):
                return None
            return payload
        except (ValueError, KeyError, TypeError):
            return None

    @classmethod
    def _parse_run_id(cls, raw: str) -> str | None:
        payload = cls._parse_payload(raw)
        return str(payload["run_id"]) if payload is not None else None

    def _handle(self, raw: str) -> None:
        payload = self._parse_payload(raw)
        if payload is None:
            logger.error("채팅 큐 페이로드 해석 실패, 폐기: %s", raw[:200])
            queue.ack(raw, "?")
            return
        run_id = str(payload["run_id"])
        if payload.get("task") == queue.PERSONA_RETRY_TASK:
            self._handle_persona_retry(raw, payload)
            return
        if payload.get("task") == queue.PERSONA_ALTERNATIVE_TASK:
            self._handle_persona_alternative(raw, payload)
            return

        run = (
            ChatRun.objects.select_related("response_message").filter(pk=run_id).first()
        )
        if run is None:
            queue.ack(raw, run_id)
            return
        if run.status in {
            ChatRun.Status.SUCCEEDED,
            ChatRun.Status.NEEDS_CLARIFICATION,
            ChatRun.Status.FAILED,
        }:
            self._publish_terminal(run)
            queue.ack(raw, run_id)
            return
        if run.status == ChatRun.Status.RUNNING and not reset_run_for_retry(run_id):
            queue.ack(raw, run_id)
            return

        self._publish(
            run_id,
            "running",
            {"run_id": run_id, "status": ChatRun.Status.RUNNING},
        )
        try:
            result = ChatOrchestrator().process(run_id)
        except ChatRunAlreadyProcessing:
            current = ChatRun.objects.select_related("response_message").get(pk=run_id)
            self._publish_terminal(current)
            queue.ack(raw, run_id)
        except Exception as exc:
            logger.exception("채팅 실행 실패: run=%s", run_id)
            current = ChatRun.objects.select_related("response_message").get(pk=run_id)
            error_code = current.error_code or getattr(exc, "code", "CHAT_RUN_FAILED")
            if self._is_retryable(exc):
                dead = queue.retry_or_dead(raw, run_id, error_code)
                if not dead and reset_run_for_retry(run_id):
                    self._publish(
                        run_id,
                        "retrying",
                        {
                            "run_id": run_id,
                            "status": ChatRun.Status.PENDING,
                            "error_code": error_code,
                        },
                    )
                    return
            else:
                queue.dead_letter(raw, run_id, error_code)
                dead = True
            if dead:
                current.refresh_from_db()
                self._publish_terminal(current)
        else:
            self._publish_terminal(result.run)
            queue.ack(raw, run_id)

    def _handle_persona_retry(
        self,
        raw: str,
        payload: dict[str, object],
    ) -> None:
        run_id = str(payload["run_id"])
        persona_id = str(payload.get("persona_id", ""))
        retry_count = payload.get("retry_count")
        key = queue.delivery_key(payload)
        if (
            not persona_id
            or isinstance(retry_count, bool)
            or not isinstance(retry_count, int)
        ):
            logger.error("스타일리스트 재실행 페이로드 해석 실패: %s", raw[:200])
            queue.dead_letter(raw, key, "STYLIST_RETRY_PAYLOAD_INVALID")
            return
        if not ChatRun.objects.filter(pk=run_id).exists():
            queue.ack(raw, key)
            return
        self._publish(
            run_id,
            "running",
            {
                "run_id": run_id,
                "status": ChatRun.Status.RUNNING,
                "persona_id": persona_id,
            },
        )
        try:
            result = ChatOrchestrator().process_persona_retry(
                run_id=run_id,
                persona_id=persona_id,
                retry_count=retry_count,
            )
        except ChatRunAlreadyProcessing:
            current = ChatRun.objects.select_related("response_message").get(pk=run_id)
            self._publish_terminal(current)
            queue.ack(raw, key)
        except Exception:
            logger.exception(
                "스타일리스트 개별 재실행 실패: run=%s persona=%s",
                run_id,
                persona_id,
            )
            current = ChatRun.objects.select_related("response_message").get(pk=run_id)
            self._publish_terminal(current)
            queue.ack(raw, key)
        else:
            self._publish_terminal(result.run)
            queue.ack(raw, key)

    def _handle_persona_alternative(
        self,
        raw: str,
        payload: dict[str, object],
    ) -> None:
        run_id = str(payload["run_id"])
        persona_id = str(payload.get("persona_id", ""))
        source_result_id = str(payload.get("source_result_id", ""))
        generation = payload.get("generation")
        key = queue.delivery_key(payload)
        if (
            not persona_id
            or not source_result_id
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 2
        ):
            queue.dead_letter(raw, key, "STYLIST_ALTERNATIVE_PAYLOAD_INVALID")
            return
        if not ChatRun.objects.filter(pk=run_id).exists():
            queue.ack(raw, key)
            return
        if finalize_persisted_alternative(
            run_id=run_id,
            persona_id=persona_id,
            generation=generation,
        ):
            current = ChatRun.objects.select_related("response_message").get(pk=run_id)
            self._publish_terminal(current)
            queue.ack(raw, key)
            return
        self._publish(
            run_id,
            "running",
            {
                "run_id": run_id,
                "status": ChatRun.Status.RUNNING,
                "persona_id": persona_id,
                "task": queue.PERSONA_ALTERNATIVE_TASK,
            },
        )
        try:
            result = ChatOrchestrator().process_persona_alternative(
                run_id=run_id,
                persona_id=persona_id,
                source_result_id=source_result_id,
                generation=generation,
            )
        except ChatRunAlreadyProcessing:
            mark_alternative_enqueue_failed(
                run_id=run_id,
                persona_id=persona_id,
            )
            current = ChatRun.objects.select_related("response_message").get(pk=run_id)
            self._publish_terminal(current)
            queue.ack(raw, key)
        except Exception:
            logger.exception(
                "스타일리스트 다른 추천 실패: run=%s persona=%s",
                run_id,
                persona_id,
            )
            # 시작 단계의 DB 잠금 오류처럼 오케스트레이터 내부 복구 전에 난 예외도
            # PENDING/RUNNING으로 남기지 않는다. 현재 카드는 그대로 보존한다.
            mark_alternative_processing_failed(
                run_id=run_id,
                persona_id=persona_id,
            )
            current = ChatRun.objects.select_related("response_message").get(pk=run_id)
            self._publish_terminal(current)
            queue.ack(raw, key)
        else:
            self._publish_terminal(result.run)
            queue.ack(raw, key)

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        return not isinstance(
            exc,
            (
                ChatLLMConfigurationError,
                ChatMoodAnalysisStateInvalid,
                ChatMoodImageInvalid,
                ChatRecommendationError,
                ChatRunInvalid,
            ),
        )

    def _publish_terminal(self, run: ChatRun) -> None:
        event = {
            ChatRun.Status.SUCCEEDED: "completed",
            ChatRun.Status.NEEDS_CLARIFICATION: "needs_clarification",
            ChatRun.Status.FAILED: "failed",
        }.get(run.status)
        if event is None:
            return
        self._publish(
            run.pk,
            event,
            {
                "run_id": str(run.pk),
                "status": run.status,
                "response_message": (
                    ChatMessageSerializer(run.response_message).data
                    if run.response_message_id
                    else None
                ),
                "error": (
                    {"code": run.error_code, "message": run.error_message}
                    if run.status == ChatRun.Status.FAILED
                    else None
                ),
            },
        )

    @staticmethod
    def _publish(run_id, event: str, data: dict) -> None:
        try:
            ChatEventStore().publish(run_id, event, data)
        except redis.RedisError:
            # DB 최종 상태가 기준이므로 이벤트 기록 실패 때문에 완료된 OpenAI 호출을
            # 다시 수행하지 않는다. SSE는 다음 heartbeat에서 DB fallback을 사용한다.
            logger.warning(
                "채팅 SSE 이벤트 기록 실패: run=%s event=%s",
                run_id,
                event,
                exc_info=True,
            )
