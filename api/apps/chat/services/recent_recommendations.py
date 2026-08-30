"""스타일리스트 추천에 사용할 회원의 최근 추천 실행 이력 로더."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

from django.db.models import Max, Prefetch

from apps.chat.models import ChatIdentity, ChatRun
from apps.recommend.models import (
    OutfitComposition,
    OutfitCompositionItem,
    RecommendationFeedback,
    RecommendationResult,
    SavedOutfit,
)

RECENT_RECOMMENDATION_RUN_LIMIT = 10

_STYLE_KEYS = ("style", "styles", "style_tags")
_COLOR_KEYS = ("color", "colors", "base_color", "color_family")
_FIT_KEYS = ("fit", "fits", "silhouette")
_NON_MAJOR_SLOT_TOKENS = (
    "ACCESSORY",
    "BAG",
    "BELT",
    "HAT",
    "JEWELRY",
    "SCARF",
    "SOCK",
    "액세서리",
    "가방",
    "모자",
    "주얼리",
)


class RecentRecommendationHistoryError(RuntimeError):
    """최근 추천 이력을 안전하게 조회할 수 없을 때 발생한다."""


class MemberRecentRecommendationsRequired(RecentRecommendationHistoryError):
    """회원 identity가 아닌 호출을 거부한다."""


class CurrentRunOwnershipMismatch(RecentRecommendationHistoryError):
    """현재 실행과 조회 identity의 소유자가 다를 때 발생한다."""


def _list_values(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    if isinstance(value, Iterable) and not isinstance(value, (bytes, dict)):
        values: list[str] = []
        for item in value:
            if item in (None, ""):
                continue
            normalized = str(item).strip()
            if normalized:
                values.append(normalized)
        return values
    return [str(value).strip()]


def _snapshot_values(snapshot: object, keys: tuple[str, ...]) -> list[str]:
    if not isinstance(snapshot, dict):
        return []
    values: list[str] = []
    sources = [snapshot]
    if isinstance(snapshot.get("tags"), dict):
        sources.append(snapshot["tags"])
    for source in sources:
        for key in keys:
            values.extend(_list_values(source.get(key)))
    return list(dict.fromkeys(values))


def _merge_values(*groups: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for group in groups for value in group))


def _is_major_slot(slot: str) -> bool:
    normalized = slot.strip().upper()
    return bool(normalized) and not any(
        token in normalized for token in _NON_MAJOR_SLOT_TOKENS
    )


def _feedback_payload(composition: OutfitComposition) -> dict[str, Any] | None:
    try:
        feedback = composition.feedback
    except RecommendationFeedback.DoesNotExist:
        return None
    return {
        "reaction": feedback.reaction,
        "reason_codes": list(feedback.reason_codes),
        "comment": feedback.comment,
        "created_at": feedback.created_at.isoformat(),
        "updated_at": feedback.updated_at.isoformat(),
    }


def _item_payload(item: OutfitCompositionItem) -> dict[str, Any]:
    snapshot = item.item_snapshot
    return {
        "item_id": str(item.id),
        "position": item.position,
        "slot": item.slot,
        "source_type": item.source_type,
        "source_collection": item.source_collection,
        "source_id": item.source_id,
        "styles": _snapshot_values(snapshot, _STYLE_KEYS),
        "colors": _snapshot_values(snapshot, _COLOR_KEYS),
        "fits": _snapshot_values(snapshot, _FIT_KEYS),
    }


def _result_payload(
    result: RecommendationResult,
    *,
    saved_at_by_composition: dict[str, str],
) -> dict[str, Any]:
    template_snapshot = (
        result.golden_template.payload_snapshot
        if hasattr(result, "golden_template")
        else {}
    )
    template_styles = _snapshot_values(template_snapshot, _STYLE_KEYS)
    template_colors = _snapshot_values(template_snapshot, _COLOR_KEYS)
    template_fits = _snapshot_values(template_snapshot, _FIT_KEYS)
    cards: list[dict[str, Any]] = []
    for composition in result.recent_validated_compositions:
        items = [_item_payload(item) for item in composition.items.all()]
        saved_at = saved_at_by_composition.get(str(composition.id))
        cards.append(
            {
                "composition_id": str(composition.id),
                "rank": composition.rank,
                "major_slots": [
                    item["slot"] for item in items if _is_major_slot(item["slot"])
                ],
                "styles": _merge_values(
                    template_styles, *(item["styles"] for item in items)
                ),
                "colors": _merge_values(
                    template_colors, *(item["colors"] for item in items)
                ),
                "fits": _merge_values(template_fits, *(item["fits"] for item in items)),
                "items": items,
                "feedback": _feedback_payload(composition),
                "is_saved": saved_at is not None,
                "saved_at": saved_at,
            }
        )
    return {
        "result_id": str(result.id),
        "response_mode": result.response_mode,
        "persona_id": result.persona_id or None,
        "recommended_at": result.created_at.isoformat(),
        "cards": cards,
    }


def _repetition_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    item_counts: Counter[tuple[str, str, str]] = Counter()
    item_slots: defaultdict[tuple[str, str, str], set[str]] = defaultdict(set)
    combination_counts: Counter[tuple[tuple[str, str, str], ...]] = Counter()
    slot_counts: Counter[str] = Counter()

    for run in runs:
        for result in run["results"]:
            for card in result["cards"]:
                signature: list[tuple[str, str, str]] = []
                for item in card["items"]:
                    item_key = (
                        item["source_type"],
                        item["source_collection"],
                        item["source_id"],
                    )
                    item_counts[item_key] += 1
                    item_slots[item_key].add(item["slot"])
                    slot_counts[item["slot"]] += 1
                    signature.append(item_key)
                if signature:
                    combination_counts[tuple(sorted(signature))] += 1

    repeated_items = [
        {
            "source_type": key[0],
            "source_collection": key[1],
            "source_id": key[2],
            "slots": sorted(item_slots[key]),
            "count": count,
        }
        for key, count in item_counts.items()
        if count >= 2
    ]
    repeated_items.sort(
        key=lambda row: (
            -row["count"],
            row["source_type"],
            row["source_collection"],
            row["source_id"],
        )
    )

    repeated_combinations = [
        {
            "items": [
                {
                    "source_type": item[0],
                    "source_collection": item[1],
                    "source_id": item[2],
                }
                for item in signature
            ],
            "count": count,
        }
        for signature, count in combination_counts.items()
        if count >= 2
    ]
    repeated_combinations.sort(
        key=lambda row: (
            -row["count"],
            str(row["items"]),
        )
    )
    repeated_slots = [
        {"slot": slot, "count": count}
        for slot, count in slot_counts.most_common()
        if count >= 2
    ]
    return {
        "items": repeated_items,
        "combinations": repeated_combinations,
        "slots": repeated_slots,
    }


def load_recent_recommendations(
    *,
    identity: ChatIdentity,
    current_run: ChatRun,
) -> dict[str, Any]:
    """현재 실행을 제외한 회원의 최근 추천 실행 최대 10회를 구조화한다.

    다중 스타일리스트 실행은 결과 행이 최대 3개여도 추천 1회로 계산한다.
    검증 통과 카드가 하나도 없는 실행은 실제 노출 추천 이력에 포함하지 않는다.
    """

    if identity.user_id is None:
        raise MemberRecentRecommendationsRequired(
            "최근 추천 개인화 이력은 로그인한 회원만 조회할 수 있습니다."
        )
    if current_run.session.identity_id != identity.id:
        raise CurrentRunOwnershipMismatch(
            "현재 채팅 실행과 최근 추천 이력의 회원이 다릅니다."
        )

    recent_run_rows = list(
        RecommendationResult.objects.filter(
            identity=identity,
            compositions__status=OutfitComposition.Status.VALIDATED,
        )
        .exclude(run_id=current_run.id)
        .values("run_id")
        .annotate(recommended_at=Max("created_at"))
        .order_by("-recommended_at", "-run_id")[:RECENT_RECOMMENDATION_RUN_LIMIT]
    )
    run_ids = [row["run_id"] for row in recent_run_rows]
    if not run_ids:
        return {
            "run_limit": RECENT_RECOMMENDATION_RUN_LIMIT,
            "runs": [],
            "repetitions": {"items": [], "combinations": [], "slots": []},
            "saved_signal_available": True,
        }

    saved_at_by_composition = {
        str(row["composition_id"]): row["created_at"].isoformat()
        for row in SavedOutfit.objects.filter(
            user_id=identity.user_id,
            composition__result__run_id__in=run_ids,
        ).values("composition_id", "created_at")
    }

    item_queryset = OutfitCompositionItem.objects.order_by("position", "created_at")
    composition_queryset = (
        OutfitComposition.objects.filter(status=OutfitComposition.Status.VALIDATED)
        .select_related("feedback")
        .prefetch_related(Prefetch("items", queryset=item_queryset))
        .order_by("rank", "created_at")
    )
    results = list(
        RecommendationResult.objects.filter(
            identity=identity,
            run_id__in=run_ids,
            compositions__status=OutfitComposition.Status.VALIDATED,
        )
        .select_related("persona_execution", "golden_template")
        .prefetch_related(
            Prefetch(
                "compositions",
                queryset=composition_queryset,
                to_attr="recent_validated_compositions",
            )
        )
        .distinct()
    )
    results_by_run: defaultdict[object, list[RecommendationResult]] = defaultdict(list)
    for result in results:
        results_by_run[result.run_id].append(result)

    runs: list[dict[str, Any]] = []
    for row in recent_run_rows:
        run_results = results_by_run[row["run_id"]]
        run_results.sort(
            key=lambda result: (
                result.persona_execution.display_order
                if result.persona_execution_id is not None
                else 0,
                result.created_at,
                str(result.id),
            )
        )
        runs.append(
            {
                "run_id": str(row["run_id"]),
                "recommended_at": row["recommended_at"].isoformat(),
                "results": [
                    _result_payload(
                        result,
                        saved_at_by_composition=saved_at_by_composition,
                    )
                    for result in run_results
                ],
            }
        )

    return {
        "run_limit": RECENT_RECOMMENDATION_RUN_LIMIT,
        "runs": runs,
        "repetitions": _repetition_summary(runs),
        "saved_signal_available": True,
    }
