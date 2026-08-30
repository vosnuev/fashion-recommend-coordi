"""스타일리스트별 검색·조합·검증·재정렬을 독립 실행하는 서비스."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.utils import timezone

from apps.chat.models import ChatRun, ChatRunPersona, ChatSession
from apps.chat.services.experimental_hypothesis_fallback import (
    ExperimentalHypothesisResolver,
    ResolvedExperimentalHypotheses,
)
from apps.chat.services.experimental_stylist_strategy import (
    ExperimentalStylistStrategy,
)
from apps.chat.services.minimal_stylist_strategy import MinimalStylistStrategy
from apps.chat.services.openai_adapter import LLMUsage, TurnAnalysis
from apps.chat.services.recommendation_diversity import select_diverse_candidates
from apps.chat.services.practical_stylist_strategy import PracticalStylistStrategy
from apps.chat.services.recommendation_pipeline import (
    ChatRecommendationPipeline,
    GeneratedRecommendationCandidates,
    ValidatedRecommendationCandidate,
)
from apps.chat.services.stylist_candidate_adapter import (
    build_strategy_candidate_view,
    build_stylist_strategy_context,
)
from apps.chat.services.stylist_personas import strategy_profile_from_snapshot
from apps.chat.services.stylist_strategy import (
    CandidateStrategyEvaluation,
    StrategyExecutionResult,
    StylistStrategy,
    StylistStrategyContext,
    StylistStrategyContractError,
    StylistStrategyRunner,
)
from apps.recommend.services.outfit_types import OutfitComposition

STYLIST_VALIDATED_TOP_K = 3


class StylistRecommendationPipelineError(RuntimeError):
    """스타일리스트별 추천 실행 범위나 전략 결과가 올바르지 않을 때 발생한다."""


@dataclass(frozen=True)
class RankedValidatedCandidate:
    """전략 평가와 실제 검증 후보를 ordinal로 다시 연결한 내부 결과."""

    candidate: ValidatedRecommendationCandidate
    evaluation: CandidateStrategyEvaluation

    @property
    def composition(self) -> OutfitComposition:
        """공통 핵심 슬롯 다양성 선택기가 읽는 조합 계약."""

        return self.candidate.composition

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (
                    *(row.reason_code for row in self.evaluation.score_adjustments),
                    self.evaluation.history_distance.reason_code,
                )
            )
        )


@dataclass(frozen=True)
class PersonaRecommendationCandidates:
    """한 스타일리스트가 독립 실행해 반환한 저장 전 유효 Top-K."""

    persona_id: str
    persona_execution_id: str
    generated: GeneratedRecommendationCandidates
    strategy_result: StrategyExecutionResult
    ranked_candidates: tuple[RankedValidatedCandidate, ...]
    hypothesis_snapshot: dict[str, Any]
    hypothesis_usage: LLMUsage
    hypothesis_response_id: str


class StylistRecommendationPipeline:
    """공통 컨텍스트를 재조회하지 않고 페르소나별 파이프라인을 새로 실행한다."""

    def __init__(
        self,
        *,
        recommendation_pipeline: ChatRecommendationPipeline | None = None,
        strategy_runner: StylistStrategyRunner | None = None,
        hypothesis_resolver: ExperimentalHypothesisResolver | None = None,
    ) -> None:
        self.recommendation_pipeline = (
            recommendation_pipeline or ChatRecommendationPipeline()
        )
        self.strategy_runner = strategy_runner or StylistStrategyRunner()
        self.hypothesis_resolver = hypothesis_resolver or (
            ExperimentalHypothesisResolver()
        )

    @staticmethod
    def build_context(
        *,
        run: ChatRun,
        context: dict[str, Any],
        analysis: TurnAnalysis,
    ) -> StylistStrategyContext:
        return build_stylist_strategy_context(
            context=context,
            analysis=analysis,
            recommendation_mode=run.session.mode,
        )

    def execute_persona(
        self,
        *,
        run: ChatRun,
        persona_execution: ChatRunPersona,
        context: dict[str, Any],
        analysis: TurnAnalysis,
        strategy_context: StylistStrategyContext | None = None,
        top_k: int = STYLIST_VALIDATED_TOP_K,
    ) -> PersonaRecommendationCandidates:
        self._validate_scope(
            run=run,
            persona_execution=persona_execution,
            top_k=top_k,
        )
        shared_context = strategy_context or self.build_context(
            run=run,
            context=context,
            analysis=analysis,
        )
        strategy, hypotheses = self._strategy(
            run=run,
            persona_execution=persona_execution,
            context=context,
        )
        plan = strategy.build_plan(shared_context)
        generated = self.recommendation_pipeline.generate_candidates(
            run=run,
            context=context,
            analysis=analysis,
            strategy_plan=plan,
            # 직전 턴에 쓴 골든 템플릿은 뺀다. 현재 실행은 제외 대상이 아니라
            # 페르소나끼리는 서로를 막지 않는다 (recent_golden_ids 주석 참고).
            exclude_golden_ids=self.recommendation_pipeline.recent_golden_ids(run),
        )
        candidate_views = tuple(
            build_strategy_candidate_view(
                candidate=candidate,
                strategy_context=shared_context,
                raw_context=context,
                total_budget=analysis.conditions.budget,
            )
            for candidate in generated.candidates
        )
        strategy_result = self.strategy_runner.run(
            strategy=strategy,
            context=shared_context,
            candidates=candidate_views,
        )
        if strategy_result.plan != plan:
            raise StylistStrategyContractError(
                "동일 실행에서 스타일리스트 검색 계획이 변경되었습니다."
            )

        candidate_by_ordinal = {
            candidate.ordinal: candidate for candidate in generated.candidates
        }
        ranked = tuple(
            RankedValidatedCandidate(
                candidate=candidate_by_ordinal[evaluation.candidate_ordinal],
                evaluation=evaluation,
            )
            for evaluation in strategy_result.ranked_candidates
        )
        # 전략 점수 상위가 같은 골든 템플릿의 액세서리 변형으로 몰리면 persona 간
        # 중복 해소기가 볼 수 있는 대안도 사라진다. 전체 전략 순위를 유지하면서
        # 핵심 슬롯이 다른 후보를 Top-K에 먼저 남긴다.
        ranked = select_diverse_candidates(
            ranked,
            limit=top_k,
        )
        return PersonaRecommendationCandidates(
            persona_id=persona_execution.persona_id,
            persona_execution_id=str(persona_execution.pk),
            generated=generated,
            strategy_result=strategy_result,
            ranked_candidates=ranked,
            hypothesis_snapshot=(hypotheses.snapshot() if hypotheses else {}),
            hypothesis_usage=(hypotheses.usage if hypotheses else LLMUsage()),
            hypothesis_response_id=(hypotheses.response_id if hypotheses else ""),
        )

    def _strategy(
        self,
        *,
        run: ChatRun,
        persona_execution: ChatRunPersona,
        context: dict[str, Any],
    ) -> tuple[StylistStrategy, ResolvedExperimentalHypotheses | None]:
        profile = strategy_profile_from_snapshot(persona_execution.strategy_snapshot)
        if persona_execution.persona_id == "minimal":
            return MinimalStylistStrategy(profile), None
        if persona_execution.persona_id == "practical":
            return PracticalStylistStrategy(profile), None
        if persona_execution.persona_id != "experimental":
            raise StylistRecommendationPipelineError(
                "지원하지 않는 스타일리스트 실행입니다."
            )

        hypotheses = self.hypothesis_resolver.resolve(
            identity_id=str(run.session.identity_id),
            context=context,
        )
        snapshot = hypotheses.snapshot()
        updated_at = timezone.now()
        ChatRunPersona.objects.filter(pk=persona_execution.pk).update(
            hypothesis_snapshot=snapshot,
            updated_at=updated_at,
        )
        persona_execution.hypothesis_snapshot = snapshot
        persona_execution.updated_at = updated_at
        return ExperimentalStylistStrategy(
            hypotheses=hypotheses,
            profile=profile,
        ), hypotheses

    @staticmethod
    def _validate_scope(
        *,
        run: ChatRun,
        persona_execution: ChatRunPersona,
        top_k: int,
    ) -> None:
        if run.response_mode != ChatSession.ResponseMode.STYLIST:
            raise StylistRecommendationPipelineError(
                "스타일리스트별 추천은 STYLIST 응답 실행에서만 지원합니다."
            )
        if persona_execution.run_id != run.pk:
            raise StylistRecommendationPipelineError(
                "스타일리스트 실행이 대상 ChatRun에 속하지 않습니다."
            )
        if persona_execution.persona_id not in run.persona_ids:
            raise StylistRecommendationPipelineError(
                "ChatRun 스냅샷에 없는 스타일리스트입니다."
            )
        if (
            isinstance(top_k, bool)
            or not isinstance(top_k, int)
            or not 1 <= top_k <= STYLIST_VALIDATED_TOP_K
        ):
            raise StylistRecommendationPipelineError(
                f"스타일리스트 Top-K는 1~{STYLIST_VALIDATED_TOP_K} 범위여야 합니다."
            )
