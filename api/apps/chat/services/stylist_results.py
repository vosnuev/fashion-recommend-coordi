"""스타일리스트별 run 결과 조회와 메시지 스냅샷을 구성한다."""

from __future__ import annotations

from django.db.models import Prefetch, QuerySet

from apps.chat.models import ChatRun, ChatRunPersona
from apps.recommend.models import OutfitComposition, RecommendationResult


def with_stylist_results(queryset: QuerySet[ChatRun]) -> QuerySet[ChatRun]:
    """run 결과 그래프를 고정된 소수의 쿼리로 미리 조회한다."""

    cards = (
        OutfitComposition.objects.filter(
            status=OutfitComposition.Status.VALIDATED,
        )
        .select_related("render_job")
        .prefetch_related("items", "saved_records")
        .order_by("rank", "created_at")
    )
    current_results = RecommendationResult.objects.filter(
        is_current=True
    ).prefetch_related(
        Prefetch(
            "compositions",
            queryset=cards,
            to_attr="public_compositions",
        )
    )
    executions = ChatRunPersona.objects.prefetch_related(
        Prefetch(
            "recommendation_results",
            queryset=current_results,
            to_attr="current_recommendation_results",
        ),
        Prefetch(
            "recommendation_results",
            queryset=RecommendationResult.objects.filter(is_current=False).order_by(
                "generation"
            ),
            to_attr="historical_recommendation_results",
        ),
    ).order_by("display_order")
    return queryset.prefetch_related(
        Prefetch("persona_executions", queryset=executions)
    )


def message_metadata_results(run: ChatRun) -> list[dict[str, object]]:
    """AI 메시지에는 카드 본문 대신 결과 연결에 필요한 최소 스냅샷만 둔다."""

    snapshots: list[dict[str, object]] = []
    for execution in run.persona_executions.all():
        try:
            result = execution.recommendation_result
        except RecommendationResult.DoesNotExist:
            result = None
        snapshots.append(
            {
                "persona_id": execution.persona_id,
                "status": execution.status,
                "result_id": str(result.pk) if result is not None else None,
                "result_type": result.result_type if result is not None else None,
                "generation": result.generation if result is not None else None,
                "alternative_status": execution.alternative_status,
                "error": (
                    {
                        "code": execution.error_code,
                        "message": execution.error_message,
                    }
                    if execution.status == ChatRunPersona.Status.FAILED
                    else None
                ),
            }
        )
    return snapshots
