"""요청 추적과 CloudWatch 친화적 구조화 로그 공통 도구."""

from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    return _request_id.get()


def bind_request_id(value: str) -> Token:
    return _request_id.set(value)


def reset_request_id(token: Token) -> None:
    _request_id.reset(token)


class RequestContextFilter(logging.Filter):
    """API와 워커 로그 모두 request_id 필드를 갖도록 보정한다."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(record, "request_id", get_request_id())
        record.service = getattr(
            record,
            "service",
            os.getenv("SERVICE_NAME", "fashion-api"),
        )
        return True


class JsonFormatter(logging.Formatter):
    """별도 라이브러리 없이 검색 가능한 한 줄 JSON 로그를 만든다."""

    CONTEXT_FIELDS = (
        "request_id",
        "run_id",
        "job_id",
        "look_id",
        "session_id",
        "result_id",
        "card_id",
        "status",
        "event",
        "event_schema_version",
        "error_code",
        "failure_code",
        "duration_ms",
        "recommendation_mode",
        "match_result",
        "selected_similarity",
        "fallback",
        "stage_durations_ms",
        "is_stylist",
        "cache_hit",
        "http_method",
        "path",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service", "fashion-api"),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in self.CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value not in (None, "", "-"):
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(
            payload, ensure_ascii=True, default=str, separators=(",", ":")
        )
