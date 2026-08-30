"""컨테이너·ALB용 liveness/readiness 엔드포인트."""

from __future__ import annotations

import time
from collections.abc import Callable

import redis
from django.conf import settings
from django.db import connection
from django.http import JsonResponse

from apps.recommend.services.qdrant import get_client as get_qdrant_client


def live(_request) -> JsonResponse:
    """프로세스가 HTTP 요청을 처리할 수 있는지만 확인한다."""
    return JsonResponse({"status": "ok"})


def _check_postgres() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def _check_redis() -> None:
    kwargs: dict = {
        "socket_connect_timeout": settings.HEALTHCHECK_TIMEOUT_SECONDS,
        "socket_timeout": settings.HEALTHCHECK_TIMEOUT_SECONDS,
    }
    if settings.REDIS_PASSWORD:
        kwargs["password"] = settings.REDIS_PASSWORD
    redis.Redis.from_url(settings.REDIS_URL, **kwargs).ping()


def _check_qdrant() -> None:
    get_qdrant_client().get_collections()


def _timed(check: Callable[[], None]) -> dict:
    started = time.monotonic()
    try:
        check()
    except Exception as exc:  # noqa: BLE001 - 상태 확인은 제공자 예외를 동일 계약으로 변환한다.
        return {
            "status": "unavailable",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": type(exc).__name__,
        }
    return {
        "status": "ok",
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


def ready(_request) -> JsonResponse:
    """채팅 추천 요청 처리에 필수인 DB·Redis·Qdrant 연결을 확인한다."""
    checks = {
        "postgres": _timed(_check_postgres),
        "redis": _timed(_check_redis),
        "qdrant": _timed(_check_qdrant),
    }
    healthy = all(row["status"] == "ok" for row in checks.values())
    return JsonResponse(
        {"status": "ready" if healthy else "not_ready", "checks": checks},
        status=200 if healthy else 503,
    )
