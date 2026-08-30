"""API 요청 상관관계 ID 미들웨어."""

from __future__ import annotations

import logging
import re
import time
import uuid

from config.observability import bind_request_id, reset_request_id

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
logger = logging.getLogger("apps.http")


class RequestIdMiddleware:
    header_name = "X-Request-ID"

    def __init__(self, get_response) -> None:
        self.get_response = get_response

    def __call__(self, request):
        started = time.monotonic()
        supplied = request.headers.get(self.header_name, "").strip()
        request_id = (
            supplied if _SAFE_REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex
        )
        request.request_id = request_id
        token = bind_request_id(request_id)
        try:
            response = self.get_response(request)
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "HTTP %s %s status=%s duration_ms=%s",
                request.method,
                request.path,
                response.status_code,
                duration_ms,
                extra={
                    "request_id": request_id,
                    "http_method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
            response[self.header_name] = request_id
            return response
        finally:
            reset_request_id(token)
