"""OpenAI 판단과 결정적 추천 파이프라인을 연결하는 채팅 오케스트레이터."""

from __future__ import annotations

import logging
import time
from typing import Any
from copy import deepcopy
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.chat.models import (
    ChatAttachment,
    ChatIdentity,
    ChatMessage,
    ChatRun,
    ChatRunPersona,
    ChatSession,
)
from apps.chat.services import mood_analysis, stylist_results
from apps.chat.services.alternative_recommendations import (
    load_alternative_exclusions,
)
from apps.chat.services.context import ChatContextService, fingerprint
from apps.chat.services.openai_adapter import (
    ChatLLMError,
    LLMUsage,
    OpenAIChatAdapter,
    TurnAnalysis,
)
from apps.chat.services.persona_narration import (
    PersonaNarrationItem,
    PersonaNarrationRequest,
    PersonaNarrationService,
    RuleBasedPersonaNarrator,
    build_persona_narration_service,
)
from apps.chat.services.personalization_snapshot import (
    build_personalization_snapshot,
)
from apps.chat.services.recommendation_explanations import (
    apply_recommendation_explanation,
)
from apps.chat.services.recommendation_pipeline import (
    ChatRecommendationError,
    ChatRecommendationPipeline,
)
from apps.chat.services.reference_recommendation_events import (
    STAGE_SNAPSHOT_VALIDATION,
    ReferenceRecommendationEventRecorder,
)
from apps.chat.services.response_text import normalize_assistant_text
from apps.chat.services.sessions import ChatSessionForbidden, append_message
from apps.chat.services.shared_reference import (
    SharedReferenceError,
    build_reference_snapshot,
)
from apps.chat.services.stylist_execution import (
    StylistExecutionCoordinator,
    StylistExecutionError,
    StylistExecutionResult,
)
from apps.chat.services.stylist_personas import load_stylist_personas
from apps.chat.services.wardrobe_scope import build_wardrobe_scope_snapshot
from apps.recommend.models import OutfitComposition, RecommendationResult
from apps.recommend.services import principle_rules
from apps.recommend.services.retriever import retrieve_principles

logger = logging.getLogger(__name__)


def _principle_styles_from_payload(approved: dict[str, Any]) -> list[str]:
    """승인된 코디의 아이템 attributes에서 스타일 값을 모은다.

    코디 자체에는 스타일 필드가 없고 아이템 태그에만 있다. 여러 코디의 스타일을
    합쳐 한 번만 조회한다 — 코디마다 따로 부르면 Qdrant 호출이 코디 수만큼 늘고,
    승인된 원칙이 53건뿐이라 그렇게 나눌 실익이 없다.
    """
    styles: list[str] = []
    for outfit in approved.get("compositions", []) or []:
        for item in outfit.get("items", []) or []:
            value = (item.get("attributes") or {}).get("style")
            values = value if isinstance(value, list) else [value]
            for entry in values:
                text = str(entry or "").strip()
                if text and text not in styles:
                    styles.append(text)
    return styles


def _slot_attributes_from_payload(
    approved: dict[str, Any],
) -> dict[str, dict[str, str]]:
    """승인된 코디의 아이템에서 슬롯별 속성을 뽑는다."""
    slots: dict[str, dict[str, str]] = {}
    for outfit in approved.get("compositions", []) or []:
        for item in outfit.get("items", []) or []:
            payload = dict(item.get("attributes") or {})
            payload.setdefault("title", item.get("name") or "")
            slot = principle_rules.slot_of(payload)
            if slot and slot not in slots:
                slots[slot] = principle_rules.extract_attributes(payload)
    return slots


def _confirmed_principles(approved: dict[str, Any]) -> list[dict[str, Any]]:
    """이 코디에 **실제로 해당하는** 원칙. 조건을 대조해 확정한 것만 돌려준다.

    벡터 검색은 "비슷한 원칙"을 주므로 LLM이 다시 판단해야 하고, 실제로 잘 하지
    못했다. 조건 대조는 해당 여부를 코드가 정하므로 LLM에게 판단을 맡기지 않는다.
    """
    try:
        slots = _slot_attributes_from_payload(approved)
        if not slots:
            return []
        styles = _principle_styles_from_payload(approved)
        outcomes = principle_rules.evaluate(
            principle_rules.rules_for_styles(styles), slots
        )
        return [
            {
                "statement": outcome.rule.statement,
                "styles": [outcome.rule.cluster_id],
                "confirmed": True,
                "matched_conditions": outcome.matched,
            }
            for outcome in outcomes
            if not outcome.violations
        ][:3]
    except Exception:
        logger.warning("원칙 조건 대조 실패, 검색으로 넘어간다.", exc_info=True)
        return []


def _principle_context(
    approved: dict[str, Any],
    current_request: str,
    conditions: dict[str, Any],
) -> list[dict[str, Any]]:
    """설명 LLM에 넘길 원칙 참고 자료. 실패하거나 없으면 빈 목록.

    골든셋은 아직 1차 사이클이라 스타일에 따라 승인된 원칙이 없을 수 있다. 그때
    추천을 막지 않고 원칙 없이 설명하도록, 여기서 예외를 바깥으로 올리지 않는다.
    """
    confirmed = _confirmed_principles(approved)
    if confirmed:
        return confirmed
    try:
        query = " ".join(
            part
            for part in (
                current_request,
                str(conditions.get("style") or ""),
                str(conditions.get("occasion") or ""),
            )
            if part
        ).strip()
        if not query:
            return []
        principles = retrieve_principles(
            query=query,
            styles=_principle_styles_from_payload(approved),
            limit=5,
        )
        return [principle.as_prompt_context() for principle in principles]
    except Exception:
        logger.warning("원칙 조회 실패, 원칙 없이 설명한다.", exc_info=True)
        return []


class ChatOrchestrationError(RuntimeError):
    code = "CHAT_RECOMMENDATION_FAILED"


class ChatRunAlreadyProcessing(ChatOrchestrationError):
    code = "CHAT_RUN_ALREADY_PROCESSING"


