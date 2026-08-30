"""`다른 추천` 후보가 현재 카드와 최근 추천 10회를 반복하지 않게 한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.chat.models import ChatRun, ChatRunPersona, ChatSession
from apps.chat.services.recent_recommendations import load_recent_recommendations
from apps.recommend.models import OutfitComposition, RecommendationResult
from apps.recommend.services.item_retriever import ItemSource
from apps.recommend.services.outfit_types import (
    OutfitComposition as DomainOutfitComposition,
)
from apps.recommend.services.outfit_types import OutfitItem, RecommendationMode


class AlternativeRecommendationError(RuntimeError):
    code = "STYLIST_ALTERNATIVE_INVALID"


class AlternativeRecommendationInProgress(AlternativeRecommendationError):
    code = "STYLIST_ALTERNATIVE_IN_PROGRESS"


class AlternativeRecommendationNotReady(AlternativeRecommendationError):
    code = "STYLIST_ALTERNATIVE_NOT_READY"


@dataclass(frozen=True)
class PreparedAlternativeRecommendation:
    run_id: str
    persona_id: str
    source_result_id: str
    generation: int


def _domain_item(payload: dict[str, Any]) -> OutfitItem:
    return OutfitItem(
        slot_id=str(payload["slot"]),
        template_point_id="history",
        category_large="history",
        layer_role="",
        source_type=ItemSource(str(payload["source_type"])),
        source_id=str(payload["source_id"]),
        source_collection=str(payload["source_collection"]),
        point_id="history",
        image_ref="",
        price=None,
        score=None,
        reasons=(),
    )


def _domain_composition(
    *,
    mode: str,
    items: list[dict[str, Any]],
) -> DomainOutfitComposition:
    return DomainOutfitComposition(
        mode=RecommendationMode(mode),
        items=tuple(_domain_item(item) for item in items),
        missing_slot_ids=(),
        total_product_price=0,
    )


def load_alternative_exclusions(
    *,
    run: ChatRun,
    persona_execution: ChatRunPersona,
) -> tuple[DomainOutfitComposition, ...]:
    """현재 카드와 현재 run을 제외한 최근 추천 10회의 검증 카드를 로드한다."""

    try:
        current = persona_execution.recommendation_result
    except RecommendationResult.DoesNotExist as exc:
        raise AlternativeRecommendationNotReady(
            "다른 추천을 만들 현재 스타일리스트 결과가 없습니다."
        ) from exc
    current_card = (
        current.compositions.filter(status=OutfitComposition.Status.VALIDATED)
        .prefetch_related("items")
        .order_by("rank", "created_at")
        .first()
    )
    if current_card is None:
        raise AlternativeRecommendationNotReady(
            "다른 추천을 만들 현재 검증 카드가 없습니다."
        )
    cards = [
        _domain_composition(
            mode=run.session.mode,
            items=[
                {
                    "slot": item.slot,
                    "source_type": item.source_type,
                    "source_collection": item.source_collection,
                    "source_id": item.source_id,
                }
                for item in current_card.items.all()
            ],
        )
    ]
    history = load_recent_recommendations(
        identity=run.session.identity,
        current_run=run,
    )
    for history_run in history["runs"]:
        for result in history_run["results"]:
            for card in result["cards"]:
                if card["items"]:
                    cards.append(
                        _domain_composition(
                            mode=run.session.mode,
                            items=card["items"],
                        )
                    )
    return tuple(cards)


@transaction.atomic
def prepare_alternative_recommendation(
    *,
    run_id,
    persona_id: str,
) -> PreparedAlternativeRecommendation:
    """성공한 대상만 PENDING으로 바꾸고 현재 결과 세대를 큐 스냅샷으로 고정한다."""

    run = ChatRun.objects.select_for_update().filter(pk=run_id).first()
    if run is None or run.response_mode != ChatSession.ResponseMode.STYLIST:
        raise AlternativeRecommendationError(
            "스타일리스트 응답 실행만 다른 추천을 만들 수 있습니다."
        )
    if run.status != ChatRun.Status.SUCCEEDED:
        raise AlternativeRecommendationInProgress(
            "현재 스타일리스트 실행이 모두 끝난 뒤 다른 추천을 요청할 수 있습니다."
        )
    execution = (
        ChatRunPersona.objects.select_for_update()
        .filter(run=run, persona_id=persona_id)
        .first()
    )
    if execution is None or execution.status != ChatRunPersona.Status.SUCCEEDED:
        raise AlternativeRecommendationNotReady(
            "성공한 스타일리스트 결과에서만 다른 추천을 요청할 수 있습니다."
        )
    try:
        current = execution.recommendation_result
    except RecommendationResult.DoesNotExist as exc:
        raise AlternativeRecommendationNotReady(
            "다른 추천을 만들 현재 결과가 없습니다."
        ) from exc

    execution.status = ChatRunPersona.Status.PENDING
    execution.alternative_status = ChatRunPersona.AlternativeStatus.PENDING
    execution.alternative_count += 1
    execution.alternative_error_code = ""
    execution.alternative_error_message = ""
    execution.latency_ms = 0
    execution.error_code = ""
    execution.error_message = ""
    execution.started_at = None
    execution.completed_at = None
    execution.save(
        update_fields=[
            "status",
            "alternative_status",
            "alternative_count",
            "alternative_error_code",
            "alternative_error_message",
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
    return PreparedAlternativeRecommendation(
        run_id=str(run.pk),
        persona_id=persona_id,
        source_result_id=str(current.pk),
        generation=current.generation + 1,
    )


@transaction.atomic
def mark_alternative_enqueue_failed(*, run_id, persona_id: str) -> None:
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
    execution.status = ChatRunPersona.Status.SUCCEEDED
    execution.alternative_status = ChatRunPersona.AlternativeStatus.FAILED
    execution.alternative_error_code = "CHAT_QUEUE_UNAVAILABLE"
    execution.alternative_error_message = (
        "다른 추천 큐에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."
    )
    execution.completed_at = now
    execution.save(
        update_fields=[
            "status",
            "alternative_status",
            "alternative_error_code",
            "alternative_error_message",
            "completed_at",
            "updated_at",
        ]
    )
    ChatRun.objects.filter(pk=run_id).update(
        status=ChatRun.Status.SUCCEEDED,
        error_code="",
        error_message="",
        completed_at=now,
        updated_at=now,
    )


@transaction.atomic
def mark_alternative_processing_failed(*, run_id, persona_id: str) -> bool:
    """워커 예외가 오케스트레이터 복구 전에 나도 기존 카드를 다시 쓸 수 있게 한다."""

    execution = (
        ChatRunPersona.objects.select_for_update()
        .filter(
            run_id=run_id,
            persona_id=persona_id,
            status__in=(
                ChatRunPersona.Status.PENDING,
                ChatRunPersona.Status.RUNNING,
            ),
            alternative_status__in=(
                ChatRunPersona.AlternativeStatus.PENDING,
                ChatRunPersona.AlternativeStatus.RUNNING,
            ),
        )
        .first()
    )
    if execution is None:
        return False
    now = timezone.now()
    execution.status = ChatRunPersona.Status.SUCCEEDED
    execution.alternative_status = ChatRunPersona.AlternativeStatus.FAILED
    execution.alternative_error_code = "STYLIST_ALTERNATIVE_FAILED"
    execution.alternative_error_message = (
        "다른 추천을 받지 못했어요. 잠시 후 다시 시도해 주세요."
    )
    execution.completed_at = now
    execution.save(
        update_fields=[
            "status",
            "alternative_status",
            "alternative_error_code",
            "alternative_error_message",
            "completed_at",
            "updated_at",
        ]
    )
    ChatRun.objects.filter(pk=run_id).update(
        status=ChatRun.Status.SUCCEEDED,
        error_code="",
        error_message="",
        completed_at=now,
        updated_at=now,
    )
    return True


@transaction.atomic
def reset_interrupted_alternative(*, run_id, persona_id: str) -> bool:
    execution = (
        ChatRunPersona.objects.select_for_update()
        .filter(
            run_id=run_id,
            persona_id=persona_id,
            alternative_status__in=(
                ChatRunPersona.AlternativeStatus.PENDING,
                ChatRunPersona.AlternativeStatus.RUNNING,
            ),
        )
        .first()
    )
    if execution is None:
        return False
    now = timezone.now()
    execution.status = ChatRunPersona.Status.PENDING
    execution.alternative_status = ChatRunPersona.AlternativeStatus.PENDING
    execution.started_at = None
    execution.completed_at = None
    execution.save(
        update_fields=[
            "status",
            "alternative_status",
            "started_at",
            "completed_at",
            "updated_at",
        ]
    )
    ChatRun.objects.filter(
        pk=run_id,
        status__in=(ChatRun.Status.PENDING, ChatRun.Status.RUNNING),
    ).update(status=ChatRun.Status.PENDING, completed_at=None, updated_at=now)
    return True


@transaction.atomic
def finalize_persisted_alternative(
    *,
    run_id,
    persona_id: str,
    generation: int,
) -> bool:
    """결과 저장 뒤 워커가 중단된 배달을 새 추천 재생성 없이 완료 처리한다."""

    execution = (
        ChatRunPersona.objects.select_for_update()
        .filter(run_id=run_id, persona_id=persona_id)
        .first()
    )
    if (
        execution is None
        or not RecommendationResult.objects.filter(
            persona_execution=execution,
            result_type=RecommendationResult.ResultType.ALTERNATIVE,
            generation=generation,
            is_current=True,
        ).exists()
    ):
        return False
    now = timezone.now()
    execution.status = ChatRunPersona.Status.SUCCEEDED
    execution.alternative_status = ChatRunPersona.AlternativeStatus.SUCCEEDED
    execution.alternative_error_code = ""
    execution.alternative_error_message = ""
    execution.completed_at = now
    execution.save(
        update_fields=[
            "status",
            "alternative_status",
            "alternative_error_code",
            "alternative_error_message",
            "completed_at",
            "updated_at",
        ]
    )
    ChatRun.objects.filter(pk=run_id).update(
        status=ChatRun.Status.SUCCEEDED,
        error_code="",
        error_message="",
        completed_at=now,
        updated_at=now,
    )
    return True
