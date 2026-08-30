"""공유 옷 레퍼런스 추천의 개인정보 비포함 운영 이벤트."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

EVENT_NAME = "REFERENCE_RECOMMENDATION_RESULT"
EVENT_SCHEMA_VERSION = "1.0"

MATCH_VISUAL_SIMILAR = "VISUAL_SIMILAR"
MATCH_STYLE_SIMILAR = "STYLE_SIMILAR"
MATCH_NO_CANDIDATE = "NO_CANDIDATE"
MATCH_RESULTS = {
    MATCH_VISUAL_SIMILAR,
    MATCH_STYLE_SIMILAR,
    MATCH_NO_CANDIDATE,
}

STAGE_SNAPSHOT_VALIDATION = "SNAPSHOT_VALIDATION"
STAGE_VECTOR_LOADING = "VECTOR_LOADING"
STAGE_SIMILAR_SEARCH = "SIMILAR_SEARCH"
STAGE_COMPOSER = "COMPOSER"
STAGE_VALIDATOR = "VALIDATOR"
STAGES = (
    STAGE_SNAPSHOT_VALIDATION,
    STAGE_VECTOR_LOADING,
    STAGE_SIMILAR_SEARCH,
    STAGE_COMPOSER,
    STAGE_VALIDATOR,
)

logger = logging.getLogger("apps.chat.reference_recommendation")


def _failure_code(error: BaseException) -> str:
    """래핑된 예외의 가장 구체적인 안정 코드만 꺼낸다.

    예외 메시지는 사용자 입력이나 외부 시스템 주소를 포함할 수 있으므로 이벤트에
    절대 싣지 않는다.
    """

    current: BaseException | None = error
    visited: set[int] = set()
    fallback = "REFERENCE_RECOMMENDATION_FAILED"
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        code = getattr(current, "code", None)
        if isinstance(code, str) and code.strip():
            fallback = code.strip()
        current = current.__cause__ or current.__context__
    return fallback


@dataclass
class ReferenceRecommendationEventRecorder:
    """한 번의 레퍼런스 추천 실행을 성공 또는 실패 이벤트 한 건으로 만든다."""

    run_id: str
    recommendation_mode: str
    is_stylist: bool
    clock: Callable[[], float] = time.perf_counter
    _started_at: float = field(init=False)
    _stage_durations_ms: dict[str, float] = field(init=False)
    _match_result: str = field(init=False, default=MATCH_NO_CANDIDATE)
    _selected_similarity: float | None = field(init=False, default=None)
    _emitted: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self._started_at = self.clock()
        self._stage_durations_ms = {stage: 0.0 for stage in STAGES}

    @contextmanager
    def measure(self, stage: str) -> Iterator[None]:
        if stage not in self._stage_durations_ms:
            raise ValueError(f"지원하지 않는 레퍼런스 추천 처리 단계입니다: {stage}")
        started_at = self.clock()
        try:
            yield
        finally:
            self.add_stage_duration(stage, (self.clock() - started_at) * 1000)

    def add_stage_duration(self, stage: str, duration_ms: float) -> None:
        if stage not in self._stage_durations_ms:
            raise ValueError(f"지원하지 않는 레퍼런스 추천 처리 단계입니다: {stage}")
        self._stage_durations_ms[stage] += max(0.0, float(duration_ms))

    def select_match(self, *, match_result: str, similarity: float) -> None:
        if match_result not in MATCH_RESULTS - {MATCH_NO_CANDIDATE}:
            raise ValueError(f"지원하지 않는 레퍼런스 매칭 결과입니다: {match_result}")
        self._match_result = match_result
        self._selected_similarity = round(float(similarity), 6)

    def success(self) -> None:
        self._emit(status="SUCCEEDED", failure_code=None)

    def failure(self, error: BaseException) -> None:
        self._emit(status="FAILED", failure_code=_failure_code(error))

    def _emit(self, *, status: str, failure_code: str | None) -> None:
        if self._emitted:
            return
        self._emitted = True
        duration_ms = max(0.0, (self.clock() - self._started_at) * 1000)
        logger.info(
            "레퍼런스 추천 운영 이벤트",
            extra={
                "event": EVENT_NAME,
                "event_schema_version": EVENT_SCHEMA_VERSION,
                "run_id": self.run_id,
                "status": status,
                "recommendation_mode": self.recommendation_mode,
                "match_result": self._match_result,
                "selected_similarity": self._selected_similarity,
                "fallback": self._match_result == MATCH_STYLE_SIMILAR,
                "stage_durations_ms": {
                    stage: round(value, 3)
                    for stage, value in self._stage_durations_ms.items()
                },
                "failure_code": failure_code,
                "is_stylist": self.is_stylist,
                "duration_ms": round(duration_ms, 3),
            },
        )

