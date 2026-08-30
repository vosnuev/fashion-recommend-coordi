"""실험형 가설 LLM 실패 시 사용하는 결정적 규칙 fallback."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from pydantic import ValidationError as PydanticValidationError

from apps.chat.services.experimental_hypotheses import (
    ExperimentalHypothesis,
    ExperimentalHypothesisBatch,
    ExperimentalHypothesisCandidateBatch,
    ExperimentAxis,
    ExperimentReasonCode,
)
from apps.chat.services.experimental_hypothesis_generation import (
    build_experimental_hypothesis_payload,
)
from apps.chat.services.openai_adapter import (
    ChatLLMError,
    LLMUsage,
    OpenAIChatAdapter,
)

_SLOT_STYLE_AXES = (
    (("BOTTOM", "PANTS", "SKIRT", "하의"), ExperimentAxis.BOTTOM_STYLE),
    (("TOP", "상의"), ExperimentAxis.TOP_STYLE),
    (("OUTER", "아우터"), ExperimentAxis.OUTER_STYLE),
    (("SHOE", "FOOTWEAR", "신발"), ExperimentAxis.FOOTWEAR_STYLE),
    (("DRESS", "ONEPIECE", "원피스", "세트"), ExperimentAxis.PROPORTION),
)
_SLOT_SILHOUETTE_AXES = (
    (("BOTTOM", "PANTS", "SKIRT", "하의"), ExperimentAxis.BOTTOM_SILHOUETTE),
    (("TOP", "상의"), ExperimentAxis.TOP_SILHOUETTE),
    (("OUTER", "아우터"), ExperimentAxis.OUTER_SILHOUETTE),
)


class ExperimentalHypothesisSource(StrEnum):
    LLM = "LLM"
    HYBRID = "HYBRID"
    RULE_FALLBACK = "RULE_FALLBACK"


@dataclass(frozen=True)
class ResolvedExperimentalHypotheses:
    batch: ExperimentalHypothesisBatch
    source: ExperimentalHypothesisSource
    usage: LLMUsage = field(default_factory=LLMUsage)
    response_id: str = ""
    fallback_error_code: str = ""
    llm_accepted_count: int = 0
    llm_rejection_codes: tuple[str, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        """ChatRunPersona.hypothesis_snapshot에 저장 가능한 ID 없는 결과."""

        return {
            "source": self.source.value,
            "hypotheses": self.batch.model_dump(mode="json")["hypotheses"],
            "fallback_error_code": self.fallback_error_code or None,
            "llm_accepted_count": self.llm_accepted_count,
            "llm_rejected_count": len(self.llm_rejection_codes),
            "llm_rejection_codes": list(self.llm_rejection_codes),
        }


@dataclass(frozen=True)
class FilteredExperimentalHypotheses:
    hypotheses: tuple[ExperimentalHypothesis, ...]
    rejection_codes: tuple[str, ...]


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _slot_axis(
    slot: object,
    mappings: tuple[tuple[tuple[str, ...], ExperimentAxis], ...],
) -> ExperimentAxis | None:
    normalized = str(slot or "").strip().upper().replace(" ", "_")
    if not normalized:
        return None
    return next(
        (
            axis
            for tokens, axis in mappings
            if any(token in normalized for token in tokens)
        ),
        None,
    )


def _preserve_axes(
    change_axes: tuple[ExperimentAxis, ...],
) -> tuple[ExperimentAxis, ...]:
    anchors = [ExperimentAxis.TOP_STYLE, ExperimentAxis.COLOR_FAMILY]
    return tuple(axis for axis in anchors if axis not in change_axes)


def _hypothesis(
    *,
    change_axes: tuple[ExperimentAxis, ...],
    reason_code: ExperimentReasonCode,
) -> ExperimentalHypothesis:
    return ExperimentalHypothesis(
        change_axes=change_axes,
        preserve_axes=_preserve_axes(change_axes),
        reason_code=reason_code,
    )


def _has_underused_features(calendar: dict[str, Any]) -> bool:
    features = _mapping(calendar.get("underused_item_features"))
    return any(_rows(value) for value in features.values())


def _has_count_at_least(rows: object, minimum: int = 2) -> bool:
    return any(int(row.get("count") or 0) >= minimum for row in _rows(rows))


def _reason_has_evidence(
    hypothesis: ExperimentalHypothesis,
    *,
    payload: dict[str, Any],
) -> bool:
    behavior = _mapping(payload.get("behavior"))
    recent = _mapping(behavior.get("recent_recommendations"))
    calendar = _mapping(behavior.get("calendar_wear"))
    summary = _mapping(behavior.get("summary"))
    reason = hypothesis.reason_code
    if reason == ExperimentReasonCode.RECENT_SLOT_REPETITION:
        return _has_count_at_least(recent.get("repeated_slots"))
    if reason == ExperimentReasonCode.RECENT_SILHOUETTE_REPETITION:
        return _has_count_at_least(recent.get("fit_counts"))
    if reason == ExperimentReasonCode.RECENT_STYLE_REPETITION:
        return _has_count_at_least(recent.get("style_counts"))
    if reason == ExperimentReasonCode.RECENT_COLOR_REPETITION:
        return _has_count_at_least(recent.get("color_counts"))
    if reason == ExperimentReasonCode.RECENT_COMBINATION_REPETITION:
        return any(
            int(count or 0) >= 2
            for count in recent.get("repeated_combination_counts") or []
        )
    if reason == ExperimentReasonCode.CALENDAR_ITEM_UNDERUSE:
        return _has_underused_features(calendar)
    if reason == ExperimentReasonCode.STRONG_PREFERENCE_ANCHOR:
        return any(
            int(summary.get(key) or 0) > 0
            for key in (
                "calendar_registrations_30d",
                "worn_item_occurrences_30d",
            )
        )
    return reason == ExperimentReasonCode.SAME_COLOR_MATERIAL_VARIATION


def _reason_matches_axes(
    hypothesis: ExperimentalHypothesis,
    *,
    payload: dict[str, Any],
) -> bool:
    reason = hypothesis.reason_code
    changed = set(hypothesis.change_axes)
    if reason == ExperimentReasonCode.RECENT_SLOT_REPETITION:
        recent = _mapping(
            _mapping(payload.get("behavior")).get("recent_recommendations")
        )
        expected = {
            axis
            for row in _rows(recent.get("repeated_slots"))
            if int(row.get("count") or 0) >= 2
            if (axis := _slot_axis(row.get("slot"), _SLOT_STYLE_AXES)) is not None
        }
        return bool(changed & expected)
    if reason == ExperimentReasonCode.RECENT_SILHOUETTE_REPETITION:
        return bool(
            changed
            & {
                ExperimentAxis.TOP_SILHOUETTE,
                ExperimentAxis.BOTTOM_SILHOUETTE,
                ExperimentAxis.OUTER_SILHOUETTE,
            }
        )
    if reason == ExperimentReasonCode.RECENT_STYLE_REPETITION:
        return bool(
            changed
            & {
                ExperimentAxis.TOP_STYLE,
                ExperimentAxis.BOTTOM_STYLE,
                ExperimentAxis.OUTER_STYLE,
                ExperimentAxis.FOOTWEAR_STYLE,
                ExperimentAxis.STYLE_MIX,
            }
        )
    if reason == ExperimentReasonCode.RECENT_COLOR_REPETITION:
        return bool(
            changed
            & {ExperimentAxis.COLOR_FAMILY, ExperimentAxis.COLOR_CONTRAST}
        )
    if reason == ExperimentReasonCode.RECENT_COMBINATION_REPETITION:
        return bool(
            changed
            & {
                ExperimentAxis.STYLE_MIX,
                ExperimentAxis.PROPORTION,
                ExperimentAxis.LAYERING,
                ExperimentAxis.MATERIAL_MIX,
                ExperimentAxis.PATTERN_DENSITY,
            }
        )
    if reason == ExperimentReasonCode.CALENDAR_ITEM_UNDERUSE:
        return ExperimentAxis.UNDERUSED_ITEM_SLOT in changed
    if reason == ExperimentReasonCode.SAME_COLOR_MATERIAL_VARIATION:
        return bool(
            changed & {ExperimentAxis.MATERIAL_MIX, ExperimentAxis.PROPORTION}
        )
    return reason == ExperimentReasonCode.STRONG_PREFERENCE_ANCHOR


def filter_experimental_hypotheses(
    value: object,
    *,
    context: dict[str, Any],
) -> FilteredExperimentalHypotheses:
    """LLM 후보를 구조·중복·근거 순서로 한 행씩 독립 검증한다."""

    if isinstance(
        value,
        (ExperimentalHypothesisCandidateBatch, ExperimentalHypothesisBatch),
    ):
        rows = value.model_dump(mode="json")["hypotheses"]
    else:
        raise ChatLLMError("실험형 가설 구조화 응답 형식이 잘못되었습니다.")

    payload = build_experimental_hypothesis_payload(context)
    accepted: list[ExperimentalHypothesis] = []
    signatures: set[
        tuple[tuple[ExperimentAxis, ...], tuple[ExperimentAxis, ...]]
    ] = set()
    rejection_codes: list[str] = []
    for row in rows:
        try:
            hypothesis = ExperimentalHypothesis.model_validate(row)
        except PydanticValidationError:
            rejection_codes.append("INVALID_HYPOTHESIS_SCHEMA")
            continue
        signature = (hypothesis.change_axes, hypothesis.preserve_axes)
        if signature in signatures:
            rejection_codes.append("DUPLICATE_HYPOTHESIS")
            continue
        if not _reason_matches_axes(hypothesis, payload=payload):
            rejection_codes.append("REASON_AXIS_MISMATCH")
            continue
        if not _reason_has_evidence(hypothesis, payload=payload):
            rejection_codes.append("UNSUPPORTED_REASON_EVIDENCE")
            continue
        accepted.append(hypothesis)
        signatures.add(signature)
    return FilteredExperimentalHypotheses(
        hypotheses=tuple(accepted),
        rejection_codes=tuple(rejection_codes),
    )


def _complete_with_rule_fallback(
    hypotheses: tuple[ExperimentalHypothesis, ...],
    *,
    context: dict[str, Any],
) -> ExperimentalHypothesisBatch:
    selected = list(hypotheses)
    signatures = {(row.change_axes, row.preserve_axes) for row in selected}
    for row in build_rule_based_experimental_hypotheses(context).hypotheses:
        signature = (row.change_axes, row.preserve_axes)
        if len(selected) >= 2:
            break
        if signature not in signatures:
            selected.append(row)
            signatures.add(signature)
    return ExperimentalHypothesisBatch(hypotheses=tuple(selected[:2]))


def build_rule_based_experimental_hypotheses(
    context: dict[str, Any],
) -> ExperimentalHypothesisBatch:
    """가용 근거를 우선순위대로 적용해 서로 다른 가설 두 개를 만든다."""

    payload = build_experimental_hypothesis_payload(context)
    behavior = _mapping(payload.get("behavior"))
    recent = _mapping(behavior.get("recent_recommendations"))
    calendar = _mapping(behavior.get("calendar_wear"))
    hypotheses: list[ExperimentalHypothesis] = []

    repeated_slots = sorted(
        _rows(recent.get("repeated_slots")),
        key=lambda row: (-int(row.get("count") or 0), str(row.get("slot") or "")),
    )
    repeated_slot_axis = next(
        (
            axis
            for row in repeated_slots
            if int(row.get("count") or 0) >= 2
            if (axis := _slot_axis(row.get("slot"), _SLOT_STYLE_AXES)) is not None
        ),
        None,
    )
    if repeated_slot_axis is not None:
        hypotheses.append(
            _hypothesis(
                change_axes=(repeated_slot_axis,),
                reason_code=ExperimentReasonCode.RECENT_SLOT_REPETITION,
            )
        )

    repeated_fit = next(
        (
            row
            for row in _rows(recent.get("fit_counts"))
            if int(row.get("count") or 0) >= 2
        ),
        None,
    )
    if repeated_fit is not None:
        silhouette_axis = next(
            (
                axis
                for row in repeated_slots
                if (axis := _slot_axis(row.get("slot"), _SLOT_SILHOUETTE_AXES))
                is not None
            ),
            ExperimentAxis.BOTTOM_SILHOUETTE,
        )
        hypotheses.append(
            _hypothesis(
                change_axes=(silhouette_axis,),
                reason_code=ExperimentReasonCode.RECENT_SILHOUETTE_REPETITION,
            )
        )

    if len(hypotheses) < 2 and _has_underused_features(calendar):
        hypotheses.append(
            _hypothesis(
                change_axes=(ExperimentAxis.UNDERUSED_ITEM_SLOT,),
                reason_code=ExperimentReasonCode.CALENDAR_ITEM_UNDERUSE,
            )
        )

    conservative_rules = (
        _hypothesis(
            change_axes=(ExperimentAxis.MATERIAL_MIX,),
            reason_code=ExperimentReasonCode.SAME_COLOR_MATERIAL_VARIATION,
        ),
        _hypothesis(
            change_axes=(ExperimentAxis.PROPORTION,),
            reason_code=ExperimentReasonCode.SAME_COLOR_MATERIAL_VARIATION,
        ),
    )
    existing_signatures = {(row.change_axes, row.preserve_axes) for row in hypotheses}
    for row in conservative_rules:
        signature = (row.change_axes, row.preserve_axes)
        if len(hypotheses) >= 2:
            break
        if signature not in existing_signatures:
            hypotheses.append(row)
            existing_signatures.add(signature)

    return ExperimentalHypothesisBatch(hypotheses=tuple(hypotheses[:2]))


class ExperimentalHypothesisResolver:
    """LLM 결과를 우선하고 제공자 실패 시 규칙 결과로 안전하게 전환한다."""

    def __init__(self, *, llm: OpenAIChatAdapter | None = None) -> None:
        self.llm = llm or OpenAIChatAdapter()

    def resolve(
        self,
        *,
        identity_id: str,
        context: dict[str, Any],
    ) -> ResolvedExperimentalHypotheses:
        try:
            result = self.llm.generate_experimental_hypotheses(
                identity_id=identity_id,
                context=context,
            )
            filtered = filter_experimental_hypotheses(
                result.value,
                context=context,
            )
            batch = _complete_with_rule_fallback(
                filtered.hypotheses,
                context=context,
            )
            if len(filtered.hypotheses) == 2:
                source = ExperimentalHypothesisSource.LLM
                fallback_error_code = ""
            elif filtered.hypotheses:
                source = ExperimentalHypothesisSource.HYBRID
                fallback_error_code = "EXPERIMENTAL_HYPOTHESIS_PARTIAL_REJECTION"
            else:
                source = ExperimentalHypothesisSource.RULE_FALLBACK
                fallback_error_code = "EXPERIMENTAL_HYPOTHESES_REJECTED"
            return ResolvedExperimentalHypotheses(
                batch=batch,
                source=source,
                usage=result.usage,
                response_id=result.response_id,
                fallback_error_code=fallback_error_code,
                llm_accepted_count=len(filtered.hypotheses),
                llm_rejection_codes=filtered.rejection_codes,
            )
        except (ChatLLMError, ImproperlyConfigured) as exc:
            error_code = getattr(exc, "code", "CHAT_LLM_CONFIGURATION_ERROR")
            return ResolvedExperimentalHypotheses(
                batch=build_rule_based_experimental_hypotheses(context),
                source=ExperimentalHypothesisSource.RULE_FALLBACK,
                fallback_error_code=str(error_code),
            )
