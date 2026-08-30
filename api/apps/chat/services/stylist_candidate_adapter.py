"""공통 채팅 컨텍스트와 검증 후보를 ID 없는 스타일리스트 입력으로 변환한다."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from apps.chat.services.openai_adapter import TurnAnalysis
from apps.chat.services.recommendation_pipeline import (
    ValidatedRecommendationCandidate,
)
from apps.chat.services.stylist_strategy import (
    NumericMetric,
    StrategyCandidateView,
    StylistStrategyContext,
    TagGroup,
)
from apps.recommend.services.item_retriever import ItemSource

_TAG_KEYS = {
    "style": ("style", "styles", "style_tags"),
    "color": ("color", "colors", "base_color", "color_family"),
    "fit": ("fit", "fits", "silhouette"),
}
_PREFERENCE_KEYS = {
    "style": ("style", "styles"),
    "color": ("color", "colors"),
    "fit": ("fit", "fits"),
}
_DIRECT_METRICS = (
    "color_cohesion",
    "silhouette_consistency",
    "silhouette_conflict",
    "visual_simplicity",
    "visual_focus_count",
    "layer_complexity",
    "pattern_detail_density",
    "weather_fit",
    "temperature_fit",
    "apparent_temperature_fit",
    "precipitation_fit",
    "wind_fit",
    "activity_fit",
    "mobility_fit",
    "walking_fit",
    "wearing_convenience",
    "footwear_convenience",
    "outerwear_convenience",
    "dressing_convenience",
    "maintenance_ease",
    "maintenance_evidence",
    "novelty",
    "cross_style",
)


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _strings(value: object) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        normalized = value.strip()
        return (normalized,) if normalized else ()
    if not isinstance(value, Iterable) or isinstance(value, (bytes, dict)):
        return (str(value).strip(),)
    result: list[str] = []
    for row in value:
        normalized = str(row).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return tuple(result)


def _merge(*groups: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for group in groups for value in group if value))


def _snapshot_values(snapshot: object, keys: tuple[str, ...]) -> tuple[str, ...]:
    source = _mapping(snapshot)
    nested_tags = _mapping(source.get("tags"))
    return _merge(
        *(_strings(source.get(key)) for key in keys),
        *(_strings(nested_tags.get(key)) for key in keys),
    )


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) else None


def _unit(value: object, *, default: float = 0.5) -> float:
    normalized = _finite(value)
    if normalized is None:
        return default
    if 1 < normalized <= 100:
        normalized /= 100
    return min(max(normalized, 0.0), 1.0)


def _numeric_from_snapshots(
    snapshots: tuple[dict[str, Any], ...],
    name: str,
) -> float | None:
    for snapshot in snapshots:
        for source in (snapshot, _mapping(snapshot.get("metrics"))):
            value = _finite(source.get(name))
            if value is not None:
                return value
    return None


def _jaccard_distance(left: Iterable[str], right: Iterable[str]) -> float | None:
    left_values = set(left)
    right_values = set(right)
    if not left_values or not right_values:
        return None
    return 1.0 - len(left_values & right_values) / len(left_values | right_values)


def _tag_groups(
    source: dict[str, Any],
    analysis: TurnAnalysis,
    *,
    polarity: str,
) -> tuple[TagGroup, ...]:
    pursuit = _mapping(_mapping(source.get("profile")).get("pursuit"))
    values = _mapping(pursuit.get(polarity))
    turn_values = {
        "style": (
            analysis.conditions.styles
            if polarity == "preferred"
            else analysis.conditions.avoided_styles
        ),
        "color": (
            analysis.conditions.colors
            if polarity == "preferred"
            else analysis.conditions.avoided_colors
        ),
        "fit": analysis.conditions.fits if polarity == "preferred" else (),
    }
    groups: list[TagGroup] = []
    for axis, keys in _PREFERENCE_KEYS.items():
        resolved = _merge(
            *(_strings(values.get(key)) for key in keys),
            _strings(turn_values[axis]),
        )
        if resolved:
            groups.append(TagGroup(axis=axis, values=resolved))
    return tuple(groups)


def _recent_summary(
    context: dict[str, Any],
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    behavior = _mapping(context.get("behavior_signals"))
    recent = _mapping(
        _mapping(behavior.get("source_data")).get("recent_recommendations")
    )
    tags: dict[str, list[str]] = {"styles": [], "colors": [], "fits": []}
    for run in _rows(recent.get("runs")):
        for result in _rows(run.get("results")):
            for card in _rows(result.get("cards")):
                for field, values in tags.items():
                    values.extend(_strings(card.get(field)))
    return (
        tuple(dict.fromkeys(tags["styles"])),
        tuple(dict.fromkeys(tags["colors"])),
        tuple(dict.fromkeys(tags["fits"])),
    )


def build_stylist_strategy_context(
    *,
    context: dict[str, Any],
    analysis: TurnAnalysis,
    recommendation_mode: str,
) -> StylistStrategyContext:
    """세 전략이 공유할 요청·날씨·최근 행동 요약을 한 번 구성한다."""

    recent_styles, recent_colors, recent_fits = _recent_summary(context)
    behavior = _mapping(context.get("behavior_signals"))
    repetition = _mapping(behavior.get("repetition_avoidance"))
    recent_repetition = _mapping(repetition.get("recent_recommendations"))
    repeated_slots = tuple(
        dict.fromkeys(
            str(row.get("slot") or "").strip()
            for row in _rows(recent_repetition.get("slots"))
            if str(row.get("slot") or "").strip()
        )
    )
    calendar = _mapping(_mapping(behavior.get("source_data")).get("calendar_wear"))
    underused_slots = tuple(
        dict.fromkeys(
            str(row.get("category_large") or "").strip()
            for row in _rows(calendar.get("not_worn_in_30d_items"))
            if str(row.get("category_large") or "").strip()
        )
    )

    weather_metrics = tuple(
        NumericMetric(name, value)
        for name, raw in _mapping(context.get("weather")).items()
        if (value := _finite(raw)) is not None
    )
    behavior_metrics = tuple(
        NumericMetric(name, value)
        for name, raw in _mapping(behavior.get("summary")).items()
        if (value := _finite(raw)) is not None
    )
    request_text = str(context.get("current_request") or "").strip()
    return StylistStrategyContext(
        request_text=request_text,
        base_search_query=(analysis.search_query or request_text),
        recommendation_mode=recommendation_mode,
        occasion=analysis.conditions.occasion,
        season=analysis.conditions.season,
        preferred_tags=_tag_groups(context, analysis, polarity="preferred"),
        avoided_tags=_tag_groups(context, analysis, polarity="avoided"),
        recent_styles=recent_styles,
        recent_colors=recent_colors,
        recent_fits=recent_fits,
        repeated_slots=repeated_slots,
        underused_slots=underused_slots,
        weather_metrics=weather_metrics,
        behavior_metrics=behavior_metrics,
    )


def _candidate_snapshots(
    candidate: ValidatedRecommendationCandidate,
) -> tuple[dict[str, Any], ...]:
    return (
        candidate.golden.payload,
        *(item.payload for item in candidate.composition.items),
    )


def _preference_fit(
    strategy_context: StylistStrategyContext,
    *,
    styles: tuple[str, ...],
    colors: tuple[str, ...],
    fits: tuple[str, ...],
) -> float:
    candidate_values = {"style": set(styles), "color": set(colors), "fit": set(fits)}
    observations: list[float] = []
    for group in strategy_context.preferred_tags:
        values = candidate_values[group.axis]
        if values:
            observations.append(1.0 if values & set(group.values) else 0.0)
    for group in strategy_context.avoided_tags:
        values = candidate_values[group.axis]
        if values:
            observations.append(0.0 if values & set(group.values) else 1.0)
    return sum(observations) / len(observations) if observations else 0.5


def _history_metrics(
    *,
    raw_context: dict[str, Any],
    strategy_context: StylistStrategyContext,
    candidate: ValidatedRecommendationCandidate,
    styles: tuple[str, ...],
    colors: tuple[str, ...],
    fits: tuple[str, ...],
) -> tuple[dict[str, float], float]:
    metrics: dict[str, float] = {}
    for name, values, recent in (
        ("style_distance", styles, strategy_context.recent_styles),
        ("color_distance", colors, strategy_context.recent_colors),
        ("fit_distance", fits, strategy_context.recent_fits),
    ):
        distance = _jaccard_distance(values, recent)
        if distance is not None:
            metrics[name] = distance

    behavior = _mapping(raw_context.get("behavior_signals"))
    recent = _mapping(
        _mapping(behavior.get("source_data")).get("recent_recommendations")
    )
    recent_items: set[tuple[str, str, str]] = set()
    recent_combinations: set[frozenset[tuple[str, str, str]]] = set()
    for run in _rows(recent.get("runs")):
        for result in _rows(run.get("results")):
            for card in _rows(result.get("cards")):
                identities = {
                    (
                        str(item.get("source_type") or ""),
                        str(item.get("source_collection") or ""),
                        str(item.get("source_id") or ""),
                    )
                    for item in _rows(card.get("items"))
                    if item.get("source_type") and item.get("source_id")
                }
                recent_items.update(identities)
                if identities:
                    recent_combinations.add(frozenset(identities))

    candidate_items = {
        (
            item.source_type.value,
            item.source_collection,
            item.source_id,
        )
        for item in candidate.composition.items
    }
    if candidate_items:
        metrics["item_overlap_ratio"] = len(candidate_items & recent_items) / len(
            candidate_items
        )
        metrics["exact_combination_repeat"] = float(
            frozenset(candidate_items) in recent_combinations
        )

    calendar = _mapping(_mapping(behavior.get("source_data")).get("calendar_wear"))
    worn_ids = {
        str(row.get("wardrobe_item_id"))
        for row in _rows(calendar.get("worn_items"))
        if row.get("wardrobe_item_id")
    }
    underused_ids = {
        str(row.get("wardrobe_item_id"))
        for row in _rows(calendar.get("not_worn_in_30d_items"))
        if row.get("wardrobe_item_id")
    }
    wardrobe_ids = {
        item.source_id
        for item in candidate.composition.items
        if item.source_type is ItemSource.WARDROBE
    }
    denominator = len(candidate.composition.items) or 1
    metrics["strong_preference_overlap_ratio"] = (
        len(wardrobe_ids & worn_ids) / denominator
    )
    underused_ratio = len(wardrobe_ids & underused_ids) / denominator
    return metrics, underused_ratio


def build_strategy_candidate_view(
    *,
    candidate: ValidatedRecommendationCandidate,
    strategy_context: StylistStrategyContext,
    raw_context: dict[str, Any],
    total_budget: int | None = None,
) -> StrategyCandidateView:
    """실제 아이템 식별자는 내부 비교에만 쓰고 전략에는 수치와 태그만 노출한다."""

    snapshots = _candidate_snapshots(candidate)
    styles = _merge(
        *(_snapshot_values(snapshot, _TAG_KEYS["style"]) for snapshot in snapshots)
    )
    colors = _merge(
        *(_snapshot_values(snapshot, _TAG_KEYS["color"]) for snapshot in snapshots)
    )
    fits = _merge(
        *(_snapshot_values(snapshot, _TAG_KEYS["fit"]) for snapshot in snapshots)
    )
    slots = tuple(dict.fromkeys(item.slot_id for item in candidate.composition.items))
    preference_fit = _preference_fit(
        strategy_context,
        styles=styles,
        colors=colors,
        fits=fits,
    )
    metrics = {
        name: value
        for name in _DIRECT_METRICS
        if (value := _numeric_from_snapshots(snapshots, name)) is not None
    }
    item_count = len(candidate.composition.items) or 1
    wardrobe_count = candidate.composition.owned_count
    product_count = candidate.composition.purchasable_count
    metrics.update(
        {
            "wardrobe_item_ratio": wardrobe_count / item_count,
            "new_purchase_ratio": product_count / item_count,
            "preference_fit": preference_fit,
            # 공통 Validator를 통과한 후보만 이 어댑터에 도달한다.
            "tpo_fit": 1.0,
        }
    )
    if total_budget is not None and total_budget > 0:
        metrics["budget_efficiency"] = max(
            0.0,
            1.0 - candidate.validation.effective_total_product_price / total_budget,
        )

    if "silhouette_conflict" not in metrics and fits:
        metrics["silhouette_conflict"] = min(max(len(fits) - 1, 0) / 2, 1.0)
    if "layer_complexity" not in metrics:
        layer_count = sum(
            item.layer_role.strip().upper() in {"OUTER", "MID", "INNER", "LAYER"}
            or "OUTER" in item.slot_id.upper()
            for item in candidate.composition.items
        )
        metrics["layer_complexity"] = min(max(layer_count - 1, 0) / 3, 1.0)

    history, underused_ratio = _history_metrics(
        raw_context=raw_context,
        strategy_context=strategy_context,
        candidate=candidate,
        styles=styles,
        colors=colors,
        fits=fits,
    )
    tag_distances = [
        history[name]
        for name in ("style_distance", "color_distance", "fit_distance")
        if name in history
    ]
    if "novelty" not in metrics:
        metrics["novelty"] = (
            sum(tag_distances) / len(tag_distances) if tag_distances else 0.5
        )
    metrics["underused_item"] = underused_ratio
    if "cross_style" not in metrics:
        metrics["cross_style"] = min(max(len(styles) - 1, 0) / 2, 1.0)

    raw_confidence = _numeric_from_snapshots(snapshots, "tag_confidence")
    tag_confidence = _unit(raw_confidence, default=preference_fit)
    similarity = _unit(candidate.golden.similarity, default=0.0)
    return StrategyCandidateView(
        ordinal=candidate.ordinal,
        base_score=candidate.golden.score,
        similarity=similarity,
        tag_confidence=tag_confidence,
        styles=styles,
        colors=colors,
        fits=fits,
        slots=slots,
        metrics=tuple(
            NumericMetric(name, value) for name, value in sorted(metrics.items())
        ),
        history_metrics=tuple(
            NumericMetric(name, value) for name, value in sorted(history.items())
        ),
    )
