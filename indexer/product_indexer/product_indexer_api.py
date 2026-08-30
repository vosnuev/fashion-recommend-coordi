"""GPU 서버에서 product-indexer drain을 시작하는 경량 HTTP API.

POST /v1/product-indexer/drain 의 payload.source로 대상 쇼핑몰을 정한다.
- naver / eleven: 해당 쇼핑몰 작업만 처리하는 drain을 띄운다. 두 쇼핑몰의
  drain은 서로 다른 subprocess라 동시에 실행된다.
- manual: 전체 쇼핑몰을 한 프로세스에서 처리한다.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

logger = logging.getLogger("product_indexer_api")

TRIGGER_PATH = "/v1/product-indexer/drain"
HEALTH_PATH = "/health"


# 트리거 payload의 source → drain 대상. manual은 전체 쇼핑몰을 한 번에 처리한다.
SOURCES = ("naver", "eleven")
ALL_SOURCES_KEY = "all"


def _drain_key(source: str) -> str:
    return source if source in SOURCES else ALL_SOURCES_KEY


class DrainProcessManager:
    """drain subprocess를 쇼핑몰별로 하나씩 실행한다.

    naver와 eleven은 서로 다른 키를 쓰므로 두 drain이 동시에 돌 수 있다.
    같은 키의 drain이 이미 실행 중이면 새로 띄우지 않고 already_running을
    돌려준다 (같은 작업을 중복 선점하지 않기 위함 — catalog API의 행 잠금이
    최종 방어선이지만 불필요한 모델 로드를 막는다).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: dict[str, subprocess.Popen[bytes]] = {}

    def _prune(self) -> None:
        """종료된 프로세스를 정리한다. 호출자가 _lock을 잡고 있어야 한다."""
        for key, process in list(self._processes.items()):
            if process.poll() is not None:
                self._processes.pop(key, None)

    def status(self) -> tuple[bool, int | None]:
        """(하나라도 실행 중인지, 대표 pid)를 반환한다 — 기존 응답 호환용."""
        with self._lock:
            self._prune()
            if not self._processes:
                return False, None
            first = next(iter(self._processes.values()))
            return True, first.pid

    def running(self) -> dict[str, int]:
        """실행 중인 drain의 {키: pid} 전체를 반환한다."""
        with self._lock:
            self._prune()
            return {key: process.pid for key, process in self._processes.items()}

    def start(self, source: str = ALL_SOURCES_KEY) -> tuple[str, int]:
        key = _drain_key(source)
        with self._lock:
            self._prune()
            existing = self._processes.get(key)
            if existing is not None:
                return "already_running", existing.pid

            command = [
                sys.executable,
                "-m",
                "product_indexer.product_indexer",
                "--drain",
            ]
            if key != ALL_SOURCES_KEY:
                command += ["--source", key]
            # 패키지 상대 import가 동작하도록 패키지 상위(/app)에서 실행한다.
            process = subprocess.Popen(
                command,
                cwd=str(Path(__file__).resolve().parents[1]),
            )
            self._processes[key] = process
            threading.Thread(
                target=self._wait_for_exit,
                args=(key, process),
                daemon=True,
            ).start()
            logger.info(
                "product-indexer drain 시작: source=%s, pid=%s",
                key,
                process.pid,
            )
            return "started", process.pid

    def _wait_for_exit(
        self,
        key: str,
        process: subprocess.Popen[bytes],
    ) -> None:
        return_code = process.wait()
        logger.info(
            "product-indexer drain 종료: source=%s, pid=%s, return_code=%s",
            key,
            process.pid,
            return_code,
        )
        with self._lock:
            if self._processes.get(key) is process:
                self._processes.pop(key, None)


manager = DrainProcessManager()


def _authorized(header_value: str | None) -> tuple[bool, bool]:
    """(인증 성공, 서버 token 설정 여부)를 반환한다."""

    expected = os.getenv("PRODUCT_INDEXER_TRIGGER_TOKEN", "").strip()
    if not expected:
        return False, False
    prefix = "Bearer "
    if not header_value or not header_value.startswith(prefix):
        return False, True
    supplied = header_value[len(prefix) :].strip()
    return secrets.compare_digest(supplied, expected), True


class ProductIndexerRequestHandler(BaseHTTPRequestHandler):
    server_version = "SKN28ProductIndexer/1.0"

    def do_GET(self) -> None:
        if self.path != HEALTH_PATH:
            self._json_response(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return
        running, pid = manager.status()
        self._json_response(
            HTTPStatus.OK,
            {
                "status": "ok",
                "drain_running": running,
                "pid": pid,
                # 쇼핑몰별 drain은 동시에 돌 수 있어 개별 상태도 함께 준다.
                "drains": manager.running(),
            },
        )

    def do_POST(self) -> None:
        if self.path != TRIGGER_PATH:
            self._json_response(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return

        authorized, configured = _authorized(self.headers.get("Authorization"))
        if not configured:
            self._json_response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"detail": "trigger token is not configured"},
            )
            return
        if not authorized:
            self._json_response(
                HTTPStatus.UNAUTHORIZED,
                {"detail": "invalid bearer token"},
                extra_headers={"WWW-Authenticate": "Bearer"},
            )
            return

        payload = self._read_json()
        if payload is None:
            return
        if not _valid_payload(payload):
            self._json_response(
                HTTPStatus.BAD_REQUEST,
                {"detail": "source and reason are required"},
            )
            return

        drain_source = _drain_key(payload["source"])
        status, pid = manager.start(drain_source)
        logger.info(
            "원격 drain 트리거 수신: source=%s, drain=%s, reason=%s, "
            "tagged_count=%s, status=%s",
            payload["source"],
            drain_source,
            payload["reason"],
            payload.get("tagged_count"),
            status,
        )
        self._json_response(
            HTTPStatus.ACCEPTED,
            {
                "status": status,
                "pid": pid,
                "source": drain_source,
            },
        )

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("HTTP %s - %s", self.address_string(), format % args)

    def _read_json(self) -> dict[str, Any] | None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if content_length < 1 or content_length > 64 * 1024:
            self._json_response(
                HTTPStatus.BAD_REQUEST,
                {"detail": "invalid request body"},
            )
            return None
        try:
            payload = json.loads(self.rfile.read(content_length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json_response(
                HTTPStatus.BAD_REQUEST,
                {"detail": "invalid JSON"},
            )
            return None
        if not isinstance(payload, dict):
            self._json_response(
                HTTPStatus.BAD_REQUEST,
                {"detail": "JSON object required"},
            )
            return None
        return payload

    def _json_response(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


def _valid_payload(payload: dict[str, Any]) -> bool:
    source = payload.get("source")
    reason = payload.get("reason")
    if source not in set(SOURCES) | {"manual"}:
        return False
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 100:
        return False
    tagged_count = payload.get("tagged_count")
    return tagged_count is None or (
        isinstance(tagged_count, int)
        and not isinstance(tagged_count, bool)
        and tagged_count >= 0
    )


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    host = os.getenv("PRODUCT_INDEXER_API_HOST", "0.0.0.0")
    port = int(os.getenv("PRODUCT_INDEXER_API_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), ProductIndexerRequestHandler)
    logger.info("product-indexer API 시작: %s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("product-indexer API 종료 요청")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