class ChatRunInvalid(ChatOrchestrationError):
    code = "CHAT_RUN_INVALID"


class ChatQueueUnavailable(ChatOrchestrationError):
    code = "CHAT_QUEUE_UNAVAILABLE"


@dataclass(frozen=True)
class OrchestrationResult:
    run: ChatRun
    response_message: ChatMessage
    recommendation_result_id: str | None = None
    recommendation_result_ids: tuple[str, ...] = ()


def _session_run_snapshot(
    session: ChatSession,
    *,
    identity: ChatIdentity,
) -> dict[str, object]:
    persona_ids = list(session.selected_persona_ids)
    catalog = load_stylist_personas()
    try:
        session.full_clean()
        persona_versions = catalog.versions(persona_ids)
    except (ValidationError, ValueError) as exc:
        raise ChatRunInvalid(
            "세션의 스타일리스트 선택 상태가 올바르지 않습니다."
        ) from exc
    return {
        "response_mode": session.response_mode,
        "persona_ids": persona_ids,
        "persona_versions": persona_versions,
        "persona_prompt_versions": {
            persona_id: catalog.get(persona_id).prompt_version
            for persona_id in persona_ids
        },
        "stylist_config_version": catalog.schema_version,
        "personalization_snapshot": build_personalization_snapshot(
            identity=identity,
            captured_at=timezone.now(),
        ),
    }


def _strategy_snapshot(persona) -> dict[str, object]:
    profile = persona.strategy_profile
    return {
        "objectives": list(profile.objectives),
        "search_directives": list(profile.search_directives),
        "score_weights": [
            {"metric": row.metric, "weight": row.weight}
            for row in profile.score_weights
        ],
        "hypothesis_count": profile.hypothesis_count,
    }


def _create_persona_executions(run: ChatRun) -> None:
    if run.response_mode != ChatSession.ResponseMode.STYLIST:
        return

    catalog = load_stylist_personas()
    rows = []
    for persona_id in run.persona_ids:
        persona = catalog.get(persona_id)
        rows.append(
            ChatRunPersona(
                run=run,
                persona_id=persona_id,
                persona_version=run.persona_versions[persona_id],
                prompt_version=run.persona_prompt_versions[persona_id],
                display_order=persona.display_order,
                strategy_snapshot=_strategy_snapshot(persona),
            )
        )
    ChatRunPersona.objects.bulk_create(rows)


@transaction.atomic
def submit_message_and_create_run(
    *,
    identity: ChatIdentity,
    session_id,
    content: str,
    client_message_id: str,
    metadata: dict | None = None,
    reference: dict[str, object] | None = None,
    wardrobe_scope: dict[str, object] | None = None,
) -> tuple[ChatMessage, bool, ChatRun, bool]:
    """메시지와 실행 스냅샷을 한 트랜잭션에서 멱등 생성한다."""

    message, message_created = append_message(
        identity=identity,
        session_id=session_id,
        role=ChatMessage.Role.USER,
        content=content,
        status=ChatMessage.Status.PENDING,
        client_message_id=client_message_id,
        metadata=metadata,
    )
    run, run_created = create_run(
        identity=identity,
        session_id=session_id,
        request_message_id=message.id,
        reference=reference,
        wardrobe_scope=wardrobe_scope,
    )
    return message, message_created, run, run_created


@transaction.atomic
def create_run(
    *,
    identity: ChatIdentity,
    session_id,
    request_message_id,
    reference: dict[str, object] | None = None,
    wardrobe_scope: dict[str, object] | None = None,
) -> tuple[ChatRun, bool]:
    """사용자 메시지당 실행을 하나만 만들고 큐 재전송을 멱등 처리한다."""
    session = (
        ChatSession.objects.select_for_update()
        .filter(
            pk=session_id,
            identity=identity,
            deleted_at__isnull=True,
        )
        .first()
    )
    if session is None:
        raise ChatSessionForbidden("채팅 세션에 접근할 수 없습니다.")
    message = (
        ChatMessage.objects.select_for_update()
        .filter(
            pk=request_message_id,
            session=session,
        )
        .first()
    )
    if message is None:
        raise ChatSessionForbidden("채팅 메시지에 접근할 수 없습니다.")
    if message.role != ChatMessage.Role.USER:
        raise ChatRunInvalid("사용자 메시지만 채팅 실행을 시작할 수 있습니다.")

    existing = ChatRun.objects.filter(request_message=message).first()
    if existing is not None:
        return existing, False

    snapshot = _session_run_snapshot(session, identity=identity)
    if reference:
        rejection_recorder = ReferenceRecommendationEventRecorder(
            # 권한 단계에서는 ChatRun이 아직 만들어지지 않았다. request_id는 로깅
            # context filter가 붙이므로 가짜 run UUID를 저장하지 않는다.
            run_id="",
            recommendation_mode=str(session.mode),
            is_stylist=(
                session.response_mode == ChatSession.ResponseMode.STYLIST
            ),
        )
        try:
            with rejection_recorder.measure(STAGE_SNAPSHOT_VALIDATION):
                snapshot["reference_snapshot"] = build_reference_snapshot(
                    identity=identity,
                    reference=reference,
                )
        except SharedReferenceError as exc:
            rejection_recorder.failure(exc)
            raise
    else:
        snapshot["reference_snapshot"] = {}
    snapshot["wardrobe_scope_snapshot"] = build_wardrobe_scope_snapshot(
        identity=identity,
        scope=wardrobe_scope,
    )

    try:
        run, created = ChatRun.objects.get_or_create(
            request_message=message,
            defaults={
                "session": session,
                "status": ChatRun.Status.PENDING,
                "provider": OpenAIChatAdapter.provider,
                "model": settings.CHAT_OPENAI_MODEL,
                "prompt_version": settings.CHAT_PROMPT_VERSION,
                **snapshot,
            },
        )
    except IntegrityError:
        run = ChatRun.objects.get(request_message=message)
        created = False
    if created:
        _create_persona_executions(run)
    if created and message.status != ChatMessage.Status.PENDING:
        message.status = ChatMessage.Status.PENDING
        message.save(update_fields=["status", "updated_at"])
    return run, created


