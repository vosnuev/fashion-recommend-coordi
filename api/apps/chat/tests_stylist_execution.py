from __future__ import annotations

from threading import Barrier, Event, Lock
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from apps.chat.models import ChatSession
from apps.chat.services.stylist_execution import (
    AllStylistExecutionsFailed,
    StylistCommonContextFailed,
    StylistExecutionCoordinator,
)
from apps.chat.services.stylist_recommendation_pipeline import (
    PersonaRecommendationCandidates,
    StylistRecommendationPipelineError,
)
from apps.chat.services.stylist_strategy import (
    SortDirection,
    SortMetric,
    SortRule,
    StrategyPlan,
)
from apps.recommend.models import RecommendationResult
from apps.recommend.services.item_retriever import ItemSource
from apps.recommend.services.outfit_types import (
    OutfitComposition,
    OutfitItem,
    RecommendationMode,
)


class _FakeStateStore:
    def __init__(self) -> None:
        self.running: tuple[str, ...] = ()
        self.succeeded: dict[str, int] = {}
        self.failed: dict[str, tuple[str, str, int]] = {}

    def mark_running(self, execution_ids: tuple[str, ...]) -> None:
        self.running = execution_ids

    def mark_succeeded(self, execution_id: str, *, latency_ms: int) -> None:
        self.succeeded[execution_id] = latency_ms

    def mark_failed(
        self,
        execution_id: str,
        *,
        error_code: str,
        error_message: str,
        latency_ms: int,
    ) -> None:
        self.failed[execution_id] = (error_code, error_message, latency_ms)


class _FakeDuplicateResolver:
    def resolve(self, results, *, allowed_duplicate_slots=()):
        selections = []
        for source in results:
            selected = (
                source.ranked_candidates[0]
                if getattr(source, "ranked_candidates", ())
                else SimpleNamespace(candidate=f"candidate-{source.persona_id}")
            )
            selections.append(
                SimpleNamespace(
                    persona_id=source.persona_id,
                    source=source,
                    selected=selected,
                    validated_reason_codes=("STRATEGY_MATCH",),
                    snapshot=lambda persona_id=source.persona_id: {
                        "selected_rank": 1,
                        "persona_id": persona_id,
                    },
                )
            )
        return SimpleNamespace(selections=tuple(selections))


class _FakePersistencePipeline:
    def __init__(self, *, failures: set[str] | None = None) -> None:
        self.failures = failures or set()
        self.calls: list[dict] = []

    def persist_candidates(self, **kwargs):
        self.calls.append(kwargs)
        persona_id = kwargs["persona_execution"].persona_id
        if persona_id in self.failures:
            raise StylistRecommendationPipelineError("추천 결과 저장 실패")
        return SimpleNamespace(result=SimpleNamespace(pk=f"result-{persona_id}"))


