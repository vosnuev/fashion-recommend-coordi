"""오케스트레이터가 기존 Retriever·Composer·Validator를 호출하는 경계."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, replace
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Max

from apps.chat.models import ChatRun, ChatRunPersona, ChatSession
from apps.chat.services.openai_adapter import TurnAnalysis
from apps.chat.services.recommendation_diversity import (
    DEFAULT_CORE_DIVERSITY_SLOTS,
    select_diverse_candidates,
)
from apps.chat.services.reference_recommendation_events import (
    STAGE_COMPOSER,
    STAGE_VALIDATOR,
    ReferenceRecommendationEventRecorder,
)
from apps.chat.services.stylist_strategy import (
    PreferencePolarity,
    StrategyPlan,
)
from apps.recommend.models import (
    GoldenTemplateSnapshot,
    OutfitCompositionItem,
    RecommendationResult,
)
from apps.recommend.models import (
    OutfitComposition as OutfitCompositionModel,
)
from apps.recommend.services import render_jobs
from apps.recommend.services.body_profile import BodyProfile, build_profile
from apps.recommend.services.item_retriever import (
    ItemCandidateRetriever,
    ItemRetrievalRequest,
    ItemSource,
    occasion_skipped_smalls,
)
from apps.recommend.services.new_item_composer import (
    NewItemCompositionRequest,
    NewItemOutfitComposer,
)
from apps.recommend.services.outfit_types import (
    OutfitComposition as DomainOutfitComposition,
)
from apps.recommend.services.outfit_types import (
    RecommendationMode,
)
from apps.recommend.services.retriever import (
    GoldenOutfitRetriever,
    occasion_kind_tags,
    OutfitCandidate,
    RetrievalRequest,
    RetrievalResult,
    normalize_presentation_groups,
)
from apps.recommend.services.shared_reference_anchor import (
    PinnedReferenceAnchor,
    SharedReferenceAnchorResolver,
    composition_contains_anchor,
    pin_reference_anchor,
)
from apps.recommend.services.text_embedding import TextEmbeddingConfigurationError
from apps.recommend.services import vocabulary
from apps.recommend.services.validator import (
    OutfitValidationResult,
    OutfitValidator,
    ReferenceValidationContract,
    ValidationContext,
    ValidationSeverity,
)
from apps.recommend.services.wardrobe_composer import (
    WardrobeCompositionRequest,
    WardrobeOutfitComposer,
)
from apps.recommend.services.wardrobe_link import (
    accessible_item_ids,
    owned_closet_item_ids,
)


class ChatRecommendationError(RuntimeError):
    code = "CHAT_RECOMMENDATION_FAILED"


logger = logging.getLogger(__name__)


class GoldenOutfitNotFound(ChatRecommendationError):
    code = "GOLDEN_OUTFIT_NOT_FOUND"


class OutfitCompositionFailed(ChatRecommendationError):
    code = "OUTFIT_COMPOSITION_FAILED"


class WardrobeOutfitUnavailable(OutfitCompositionFailed):
    """옷장 아이템만으로 검증 가능한 코디를 완성할 수 없는 상태."""

    code = "WARDROBE_OUTFIT_UNAVAILABLE"


WARDROBE_OUTFIT_UNAVAILABLE_MESSAGE = (
    "코디를 완성하기에 옷장에 준비된 옷이 부족해요. "
    "옷을 조금 더 추가하면 어울리는 조합을 추천해드릴게요."
)


@dataclass(frozen=True)
class RecommendationPipelineResult:
    result: RecommendationResult
    approved_payload: dict[str, Any]


@dataclass(frozen=True)
class ValidatedRecommendationCandidate:
    """DB에 저장하기 전 Validator를 통과한 코디 한 건."""

    ordinal: int
    template_rank: int
    composition_rank: int
    golden: OutfitCandidate
    composition: DomainOutfitComposition
    validation: OutfitValidationResult


@dataclass(frozen=True)
class GeneratedRecommendationCandidates:
    """한 ChatRun 범위에서 생성된 저장 전 추천 후보 묶음."""

    run_id: str
    session_id: str
    identity_id: str
    response_mode: str
    mode: str
    search_mode: str
    candidates: tuple[ValidatedRecommendationCandidate, ...]


class ChatRecommendationPipeline:
    """LLM 판단 뒤 결정적 추천 컴포넌트만 순서대로 실행한다."""

    def __init__(
        self,
        *,
        golden_retriever: GoldenOutfitRetriever | None = None,
        item_retriever: ItemCandidateRetriever | None = None,
        wardrobe_composer: WardrobeOutfitComposer | None = None,
        new_item_composer: NewItemOutfitComposer | None = None,
        validator: OutfitValidator | None = None,
        reference_anchor_resolver: SharedReferenceAnchorResolver | None = None,
        diversity_slots: Collection[str] = DEFAULT_CORE_DIVERSITY_SLOTS,
    ) -> None:
        self.golden_retriever = golden_retriever or GoldenOutfitRetriever()
        self.item_retriever = item_retriever or ItemCandidateRetriever()
        self.wardrobe_composer = wardrobe_composer or WardrobeOutfitComposer()
        self.new_item_composer = new_item_composer or NewItemOutfitComposer()
        self.validator = validator or OutfitValidator()
        self.reference_anchor_resolver = (
            reference_anchor_resolver or SharedReferenceAnchorResolver()
        )
        self.diversity_slots = tuple(diversity_slots)

    def _retrieve_golden(self, request: RetrievalRequest) -> RetrievalResult:
        """골든 코디를 찾는다. 질의 임베딩을 못 쓰면 필터 검색으로 내려간다.

        채팅은 항상 질의문을 넘기므로 리트리버가 텍스트 검색 모드를 고르고, 그러려면 외부
        임베딩 서비스(TEXT_EMBEDDING_API_URL)가 있어야 한다. 그 설정이 비어 있으면
        TextEmbeddingConfigurationError 가 나는데, 이건 RuntimeError 라서 아래 후보 루프의
        `except (ValueError, RuntimeError)` 에도 걸리지 않고 그대로 run 을 죽인다.
        그러면 "채팅 추천 처리 중 내부 오류" 한 줄만 남고, 워커는 해결될 리 없는 요청을
        두 번 더 재시도한다.

        임베딩이 없어도 추천을 아예 못 하는 것은 아니다. query_text 를 비우면 리트리버가
        필터 검색으로 동작하고, 체형·추구미·성별·계절 조건은 그대로 살아 있다. 의미 검색이
        빠져 문장의 뉘앙스 반영은 약해지지만, 핵심 기능이 외부 서비스 하나 때문에 통째로
        멈추는 것보다는 낫다는 판단이다.

        ⚠️ 이건 바닥이지 대체가 아니다. 임베딩 서비스가 설정되면 이 경로는 자동으로 쓰이지
           않는다. 이 경로를 탔는지는 로그와 결과의 search_mode('filter')로 확인한다.
        ⚠️ 설정이 **없는** 경우만 내려간다. 서비스가 있는데 일시적으로 실패한 것이라면
           그대로 올려보내 재시도되게 둔다 — 잠깐의 장애 때문에 추천 품질을 낮추지 않는다.
        """
        try:
            return self.golden_retriever.retrieve(request)
        except TextEmbeddingConfigurationError:
            logger.warning(
                "질의 임베딩 설정이 없어 골든 코디를 필터 검색으로 찾는다 "
                "(TEXT_EMBEDDING_API_URL·TEXT_EMBEDDING_API_TOKEN 미설정)"
            )
            return self.golden_retriever.retrieve(replace(request, query_text=""))

    def execute(
        self,
        *,
        run: ChatRun,
        context: dict[str, Any],
        analysis: TurnAnalysis,
    ) -> RecommendationPipelineResult:
        """일반 모드 후보를 생성하고 핵심 슬롯 다양성을 적용해 저장한다."""

        if run.response_mode != ChatSession.ResponseMode.DEFAULT:
            raise OutfitCompositionFailed(
                "스타일리스트 응답은 후보를 생성한 뒤 개별 실행별로 저장해야 합니다."
            )
        existing = RecommendationResult.objects.filter(
            run=run,
            response_mode=RecommendationResult.ResponseMode.DEFAULT,
        ).first()
        if existing is not None:
            self._schedule_render_on_commit(run=run, result_id=existing.pk)
            return RecommendationPipelineResult(
                result=existing,
                approved_payload=self._approved_payload(existing),
            )

        generated = self.generate_candidates(
            run=run,
            context=context,
            analysis=analysis,
            # 기존 기본 추천은 첫 번째로 성공한 골든 템플릿의 조합만 저장했다.
            max_validated_templates=1,
            exclude_golden_ids=self.recent_golden_ids(run),
        )
        selected = select_diverse_candidates(
            generated.candidates,
            diversity_slots=self.diversity_slots,
            limit=3,
        )
        if len(selected) < min(3, len(generated.candidates)):
            logger.info(
                "핵심 슬롯이 같은 일반 추천 후보를 제외함: "
                "run=%s generated=%s selected=%s slots=%s",
                run.pk,
                len(generated.candidates),
                len(selected),
                self.diversity_slots,
            )
        return self.persist_candidates(
            run=run,
            generated=generated,
            selected=selected,
        )

    def generate_candidates(
        self,
        *,
        run: ChatRun,
        context: dict[str, Any],
        analysis: TurnAnalysis,
        max_validated_templates: int | None = None,
        strategy_plan: StrategyPlan | None = None,
        exclude_golden_ids: frozenset[str] | None = None,
    ) -> GeneratedRecommendationCandidates:
        """레퍼런스 추천이면 성공·실패를 포함한 운영 이벤트를 실행당 한 번 남긴다.

        exclude_golden_ids: 검색에서 뺄 골든 코디 id. 호출부가 조회해 넘긴다 —
        파이프라인이 직접 DB를 읽으면 스타일리스트가 페르소나마다 같은 질의를
        반복하고, DB 없이 도는 단위 테스트도 못 쓰게 된다.
        """

        recorder = (
            ReferenceRecommendationEventRecorder(
                run_id=str(run.pk),
                recommendation_mode=str(run.session.mode),
                is_stylist=(
                    run.response_mode == ChatSession.ResponseMode.STYLIST
                ),
            )
            if getattr(run, "reference_snapshot", None)
            else None
        )
        try:
            generated = self._generate_candidates(
                run=run,
                context=context,
                analysis=analysis,
                max_validated_templates=max_validated_templates,
                strategy_plan=strategy_plan,
                event_recorder=recorder,
                exclude_golden_ids=exclude_golden_ids or frozenset(),
            )
        except Exception as exc:
            if recorder is not None:
                recorder.failure(exc)
            raise
        if recorder is not None:
            recorder.success()
        return generated

    def _generate_candidates(
        self,
        *,
        run: ChatRun,
        context: dict[str, Any],
        analysis: TurnAnalysis,
        max_validated_templates: int | None = None,
        strategy_plan: StrategyPlan | None = None,
        event_recorder: ReferenceRecommendationEventRecorder | None = None,
        exclude_golden_ids: frozenset[str] = frozenset(),
    ) -> GeneratedRecommendationCandidates:
        """Retriever·Composer·Validator를 실행하고 DB 저장 전 후보를 반환한다."""

        if max_validated_templates is not None and (
            isinstance(max_validated_templates, bool)
            or not isinstance(max_validated_templates, int)
            or max_validated_templates < 1
        ):
            raise ValueError("max_validated_templates는 1 이상의 정수여야 합니다.")

        session = run.session
        user_id = session.identity.user_id
        if session.mode == ChatSession.Mode.WARDROBE_BASED and user_id is None:
            raise OutfitCompositionFailed(
                "옷장 기반 추천은 회원의 확정된 옷장 아이템이 필요합니다."
            )
        scope_snapshot = getattr(run, "wardrobe_scope_snapshot", None) or {}
        scoped_item_ids = tuple(scope_snapshot.get("candidate_item_ids") or ())
        allowed_wardrobe_item_ids = (
            tuple(
                owned_closet_item_ids(session.identity.user)
                if scoped_item_ids
                else accessible_item_ids(session.identity.user)
            )
            if user_id is not None
            else None
        )
        if (
            session.mode == ChatSession.Mode.WARDROBE_BASED
            and not allowed_wardrobe_item_ids
        ):
            raise WardrobeOutfitUnavailable(
                WARDROBE_OUTFIT_UNAVAILABLE_MESSAGE
            )

        pursuit = self._merged_pursuit(context, analysis)
        if strategy_plan is not None:
            pursuit = self._apply_strategy_preferences(pursuit, strategy_plan)
        requested = self._requested_conditions(analysis)
        candidate_limit = strategy_plan.candidate_limit if strategy_plan else 5
        body = build_profile(context.get("profile", {}).get("body"))
        category_budgets = context.get("profile", {}).get("category_budgets", {})
        total_budget = analysis.conditions.budget
        reference_anchor = self._resolve_reference_anchor(
            run=run,
            user_id=user_id,
            total_budget=total_budget,
            category_budgets=category_budgets,
            event_recorder=event_recorder,
        )
        if reference_anchor is not None and event_recorder is not None:
            event_recorder.select_match(
                match_result=reference_anchor.match_type,
                similarity=reference_anchor.candidate.score,
            )
        retrieval = self._retrieve_golden(
            RetrievalRequest(
                body=body,
                pursuit=pursuit,
                requested=requested,
                weather=context.get("weather"),
                gender=self._gender(context),
                occasion=analysis.conditions.occasion,
                season=analysis.conditions.season,
                query_text=(
                    strategy_plan.search_query
                    if strategy_plan is not None
                    else analysis.search_query or context["current_request"]
                ),
                presentation_groups=self._presentation_groups(
                    context=context,
                    analysis=analysis,
                ),
                required_item_categories=(
                    (reference_anchor.reference.tags.category_large,)
                    if reference_anchor is not None
                    else ()
                ),
                required_item_layer_roles=(
                    (reference_anchor.reference.tags.layer_role,)
                    if reference_anchor is not None
                    else ()
                ),
                dataset_version=settings.CHAT_GOLDENSET_DATASET_VERSION,
                dataset_statuses=settings.CHAT_GOLDENSET_DATASET_STATUSES,
                limit=candidate_limit,
                hard_filter=True,
                # 같은 세션에서 직전에 쓴 골든 템플릿은 뺀다. 요청 스타일은
                # 하드 필터가 아니라 가산점이라(style_rules.preference_match),
                # 스타일만 바꿔 다시 물으면 같은 템플릿이 그대로 1위로 남고
                # 아이템 검색은 템플릿 아이템 벡터만 보므로 코디가 통째로
                # 재현됐다. 후보가 전부 제외되면 retriever가 제외를 풀고
                # 돌려주므로 '추천 없음'으로 떨어지지는 않는다.
                exclude_golden_ids=exclude_golden_ids,
                # 요청 TPO를 골든 코디 선택의 실질 조건으로 건다. 예전에는 맞으면
                # +10, 틀려도 감점 0이라 출근 요청에 휴양 코디가 1순위로 올라왔다.
                occasion_kinds=occasion_kind_tags(analysis.conditions.occasion_kind),
                # 후보가 이 수 미만이면 완화 사다리를 오른다(최근 제외 → TPO →
                # 축적 기피 순). 성별·발화 조건·예산은 풀지 않는다.
                relax_below=settings.CHAT_GOLDEN_RELAX_AVOIDED_BELOW,
                # 골든 코디는 내부 조합 템플릿이다. 원본 이미지 노출 권한은
                # 결과 표출·렌더링 경계에서 별도로 검사한다.
                exposable_only=False,
            )
        )
        if not retrieval.candidates:
            raise GoldenOutfitNotFound("조건에 맞는 골든 코디를 찾지 못했습니다.")

        # 골든 코디 검색에만 걸려 있던 조건을 아이템 후보 검색에도 넘긴다.
        # 기피는 축적·발화 양쪽을 합친다 — 어느 쪽이든 "빼달라"는 말이다.
        avoided_labels: dict[str, set[str]] = {}
        for source in (pursuit.get("avoided"), requested.get("avoided")):
            for tag_field, labels in vocabulary.translate(source).tags.items():
                avoided_labels.setdefault(tag_field, set()).update(labels)
        avoided_tags = {
            tag_field: tuple(sorted(labels))
            for tag_field, labels in avoided_labels.items()
        }
        # 요청 조건은 아이템 선택 단계까지 가야 한다. 여기까지 오지 않으면
        # 아이템은 템플릿 벡터 유사도로만 뽑혀, 골든 코디가 바뀌어도 슬롯을
        # 채우는 옷은 요청과 무관하게 정해진다.
        requested_tags = {
            tag_field: tuple(sorted(labels))
            for tag_field, labels in vocabulary.translate(
                requested.get("preferred")
            ).tags.items()
        }

        generated: list[ValidatedRecommendationCandidate] = []
        validated_template_count = 0
        # 어느 단계에서 몇 건이 떨어졌는지 남긴다. 이게 없으면 실패가
        # "조합을 만들지 못했습니다" 한 줄로만 남아 원인을 추적할 수 없다.
        failure_reasons: Counter[str] = Counter()
        for template_rank, candidate in enumerate(retrieval.candidates, start=1):
            template_ids = self._template_item_ids(candidate)
            # 격식 자리에서는 모자·선글라스 슬롯을 **검색조차 하지 않는다.**
            # 채우면 반드시 무언가가 들어가므로, 안 어울리는 자리에서는 자리를
            # 없애는 것이 유일하게 확실한 방법이다 (usage 태그로는 못 막는다).
            template_ids = self._drop_skipped_slots(
                candidate,
                template_ids,
                occasion_skipped_smalls(analysis.conditions.occasion_kind),
            )
            if not template_ids:
                continue
            try:
                def retrieve_slot(point_id):
                    request_kwargs = dict(
                        template_item_point_id=point_id,
                        sources=self._sources(session.mode, user_id),
                        user_id=user_id,
                        max_price=total_budget,
                        category_budgets=category_budgets,
                        dataset_version=settings.CHAT_GOLDENSET_DATASET_VERSION,
                        dataset_statuses=settings.CHAT_GOLDENSET_DATASET_STATUSES,
                        limit_per_source=10,
                        avoided_tags=avoided_tags,
                        preferred_tags=requested_tags,
                        # 요청 TPO를 아이템 선택까지 내린다. 이게 없으면 출근룩
                        # 액세서리 슬롯이 밀짚모자·라탄백으로 채워진다.
                        occasion_kind=analysis.conditions.occasion_kind,
                        gender=self._gender(context),
                        season=analysis.conditions.season,
                    )
                    if (
                        scoped_item_ids
                        and session.mode == ChatSession.Mode.WARDROBE_BASED
                    ):
                        scoped = self.item_retriever.retrieve(
                            ItemRetrievalRequest(
                                **request_kwargs,
                                allowed_wardrobe_item_ids=scoped_item_ids,
                            )
                        )
                        if scoped.candidates:
                            return scoped
                    return self.item_retriever.retrieve(
                        ItemRetrievalRequest(
                            **request_kwargs,
                            allowed_wardrobe_item_ids=allowed_wardrobe_item_ids,
                        )
                    )

                slot_results = tuple(retrieve_slot(point_id) for point_id in template_ids)
                if reference_anchor is not None:
                    slot_results = pin_reference_anchor(
                        reference_anchor,
                        slot_results,
                    )
                with (
                    event_recorder.measure(STAGE_COMPOSER)
                    if event_recorder is not None
                    else nullcontext()
                ):
                    batch = self._compose(
                        session.mode,
                        slot_results,
                        budget=total_budget,
                        category_budgets=category_budgets,
                    )
            except (ValueError, RuntimeError) as exc:
                failure_reasons[type(exc).__name__] += 1
                continue

            template_candidates: list[ValidatedRecommendationCandidate] = []
            for composition_rank, composition in enumerate(
                batch.compositions,
                start=1,
            ):
                if len(generated) + len(template_candidates) >= candidate_limit:
                    break
                if (
                    scoped_item_ids
                    and scope_snapshot.get("match_mode") == "REQUIRED"
                    and session.mode == ChatSession.Mode.WARDROBE_BASED
                    and not any(
                        item.source_id in scoped_item_ids
                        for item in composition.items
                    )
                ):
                    continue
                if reference_anchor is not None and not composition_contains_anchor(
                    composition,
                    reference_anchor,
                ):
                    continue
                with (
                    event_recorder.measure(STAGE_VALIDATOR)
                    if event_recorder is not None
                    else nullcontext()
                ):
                    validation = self.validator.validate(
                        composition,
                        context=self._validation_context(
                            user_id=user_id,
                            context=context,
                            analysis=analysis,
                            body=body,
                            reference_anchor=reference_anchor,
                        ),
                    )
                if not validation.valid:
                    failure_reasons.update(
                        issue.code
                        for issue in validation.issues
                        if issue.severity is ValidationSeverity.ERROR
                    )
                if validation.valid:
                    template_candidates.append(
                        ValidatedRecommendationCandidate(
                            ordinal=len(generated) + len(template_candidates) + 1,
                            template_rank=template_rank,
                            composition_rank=composition_rank,
                            golden=candidate,
                            composition=composition,
                            validation=validation,
                        )
                    )
            if template_candidates:
                generated.extend(template_candidates)
                validated_template_count += 1
                if len(generated) >= candidate_limit:
                    break
                if (
                    max_validated_templates is not None
                    and validated_template_count >= max_validated_templates
                ):
                    break

        if not generated:
            detail = ", ".join(
                f"{reason}x{count}" for reason, count in failure_reasons.most_common(3)
            )
            # 사용자 메시지에는 내부 코드를 싣지 않는다. 운영자는 로그로 본다.
            logger.warning(
                "골든 코디 %d건에서 유효 조합 0건 (run=%s, 사유=%s)",
                len(retrieval.candidates),
                run.pk,
                detail or "사유 기록 없음",
            )
            failure_type = (
                WardrobeOutfitUnavailable
                if session.mode == ChatSession.Mode.WARDROBE_BASED
                else OutfitCompositionFailed
            )
            failure_message = (
                WARDROBE_OUTFIT_UNAVAILABLE_MESSAGE
                if session.mode == ChatSession.Mode.WARDROBE_BASED
                else "검색된 골든 코디로 검증 가능한 최종 조합을 만들지 못했습니다."
            )
            failure = failure_type(failure_message)
            failure.detail = detail
            raise failure
        return GeneratedRecommendationCandidates(
            run_id=str(run.pk),
            session_id=str(session.pk),
            identity_id=str(session.identity_id),
            response_mode=run.response_mode,
            mode=session.mode,
            search_mode=retrieval.search_mode,
            candidates=tuple(generated),
        )

    @staticmethod
    def recent_golden_ids(run: ChatRun) -> frozenset[str]:
        """같은 세션의 최근 실행이 쓴 골든 템플릿 id.

        현재 실행은 뺀다 — 스타일리스트는 한 실행에서 페르소나별로 여러 번
        검색하므로, 현재 실행까지 제외하면 두 번째 페르소나부터 자기 자신이
        만든 템플릿을 피하려다 후보가 말라붙는다.

        한도가 0이면 조회 자체를 하지 않는다(기능 끄기).
        """
        limit = settings.CHAT_RECENT_GOLDEN_EXCLUSION_LIMIT
        if limit <= 0:
            return frozenset()
        recent = (
            GoldenTemplateSnapshot.objects.filter(result__session_id=run.session_id)
            .exclude(result__run_id=run.pk)
            .order_by("-result__created_at")
            .values_list("golden_id", flat=True)[:limit]
        )
        return frozenset(value for value in recent if value)

    def _resolve_reference_anchor(
        self,
        *,
        run: ChatRun,
        user_id: int | None,
        total_budget: int | None,
        category_budgets: Mapping[str, int],
        event_recorder: ReferenceRecommendationEventRecorder | None,
    ) -> PinnedReferenceAnchor | None:
        snapshot = getattr(run, "reference_snapshot", None)
        if not snapshot:
            return None
        try:
            return self.reference_anchor_resolver.resolve(
                snapshot=snapshot,
                mode=RecommendationMode(run.session.mode),
                user_id=user_id,
                total_budget=total_budget,
                category_budgets=category_budgets,
                stage_observer=(
                    event_recorder.add_stage_duration
                    if event_recorder is not None
                    else None
                ),
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            raise OutfitCompositionFailed(
                "공유 옷을 최종 코디의 고정 아이템으로 연결하지 못했습니다."
            ) from exc

    @transaction.atomic
    def persist_candidates(
        self,
        *,
        run: ChatRun,
        generated: GeneratedRecommendationCandidates,
        selected: Sequence[ValidatedRecommendationCandidate],
        persona_execution: ChatRunPersona | None = None,
        persona_explanation: str = "",
        validated_reason_codes: Sequence[str] = (),
        strategy_snapshot: dict[str, Any] | None = None,
        result_type: str = RecommendationResult.ResultType.INITIAL,
        replace_current: bool = False,
    ) -> RecommendationPipelineResult:
        """중복 검사·재정렬 뒤 선택된 후보만 최종 추천 결과로 저장한다."""

        selected_candidates = tuple(selected)
        normalized_reason_codes = self._normalize_reason_codes(validated_reason_codes)
        if len(persona_explanation.strip()) > 500:
            raise OutfitCompositionFailed(
                "스타일리스트 추천 설명은 500자 이하여야 합니다."
            )
        if strategy_snapshot is not None and not isinstance(strategy_snapshot, dict):
            raise OutfitCompositionFailed("전략 스냅샷은 JSON 객체여야 합니다.")
        if result_type not in RecommendationResult.ResultType.values:
            raise OutfitCompositionFailed("지원하지 않는 추천 결과 생성 목적입니다.")
        if replace_current != (
            result_type == RecommendationResult.ResultType.ALTERNATIVE
        ):
            raise OutfitCompositionFailed(
                "다른 추천 결과만 현재 스타일리스트 결과를 교체할 수 있습니다."
            )
        self._validate_persistence_scope(
            run=run,
            generated=generated,
            selected=selected_candidates,
            persona_execution=persona_execution,
        )

        locked_run = (
            ChatRun.objects.select_for_update()
            .select_related(
                "session",
                "session__identity",
            )
            .get(pk=run.pk)
        )
        existing = self._existing_result(
            run=locked_run,
            persona_execution=persona_execution,
        )
        if existing is not None and not replace_current:
            self._schedule_render_on_commit(
                run=locked_run,
                result_id=existing.pk,
            )
            return RecommendationPipelineResult(
                result=existing,
                approved_payload=self._approved_payload(existing),
            )
        if replace_current and (persona_execution is None or existing is None):
            raise OutfitCompositionFailed(
                "다른 추천을 생성할 현재 스타일리스트 결과가 없습니다."
            )

        generation = 1
        replaces = None
        if replace_current:
            generation = (
                RecommendationResult.objects.filter(
                    run=locked_run,
                    persona_id=persona_execution.persona_id,
                ).aggregate(value=Max("generation"))["value"]
                or 1
            ) + 1
            replaces = existing
            RecommendationResult.objects.filter(pk=existing.pk).update(is_current=False)

        candidate = selected_candidates[0].golden
        result = RecommendationResult.objects.create(
            identity=locked_run.session.identity,
            session=locked_run.session,
            run=locked_run,
            persona_execution=persona_execution,
            response_mode=locked_run.response_mode,
            persona_id=(persona_execution.persona_id if persona_execution else ""),
            persona_version=(
                persona_execution.persona_version if persona_execution else None
            ),
            persona_explanation=persona_explanation.strip(),
            validated_reason_codes=normalized_reason_codes,
            strategy_snapshot=(
                dict(strategy_snapshot)
                if strategy_snapshot is not None
                else (
                    dict(persona_execution.strategy_snapshot)
                    if persona_execution is not None
                    else {}
                )
            ),
            result_type=result_type,
            generation=generation,
            is_current=True,
            replaces=replaces,
            mode=locked_run.session.mode,
            dataset_version=(
                settings.CHAT_GOLDENSET_DATASET_VERSION
                or str(candidate.payload.get("dataset_version") or "unversioned")
            ),
        )
        GoldenTemplateSnapshot.objects.create(
            result=result,
            golden_id=candidate.golden_id or candidate.point_id,
            point_id=candidate.point_id,
            retrieval_score=candidate.score,
            payload_snapshot=candidate.payload,
            reasons=[
                {"source": reason.source, "delta": reason.delta, "text": reason.text}
                for reason in candidate.reasons
            ],
        )
        for rank, selected_candidate in enumerate(selected_candidates, start=1):
            self._persist_composition(
                result=result,
                rank=rank,
                candidate=selected_candidate,
            )

        result_id = result.pk
        self._schedule_render_on_commit(run=locked_run, result_id=result_id)
        return RecommendationPipelineResult(
            result=result,
            approved_payload=self._approved_payload(result),
        )

    @staticmethod
    def _schedule_render_on_commit(*, run: ChatRun, result_id: Any) -> None:
        """기본 추천만 저장 커밋 후 이미지를 자동 생성한다."""

        if run.response_mode != ChatSession.ResponseMode.DEFAULT:
            return
        transaction.on_commit(lambda: render_jobs.schedule_result(result_id))

    @staticmethod
    def _validate_persistence_scope(
        *,
        run: ChatRun,
        generated: GeneratedRecommendationCandidates,
        selected: tuple[ValidatedRecommendationCandidate, ...],
        persona_execution: ChatRunPersona | None,
    ) -> None:
        expected_scope = (
            str(run.pk),
            str(run.session_id),
            str(run.session.identity_id),
            run.response_mode,
            run.session.mode,
        )
        actual_scope = (
            generated.run_id,
            generated.session_id,
            generated.identity_id,
            generated.response_mode,
            generated.mode,
        )
        if actual_scope != expected_scope:
            raise OutfitCompositionFailed(
                "다른 채팅 실행에서 생성한 추천 후보는 저장할 수 없습니다."
            )
        if not selected:
            raise OutfitCompositionFailed("최종 저장할 추천 후보가 없습니다.")

        available = {candidate.ordinal: candidate for candidate in generated.candidates}
        if len({candidate.ordinal for candidate in selected}) != len(selected) or any(
            available.get(candidate.ordinal) != candidate for candidate in selected
        ):
            raise OutfitCompositionFailed(
                "생성 결과에 속한 서로 다른 추천 후보만 저장할 수 있습니다."
            )

        golden_keys = {
            (candidate.golden.point_id, candidate.golden.golden_id)
            for candidate in selected
        }
        if len(golden_keys) != 1:
            raise OutfitCompositionFailed(
                "하나의 추천 결과에는 같은 골든 템플릿의 후보만 저장할 수 있습니다."
            )

        if run.response_mode == ChatSession.ResponseMode.DEFAULT:
            if persona_execution is not None:
                raise OutfitCompositionFailed(
                    "기본 응답에는 스타일리스트 실행을 연결할 수 없습니다."
                )
            if len(selected) > 3:
                raise OutfitCompositionFailed(
                    "기본 응답은 검증된 코디를 최대 3개까지 저장할 수 있습니다."
                )
            return

        if run.response_mode != ChatSession.ResponseMode.STYLIST:
            raise OutfitCompositionFailed("지원하지 않는 추천 응답 모드입니다.")
        if persona_execution is None or persona_execution.run_id != run.pk:
            raise OutfitCompositionFailed(
                "스타일리스트 응답에는 같은 ChatRun의 개별 실행이 필요합니다."
            )
        if len(selected) != 1:
            raise OutfitCompositionFailed(
                "스타일리스트별 추천 결과는 코디 하나만 저장해야 합니다."
            )

    @staticmethod
    def _existing_result(
        *,
        run: ChatRun,
        persona_execution: ChatRunPersona | None,
    ) -> RecommendationResult | None:
        queryset = RecommendationResult.objects.filter(
            run=run,
            response_mode=run.response_mode,
        )
        if persona_execution is None:
            return queryset.filter(persona_execution__isnull=True).first()
        return queryset.filter(
            persona_execution=persona_execution,
            is_current=True,
        ).first()

    @staticmethod
    def _normalize_reason_codes(codes: Sequence[str]) -> list[str]:
        normalized: list[str] = []
        for code in codes:
            if not isinstance(code, str) or not code.strip():
                raise OutfitCompositionFailed(
                    "검증 근거 코드는 비어 있지 않은 문자열이어야 합니다."
                )
            value = code.strip()
            if value not in normalized:
                normalized.append(value)
        return normalized

    @staticmethod
    def _sources(mode: str, user_id: int | None) -> tuple[ItemSource, ...]:
        if mode == ChatSession.Mode.WARDROBE_BASED:
            return (ItemSource.WARDROBE,)
        if user_id is None:
            return (ItemSource.PRODUCT,)
        return (ItemSource.WARDROBE, ItemSource.PRODUCT)

    def _compose(
        self,
        mode: str,
        slot_results: tuple,
        *,
        budget: int | None,
        category_budgets: dict[str, int],
    ):
        if mode == ChatSession.Mode.WARDROBE_BASED:
            return self.wardrobe_composer.compose(
                WardrobeCompositionRequest(slot_results=slot_results)
            )
        return self.new_item_composer.compose(
            NewItemCompositionRequest(
                slot_results=slot_results,
                total_budget=budget,
                category_budgets=category_budgets,
            )
        )

    @staticmethod
    def _template_item_ids(candidate: OutfitCandidate) -> tuple[str, ...]:
        values: list[str] = []
        raw_ids = candidate.payload.get("item_point_ids")
        if isinstance(raw_ids, (list, tuple)):
            values.extend(str(value) for value in raw_ids if value not in (None, ""))
        for item in candidate.items:
            if not isinstance(item, dict):
                continue
            value = item.get("item_point_id") or item.get("point_id")
            if value not in (None, ""):
                values.append(str(value))
        return tuple(dict.fromkeys(values))

    @staticmethod
    def _drop_skipped_slots(
        candidate: OutfitCandidate,
        template_ids: tuple[str, ...],
        skipped_smalls: Collection[str],
    ) -> tuple[str, ...]:
        """건너뛸 소분류의 슬롯을 템플릿에서 뺀다.

        전부 빠지는 경우에는 원본을 그대로 둔다 — 액세서리만 있는 템플릿에서
        슬롯이 0개가 되면 코디가 성립하지 않는다.
        """
        if not skipped_smalls:
            return template_ids
        drop = {
            str(item.get("item_point_id") or item.get("point_id") or "")
            for item in candidate.items
            if isinstance(item, dict)
            and str(item.get("category_small") or "") in skipped_smalls
        }
        drop.discard("")
        if not drop:
            return template_ids
        kept = tuple(pid for pid in template_ids if pid not in drop)
        return kept or template_ids

    @staticmethod
    def _merged_pursuit(context: dict, analysis: TurnAnalysis) -> dict:
        """축적된 취향(온보딩·행동)만 담는다.

        예전에는 이번 발화 조건을 여기에 합쳐 넣었다. 그러면 "이번엔 러블리로"가
        기존 취향과 **같은 무게의 가산점 하나**가 돼, 예전 취향으로 이미 점수를
        받은 코디의 서열을 못 바꿨다. 사용자가 방금 한 말이 결과에 반영되지 않는
        원인이었다. 발화 조건은 _requested_conditions()가 따로 들고 간다.
        """
        del analysis  # 발화 조건은 여기 섞지 않는다 (위 주석 참고)
        source = context.get("profile", {}).get("pursuit") or {}
        preferred = {
            key: list(values) for key, values in (source.get("preferred") or {}).items()
        }
        avoided = {
            key: list(values) for key, values in (source.get("avoided") or {}).items()
        }
        return {"preferred": preferred, "avoided": avoided}

    @staticmethod
    def _requested_conditions(analysis: TurnAnalysis) -> dict[str, dict[str, list[str]]]:
        """이번 발화에서 사용자가 직접 말한 조건만 담는다.

        pursuit과 모양은 같지만 축이 다르다 — 검색은 preferred를
        Weights.request_match로 크게 가산하고, avoided는 하드 필터로 건다.
        """
        return {
            "preferred": {
                "styles": list(dict.fromkeys(analysis.conditions.styles)),
                "colors": list(dict.fromkeys(analysis.conditions.colors)),
                "fits": list(dict.fromkeys(analysis.conditions.fits)),
            },
            "avoided": {
                "styles": list(dict.fromkeys(analysis.conditions.avoided_styles)),
                "colors": list(dict.fromkeys(analysis.conditions.avoided_colors)),
            },
        }

    @staticmethod
    def _apply_strategy_preferences(
        pursuit: dict[str, dict[str, list[str]]],
        plan: StrategyPlan,
    ) -> dict[str, dict[str, list[str]]]:
        """전략의 소프트 보정을 원본 사용자 조건을 보존한 검색 입력으로 변환한다."""

        adjusted = {
            polarity: {
                axis: list(values)
                for axis, values in (pursuit.get(polarity) or {}).items()
            }
            for polarity in ("preferred", "avoided")
        }
        axis_names = {"style": "styles", "color": "colors", "fit": "fits"}
        for row in plan.preference_adjustments:
            polarity = (
                "preferred" if row.polarity is PreferencePolarity.PREFER else "avoided"
            )
            axis = axis_names[row.axis]
            adjusted[polarity][axis] = list(
                dict.fromkeys([*adjusted[polarity].get(axis, []), *row.values])
            )
        return adjusted

    @staticmethod
    def _presentation_groups(
        *,
        context: dict[str, Any],
        analysis: TurnAnalysis,
    ) -> tuple[str, ...]:
        explicit = analysis.conditions.presentation_groups
        if explicit:
            return normalize_presentation_groups(explicit)
        return ()

    @staticmethod
    def _gender(context: dict[str, Any]) -> str:
        body = context.get("profile", {}).get("body") or {}
        return str(body.get("gender") or "") if isinstance(body, dict) else ""

    @staticmethod
    def _validation_context(
        *,
        user_id: int | None,
        context: dict,
        analysis: TurnAnalysis,
        body: BodyProfile,
        reference_anchor: PinnedReferenceAnchor | None,
    ) -> ValidationContext:
        return ValidationContext(
            user_id=user_id,
            body=body,
            season=analysis.conditions.season,
            weather=context.get("weather"),
            occasion=analysis.conditions.occasion,
            total_budget=analysis.conditions.budget,
            category_budgets=context.get("profile", {}).get("category_budgets", {}),
            excluded_source_ids=tuple(analysis.conditions.excluded_source_ids),
            preferred_tags={
                "style": tuple(analysis.conditions.styles),
                "color": tuple(analysis.conditions.colors),
                "fit": tuple(analysis.conditions.fits),
            },
            avoided_tags={
                "style": tuple(analysis.conditions.avoided_styles),
                "color": tuple(analysis.conditions.avoided_colors),
            },
            require_image=True,
            reference=(
                ReferenceValidationContract(
                    original_wardrobe_item_ids=(
                        reference_anchor.reference.exclusions.wardrobe_item_ids
                    ),
                    original_qdrant_point_ids=(
                        reference_anchor.reference.exclusions.qdrant_point_ids
                    ),
                    anchor_identity=reference_anchor.identity,
                )
                if reference_anchor is not None
                else None
            ),
        )

    @staticmethod
    def _composition_fingerprint(composition) -> str:
        value = [
            {
                "position": position,
                "slot": item.slot_id,
                "source_type": item.source_type.value,
                "source_id": item.source_id,
                "image_ref": item.image_ref,
            }
            for position, item in enumerate(composition.items, start=1)
        ]
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _persist_composition(
        self,
        *,
        result: RecommendationResult,
        rank: int,
        candidate: ValidatedRecommendationCandidate,
    ) -> None:
        composition = candidate.composition
        validation = candidate.validation
        self._validate_composition_for_persistence(
            result=result,
            composition=composition,
        )
        row = OutfitCompositionModel.objects.create(
            result=result,
            rank=rank,
            status=OutfitCompositionModel.Status.VALIDATED,
            composition_fingerprint=self._composition_fingerprint(composition),
            total_product_price=validation.effective_total_product_price,
            validation_reasons=[
                {
                    "severity": issue.severity.value,
                    "code": issue.code,
                    "message": issue.message,
                    "slot": issue.slot_id,
                }
                for issue in validation.issues
            ],
            reference_match=self._reference_match(composition),
            warnings=list(composition.warnings),
        )
        for position, item in enumerate(composition.items, start=1):
            OutfitCompositionItem.objects.create(
                composition=row,
                position=position,
                slot=item.slot_id,
                source_type=item.source_type.value,
                source_id=item.source_id,
                source_collection=item.source_collection,
                source_point_id=item.point_id,
                template_item_point_id=item.template_point_id,
                replacement_score=item.score,
                image_ref=item.image_ref,
                price_snapshot=item.price,
                reasons=list(item.reasons),
                item_snapshot=item.payload,
            )

    @staticmethod
    def _validate_composition_for_persistence(
        *,
        result: RecommendationResult,
        composition: DomainOutfitComposition,
    ) -> None:
        allowed = {
            RecommendationResult.Mode.WARDROBE_BASED: {ItemSource.WARDROBE},
            RecommendationResult.Mode.NEW_ITEM: {
                ItemSource.WARDROBE,
                ItemSource.PRODUCT,
            },
        }.get(result.mode, set())
        if any(item.source_type not in allowed for item in composition.items):
            raise OutfitCompositionFailed(
                "추천 모드에서 허용되지 않는 최종 아이템 출처는 저장할 수 없습니다."
            )
        if result.mode == RecommendationResult.Mode.NEW_ITEM and not any(
            item.source_type is ItemSource.PRODUCT for item in composition.items
        ):
            raise OutfitCompositionFailed(
                "신규 상품 추천에는 판매 상품이 최소 한 개 필요합니다."
            )

        run = getattr(result, "run", None)
        snapshot = getattr(run, "reference_snapshot", None) or {}
        if not snapshot:
            return
        original_item_id = str(snapshot.get("wardrobe_item_id") or "")
        original_point_id = str(snapshot.get("qdrant_point_id") or "")
        for item in composition.items:
            if (
                (original_item_id and item.source_id == original_item_id)
                or (original_point_id and item.point_id == original_point_id)
                or (
                    original_item_id
                    and str(item.payload.get("item_id") or "") == original_item_id
                )
            ):
                raise OutfitCompositionFailed(
                    "참고용 친구 옷 원본은 최종 코디에 저장할 수 없습니다."
                )
        anchors = [
            item
            for item in composition.items
            if item.payload.get("selection_role") == "PINNED_REFERENCE_ANCHOR"
        ]
        if len(anchors) != 1:
            raise OutfitCompositionFailed(
                "공유 옷 추천 결과에는 고정 anchor가 정확히 하나 필요합니다."
            )

    @staticmethod
    def _reference_match(composition: DomainOutfitComposition) -> dict[str, Any]:
        anchors = [
            item
            for item in composition.items
            if item.payload.get("selection_role") == "PINNED_REFERENCE_ANCHOR"
        ]
        if not anchors:
            return {}
        if len(anchors) != 1:
            raise OutfitCompositionFailed(
                "최종 코디에는 공유 옷 고정 anchor가 정확히 하나여야 합니다."
            )
        item = anchors[0]
        match_type = str(item.payload.get("match_type") or "").strip()
        if match_type not in {"VISUAL_SIMILAR", "STYLE_SIMILAR"}:
            raise OutfitCompositionFailed(
                "공유 옷 고정 anchor의 매칭 유형이 올바르지 않습니다."
            )
        return {
            "schema_version": "1.0",
            "match_type": match_type,
            "selection_role": "PINNED_REFERENCE_ANCHOR",
            "source_type": item.source_type.value,
            "source_id": item.source_id,
            "source_collection": item.source_collection,
            "source_point_id": item.point_id,
            "template_item_point_id": item.template_point_id,
            "score": item.score,
            "reasons": list(item.reasons),
        }

    @staticmethod
    def _approved_payload(result: RecommendationResult) -> dict[str, Any]:
        compositions = []
        attribute_keys = (
            "category_large",
            "category_small",
            "color",
            "base_color",
            "style",
            "fit",
            "material",
            "season",
            "brand",
        )
        # 설명 생성의 ID 계약은 이 순서가 기준이다. 검증부
        # (recommendation_explanations.apply_recommendation_explanation)와 반드시
        # 같은 필터·같은 정렬이어야 한다 — 한쪽만 상태로 거르면 모델은 받은 대로
        # 답했는데 계약 위반으로 처리돼 설명 전체가 규칙 폴백으로 떨어진다.
        approved = (
            result.compositions.filter(status=OutfitCompositionModel.Status.VALIDATED)
            .prefetch_related("items")
            .order_by("rank", "created_at")
        )
        for outfit_index, composition in enumerate(approved, start=1):
            compositions.append(
                {
                    "outfit_index": outfit_index,
                    "rank": composition.rank,
                    "total_product_price": composition.total_product_price,
                    "reference_match": composition.reference_match,
                    "warnings": composition.warnings,
                    "validation_reasons": composition.validation_reasons,
                    "items": [
                        {
                            "item_index": item_index,
                            "slot": item.slot.split(":", 1)[0],
                            "source_type": item.source_type,
                            "name": (
                                item.item_snapshot.get("display_name")
                                or item.item_snapshot.get("product_name")
                                or item.item_snapshot.get("item_name")
                                or item.item_snapshot.get("name")
                                or item.item_snapshot.get("title")
                                or item.item_snapshot.get("category_small")
                                or item.item_snapshot.get("category_large")
                                or "구성 아이템"
                            ),
                            "price": item.price_snapshot,
                            "reasons": item.reasons,
                            "attributes": {
                                key: item.item_snapshot[key]
                                for key in attribute_keys
                                if item.item_snapshot.get(key) not in (None, "", [], {})
                            },
                        }
                        for item_index, item in enumerate(
                            composition.items.all(), start=1
                        )
                    ],
                }
            )
        return {
            "result_id": str(result.id),
            "mode": result.mode,
            "compositions": compositions,
        }