@transaction.atomic
def mark_enqueue_failed(run_id) -> ChatRun | None:
    """DB 접수 후 Redis 적재에 실패한 실행을 무한 대기 대신 실패로 종료한다."""
    run = ChatRun.objects.select_for_update().filter(pk=run_id).first()
    if run is None or run.status != ChatRun.Status.PENDING:
        return run
    now = timezone.now()
    run.status = ChatRun.Status.FAILED
    run.error_code = ChatQueueUnavailable.code
    run.error_message = "채팅 실행 큐에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."
    run.completed_at = now
    run.save(
        update_fields=[
            "status",
            "error_code",
            "error_message",
            "completed_at",
            "updated_at",
        ]
    )
    ChatMessage.objects.filter(pk=run.request_message_id).update(
        status=ChatMessage.Status.FAILED,
        updated_at=now,
    )
    ChatAttachment.objects.filter(message_id=run.request_message_id).exclude(
        analysis_status=ChatAttachment.AnalysisStatus.SUCCEEDED
    ).update(analysis_status=ChatAttachment.AnalysisStatus.FAILED)
    return run


@transaction.atomic
def reset_run_for_retry(run_id) -> bool:
    """단일 워커가 실패·중단된 실행을 같은 ID로 안전하게 재시도하게 만든다."""
    run = ChatRun.objects.select_for_update().filter(pk=run_id).first()
    if run is None or run.status not in {ChatRun.Status.RUNNING, ChatRun.Status.FAILED}:
        return False
    if run.response_message_id is not None:
        return False
    now = timezone.now()
    run.status = ChatRun.Status.PENDING
    run.context_fingerprint = ""
    run.context_cache_hit = False
    run.provider_response_id = ""
    run.input_tokens = 0
    run.cached_input_tokens = 0
    run.output_tokens = 0
    run.latency_ms = 0
    run.error_code = ""
    run.error_message = ""
    run.started_at = None
    run.completed_at = None
    run.save(
        update_fields=[
            "status",
            "context_fingerprint",
            "context_cache_hit",
            "provider_response_id",
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "latency_ms",
            "error_code",
            "error_message",
            "started_at",
            "completed_at",
            "updated_at",
        ]
    )
    ChatMessage.objects.filter(pk=run.request_message_id).update(
        status=ChatMessage.Status.PENDING,
        updated_at=now,
    )
    ChatAttachment.objects.filter(
        message_id=run.request_message_id,
        analysis_status__in={
            ChatAttachment.AnalysisStatus.PROCESSING,
            ChatAttachment.AnalysisStatus.FAILED,
        },
    ).update(analysis_status=ChatAttachment.AnalysisStatus.QUEUED)
    ChatRunPersona.objects.filter(run=run).update(
        status=ChatRunPersona.Status.PENDING,
        latency_ms=0,
        error_code="",
        error_message="",
        started_at=None,
        completed_at=None,
        updated_at=now,
    )
    return True