class StylistExecutionCoordinatorTests(SimpleTestCase):
    def test_one_persona_failure_does_not_cancel_other_parallel_results(self) -> None:
        persona_ids = ("minimal", "experimental", "practical")
        run, executions = self._scope(persona_ids)
        barrier = Barrier(3)
        factory_calls: list[object] = []
        context_calls: list[object] = []
        lock = Lock()

        class Pipeline:
            def build_context(self, **_kwargs):
                context_calls.append(object())
                return object()

            def execute_persona(self, *, persona_execution, **_kwargs):
                barrier.wait(timeout=1)
                if persona_execution.persona_id == "experimental":
                    raise StylistRecommendationPipelineError(
                        "실험형 검색 제공자 응답 실패"
                    )
                return self_result(persona_execution)

        def factory():
            pipeline = Pipeline()
            with lock:
                factory_calls.append(pipeline)
            return pipeline

        state = _FakeStateStore()
        persistence = _FakePersistencePipeline()
        coordinator = self._coordinator(
            run=run,
            executions=executions,
            pipeline_factory=factory,
            state=state,
            persistence=persistence,
        )

        result = coordinator.execute(
            run=run,
            persona_executions=tuple(reversed(executions)),
            context={"shared": True},
            analysis=Mock(),
        )

        self.assertTrue(result.partial_failure)
        self.assertEqual(
            [row.persona_id for row in result.successes],
            ["minimal", "practical"],
        )
        self.assertEqual(
            [row.persona_id for row in result.failures],
            ["experimental"],
        )
        self.assertEqual(
            result.recommendation_result_ids,
            (
                "result-minimal",
                "result-practical",
            ),
        )
        self.assertEqual(len(context_calls), 1)
        self.assertEqual(len(factory_calls), 4)
        self.assertEqual(
            state.running,
            (
                "execution-minimal",
                "execution-experimental",
                "execution-practical",
            ),
        )
        self.assertEqual(
            set(state.succeeded),
            {
                "execution-minimal",
                "execution-practical",
            },
        )
        self.assertEqual(
            state.failed["execution-experimental"][0],
            "STYLIST_PERSONA_FAILED",
        )
        self.assertEqual(len(persistence.calls), 2)

    def test_persistence_failure_is_isolated_and_other_result_is_kept(self) -> None:
        persona_ids = ("minimal", "practical")
        run, executions = self._scope(persona_ids)
        state = _FakeStateStore()
        persistence = _FakePersistencePipeline(failures={"minimal"})

        coordinator = self._coordinator(
            run=run,
            executions=executions,
            pipeline_factory=self._successful_factory(),
            state=state,
            persistence=persistence,
        )
        result = coordinator.execute(
            run=run,
            persona_executions=executions,
            context={},
            analysis=Mock(),
        )

        self.assertTrue(result.partial_failure)
        self.assertEqual(result.recommendation_result_ids, ("result-practical",))
        self.assertIn("execution-minimal", state.failed)
        self.assertIn("execution-practical", state.succeeded)

    def test_all_persona_failures_are_recorded_before_overall_failure(self) -> None:
        persona_ids = ("minimal", "experimental", "practical")
        run, executions = self._scope(persona_ids)
        barrier = Barrier(3)

        class Pipeline:
            def build_context(self, **_kwargs):
                return object()

            def execute_persona(self, **_kwargs):
                barrier.wait(timeout=1)
                raise RuntimeError("민감할 수 있는 내부 예외")

        state = _FakeStateStore()
        persistence = _FakePersistencePipeline()
        coordinator = self._coordinator(
            run=run,
            executions=executions,
            pipeline_factory=Pipeline,
            state=state,
            persistence=persistence,
        )

        with self.assertRaises(AllStylistExecutionsFailed) as caught:
            coordinator.execute(
                run=run,
                persona_executions=executions,
                context={},
                analysis=Mock(),
            )

        self.assertTrue(caught.exception.result.all_failed)
        self.assertEqual(len(caught.exception.result.failures), 3)
        self.assertEqual(len(state.failed), 3)
        self.assertTrue(
            all(
                row.error_message
                == "스타일리스트 추천 처리 중 내부 오류가 발생했습니다."
                for row in caught.exception.result.failures
            )
        )
        self.assertFalse(persistence.calls)

    def test_common_context_failure_marks_every_persona_failed_without_jobs(
        self,
    ) -> None:
        run, executions = self._scope(("minimal", "practical"))
        execution_calls: list[object] = []

        class Pipeline:
            def build_context(self, **_kwargs):
                raise ValueError("잘못된 공통 컨텍스트")

            def execute_persona(self, **_kwargs):
                execution_calls.append(object())

        state = _FakeStateStore()
        coordinator = self._coordinator(
            run=run,
            executions=executions,
            pipeline_factory=Pipeline,
            state=state,
            persistence=_FakePersistencePipeline(),
        )

        with self.assertRaises(StylistCommonContextFailed) as caught:
            coordinator.execute(
                run=run,
                persona_executions=executions,
                context={},
                analysis=Mock(),
            )

        self.assertTrue(caught.exception.result.all_failed)
        self.assertEqual(len(state.failed), 2)
        self.assertTrue(
            all(
                values[0] == "STYLIST_COMMON_CONTEXT_FAILED"
                for values in state.failed.values()
            )
        )
        self.assertFalse(execution_calls)

    def test_duplicate_resolution_failure_does_not_leave_running_status(self) -> None:
        run, executions = self._scope(("minimal", "practical"))
        state = _FakeStateStore()
        coordinator = self._coordinator(
            run=run,
            executions=executions,
            pipeline_factory=self._successful_factory(),
            state=state,
            persistence=_FakePersistencePipeline(),
        )
        coordinator.duplicate_resolver = Mock(
            resolve=Mock(side_effect=RuntimeError("중복 검사 내부 오류"))
        )

        with self.assertRaises(AllStylistExecutionsFailed) as caught:
            coordinator.execute(
                run=run,
                persona_executions=executions,
                context={},
                analysis=Mock(),
            )

        self.assertTrue(caught.exception.result.all_failed)
        self.assertEqual(len(state.failed), 2)
        self.assertTrue(
            all(
                values[0] == "STYLIST_DUPLICATE_RESOLUTION_FAILED"
                for values in state.failed.values()
            )
        )

    def test_timeout_becomes_one_persona_failure_without_waiting_for_late_job(
        self,
    ) -> None:
        run, executions = self._scope(("minimal", "practical"))
        release = Event()

        class Pipeline:
            def build_context(self, **_kwargs):
                return object()

            def execute_persona(self, *, persona_execution, **_kwargs):
                if persona_execution.persona_id == "practical":
                    release.wait(timeout=1)
                return self_result(persona_execution)

        state = _FakeStateStore()
        coordinator = self._coordinator(
            run=run,
            executions=executions,
            pipeline_factory=Pipeline,
            state=state,
            persistence=_FakePersistencePipeline(),
            timeout_seconds=0.03,
        )
        try:
            result = coordinator.execute(
                run=run,
                persona_executions=executions,
                context={},
                analysis=Mock(),
            )
        finally:
            release.set()

        self.assertTrue(result.partial_failure)
        self.assertEqual(result.recommendation_result_ids, ("result-minimal",))
        timeout = result.failures[0]
        self.assertEqual(timeout.persona_id, "practical")
        self.assertEqual(timeout.error_code, "STYLIST_PERSONA_TIMEOUT")
        self.assertEqual(timeout.latency_ms, 30)

    def test_retry_executes_only_requested_persona(self) -> None:
        run, executions = self._scope(("minimal", "practical"))
        target = executions[1]
        state = _FakeStateStore()
        persistence = _FakePersistencePipeline()
        coordinator = self._coordinator(
            run=run,
            executions=executions,
            pipeline_factory=self._successful_factory(),
            state=state,
            persistence=persistence,
        )

        result = coordinator.execute_retry(
            run=run,
            persona_execution=target,
            context={"original": True},
            analysis=Mock(),
        )

        self.assertEqual(result.recommendation_result_ids, ("result-practical",))
        self.assertEqual(state.running, ("execution-practical",))
        self.assertEqual(set(state.succeeded), {"execution-practical"})
        self.assertEqual(len(persistence.calls), 1)

    def test_alternative_skips_current_duplicate_and_persists_next_candidate(
        self,
    ) -> None:
        run, executions = self._scope(("minimal",))
        duplicate = _composition(top="top-1", bottom="bottom-1", shoes="shoes-1")
        distinct = _composition(top="top-2", bottom="bottom-2", shoes="shoes-2")

        class Pipeline:
            def build_context(self, **_kwargs):
                return object()

            def execute_persona(self, *, persona_execution, **_kwargs):
                return self_result(
                    persona_execution,
                    compositions=(duplicate, distinct),
                )

        state = _FakeStateStore()
        persistence = _FakePersistencePipeline()
        coordinator = self._coordinator(
            run=run,
            executions=executions,
            pipeline_factory=Pipeline,
            state=state,
            persistence=persistence,
        )

        coordinator.execute_alternative(
            run=run,
            persona_execution=executions[0],
            context={},
            analysis=Mock(),
            excluded_compositions=(duplicate,),
        )

        call = persistence.calls[0]
        self.assertEqual(call["selected"][0].composition, distinct)
        self.assertEqual(
            call["result_type"],
            RecommendationResult.ResultType.ALTERNATIVE,
        )
        self.assertTrue(call["replace_current"])
        self.assertIn("STYLIST_ALTERNATIVE_DISTINCT", call["validated_reason_codes"])

    @staticmethod
    def _scope(persona_ids: tuple[str, ...]):
        run = SimpleNamespace(
            pk="run-1",
            response_mode=ChatSession.ResponseMode.STYLIST,
            persona_ids=list(persona_ids),
        )
        executions = tuple(
            SimpleNamespace(
                pk=f"execution-{persona_id}",
                run_id=run.pk,
                persona_id=persona_id,
                status="PENDING",
                strategy_snapshot={"persona_id": persona_id},
            )
            for persona_id in persona_ids
        )
        return run, executions

    @staticmethod
    def _successful_factory():
        class Pipeline:
            def build_context(self, **_kwargs):
                return object()

            def execute_persona(self, *, persona_execution, **_kwargs):
                return self_result(persona_execution)

        return Pipeline

    @staticmethod
    def _coordinator(
        *,
        run,
        executions,
        pipeline_factory,
        state,
        persistence,
        timeout_seconds: float = 1,
    ) -> StylistExecutionCoordinator:
        execution_by_id = {str(row.pk): row for row in executions}
        return StylistExecutionCoordinator(
            pipeline_factory=pipeline_factory,
            persistence_pipeline=persistence,
            duplicate_resolver=_FakeDuplicateResolver(),
            state_store=state,
            scope_loader=lambda _run_id, execution_id: (
                run,
                execution_by_id[execution_id],
            ),
            timeout_seconds=timeout_seconds,
        )


