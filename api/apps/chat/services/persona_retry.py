"""실패한 스타일리스트 한 명의 수동 재실행 상태 전이."""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.chat.models import ChatRun, ChatRunPersona, ChatSession
from apps.recommend.models import RecommendationResult


class PersonaRetryError(RuntimeError):
    code = "STYLIST_RETRY_INVALID"


class PersonaRetryNotFailed(PersonaRetryError):
    code = "STYLIST_RETRY_NOT_FAILED"


class PersonaRetryResultExists(PersonaRetryError):
    code = "STYLIST_RETRY_RESULT_EXISTS"


class PersonaRetryInProgress(PersonaRetryError):
    code = "STYLIST_RETRY_IN_PROGRESS"


@dataclass(frozen=True)
class PreparedPersonaRetry:
    run_id: str
    persona_id: str
    retry_count: int


def _history_entry(execution: ChatRunPersona) -> dict[str, object]:
    occurred_at = execution.completed_at or timezone.now()
    return {
        "attempt": execution.retry_count,
        "occurred_at": occurred_at.isoformat(),
        "error_code": execution.error_code,
        "error_message": execution.error_message,
        "latency_ms": execution.latency_ms,
    }


@transaction.atomic
def prepare_failed_persona_retry(
    *,
    run_id,
    persona_id: str,
) -> PreparedPersonaRetry:
    """FAILED 행 하나만 PENDING으로 되돌리고 직전 오류를 이력에 보존한다."""

    run = ChatRun.objects.select_for_update().filter(pk=run_id).first()
    if run is None or run.response_mode != ChatSession.ResponseMode.STYLIST:
        raise PersonaRetryError("스타일리스트 응답 실행만 개별 재시도할 수 있습니다.")
    if run.status not in {ChatRun.Status.SUCCEEDED, ChatRun.Status.FAILED}:
        raise PersonaRetryInProgress(
            "다른 스타일리스트 실행이 끝난 뒤 실패 카드를 재시도할 수 있습니다."
        )
    execution = (
        ChatRunPersona.objects.select_for_update()
        .filter(run=run, persona_id=persona_id)
        .first()
    )
    if execution is None:
        raise PersonaRetryError("원본 실행에 선택되지 않은 스타일리스트입니다.")
    if execution.status != ChatRunPersona.Status.FAILED:
        raise PersonaRetryNotFailed(
            "FAILED 상태인 스타일리스트만 재시도할 수 있습니다."
        )
    if RecommendationResult.objects.filter(persona_execution=execution).exists():
        raise PersonaRetryResultExists(
            "이미 저장된 추천 결과가 있는 스타일리스트는 실패 재시도할 수 없습니다."
        )

    history = list(execution.error_history or [])
    history.append(_history_entry(execution))
    execution.status = ChatRunPersona.Status.PENDING
    execution.retry_count += 1
    execution.error_history = history
    execution.latency_ms = 0
    execution.error_code = ""
    execution.error_message = ""
    execution.started_at = None
    execution.completed_at = None
    execution.save(
        update_fields=[
            "status",
            "retry_count",
            "error_history",
            "latency_ms",
            "error_code",
            "error_message",
            "started_at",
            "completed_at",
            "updated_at",
        ]
    )
    run.status = ChatRun.Status.PENDING
    run.enqueued_at = None
    run.error_code = ""
    run.error_message = ""
    run.completed_at = None
    run.save(
        update_fields=[
            "status",
            "enqueued_at",
            "error_code",
            "error_message",
            "completed_at",
            "updated_at",
        ]
    )
    return PreparedPersonaRetry(
        run_id=str(run.pk),
        persona_id=persona_id,
        retry_count=execution.retry_count,
    )


@transaction.atomic
def mark_persona_retry_enqueue_failed(*, run_id, persona_id: str) -> None:
    """Redis 적재 실패 시 해당 카드만 다시 FAILED로 종료한다."""

    execution = (
        ChatRunPersona.objects.select_for_update()
        .filter(
            run_id=run_id,
            persona_id=persona_id,
            status=ChatRunPersona.Status.PENDING,
        )
        .first()
    )
    if execution is None:
        return
    now = timezone.now()
    execution.status = ChatRunPersona.Status.FAILED
    execution.error_code = "CHAT_QUEUE_UNAVAILABLE"
    execution.error_message = (
        "스타일리스트 재실행 큐에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."
    )
    execution.completed_at = now
    execution.save(
        update_fields=[
            "status",
            "error_code",
            "error_message",
            "completed_at",
            "updated_at",
        ]
    )
    _refresh_parent_terminal_state(execution.run_id, now=now)


@transaction.atomic
def reset_interrupted_persona_retry(*, run_id, persona_id: str) -> bool:
    """워커 중단으로 RUNNING에 남은 수동 재시도를 같은 배달로 복구한다."""

    execution = (
        ChatRunPersona.objects.select_for_update()
        .filter(
            run_id=run_id,
            persona_id=persona_id,
            status__in=(
                ChatRunPersona.Status.PENDING,
                ChatRunPersona.Status.RUNNING,
            ),
        )
        .first()
    )
    if execution is None:
        return False
    now = timezone.now()
    execution.status = ChatRunPersona.Status.PENDING
    execution.latency_ms = 0
    execution.error_code = ""
    execution.error_message = ""
    execution.started_at = None
    execution.completed_at = None
    execution.save(
        update_fields=[
            "status",
            "latency_ms",
            "error_code",
            "error_message",
            "started_at",
            "completed_at",
            "updated_at",
        ]
    )
    ChatRun.objects.filter(
        pk=run_id,
        status__in=(ChatRun.Status.PENDING, ChatRun.Status.RUNNING),
    ).update(
        status=ChatRun.Status.PENDING,
        completed_at=None,
        updated_at=now,
    )
    return True


def _refresh_parent_terminal_state(run_id, *, now) -> None:
    has_success = ChatRunPersona.objects.filter(
        run_id=run_id,
        status=ChatRunPersona.Status.SUCCEEDED,
    ).exists()
    ChatRun.objects.filter(pk=run_id).update(
        status=(ChatRun.Status.SUCCEEDED if has_success else ChatRun.Status.FAILED),
        error_code=("" if has_success else "ALL_STYLIST_EXECUTIONS_FAILED"),
        error_message=(
            "" if has_success else "선택한 모든 스타일리스트 추천에 실패했습니다."
        ),
        completed_at=now,
        updated_at=now,
    )
