"""레퍼런스 추천 JSON 이벤트의 기간·모드별 운영 지표 집계."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import boto3

from apps.chat.services.reference_recommendation_events import (
    EVENT_NAME,
    MATCH_NO_CANDIDATE,
    MATCH_STYLE_SIMILAR,
    MATCH_VISUAL_SIMILAR,
    STAGES,
)

RECOMMENDATION_MODES = {"WARDROBE_BASED", "NEW_ITEM"}
PERMISSION_FAILURE_CODES = {"REFERENCE_ITEM_FORBIDDEN"}
VECTOR_MISSING_FAILURE_CODES = {
    "REFERENCE_VECTOR_NOT_FOUND",
    "REFERENCE_VECTOR_MISSING",
}
INDEX_MISMATCH_FAILURE_CODES = {"REFERENCE_INDEX_MISMATCH"}
CLOUDWATCH_QUERY_TERMINAL_STATUSES = {
    "Complete",
    "Failed",
    "Cancelled",
    "Timeout",
    "Unknown",
}


class ReferenceRecommendationMetricsError(RuntimeError):
    """운영 이벤트가 집계 계약을 충족하지 못하는 경우."""


class ReferenceRecommendationMetricsQueryFailed(ReferenceRecommendationMetricsError):
    """CloudWatch Logs Insights 조회가 완료되지 않은 경우."""


class ReferenceRecommendationMetricsQueryTruncated(
    ReferenceRecommendationMetricsError
):
    """조회 상한 때문에 지표가 일부 이벤트만 포함할 위험이 있는 경우."""


@dataclass(frozen=True)
class ReferenceRecommendationMetricsQuery:
    started_at: datetime
    ended_at: datetime
    recommendation_mode: str | None = None
    is_stylist: bool | None = None

    def __post_init__(self) -> None:
        if self.started_at.tzinfo is None or self.ended_at.tzinfo is None:
            raise ValueError("지표 조회 기간에는 시간대 정보가 필요합니다.")
        if self.started_at >= self.ended_at:
            raise ValueError("지표 조회 종료 시각은 시작 시각보다 늦어야 합니다.")
        if (
            self.recommendation_mode is not None
            and self.recommendation_mode not in RECOMMENDATION_MODES
        ):
            raise ValueError("지원하지 않는 레퍼런스 추천 모드입니다.")


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ReferenceRecommendationMetricsError("운영 이벤트 timestamp가 필요합니다.")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ReferenceRecommendationMetricsError(
            "운영 이벤트 timestamp 형식이 올바르지 않습니다."
        ) from exc
    if parsed.tzinfo is None:
        raise ReferenceRecommendationMetricsError(
            "운영 이벤트 timestamp에는 시간대 정보가 필요합니다."
        )
    return parsed.astimezone(UTC)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _average(values: Iterable[float]) -> float | None:
    rows = tuple(values)
    if not rows:
        return None
    return round(sum(rows) / len(rows), 3)


def _summarize(events: list[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(events)
    succeeded = sum(event.get("status") == "SUCCEEDED" for event in events)
    visual_successes = sum(
        event.get("status") == "SUCCEEDED"
        and event.get("match_result") == MATCH_VISUAL_SIMILAR
        for event in events
    )
    style_fallbacks = sum(
        event.get("match_result") == MATCH_STYLE_SIMILAR for event in events
    )
    no_candidates = sum(
        event.get("match_result") == MATCH_NO_CANDIDATE for event in events
    )
    similarities = tuple(
        similarity
        for event in events
        if (similarity := _number(event.get("selected_similarity"))) is not None
    )
    total_durations = tuple(
        duration
        for event in events
        if (duration := _number(event.get("duration_ms"))) is not None
    )

    stage_averages: dict[str, float | None] = {}
    for stage in STAGES:
        values: list[float] = []
        for event in events:
            durations = event.get("stage_durations_ms")
            if not isinstance(durations, Mapping):
                continue
            duration = _number(durations.get(stage))
            # 0은 해당 단계에 진입하지 않은 실행이다. 실행된 단계만 평균낸다.
            if duration is not None and duration > 0:
                values.append(duration)
        stage_averages[stage] = _average(values)

    failure_codes = tuple(
        str(event.get("failure_code") or "") for event in events
    )
    return {
        "total_count": total,
        "success_count": succeeded,
        "success_rate": _rate(succeeded, total),
        "visual_similarity_success_rate": _rate(visual_successes, total),
        "style_fallback_rate": _rate(style_fallbacks, total),
        "no_candidate_rate": _rate(no_candidates, total),
        "average_similarity": _average(similarities),
        "average_duration_ms": _average(total_durations),
        "average_stage_duration_ms": stage_averages,
        "permission_error_count": sum(
            code in PERMISSION_FAILURE_CODES for code in failure_codes
        ),
        "vector_missing_error_count": sum(
            code in VECTOR_MISSING_FAILURE_CODES for code in failure_codes
        ),
        "index_mismatch_error_count": sum(
            code in INDEX_MISMATCH_FAILURE_CODES for code in failure_codes
        ),
    }


def aggregate_reference_recommendation_metrics(
    events: Iterable[Mapping[str, Any]],
    *,
    query: ReferenceRecommendationMetricsQuery,
) -> dict[str, Any]:
    """같은 이벤트 집합을 전체·모드·응답 유형 기준으로 집계한다."""

    started_at = query.started_at.astimezone(UTC)
    ended_at = query.ended_at.astimezone(UTC)
    selected: list[Mapping[str, Any]] = []
    for event in events:
        if event.get("event") != EVENT_NAME:
            continue
        occurred_at = _timestamp(event.get("timestamp"))
        if not started_at <= occurred_at < ended_at:
            continue
        mode = event.get("recommendation_mode")
        if mode not in RECOMMENDATION_MODES:
            raise ReferenceRecommendationMetricsError(
                "운영 이벤트 추천 모드가 올바르지 않습니다."
            )
        is_stylist = event.get("is_stylist")
        if not isinstance(is_stylist, bool):
            raise ReferenceRecommendationMetricsError(
                "운영 이벤트 스타일리스트 여부가 boolean이 아닙니다."
            )
        if query.recommendation_mode and mode != query.recommendation_mode:
            continue
        if query.is_stylist is not None and is_stylist != query.is_stylist:
            continue
        selected.append(event)

    by_mode = {
        mode: _summarize(
            [event for event in selected if event["recommendation_mode"] == mode]
        )
        for mode in sorted(RECOMMENDATION_MODES)
        if any(event["recommendation_mode"] == mode for event in selected)
    }
    by_response_mode = {
        response_mode: _summarize(
            [
                event
                for event in selected
                if bool(event["is_stylist"]) == expected_stylist
            ]
        )
        for response_mode, expected_stylist in (
            ("DEFAULT", False),
            ("STYLIST", True),
        )
        if any(
            bool(event["is_stylist"]) == expected_stylist for event in selected
        )
    }
    return {
        "period": {
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
        },
        "filters": {
            "recommendation_mode": query.recommendation_mode,
            "response_mode": (
                "STYLIST"
                if query.is_stylist is True
                else "DEFAULT"
                if query.is_stylist is False
                else None
            ),
        },
        "overall": _summarize(selected),
        "by_mode": by_mode,
        "by_response_mode": by_response_mode,
    }


class CloudWatchReferenceRecommendationEventSource:
    """CloudWatch Logs Insights에서 작업 6의 JSON 이벤트를 읽는다."""

    def __init__(
        self,
        *,
        log_group_name: str,
        region_name: str,
        limit: int = 10_000,
        poll_interval_seconds: float = 0.5,
        timeout_seconds: float = 30.0,
        client=None,
    ) -> None:
        if not log_group_name.strip():
            raise ValueError("레퍼런스 추천 로그 그룹 이름이 필요합니다.")
        if not 1 <= limit <= 10_000:
            raise ValueError("CloudWatch 조회 limit은 1~10000이어야 합니다.")
        self.log_group_name = log_group_name.strip()
        self.limit = limit
        self.poll_interval_seconds = max(0.05, poll_interval_seconds)
        self.timeout_seconds = max(1.0, timeout_seconds)
        self.client = client or boto3.client("logs", region_name=region_name)

    def fetch(
        self,
        query: ReferenceRecommendationMetricsQuery,
    ) -> list[dict[str, Any]]:
        filters = [f'filter event = "{EVENT_NAME}"']
        if query.recommendation_mode:
            filters.append(
                f'filter recommendation_mode = "{query.recommendation_mode}"'
            )
        if query.is_stylist is not None:
            filters.append(
                f"filter is_stylist = {str(query.is_stylist).lower()}"
            )
        query_string = "\n| ".join(
            (
                "fields @timestamp, @message",
                *filters,
                "sort @timestamp asc",
                f"limit {self.limit}",
            )
        )
        response = self.client.start_query(
            logGroupName=self.log_group_name,
            startTime=int(query.started_at.timestamp()),
            endTime=int(query.ended_at.timestamp()),
            queryString=query_string,
            limit=self.limit,
        )
        query_id = response["queryId"]
        deadline = time.monotonic() + self.timeout_seconds
        result: dict[str, Any] = {}
        while time.monotonic() < deadline:
            result = self.client.get_query_results(queryId=query_id)
            status = str(result.get("status") or "Unknown")
            if status in CLOUDWATCH_QUERY_TERMINAL_STATUSES:
                break
            time.sleep(self.poll_interval_seconds)
        status = str(result.get("status") or "Timeout")
        if status != "Complete":
            raise ReferenceRecommendationMetricsQueryFailed(
                f"CloudWatch 지표 조회가 완료되지 않았습니다: {status}"
            )

        rows: list[dict[str, Any]] = []
        for result_row in result.get("results") or []:
            fields = {
                str(field.get("field")): field.get("value")
                for field in result_row
                if isinstance(field, Mapping)
            }
            raw_message = fields.get("@message")
            if not isinstance(raw_message, str):
                continue
            try:
                event = json.loads(raw_message)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if "timestamp" not in event and isinstance(fields.get("@timestamp"), str):
                event["timestamp"] = fields["@timestamp"]
            rows.append(event)
        if len(rows) >= self.limit:
            raise ReferenceRecommendationMetricsQueryTruncated(
                "CloudWatch 조회 상한에 도달했습니다. 기간을 더 짧게 나눠 조회해 주세요."
            )
        return rows

