"""스타일리스트 추천을 병렬 실행하고 부분 실패를 독립적으로 확정한다."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from typing import Any, Protocol

from django.db import close_old_connections, connections, transaction
from django.utils import timezone

from apps.chat.models import ChatRun, ChatRunPersona, ChatSession
from apps.chat.services.openai_adapter import TurnAnalysis
from apps.chat.services.recommendation_pipeline import (
    ChatRecommendationError,
    ChatRecommendationPipeline,
    RecommendationPipelineResult,
)
from apps.chat.services.stylist_duplicate_resolver import (
    StylistCandidateSelection,
    StylistDuplicateResolutionError,
    StylistDuplicateResolver,
    classify_duplicate,
)
from apps.chat.services.stylist_personas import EXPECTED_PERSONA_ORDER
from apps.chat.services.stylist_recommendation_pipeline import (
    PersonaRecommendationCandidates,
    StylistRecommendationPipeline,
    StylistRecommendationPipelineError,
)
from apps.chat.services.stylist_strategy import StylistStrategyContractError
from apps.recommend.models import RecommendationResult
from apps.recommend.services.outfit_types import OutfitComposition

MAX_STYLIST_WORKERS = 3
STYLIST_BATCH_TIMEOUT_SECONDS = 20.0
_GENERIC_FAILURE_CODE = "STYLIST_PERSONA_FAILED"
_GENERIC_FAILURE_MESSAGE = "스타일리스트 추천 처리 중 내부 오류가 발생했습니다."
_TIMEOUT_CODE = "STYLIST_PERSONA_TIMEOUT"
_TIMEOUT_MESSAGE = "스타일리스트 추천 처리 시간이 초과되었습니다."
_COMMON_CONTEXT_CODE = "STYLIST_COMMON_CONTEXT_FAILED"
_DUPLICATE_RESOLUTION_CODE = "STYLIST_DUPLICATE_RESOLUTION_FAILED"


class StylistExecutionError(RuntimeError):
    """스타일리스트 묶음 실행 계약 또는 전체 실행 실패."""

    code = "STYLIST_EXECUTION_FAILED"


class StylistExecutionScopeError(StylistExecutionError):
    code = "STYLIST_EXECUTION_SCOPE_INVALID"


class StylistCommonContextFailed(StylistExecutionError):
    code = _COMMON_CONTEXT_CODE

    def __init__(self, result: StylistExecutionResult) -> None:
        super().__init__("공통 추천 컨텍스트를 생성하지 못했습니다.")
        self.result = result


class AllStylistExecutionsFailed(StylistExecutionError):
    code = "ALL_STYLIST_EXECUTIONS_FAILED"

    def __init__(self, result: StylistExecutionResult) -> None:
        super().__init__("선택한 모든 스타일리스트 추천에 실패했습니다.")
        self.result = result


class StylistAlternativeExhausted(StylistExecutionError):
    code = "STYLIST_ALTERNATIVE_EXHAUSTED"


@dataclass(frozen=True)
class StylistPersonaSuccess:
    persona_id: str
    persona_execution_id: str
    candidates: PersonaRecommendationCandidates
    selection: StylistCandidateSelection
    persisted: RecommendationPipelineResult
    latency_ms: int

    @property
    def recommendation_result_id(self) -> str:
        return str(self.persisted.result.pk)


@dataclass(frozen=True)
class StylistPersonaFailure:
    persona_id: str
    persona_execution_id: str
    error_code: str
    error_message: str
    latency_ms: int

    def snapshot(self) -> dict[str, object]:
        return {
            "persona_id": self.persona_id,
            "persona_execution_id": self.persona_execution_id,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "latency_ms": self.latency_ms,
        }


@dataclass(frozen=True)
class StylistExecutionResult:
    run_id: str
    successes: tuple[StylistPersonaSuccess, ...]
    failures: tuple[StylistPersonaFailure, ...]
    latency_ms: int

    @property
    def partial_failure(self) -> bool:
        return bool(self.successes and self.failures)

    @property
    def all_failed(self) -> bool:
        return not self.successes

    @property
    def recommendation_result_ids(self) -> tuple[str, ...]:
        return tuple(row.recommendation_result_id for row in self.successes)


@dataclass(frozen=True)
class _TaskOutcome:
    persona_id: str
    persona_execution_id: str
    candidates: PersonaRecommendationCandidates | None
    error: Exception | None
    latency_ms: int


class PersonaExecutionStateStore(Protocol):
    def mark_running(self, execution_ids: tuple[str, ...]) -> None: ...

    def mark_succeeded(self, execution_id: str, *, latency_ms: int) -> None: ...

    def mark_failed(
        self,
        execution_id: str,
        *,
        error_code: str,
        error_message: str,
        latency_ms: int,
    ) -> None: ...


class DjangoPersonaExecutionStateStore:
    """상태 전이를 짧은 독립 트랜잭션으로 DB에 반영한다."""

    @transaction.atomic
    def mark_running(self, execution_ids: tuple[str, ...]) -> None:
        rows = list(
            ChatRunPersona.objects.select_for_update()
            .filter(pk__in=execution_ids)
            .order_by("display_order")
        )
        if len(rows) != len(execution_ids) or any(
            row.status != ChatRunPersona.Status.PENDING for row in rows
        ):
            raise StylistExecutionScopeError(
                "PENDING 상태인 스타일리스트 실행만 시작할 수 있습니다."
            )
        now = timezone.now()
        ChatRunPersona.objects.filter(pk__in=execution_ids).update(
            status=ChatRunPersona.Status.RUNNING,
            latency_ms=0,
            error_code="",
            error_message="",
            started_at=now,
            completed_at=None,
            updated_at=now,
        )

    def mark_succeeded(self, execution_id: str, *, latency_ms: int) -> None:
        self._finish(
            execution_id,
            status=ChatRunPersona.Status.SUCCEEDED,
            error_code="",
            error_message="",
            latency_ms=latency_ms,
        )

    def mark_failed(
        self,
        execution_id: str,
        *,
        error_code: str,
        error_message: str,
        latency_ms: int,
    ) -> None:
        self._finish(
            execution_id,
            status=ChatRunPersona.Status.FAILED,
            error_code=error_code,
            error_message=error_message,
            latency_ms=latency_ms,
        )

    @staticmethod
    @transaction.atomic
    def _finish(
        execution_id: str,
        *,
        status: str,
        error_code: str,
        error_message: str,
        latency_ms: int,
    ) -> None:
        execution = (
            ChatRunPersona.objects.select_for_update().filter(pk=execution_id).first()
        )
        if execution is None or execution.status != ChatRunPersona.Status.RUNNING:
            raise StylistExecutionScopeError(
                "RUNNING 상태인 스타일리스트 실행만 종료할 수 있습니다."
            )
        now = timezone.now()
        execution.status = status
        execution.latency_ms = max(latency_ms, 0)
        execution.error_code = error_code[:64]
        execution.error_message = error_message[:500]
        execution.completed_at = now
        execution.save(
            update_fields=[
                "status",
                "latency_ms",
                "error_code",
                "error_message",
                "completed_at",
                "updated_at",
            ]
        )


PipelineFactory = Callable[[], StylistRecommendationPipeline]
ScopeLoader = Callable[[str, str], tuple[ChatRun, ChatRunPersona]]


class StylistExecutionCoordinator:
    """공통 분석을 재사용하며 후보 생성 실패를 스타일리스트별로 격리한다."""

    def __init__(
        self,
        *,
        pipeline_factory: PipelineFactory | None = None,
        persistence_pipeline: ChatRecommendationPipeline | None = None,
        duplicate_resolver: StylistDuplicateResolver | None = None,
        state_store: PersonaExecutionStateStore | None = None,
        scope_loader: ScopeLoader | None = None,
        max_workers: int = MAX_STYLIST_WORKERS,
        timeout_seconds: float = STYLIST_BATCH_TIMEOUT_SECONDS,
    ) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int):
            raise StylistExecutionScopeError("병렬 실행 수는 1~3의 정수여야 합니다.")
        if not 1 <= max_workers <= MAX_STYLIST_WORKERS:
            raise StylistExecutionScopeError("병렬 실행 수는 1~3이어야 합니다.")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise StylistExecutionScopeError(
                "스타일리스트 실행 제한 시간은 0보다 큰 유한한 숫자여야 합니다."
            )
        self.pipeline_factory = pipeline_factory or StylistRecommendationPipeline
        self.persistence_pipeline = persistence_pipeline or ChatRecommendationPipeline()
        self.duplicate_resolver = duplicate_resolver or StylistDuplicateResolver()
        self.state_store = state_store or DjangoPersonaExecutionStateStore()
        self.scope_loader = scope_loader or self._load_scope
        self.max_workers = max_workers
        self.timeout_seconds = float(timeout_seconds)

    def execute(
        self,
        *,
        run: ChatRun,
        persona_executions: Sequence[ChatRunPersona],
        context: dict[str, Any],
        analysis: TurnAnalysis,
        allowed_duplicate_slots: tuple[str, ...] = (),
    ) -> StylistExecutionResult:
        started = time.monotonic()
        ordered = self._validate_scope(run, persona_executions)
        execution_ids = tuple(str(row.pk) for row in ordered)
        self.state_store.mark_running(execution_ids)

        try:
            strategy_context = self.pipeline_factory().build_context(
                run=run,
                context=context,
                analysis=analysis,
            )
        except Exception as exc:
            failures = self._fail_all(
                ordered,
                exc=exc,
                error_code=_COMMON_CONTEXT_CODE,
                started=started,
            )
            result = self._result(run, (), failures, started)
            raise StylistCommonContextFailed(result) from exc

        outcomes = self._execute_parallel(
            run_id=str(run.pk),
            executions=ordered,
            context=context,
            analysis=analysis,
            strategy_context=strategy_context,
        )
        failures: list[StylistPersonaFailure] = []
        generated: list[PersonaRecommendationCandidates] = []
        latency_by_persona: dict[str, int] = {}
        for outcome in outcomes:
            latency_by_persona[outcome.persona_id] = outcome.latency_ms
            if outcome.candidates is not None:
                generated.append(outcome.candidates)
                continue
            failure = self._failure_from_outcome(outcome)
            failures.append(failure)
            self._mark_failure(failure)

        if generated:
            try:
                resolution = self.duplicate_resolver.resolve(
                    tuple(generated),
                    allowed_duplicate_slots=allowed_duplicate_slots,
                )
            except Exception as exc:  # noqa: BLE001 - RUNNING 상태 잔류를 막는다.
                for candidates in generated:
                    failure = self._failure(
                        persona_id=candidates.persona_id,
                        execution_id=candidates.persona_execution_id,
                        exc=exc,
                        latency_ms=latency_by_persona[candidates.persona_id],
                        error_code=_DUPLICATE_RESOLUTION_CODE,
                    )
                    failures.append(failure)
                    self._mark_failure(failure)
                generated = []

        successes: list[StylistPersonaSuccess] = []
        if generated:
            execution_by_persona = {row.persona_id: row for row in ordered}
            for selection in resolution.selections:
                execution = execution_by_persona[selection.persona_id]
                latency_ms = latency_by_persona[selection.persona_id]
                persistence_started = time.monotonic()
                try:
                    persisted = self.persistence_pipeline.persist_candidates(
                        run=run,
                        generated=selection.source.generated,
                        selected=(selection.selected.candidate,),
                        persona_execution=execution,
                        validated_reason_codes=selection.validated_reason_codes,
                        strategy_snapshot=self._strategy_snapshot(
                            selection,
                            base=execution.strategy_snapshot,
                        ),
                    )
                except Exception as exc:  # noqa: BLE001 - 개별 저장 실패를 격리한다.
                    latency_ms += self._elapsed_ms(persistence_started)
                    failure = self._failure(
                        persona_id=selection.persona_id,
                        execution_id=str(execution.pk),
                        exc=exc,
                        latency_ms=latency_ms,
                    )
                    failures.append(failure)
                    self._mark_failure(failure)
                    continue
                latency_ms += self._elapsed_ms(persistence_started)
                self.state_store.mark_succeeded(
                    str(execution.pk),
                    latency_ms=latency_ms,
                )
                successes.append(
                    StylistPersonaSuccess(
                        persona_id=selection.persona_id,
                        persona_execution_id=str(execution.pk),
                        candidates=selection.source,
                        selection=selection,
                        persisted=persisted,
                        latency_ms=latency_ms,
                    )
                )

        result = self._result(run, successes, failures, started)
        if result.all_failed:
            raise AllStylistExecutionsFailed(result)
        return result

    def execute_retry(
        self,
        *,
        run: ChatRun,
        persona_execution: ChatRunPersona,
        context: dict[str, Any],
        analysis: TurnAnalysis,
        allowed_duplicate_slots: tuple[str, ...] = (),
        excluded_compositions: tuple[OutfitComposition, ...] = (),
        result_type: str = RecommendationResult.ResultType.INITIAL,
        replace_current: bool = False,
    ) -> StylistExecutionResult:
        """PENDING으로 준비된 스타일리스트 한 명만 같은 실행 스냅샷으로 재실행한다."""

        started = time.monotonic()
        self._validate_retry_scope(run, persona_execution)
        execution_id = str(persona_execution.pk)
        self.state_store.mark_running((execution_id,))
        try:
            strategy_context = self.pipeline_factory().build_context(
                run=run,
                context=context,
                analysis=analysis,
            )
        except Exception as exc:
            failure = self._failure(
                persona_id=persona_execution.persona_id,
                execution_id=execution_id,
                exc=exc,
                latency_ms=self._elapsed_ms(started),
                error_code=_COMMON_CONTEXT_CODE,
            )
            self._mark_failure(failure)
            result = self._result(run, (), (failure,), started)
            raise StylistCommonContextFailed(result) from exc

        outcome = self._execute_one(
            run_id=str(run.pk),
            execution_id=execution_id,
            persona_id=persona_execution.persona_id,
            context=context,
            analysis=analysis,
            strategy_context=strategy_context,
        )
        if outcome.candidates is None:
            failure = self._failure_from_outcome(outcome)
            self._mark_failure(failure)
            result = self._result(run, (), (failure,), started)
            raise AllStylistExecutionsFailed(result)

        try:
            source = outcome.candidates
            if excluded_compositions:
                distinct = tuple(
                    candidate
                    for candidate in source.ranked_candidates
                    if all(
                        classify_duplicate(
                            candidate.candidate.composition,
                            excluded,
                            allowed_duplicate_slots=allowed_duplicate_slots,
                        )
                        is None
                        for excluded in excluded_compositions
                    )
                )
                if not distinct:
                    raise StylistAlternativeExhausted(
                        "현재 카드와 최근 추천 10회를 피하는 다른 코디를 찾지 못했습니다."
                    )
                source = replace(source, ranked_candidates=distinct)
            selection = self.duplicate_resolver.resolve(
                (source,),
                allowed_duplicate_slots=allowed_duplicate_slots,
            ).selections[0]
            reason_codes = selection.validated_reason_codes
            strategy_snapshot = self._strategy_snapshot(
                selection,
                base=persona_execution.strategy_snapshot,
            )
            if replace_current:
                reason_codes = (*reason_codes, "STYLIST_ALTERNATIVE_DISTINCT")
                strategy_snapshot["alternative_exclusions"] = {
                    "card_count": len(excluded_compositions),
                    "recent_run_limit": 10,
                }
            persisted = self.persistence_pipeline.persist_candidates(
                run=run,
                generated=selection.source.generated,
                selected=(selection.selected.candidate,),
                persona_execution=persona_execution,
                validated_reason_codes=reason_codes,
                strategy_snapshot=strategy_snapshot,
                result_type=result_type,
                replace_current=replace_current,
            )
        except Exception as exc:  # noqa: BLE001 - 해당 카드만 실패로 확정한다.
            failure = self._failure(
                persona_id=persona_execution.persona_id,
                execution_id=execution_id,
                exc=exc,
                latency_ms=self._elapsed_ms(started),
            )
            self._mark_failure(failure)
            result = self._result(run, (), (failure,), started)
            raise AllStylistExecutionsFailed(result) from exc

        latency_ms = self._elapsed_ms(started)
        self.state_store.mark_succeeded(execution_id, latency_ms=latency_ms)
        success = StylistPersonaSuccess(
            persona_id=persona_execution.persona_id,
            persona_execution_id=execution_id,
            candidates=outcome.candidates,
            selection=selection,
            persisted=persisted,
            latency_ms=latency_ms,
        )
        return self._result(run, (success,), (), started)

    def execute_alternative(
        self,
        *,
        run: ChatRun,
        persona_execution: ChatRunPersona,
        context: dict[str, Any],
        analysis: TurnAnalysis,
        excluded_compositions: tuple[OutfitComposition, ...],
        allowed_duplicate_slots: tuple[str, ...] = (),
    ) -> StylistExecutionResult:
        return self.execute_retry(
            run=run,
            persona_execution=persona_execution,
            context=context,
            analysis=analysis,
            allowed_duplicate_slots=allowed_duplicate_slots,
            excluded_compositions=excluded_compositions,
            result_type=RecommendationResult.ResultType.ALTERNATIVE,
            replace_current=True,
        )

    def _execute_parallel(
        self,
        *,
        run_id: str,
        executions: tuple[ChatRunPersona, ...],
        context: dict[str, Any],
        analysis: TurnAnalysis,
        strategy_context: Any,
    ) -> tuple[_TaskOutcome, ...]:
        executor = ThreadPoolExecutor(
            max_workers=min(self.max_workers, len(executions)),
            thread_name_prefix="stylist",
        )
        futures: dict[Future[_TaskOutcome], ChatRunPersona] = {
            executor.submit(
                self._execute_one,
                run_id=run_id,
                execution_id=str(execution.pk),
                persona_id=execution.persona_id,
                context=context,
                analysis=analysis,
                strategy_context=strategy_context,
            ): execution
            for execution in executions
        }
        done, unfinished = wait(futures, timeout=self.timeout_seconds)
        outcomes = [future.result() for future in done]
        timeout_ms = int(self.timeout_seconds * 1000)
        for future in unfinished:
            future.cancel()
            execution = futures[future]
            outcomes.append(
                _TaskOutcome(
                    persona_id=execution.persona_id,
                    persona_execution_id=str(execution.pk),
                    candidates=None,
                    error=TimeoutError(_TIMEOUT_MESSAGE),
                    latency_ms=timeout_ms,
                )
            )
        executor.shutdown(wait=False, cancel_futures=True)
        order = {
            persona_id: index for index, persona_id in enumerate(EXPECTED_PERSONA_ORDER)
        }
        return tuple(sorted(outcomes, key=lambda row: order[row.persona_id]))

    def _execute_one(
        self,
        *,
        run_id: str,
        execution_id: str,
        persona_id: str,
        context: dict[str, Any],
        analysis: TurnAnalysis,
        strategy_context: Any,
    ) -> _TaskOutcome:
        started = time.monotonic()
        close_old_connections()
        try:
            run, execution = self.scope_loader(run_id, execution_id)
            candidates = self.pipeline_factory().execute_persona(
                run=run,
                persona_execution=execution,
                context=context,
                analysis=analysis,
                strategy_context=strategy_context,
            )
            if (
                candidates.persona_id != persona_id
                or candidates.persona_execution_id != execution_id
            ):
                raise StylistExecutionScopeError(
                    "스타일리스트 실행 결과의 범위가 요청과 다릅니다."
                )
            return _TaskOutcome(
                persona_id=persona_id,
                persona_execution_id=execution_id,
                candidates=candidates,
                error=None,
                latency_ms=self._elapsed_ms(started),
            )
        except Exception as exc:  # noqa: BLE001 - 페르소나별 실패를 결과로 격리한다.
            return _TaskOutcome(
                persona_id=persona_id,
                persona_execution_id=execution_id,
                candidates=None,
                error=exc,
                latency_ms=self._elapsed_ms(started),
            )
        finally:
            connections.close_all()

    @staticmethod
    def _load_scope(run_id: str, execution_id: str) -> tuple[ChatRun, ChatRunPersona]:
        run = ChatRun.objects.select_related(
            "session",
            "session__identity",
        ).get(pk=run_id)
        execution = ChatRunPersona.objects.get(pk=execution_id, run=run)
        return run, execution

    @staticmethod
    def _validate_scope(
        run: ChatRun,
        persona_executions: Sequence[ChatRunPersona],
    ) -> tuple[ChatRunPersona, ...]:
        rows = tuple(persona_executions)
        if run.response_mode != ChatSession.ResponseMode.STYLIST:
            raise StylistExecutionScopeError(
                "스타일리스트 병렬 실행은 STYLIST 응답 모드만 지원합니다."
            )
        if not 1 <= len(rows) <= MAX_STYLIST_WORKERS:
            raise StylistExecutionScopeError(
                "스타일리스트 실행은 한 번에 1~3개여야 합니다."
            )
        persona_ids = [row.persona_id for row in rows]
        execution_ids = [str(row.pk) for row in rows]
        if len(persona_ids) != len(set(persona_ids)) or len(execution_ids) != len(
            set(execution_ids)
        ):
            raise StylistExecutionScopeError(
                "같은 스타일리스트 실행을 중복 처리할 수 없습니다."
            )
        if any(
            row.run_id != run.pk
            or row.persona_id not in run.persona_ids
            or row.persona_id not in EXPECTED_PERSONA_ORDER
            for row in rows
        ):
            raise StylistExecutionScopeError(
                "ChatRun 스냅샷에 속한 스타일리스트 실행만 처리할 수 있습니다."
            )
        if set(persona_ids) != set(run.persona_ids):
            raise StylistExecutionScopeError(
                "ChatRun이 선택한 모든 스타일리스트 실행이 필요합니다."
            )
        order = {
            persona_id: index for index, persona_id in enumerate(EXPECTED_PERSONA_ORDER)
        }
        return tuple(sorted(rows, key=lambda row: order[row.persona_id]))

    @staticmethod
    def _validate_retry_scope(
        run: ChatRun,
        execution: ChatRunPersona,
    ) -> None:
        if run.response_mode != ChatSession.ResponseMode.STYLIST:
            raise StylistExecutionScopeError(
                "스타일리스트 개별 재실행은 STYLIST 응답 모드만 지원합니다."
            )
        if (
            execution.run_id != run.pk
            or execution.persona_id not in run.persona_ids
            or execution.persona_id not in EXPECTED_PERSONA_ORDER
        ):
            raise StylistExecutionScopeError(
                "ChatRun 스냅샷에 속한 스타일리스트만 재실행할 수 있습니다."
            )
        if execution.status != ChatRunPersona.Status.PENDING:
            raise StylistExecutionScopeError(
                "PENDING 상태로 준비된 스타일리스트만 재실행할 수 있습니다."
            )

    def _fail_all(
        self,
        executions: tuple[ChatRunPersona, ...],
        *,
        exc: Exception,
        error_code: str,
        started: float,
    ) -> tuple[StylistPersonaFailure, ...]:
        latency_ms = self._elapsed_ms(started)
        failures = tuple(
            self._failure(
                persona_id=row.persona_id,
                execution_id=str(row.pk),
                exc=exc,
                latency_ms=latency_ms,
                error_code=error_code,
            )
            for row in executions
        )
        for failure in failures:
            self._mark_failure(failure)
        return failures

    def _failure_from_outcome(self, outcome: _TaskOutcome) -> StylistPersonaFailure:
        assert outcome.error is not None
        return self._failure(
            persona_id=outcome.persona_id,
            execution_id=outcome.persona_execution_id,
            exc=outcome.error,
            latency_ms=outcome.latency_ms,
            error_code=(
                _TIMEOUT_CODE if isinstance(outcome.error, TimeoutError) else None
            ),
        )

    @staticmethod
    def _failure(
        *,
        persona_id: str,
        execution_id: str,
        exc: Exception,
        latency_ms: int,
        error_code: str | None = None,
    ) -> StylistPersonaFailure:
        safe_errors = (
            ChatRecommendationError,
            StylistExecutionError,
            StylistRecommendationPipelineError,
            StylistStrategyContractError,
            StylistDuplicateResolutionError,
        )
        code = error_code or getattr(exc, "code", _GENERIC_FAILURE_CODE)
        if isinstance(exc, TimeoutError):
            message = _TIMEOUT_MESSAGE
        elif isinstance(exc, safe_errors):
            message = str(exc)[:500]
        else:
            message = _GENERIC_FAILURE_MESSAGE
        return StylistPersonaFailure(
            persona_id=persona_id,
            persona_execution_id=execution_id,
            error_code=str(code)[:64],
            error_message=message,
            latency_ms=max(latency_ms, 0),
        )

    def _mark_failure(self, failure: StylistPersonaFailure) -> None:
        self.state_store.mark_failed(
            failure.persona_execution_id,
            error_code=failure.error_code,
            error_message=failure.error_message,
            latency_ms=failure.latency_ms,
        )

    @staticmethod
    def _strategy_snapshot(
        selection: StylistCandidateSelection,
        *,
        base: dict[str, Any],
    ) -> dict[str, Any]:
        plan = selection.source.strategy_result.plan
        snapshot = dict(base)
        snapshot["execution_plan"] = {
            "search_query": plan.search_query,
            "preference_adjustments": [
                {
                    "axis": row.axis,
                    "values": list(row.values),
                    "polarity": row.polarity.value,
                    "weight": row.weight,
                }
                for row in plan.preference_adjustments
            ],
            "candidate_limit": plan.candidate_limit,
            "sort_rules": [
                {
                    "metric": row.metric.value,
                    "direction": row.direction.value,
                }
                for row in plan.sort_rules
            ],
        }
        snapshot["candidate_selection"] = selection.snapshot()
        if selection.source.hypothesis_snapshot:
            snapshot["experimental_hypotheses"] = dict(
                selection.source.hypothesis_snapshot
            )
        return snapshot

    @staticmethod
    def _result(
        run: ChatRun,
        successes: Sequence[StylistPersonaSuccess],
        failures: Sequence[StylistPersonaFailure],
        started: float,
    ) -> StylistExecutionResult:
        order = {
            persona_id: index for index, persona_id in enumerate(EXPECTED_PERSONA_ORDER)
        }
        return StylistExecutionResult(
            run_id=str(run.pk),
            successes=tuple(sorted(successes, key=lambda row: order[row.persona_id])),
            failures=tuple(sorted(failures, key=lambda row: order[row.persona_id])),
            latency_ms=StylistExecutionCoordinator._elapsed_ms(started),
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(int((time.monotonic() - started) * 1000), 0)