def self_result(execution, *, compositions=()):
    plan = StrategyPlan(
        search_query=f"{execution.persona_id} query",
        preference_adjustments=(),
        candidate_limit=3,
        sort_rules=(SortRule(SortMetric.ORIGINAL_ORDER, SortDirection.ASC),),
    )
    return PersonaRecommendationCandidates(
        persona_id=execution.persona_id,
        persona_execution_id=str(execution.pk),
        generated=SimpleNamespace(run_id="run-1"),
        strategy_result=SimpleNamespace(plan=plan),
        hypothesis_snapshot={},
        ranked_candidates=tuple(
            SimpleNamespace(
                candidate=SimpleNamespace(composition=composition),
            )
            for composition in compositions
        ),
        hypothesis_usage=SimpleNamespace(),
        hypothesis_response_id="",
    )


def _composition(*, top: str, bottom: str, shoes: str) -> OutfitComposition:
    values = (("TOP", top), ("BOTTOM", bottom), ("FOOTWEAR", shoes))
    return OutfitComposition(
        mode=RecommendationMode.NEW_ITEM,
        items=tuple(
            OutfitItem(
                slot_id=slot,
                template_point_id=f"template-{slot}",
                category_large=slot,
                layer_role="",
                source_type=ItemSource.PRODUCT,
                source_id=source_id,
                source_collection="products",
                point_id=source_id,
                image_ref="",
                price=10000,
                score=0.9,
                reasons=(),
            )
            for slot, source_id in values
        ),
        missing_slot_ids=(),
        total_product_price=30000,
    )
