"""실험형 스타일리스트의 LLM 검색 가설 구조와 고정 허용값."""

from __future__ import annotations

from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)


class ExperimentAxis(StrEnum):
    """검색·조합 특성으로 변환할 수 있는 ID 없는 실험 축."""

    TOP_STYLE = "top_style"
    BOTTOM_STYLE = "bottom_style"
    OUTER_STYLE = "outer_style"
    FOOTWEAR_STYLE = "footwear_style"
    STYLE_MIX = "style_mix"
    TOP_SILHOUETTE = "top_silhouette"
    BOTTOM_SILHOUETTE = "bottom_silhouette"
    OUTER_SILHOUETTE = "outer_silhouette"
    COLOR_FAMILY = "color_family"
    COLOR_CONTRAST = "color_contrast"
    PROPORTION = "proportion"
    LAYERING = "layering"
    MATERIAL_MIX = "material_mix"
    PATTERN_DENSITY = "pattern_density"
    UNDERUSED_ITEM_SLOT = "underused_item_slot"


class ExperimentReasonCode(StrEnum):
    """공통 개인화 컨텍스트에서 근거를 확인할 수 있는 가설 사유."""

    RECENT_SLOT_REPETITION = "RECENT_SLOT_REPETITION"
    RECENT_SILHOUETTE_REPETITION = "RECENT_SILHOUETTE_REPETITION"
    RECENT_STYLE_REPETITION = "RECENT_STYLE_REPETITION"
    RECENT_COLOR_REPETITION = "RECENT_COLOR_REPETITION"
    RECENT_COMBINATION_REPETITION = "RECENT_COMBINATION_REPETITION"
    CALENDAR_ITEM_UNDERUSE = "CALENDAR_ITEM_UNDERUSE"
    STRONG_PREFERENCE_ANCHOR = "STRONG_PREFERENCE_ANCHOR"
    SAME_COLOR_MATERIAL_VARIATION = "SAME_COLOR_MATERIAL_VARIATION"


EXPERIMENT_AXIS_VALUES = tuple(axis.value for axis in ExperimentAxis)
EXPERIMENT_REASON_CODE_VALUES = tuple(code.value for code in ExperimentReasonCode)
EXPERIMENT_HYPOTHESIS_COUNT = 2

_PRESERVABLE_AXES = frozenset(ExperimentAxis) - {
    ExperimentAxis.UNDERUSED_ITEM_SLOT,
}
_AXIS_ORDER = {axis: index for index, axis in enumerate(ExperimentAxis)}


class ExperimentalHypothesisCandidate(BaseModel):
    """LLM 구조화 출력에서 개별 검증 전까지 값을 문자열로 보존한다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    change_axes: tuple[str, ...]
    preserve_axes: tuple[str, ...]
    reason_code: str


class ExperimentalHypothesisCandidateBatch(BaseModel):
    """두 후보를 모두 수신한 뒤 각 가설을 독립 검증하기 위한 전송 계약."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hypotheses: tuple[ExperimentalHypothesisCandidate, ...] = Field(
        min_length=EXPERIMENT_HYPOTHESIS_COUNT,
        max_length=EXPERIMENT_HYPOTHESIS_COUNT,
    )


class ExperimentalHypothesis(BaseModel):
    """아이템을 선택하지 않고 검색에서 바꿀 관계와 유지할 관계만 표현한다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    change_axes: tuple[ExperimentAxis, ...] = Field(
        min_length=1,
        max_length=2,
        description="이번 탐색에서 변경할 허용 축 1~2개",
    )
    preserve_axes: tuple[ExperimentAxis, ...] = Field(
        min_length=1,
        max_length=3,
        description="사용자 취향과 익숙함을 위해 유지할 허용 축 1~3개",
    )
    reason_code: ExperimentReasonCode = Field(
        description="최근 추천·착용·선호 데이터로 확인 가능한 제한 사유 코드",
    )

    @field_validator("change_axes", "preserve_axes")
    @classmethod
    def validate_axes(
        cls,
        value: tuple[ExperimentAxis, ...],
        info: ValidationInfo,
    ) -> tuple[ExperimentAxis, ...]:
        if len(value) != len(set(value)):
            raise ValueError(f"{info.field_name}에는 중복 축을 넣을 수 없습니다.")
        if info.field_name == "preserve_axes" and any(
            axis not in _PRESERVABLE_AXES for axis in value
        ):
            raise ValueError(
                "underused_item_slot은 변경 탐색 전용이며 유지 축으로 쓸 수 없습니다."
            )
        return tuple(sorted(value, key=_AXIS_ORDER.__getitem__))

    @model_validator(mode="after")
    def validate_disjoint_axes(self) -> ExperimentalHypothesis:
        overlap = set(self.change_axes) & set(self.preserve_axes)
        if overlap:
            labels = ", ".join(
                axis.value for axis in sorted(overlap, key=_AXIS_ORDER.__getitem__)
            )
            raise ValueError(f"같은 축을 변경하면서 유지할 수 없습니다: {labels}")
        return self


class ExperimentalHypothesisBatch(BaseModel):
    """메인 LLM이 한 요청에서 반환해야 하는 정확히 두 개의 가설."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hypotheses: tuple[ExperimentalHypothesis, ...] = Field(
        min_length=EXPERIMENT_HYPOTHESIS_COUNT,
        max_length=EXPERIMENT_HYPOTHESIS_COUNT,
        description="서로 다른 구조화 검색 가설 두 개",
    )

    @model_validator(mode="after")
    def validate_distinct_hypotheses(self) -> ExperimentalHypothesisBatch:
        signatures = [
            (hypothesis.change_axes, hypothesis.preserve_axes)
            for hypothesis in self.hypotheses
        ]
        if len(signatures) != len(set(signatures)):
            raise ValueError(
                "reason_code만 다른 동일한 변경·유지 축 가설을 중복할 수 없습니다."
            )
        return self