class ChatOrchestrator:
    """Redis 큐 워커가 실행 ID로 호출하는 동기 오케스트레이션 코어."""

    def __init__(
        self,
        *,
        context_service: ChatContextService | None = None,
        llm: OpenAIChatAdapter | None = None,
        recommendation_pipeline: ChatRecommendationPipeline | None = None,
        stylist_coordinator: StylistExecutionCoordinator | None = None,
        persona_narration_service: PersonaNarrationService | None = None,
    ) -> None:
        self.context_service = context_service or ChatContextService()
        self.llm = llm or OpenAIChatAdapter()
        self.recommendation_pipeline = (
            recommendation_pipeline or ChatRecommendationPipeline()
        )
        self.stylist_coordinator = stylist_coordinator or StylistExecutionCoordinator(
            persistence_pipeline=self.recommendation_pipeline,
        )
        self.persona_narration_service = persona_narration_service

    def process(self, run_id) -> OrchestrationResult:
        started = time.monotonic()
        run = self._start(run_id)
        usage = LLMUsage()
        provider_response_id = ""
        context_fingerprint = ""
        context_cache_hit = False
        attachment = run.request_message.attachments.first()
        try:
            if attachment is not None:
                return self._process_photo_mood(
                    run=run,
                    attachment=attachment,
                    started=started,
                )
            context = self.context_service.build(
                session=run.session,
                request_message=run.request_message,
                current_run=run,
            )
            context_fingerprint = context.fingerprint
            context_cache_hit = context.cache_hit
            analyzed = self.llm.analyze_turn(
                identity_id=str(run.session.identity_id),
                context=context.payload,
            )
            usage += analyzed.usage
            provider_response_id = analyzed.response_id
            analysis = self._effective_analysis(run.session, analyzed.value)
            self._update_session_conditions(run.session, analysis)
            context_fingerprint = fingerprint(
                {
                    "initial": context.fingerprint,
                    "extracted_conditions": analysis.conditions.model_dump(),
                    "action": analysis.action,
                    "target_mode": analysis.target_mode,
                }
            )

            recommendation_result_id = None
            recommendation_result_ids: tuple[str, ...] = ()
            final_status = ChatRun.Status.SUCCEEDED
            response_text: str
            response_metadata: dict = {"run_id": str(run.id)}

            if self._requests_mode_change(run.session.mode, analysis):
                self._discard_unstarted_persona_executions(run)
                final_status = ChatRun.Status.NEEDS_CLARIFICATION
                response_text = (
                    analysis.response_text.strip()
                    or "추천 모드를 바꾸려면 현재 조건을 이어받은 새 채팅을 만들어 주세요."
                )
                response_metadata["target_mode"] = analysis.target_mode
            elif analysis.action == "CLARIFY":
                self._discard_unstarted_persona_executions(run)
                final_status = ChatRun.Status.NEEDS_CLARIFICATION
                response_text = (
                    analysis.clarification_question.strip()
                    or "추천에 필요한 상황이나 조건을 조금 더 알려주세요."
                )
            elif analysis.action == "RESPOND":
                self._discard_unstarted_persona_executions(run)
                response_text = (
                    analysis.response_text.strip()
                    or "패션 추천과 관련해 궁금한 조건을 알려주세요."
                )
            else:
                if run.response_mode == ChatSession.ResponseMode.STYLIST:
                    stylist_execution = self.stylist_coordinator.execute(
                        run=run,
                        persona_executions=tuple(run.persona_executions.all()),
                        context=context.payload,
                        analysis=analysis,
                    )
                    usage += self._apply_persona_narrations(stylist_execution)
                    recommendation_result_ids = (
                        stylist_execution.recommendation_result_ids
                    )
                    response_metadata["recommendation_result_ids"] = list(
                        recommendation_result_ids
                    )
                    response_metadata["stylist_results"] = (
                        stylist_results.message_metadata_results(run)
                    )
                    response_text = (
                        "완료된 스타일리스트 추천부터 확인해 주세요. "
                        "일부 추천은 처리하지 못했어요."
                        if stylist_execution.partial_failure
                        else "선택한 스타일리스트의 추천이 준비됐어요."
                    )
                else:
                    pipeline_result = self.recommendation_pipeline.execute(
                        run=run,
                        context=context.payload,
                        analysis=analysis,
                    )
                    explanation = None
                    explanation_fallback_reason = ""
                    try:
                        explained = self.llm.explain_recommendation(
                            identity_id=str(run.session.identity_id),
                            persona=context.payload["persona"],
                            mode=run.session.mode,
                            approved_recommendation=pipeline_result.approved_payload,
                            current_request=context.payload["current_request"],
                            recent_messages=context.payload["recent_messages"],
                            weather=context.payload["weather"],
                            budget=analysis.conditions.budget,
                            conditions=analysis.conditions.model_dump(),
                            principles=_principle_context(
                                pipeline_result.approved_payload,
                                context.payload["current_request"],
                                analysis.conditions.model_dump(),
                            ),
                        )
                        usage += explained.usage
                        provider_response_id = explained.response_id
                        explanation = explained.value
                    except ChatLLMError as exc:
                        explanation_fallback_reason = str(
                            getattr(exc, "code", ChatLLMError.code)
                        )
                        logger.warning(
                            "일반 추천 설명 생성 실패, 규칙 폴백 사용: code=%s",
                            explanation_fallback_reason,
                        )
                    applied_explanation = apply_recommendation_explanation(
                        result=pipeline_result.result,
                        explanation=explanation,
                        mode=run.session.mode,
                        budget=analysis.conditions.budget,
                        conditions=analysis.conditions.model_dump(),
                        weather=context.payload["weather"],
                        recent_messages=context.payload["recent_messages"],
                        fallback_reason=explanation_fallback_reason,
                    )
                    # 설명이 규칙 폴백으로 떨어져도 런은 SUCCEEDED다. 그래서
                    # 예전에는 10번 중 10번 폴백이 나도 지표상 정상으로 보였고,
                    # 화면을 직접 볼 때까지 아무도 몰랐다. 실행에 남겨 셀 수
                    # 있게 한다 — ChatRunSerializer 필드 목록에 없으므로
                    # 클라이언트 응답에는 실리지 않는다.
                    if applied_explanation.fallback_used:
                        run.degradation = {
                            **(run.degradation or {}),
                            "explanation_fallback": True,
                            "explanation_fallback_reason": (
                                applied_explanation.fallback_reason
                            ),
                        }
                        run.save(update_fields=["degradation", "updated_at"])
                        logger.warning(
                            "일반 추천 설명이 규칙 폴백으로 나갔습니다: run=%s reason=%s",
                            run.pk,
                            applied_explanation.fallback_reason,
                            extra={
                                "run_id": str(run.pk),
                                "session_id": str(run.session_id),
                                "fallback_reason": applied_explanation.fallback_reason,
                            },
                        )
                    response_text = applied_explanation.opening
                    recommendation_result_id = str(pipeline_result.result.id)
                    recommendation_result_ids = (recommendation_result_id,)
                    response_metadata["recommendation_result_id"] = (
                        recommendation_result_id
                    )

            response_text = normalize_assistant_text(response_text)
            response_message, _ = append_message(
                identity=run.session.identity,
                session_id=run.session_id,
                role=ChatMessage.Role.ASSISTANT,
                content=response_text,
                status=ChatMessage.Status.COMPLETED,
                client_message_id=f"run:{run.pk}:response",
                metadata=response_metadata,
            )
            summary_usage = self._maybe_refresh_summary(run.session)
            usage += summary_usage
            duration_ms = int((time.monotonic() - started) * 1000)
            self._finish(
                run=run,
                status=final_status,
                response_message=response_message,
                context_fingerprint=context_fingerprint,
                context_cache_hit=context_cache_hit,
                usage=usage,
                provider_response_id=provider_response_id,
                latency_ms=duration_ms,
            )
            run.refresh_from_db()
            logger.info(
                "채팅 실행 완료: run=%s status=%s latency=%sms cache_hit=%s",
                run.pk,
                run.status,
                duration_ms,
                context_cache_hit,
                extra={
                    "run_id": str(run.pk),
                    "session_id": str(run.session_id),
                    "result_id": recommendation_result_id,
                    "status": run.status,
                    "duration_ms": duration_ms,
                    "cache_hit": context_cache_hit,
                },
            )
            return OrchestrationResult(
                run=run,
                response_message=response_message,
                recommendation_result_id=recommendation_result_id,
                recommendation_result_ids=recommendation_result_ids,
            )
        except Exception as exc:
            if attachment is not None:
                mood_analysis.mark_analysis_failed(attachment.pk)
            duration_ms = int((time.monotonic() - started) * 1000)
            self._fail(
                run=run,
                exc=exc,
                usage=usage,
                provider_response_id=provider_response_id,
                latency_ms=duration_ms,
                context_fingerprint=context_fingerprint,
                context_cache_hit=context_cache_hit,
            )
            logger.error(
                "채팅 실행 종료 실패: run=%s code=%s latency=%sms",
                run.pk,
                getattr(exc, "code", type(exc).__name__),
                duration_ms,
                extra={
                    "run_id": str(run.pk),
                    "session_id": str(run.session_id),
                    "status": ChatRun.Status.FAILED,
                    "duration_ms": duration_ms,
                    "error_code": getattr(exc, "code", type(exc).__name__),
                },
            )
            raise

    def process_persona_retry(
        self,
        *,
        run_id,
        persona_id: str,
        retry_count: int,
    ) -> OrchestrationResult:
        """원본 run과 메시지를 유지하며 실패한 스타일리스트 한 명만 재실행한다."""

        started = time.monotonic()
        run, execution = self._start_persona_retry(
            run_id=run_id,
            persona_id=persona_id,
            retry_count=retry_count,
        )
        usage = LLMUsage()
        provider_response_id = ""
        try:
            context = self.context_service.build(
                session=run.session,
                request_message=run.request_message,
                current_run=run,
            )
            analyzed = self.llm.analyze_turn(
                identity_id=str(run.session.identity_id),
                context=context.payload,
            )
            usage += analyzed.usage
            provider_response_id = analyzed.response_id
            analysis = self._effective_analysis(run.session, analyzed.value)
            analysis = analysis.model_copy(
                update={"action": "RECOMMEND", "target_mode": "CURRENT"}
            )
            retried = self.stylist_coordinator.execute_retry(
                run=run,
                persona_execution=execution,
                context=context.payload,
                analysis=analysis,
            )
            usage += self._apply_persona_narrations(retried)
            response_message = self._upsert_stylist_response_message(run)
            duration_ms = int((time.monotonic() - started) * 1000)
            now = timezone.now()
            ChatRun.objects.filter(pk=run.pk).update(
                status=ChatRun.Status.SUCCEEDED,
                response_message=response_message,
                provider_response_id=provider_response_id,
                input_tokens=run.input_tokens + usage.input_tokens,
                cached_input_tokens=(
                    run.cached_input_tokens + usage.cached_input_tokens
                ),
                output_tokens=run.output_tokens + usage.output_tokens,
                latency_ms=run.latency_ms + duration_ms,
                error_code="",
                error_message="",
                completed_at=now,
                updated_at=now,
            )
            run.refresh_from_db()
            return OrchestrationResult(
                run=run,
                response_message=response_message,
                recommendation_result_ids=retried.recommendation_result_ids,
            )
        except Exception as exc:
            self._fail_persona_retry(
                run=run,
                execution=execution,
                exc=exc,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            raise

    def process_persona_alternative(
        self,
        *,
        run_id,
        persona_id: str,
        source_result_id: str,
        generation: int,
    ) -> OrchestrationResult:
        """현재 결과를 보존한 채 대상 스타일리스트의 다른 추천만 생성한다."""

        started = time.monotonic()
        run, execution = self._start_persona_alternative(
            run_id=run_id,
            persona_id=persona_id,
            source_result_id=source_result_id,
            generation=generation,
        )
        usage = LLMUsage()
        provider_response_id = ""
        try:
            exclusions = load_alternative_exclusions(
                run=run,
                persona_execution=execution,
            )
            context = self.context_service.build(
                session=run.session,
                request_message=run.request_message,
                current_run=run,
            )
            analyzed = self.llm.analyze_turn(
                identity_id=str(run.session.identity_id),
                context=context.payload,
            )
            usage += analyzed.usage
            provider_response_id = analyzed.response_id
            analysis = self._effective_analysis(run.session, analyzed.value).model_copy(
                update={"action": "RECOMMEND", "target_mode": "CURRENT"}
            )
            generated = self.stylist_coordinator.execute_alternative(
                run=run,
                persona_execution=execution,
                context=context.payload,
                analysis=analysis,
                excluded_compositions=exclusions,
            )
            usage += self._apply_persona_narrations(generated)
            response_message = self._upsert_stylist_response_message(run)
            duration_ms = int((time.monotonic() - started) * 1000)
            now = timezone.now()
            ChatRunPersona.objects.filter(pk=execution.pk).update(
                alternative_status=ChatRunPersona.AlternativeStatus.SUCCEEDED,
                alternative_error_code="",
                alternative_error_message="",
                updated_at=now,
            )
            ChatRun.objects.filter(pk=run.pk).update(
                status=ChatRun.Status.SUCCEEDED,
                response_message=response_message,
                provider_response_id=provider_response_id,
                input_tokens=run.input_tokens + usage.input_tokens,
                cached_input_tokens=run.cached_input_tokens + usage.cached_input_tokens,
                output_tokens=run.output_tokens + usage.output_tokens,
                latency_ms=run.latency_ms + duration_ms,
                error_code="",
                error_message="",
                completed_at=now,
                updated_at=now,
            )
            run.refresh_from_db()
            return OrchestrationResult(
                run=run,
                response_message=response_message,
                recommendation_result_ids=generated.recommendation_result_ids,
            )
        except Exception as exc:
            self._fail_persona_alternative(
                run=run,
                execution=execution,
                exc=exc,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            raise

    @staticmethod
    @transaction.atomic
    def _start_persona_alternative(
        *,
        run_id,
        persona_id: str,
        source_result_id: str,
        generation: int,
    ) -> tuple[ChatRun, ChatRunPersona]:
        # nullable 관계(user, response_message)를 JOIN한 쿼리에 FOR UPDATE를 적용하면
        # PostgreSQL이 outer join의 nullable side 잠금을 거부한다. 상태 전이의 기준인
        # ChatRun 한 행만 먼저 잠그고, 필요한 관계는 같은 트랜잭션에서 별도로 읽는다.
        locked_run = ChatRun.objects.select_for_update().filter(pk=run_id).first()
        run = (
            ChatRun.objects.select_related(
                "session",
                "session__identity",
                "session__identity__user",
                "request_message",
                "response_message",
            )
            .filter(pk=locked_run.pk)
            .first()
            if locked_run is not None
            else None
        )
        if run is None:
            raise ChatRunInvalid("채팅 실행을 찾을 수 없습니다.")
        if (
            run.response_mode != ChatSession.ResponseMode.STYLIST
            or run.status != ChatRun.Status.PENDING
        ):
            raise ChatRunAlreadyProcessing(
                "현재 상태에서는 다른 추천을 시작할 수 없습니다."
            )
        execution = (
            ChatRunPersona.objects.select_for_update()
            .filter(
                run=run,
                persona_id=persona_id,
                status=ChatRunPersona.Status.PENDING,
                alternative_status=ChatRunPersona.AlternativeStatus.PENDING,
            )
            .first()
        )
        if execution is None or generation < 2:
            raise ChatRunAlreadyProcessing(
                "현재 결과 세대와 다른 추천 큐 스냅샷이 일치하지 않습니다."
            )
        current_exists = RecommendationResult.objects.filter(
            pk=source_result_id,
            persona_execution=execution,
            is_current=True,
            generation=generation - 1,
        ).exists()
        if not current_exists:
            raise ChatRunAlreadyProcessing(
                "현재 결과 세대와 다른 추천 큐 스냅샷이 일치하지 않습니다."
            )
        now = timezone.now()
        run.status = ChatRun.Status.RUNNING
        run.started_at = now
        run.completed_at = None
        run.save(update_fields=["status", "started_at", "completed_at", "updated_at"])
        execution.alternative_status = ChatRunPersona.AlternativeStatus.RUNNING
        execution.save(update_fields=["alternative_status", "updated_at"])
        return run, execution

    @staticmethod
    @transaction.atomic
    def _start_persona_retry(
        *,
        run_id,
        persona_id: str,
        retry_count: int,
    ) -> tuple[ChatRun, ChatRunPersona]:
        now = timezone.now()
        run = (
            ChatRun.objects.select_for_update()
            .select_related(
                "session",
                "session__identity",
                "session__identity__user",
                "request_message",
                "response_message",
            )
            .filter(pk=run_id)
            .first()
        )
        if run is None:
            raise ChatRunInvalid("채팅 실행을 찾을 수 없습니다.")
        if (
            run.response_mode != ChatSession.ResponseMode.STYLIST
            or run.status != ChatRun.Status.PENDING
        ):
            raise ChatRunAlreadyProcessing(
                "현재 상태에서는 해당 스타일리스트 재실행을 시작할 수 없습니다."
            )
        execution = (
            ChatRunPersona.objects.select_for_update()
            .filter(
                run=run,
                persona_id=persona_id,
                status=ChatRunPersona.Status.PENDING,
                retry_count=retry_count,
            )
            .first()
        )
        if execution is None:
            raise ChatRunAlreadyProcessing(
                "현재 상태에서는 해당 스타일리스트 재실행을 시작할 수 없습니다."
            )
        run.status = ChatRun.Status.RUNNING
        run.started_at = now
        run.completed_at = None
        run.save(update_fields=["status", "started_at", "completed_at", "updated_at"])
        return run, execution

    def _upsert_stylist_response_message(self, run: ChatRun) -> ChatMessage:
        current = stylist_results.with_stylist_results(
            ChatRun.objects.select_related("response_message")
        ).get(pk=run.pk)
        snapshots = stylist_results.message_metadata_results(current)
        result_ids = [
            row["result_id"] for row in snapshots if row["result_id"] is not None
        ]
        has_failure = any(
            row["status"] == ChatRunPersona.Status.FAILED for row in snapshots
        )
        content = (
            "완료된 스타일리스트 추천부터 확인해 주세요. 일부 추천은 처리하지 못했어요."
            if has_failure
            else "선택한 스타일리스트의 추천이 준비됐어요."
        )
        metadata = {
            "run_id": str(run.pk),
            "recommendation_result_ids": result_ids,
            "stylist_results": snapshots,
        }
        if current.response_message_id:
            ChatMessage.objects.filter(pk=current.response_message_id).update(
                content=content,
                metadata=metadata,
                status=ChatMessage.Status.COMPLETED,
                updated_at=timezone.now(),
            )
            current.response_message.refresh_from_db()
            return current.response_message
        response_message, _ = append_message(
            identity=run.session.identity,
            session_id=run.session_id,
            role=ChatMessage.Role.ASSISTANT,
            content=content,
            status=ChatMessage.Status.COMPLETED,
            client_message_id=f"run:{run.pk}:response",
            metadata=metadata,
        )
        return response_message

    @staticmethod
    def _fail_persona_retry(
        *,
        run: ChatRun,
        execution: ChatRunPersona,
        exc: Exception,
        latency_ms: int,
    ) -> None:
        now = timezone.now()
        error_code = str(getattr(exc, "code", ChatOrchestrationError.code))[:64]
        safe_message = (
            str(exc)[:500]
            if isinstance(
                exc,
                (
                    ChatLLMError,
                    ChatRecommendationError,
                    ChatOrchestrationError,
                    StylistExecutionError,
                ),
            )
            else "스타일리스트 추천 처리 중 내부 오류가 발생했습니다."
        )
        ChatRunPersona.objects.filter(
            pk=execution.pk,
            status__in=(
                ChatRunPersona.Status.PENDING,
                ChatRunPersona.Status.RUNNING,
            ),
        ).update(
            status=ChatRunPersona.Status.FAILED,
            latency_ms=max(latency_ms, 0),
            error_code=error_code,
            error_message=safe_message,
            completed_at=now,
            updated_at=now,
        )
        has_success = ChatRunPersona.objects.filter(
            run=run,
            status=ChatRunPersona.Status.SUCCEEDED,
        ).exists()
        ChatRun.objects.filter(pk=run.pk).update(
            status=(ChatRun.Status.SUCCEEDED if has_success else ChatRun.Status.FAILED),
            error_code=("" if has_success else error_code),
            error_message=("" if has_success else safe_message),
            latency_ms=run.latency_ms + max(latency_ms, 0),
            completed_at=now,
            updated_at=now,
        )

    @staticmethod
    def _fail_persona_alternative(
        *,
        run: ChatRun,
        execution: ChatRunPersona,
        exc: Exception,
        latency_ms: int,
    ) -> None:
        now = timezone.now()
        error_code = str(getattr(exc, "code", ChatOrchestrationError.code))[:64]
        safe_message = (
            str(exc)[:500]
            if isinstance(
                exc,
                (
                    ChatLLMError,
                    ChatRecommendationError,
                    ChatOrchestrationError,
                    StylistExecutionError,
                ),
            )
            else "다른 추천 처리 중 내부 오류가 발생했습니다."
        )
        ChatRunPersona.objects.filter(pk=execution.pk).update(
            status=ChatRunPersona.Status.SUCCEEDED,
            error_code="",
            error_message="",
            alternative_status=ChatRunPersona.AlternativeStatus.FAILED,
            alternative_error_code=error_code,
            alternative_error_message=safe_message,
            completed_at=now,
            updated_at=now,
        )
        ChatRun.objects.filter(pk=run.pk).update(
            status=ChatRun.Status.SUCCEEDED,
            error_code="",
            error_message="",
            latency_ms=run.latency_ms + max(latency_ms, 0),
            completed_at=now,
            updated_at=now,
        )

    def _process_photo_mood(
        self,
        *,
        run: ChatRun,
        attachment: ChatAttachment,
        started: float,
    ) -> OrchestrationResult:
        processed = mood_analysis.process_attachment(
            attachment=attachment,
            identity_id=str(run.session.identity_id),
            llm=self.llm,
        )
        tags = processed.analysis_result["tags"]
        response_text = (
            f"사진에서 {', '.join(tags)} 무드가 보여요. "
            "이 분위기를 추천 조건에 반영할까요?"
        )
        response_message, _ = append_message(
            identity=run.session.identity,
            session_id=run.session_id,
            role=ChatMessage.Role.ASSISTANT,
            content=response_text,
            status=ChatMessage.Status.COMPLETED,
            client_message_id=f"run:{run.pk}:response",
            metadata={
                "run_id": str(run.pk),
                "message_kind": "mood",
                "attachment_id": str(attachment.pk),
                "mood_analysis": processed.analysis_result,
            },
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        self._finish(
            run=run,
            status=ChatRun.Status.SUCCEEDED,
            response_message=response_message,
            context_fingerprint=fingerprint(
                {
                    "attachment_id": str(attachment.pk),
                    "sha256": attachment.sha256,
                    "analysis_result": processed.analysis_result,
                }
            ),
            context_cache_hit=False,
            usage=processed.usage,
            provider_response_id=processed.response_id,
            latency_ms=duration_ms,
        )
        run.refresh_from_db()
        logger.info(
            "채팅 사진 무드 분석 완료: run=%s attachment=%s latency=%sms",
            run.pk,
            attachment.pk,
            duration_ms,
        )
        return OrchestrationResult(
            run=run,
            response_message=response_message,
        )

    def _apply_persona_narrations(
        self,
        execution: StylistExecutionResult,
    ) -> LLMUsage:
        usage = LLMUsage()
        catalog = load_stylist_personas()
        service = self.persona_narration_service
        if service is None:
            try:
                service = build_persona_narration_service(
                    openai_chat_adapter=self.llm,
                )
            except Exception:  # noqa: BLE001 - 설명 실패는 추천 성공과 격리한다.
                logger.warning(
                    "페르소나 말투 서비스 초기화 실패, 규칙 fallback 사용",
                    exc_info=True,
                )

        for success in execution.successes:
            result = success.persisted.result
            composition = (
                result.compositions.filter(
                    status=OutfitComposition.Status.VALIDATED,
                )
                .prefetch_related("items")
                .order_by("rank", "created_at")
                .first()
            )
            if composition is None:
                continue
            items = tuple(
                PersonaNarrationItem(
                    slot=item.slot,
                    name=self._item_display_name(item.item_snapshot, item.slot),
                )
                for item in composition.items.all()
            )
            request = PersonaNarrationRequest(
                persona_id=success.persona_id,
                outfit_id=str(composition.pk),
                items=items,
                reason_codes=tuple(result.validated_reason_codes),
                voice_profile=catalog.get(success.persona_id).voice_profile,
            )
            try:
                if service is None:
                    raise RuntimeError("페르소나 말투 제공자가 초기화되지 않았습니다.")
                narrated = service.generate(request)
            except Exception:  # noqa: BLE001 - 말투 실패는 추천 성공과 격리한다.
                logger.warning(
                    "페르소나 설명 생성 실패, 규칙 fallback 사용: persona=%s",
                    success.persona_id,
                    exc_info=True,
                )
                narrated = RuleBasedPersonaNarrator().generate(
                    request,
                    requested_provider=settings.PERSONA_LLM_PROVIDER,
                    reason="PERSONA_NARRATION_FAILED",
                )
            narrated_message = normalize_assistant_text(narrated.message)
            RecommendationResult.objects.filter(pk=result.pk).update(
                persona_explanation=narrated_message,
            )
            result.persona_explanation = narrated_message
            usage += narrated.usage
        return usage

    @staticmethod
    def _item_display_name(snapshot: object, fallback: str) -> str:
        if isinstance(snapshot, dict):
            for key in ("display_name", "item_name", "product_name", "name", "title"):
                value = snapshot.get(key)
                if value not in (None, ""):
                    return str(value)
        return fallback

    @staticmethod
    def _discard_unstarted_persona_executions(run: ChatRun) -> None:
        ChatRunPersona.objects.filter(
            run=run,
            status=ChatRunPersona.Status.PENDING,
        ).delete()

    @staticmethod
    def _start(run_id) -> ChatRun:
        now = timezone.now()
        updated = ChatRun.objects.filter(
            pk=run_id,
            status=ChatRun.Status.PENDING,
        ).update(
            status=ChatRun.Status.RUNNING,
            started_at=now,
            completed_at=None,
            error_code="",
            error_message="",
            updated_at=now,
        )
        run = (
            ChatRun.objects.select_related(
                "session",
                "session__identity",
                "session__identity__user",
                "request_message",
            )
            .filter(pk=run_id)
            .first()
        )
        if run is None:
            raise ChatRunInvalid("채팅 실행을 찾을 수 없습니다.")
        if not updated:
            raise ChatRunAlreadyProcessing(
                f"현재 상태({run.status})에서는 실행을 시작할 수 없습니다."
            )
        ChatMessage.objects.filter(pk=run.request_message_id).update(
            status=ChatMessage.Status.PROCESSING,
            updated_at=now,
        )
        run.request_message.status = ChatMessage.Status.PROCESSING
        return run

    @staticmethod
    def _requests_mode_change(current_mode: str, analysis: TurnAnalysis) -> bool:
        return analysis.action == "MODE_CHANGE" or analysis.target_mode not in {
            "CURRENT",
            current_mode,
        }

    @staticmethod
    def _effective_analysis(
        session: ChatSession,
        analysis: TurnAnalysis,
    ) -> TurnAnalysis:
        """현재 발화에 없는 조건은 승인된 사진을 포함한 세션 조건에서 보충한다."""
        saved = dict(
            (session.context_state or {}).get("recommendation_conditions") or {}
        )
        conditions = analysis.conditions.model_dump()
        for key, value in conditions.items():
            if value in (None, "", []) and saved.get(key) not in (None, "", []):
                conditions[key] = saved[key]
        return analysis.model_copy(
            update={"conditions": analysis.conditions.model_copy(update=conditions)}
        )

    @staticmethod
    def _update_session_conditions(
        session: ChatSession,
        analysis: TurnAnalysis,
    ) -> None:
        state = deepcopy(session.context_state or {})
        current = dict(state.get("recommendation_conditions") or {})
        extracted = analysis.conditions.model_dump()
        for key, value in extracted.items():
            if value not in (None, "", []):
                current[key] = value
        state["recommendation_conditions"] = current
        session.context_state = state
        session.save(update_fields=["context_state", "updated_at"])

    def _maybe_refresh_summary(self, session: ChatSession) -> LLMUsage:
        session.refresh_from_db()
        last_sequence = (
            session.messages.order_by("-sequence")
            .values_list("sequence", flat=True)
            .first()
        )
        if not last_sequence or last_sequence < settings.CHAT_SUMMARY_TRIGGER_MESSAGES:
            return LLMUsage()
        through_sequence = max(
            0,
            last_sequence - settings.CHAT_CONTEXT_RECENT_MESSAGES,
        )
        if through_sequence <= session.summary_through_sequence:
            return LLMUsage()
        messages = list(
            session.messages.filter(
                sequence__gt=session.summary_through_sequence,
                sequence__lte=through_sequence,
            ).values("sequence", "role", "content")
        )
        if not messages:
            return LLMUsage()
        try:
            persona = session.persona_profile
            persona_payload = (
                {
                    "name": persona.name,
                    "version": persona.version,
                    "prompt_config": persona.prompt_config,
                }
                if persona is not None
                else {}
            )
            summarized = self.llm.summarize_conversation(
                identity_id=str(session.identity_id),
                persona=persona_payload,
                previous_summary=session.conversation_summary,
                messages=messages,
            )
        except ChatLLMError as exc:
            logger.warning("대화 요약 갱신 생략: %s", exc.code)
            return LLMUsage()
        session.conversation_summary = summarized.value.summary.strip()
        session.summary_through_sequence = through_sequence
        session.save(
            update_fields=[
                "conversation_summary",
                "summary_through_sequence",
                "updated_at",
            ]
        )
        return summarized.usage

    @staticmethod
    def _finish(
        *,
        run: ChatRun,
        status: str,
        response_message: ChatMessage,
        context_fingerprint: str,
        context_cache_hit: bool,
        usage: LLMUsage,
        provider_response_id: str,
        latency_ms: int,
    ) -> None:
        now = timezone.now()
        ChatRun.objects.filter(pk=run.pk).update(
            status=status,
            response_message=response_message,
            context_fingerprint=context_fingerprint,
            context_cache_hit=context_cache_hit,
            provider=OpenAIChatAdapter.provider,
            model=settings.CHAT_OPENAI_MODEL,
            prompt_version=settings.CHAT_PROMPT_VERSION,
            provider_response_id=provider_response_id,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=max(latency_ms, 0),
            error_code="",
            error_message="",
            completed_at=now,
            updated_at=now,
        )
        ChatMessage.objects.filter(pk=run.request_message_id).update(
            status=ChatMessage.Status.COMPLETED,
            updated_at=now,
        )

    @staticmethod
    def _fail(
        *,
        run: ChatRun,
        exc: Exception,
        usage: LLMUsage,
        provider_response_id: str,
        latency_ms: int,
        context_fingerprint: str,
        context_cache_hit: bool,
    ) -> None:
        now = timezone.now()
        error_code = getattr(exc, "code", ChatOrchestrationError.code)
        if isinstance(
            exc,
            (
                ChatLLMError,
                ChatRecommendationError,
                ChatOrchestrationError,
                StylistExecutionError,
                mood_analysis.ChatMoodError,
            ),
        ):
            safe_message = str(exc)[:500]
        else:
            safe_message = "채팅 추천 처리 중 내부 오류가 발생했습니다."
        ChatRun.objects.filter(pk=run.pk).update(
            status=ChatRun.Status.FAILED,
            context_fingerprint=context_fingerprint,
            context_cache_hit=context_cache_hit,
            provider_response_id=provider_response_id,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=max(latency_ms, 0),
            error_code=error_code,
            error_message=safe_message,
            completed_at=now,
            updated_at=now,
        )
        ChatMessage.objects.filter(pk=run.request_message_id).update(
            status=ChatMessage.Status.FAILED,
            updated_at=now,
        )
        ChatRunPersona.objects.filter(
            run=run,
            status__in=(
                ChatRunPersona.Status.PENDING,
                ChatRunPersona.Status.RUNNING,
            ),
        ).update(
            status=ChatRunPersona.Status.FAILED,
            error_code=str(error_code)[:64],
            error_message=safe_message,
            completed_at=now,
            updated_at=now,
        )
        logger.warning(
            "채팅 실행 실패: code=%s type=%s", error_code, type(exc).__name__
        )
