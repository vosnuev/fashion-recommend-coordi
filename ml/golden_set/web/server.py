"""골든셋 확인용 웹 서버.

의존성을 늘리지 않으려고 stdlib ThreadingHTTPServer를 쓴다 —
indexer/product_indexer/product_indexer_api.py와 같은 방식이다. 이 컨테이너는
이미 torch까지 들어 있어서 웹 프레임워크를 더 얹을 이유가 없다.

라우트
  GET  /                     단일 페이지
  GET  /health               헬스체크 (인증 없음)
  GET  /api/status           데이터셋·진행률·Qdrant·run 요약
  GET  /api/outfits          코디 목록 (미처리 원본 포함)
  GET  /api/outfits/{id}     아이템 상세 + presigned 미리보기
  POST /api/scan             스캔 1회 실행 (기본 비활성)
  GET  /api/scan             마지막 스캔 상태

인증: GOLDEN_WEB_TOKEN이 설정돼 있으면 /health를 뺀 모든 경로에
`Authorization: Bearer <token>` 또는 `?token=`을 요구한다. 비워두면 무인증이라
사설망에서만 노출해야 한다.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import secrets
import threading
import traceback
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from ..config import GoldenSettings, load_project_env
from . import service

logger = logging.getLogger("golden_set.web")

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"


class ScanRunner:
    """스캔을 한 번에 하나만 돌린다.

    확인용 화면에서 버튼을 연타해도 무거운 사이클이 겹치지 않게 단일 실행을
    보장한다. 기본은 비활성 — 웹은 API 서버에 있고 임베딩은 GPU 서버 몫이라,
    여기서 스캔을 도는 건 명시적으로 켠 경우에만 허용한다.
    """

    def __init__(self, settings: GoldenSettings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {"status": "IDLE"}

    @property
    def state(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def start(self) -> tuple[bool, dict[str, Any]]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False, dict(self._state)
            self._state = {
                "status": "RUNNING",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            self._thread = threading.Thread(
                target=self._run, name="golden-scan", daemon=True
            )
            self._thread.start()
            return True, dict(self._state)

    def _run(self) -> None:
        # runner를 여기서 import한다 — torch·open_clip 로드를 서버 기동까지
        # 끌고 가지 않기 위해서다(스캔을 안 쓰면 영영 로드하지 않는다).
        from ..runner import run_once

        try:
            summary = run_once(self._settings)
            finished = {
                "status": "SUCCEEDED",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "summary": summary,
            }
        except Exception as error:  # noqa: BLE001 — 실패도 화면에 보여준다
            logger.exception("스캔 실패")
            finished = {
                "status": "FAILED",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": f"{type(error).__name__}: {error}",
                "traceback": traceback.format_exc()[-4000:],
            }
        with self._lock:
            self._state = {**self._state, **finished}


class GoldenWebHandler(BaseHTTPRequestHandler):
    server_version = "GoldenSetWeb/1.0"

    # 주입 (main에서 설정)
    settings: GoldenSettings
    scan_runner: ScanRunner
    allow_scan: bool = False
    token: str = ""

    # ── 라우팅 ────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler 계약
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if not self._authorized(parsed):
            return

        if path == "/":
            self._static(INDEX_FILE)
        elif path == "/api/status":
            self._guarded(lambda: service.collect_status(self.settings))
        elif path == "/api/outfits":
            self._guarded(
                lambda: {"outfits": service.outfit_rows(self.settings)}
            )
        elif path.startswith("/api/outfits/"):
            golden_id = unquote(path[len("/api/outfits/") :])
            self._guarded(
                lambda: service.outfit_detail(self.settings, golden_id)
            )
        elif path == "/api/scan":
            self._json(
                HTTPStatus.OK,
                {"allowed": self.allow_scan, **self.scan_runner.state},
            )
        else:
            self._json(HTTPStatus.NOT_FOUND, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") != "/api/scan":
            self._json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return
        if not self._authorized(parsed):
            return
        if not self.allow_scan:
            self._json(
                HTTPStatus.FORBIDDEN,
                {
                    "detail": "스캔이 비활성 상태입니다. "
                    "GOLDEN_WEB_ALLOW_SCAN=1로 켜세요."
                },
            )
            return
        started, state = self.scan_runner.start()
        self._json(
            HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT,
            {"started": started, **state},
        )

    # ── 보조 ──────────────────────────────────────────────
    def _token_candidates(self, parsed) -> list[tuple[str, str]]:
        """토큰이 실릴 수 있는 모든 자리. (출처 이름, 값)

        하나라도 맞으면 통과시킨다. 앞의 자리가 채워졌다고 뒤를 건너뛰면
        안 된다 — Cloudflare Access 같은 프록시가 자기 JWT를
        `Authorization: Bearer`로 끼워 넣으면 쿼리 파라미터가 통째로
        무시되어, 올바른 토큰을 줘도 401이 난다.
        """
        candidates: list[tuple[str, str]] = []
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            candidates.append(("authorization", header[7:].strip()))
        # 프록시가 Authorization을 점유한 환경을 위한 전용 헤더.
        custom = self.headers.get("X-Golden-Token", "").strip()
        if custom:
            candidates.append(("x-golden-token", custom))
        query = (parse_qs(parsed.query).get("token") or [""])[0]
        if query:
            candidates.append(("query", query))
        return candidates

    def _authorized(self, parsed) -> bool:
        if not self.token:
            return True
        candidates = self._token_candidates(parsed)
        for _, value in candidates:
            try:
                if secrets.compare_digest(value, self.token):
                    return True
            except TypeError:
                # compare_digest는 비ASCII str을 거부한다. 토큰 오탈자로
                # 500을 내지 않고 그냥 불일치로 처리한다.
                continue
        # 값은 절대 남기지 않고, 어느 자리에 무엇이 들어왔는지만 남긴다.
        logger.warning(
            "토큰 불일치: 확인한 자리=%s (기대 길이=%d)",
            [name for name, _ in candidates] or ["없음"],
            len(self.token),
        )
        self._json(
            HTTPStatus.UNAUTHORIZED,
            {
                "detail": "invalid token",
                "checked": [name for name, _ in candidates],
                "hint": (
                    "?token= 또는 X-Golden-Token 헤더로 전달하세요. "
                    "토큰에 +, /, = 가 있으면 URL 인코딩이 필요합니다."
                ),
            },
        )
        return False

    def _guarded(self, func) -> None:
        """조회 실패를 500 JSON으로 바꿔 화면이 원인을 볼 수 있게 한다."""
        try:
            self._json(HTTPStatus.OK, func())
        except Exception as error:  # noqa: BLE001
            logger.exception("요청 처리 실패: %s", self.path)
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"detail": f"{type(error).__name__}: {error}"},
            )

    def _static(self, path: Path) -> None:
        if not path.is_file():
            self._json(HTTPStatus.NOT_FOUND, {"detail": "page not found"})
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "text/plain"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.info("%s - %s", self.address_string(), format % args)


def build_server(settings: GoldenSettings) -> ThreadingHTTPServer:
    host = os.getenv("GOLDEN_WEB_HOST", "0.0.0.0")  # noqa: S104 — 컨테이너 내부
    port = int(os.getenv("GOLDEN_WEB_PORT", "8081"))
    GoldenWebHandler.settings = settings
    GoldenWebHandler.scan_runner = ScanRunner(settings)
    GoldenWebHandler.allow_scan = (
        os.getenv("GOLDEN_WEB_ALLOW_SCAN", "0").strip().lower()
        in {"1", "true", "yes", "y"}
    )
    GoldenWebHandler.token = os.getenv("GOLDEN_WEB_TOKEN", "").strip()
    return ThreadingHTTPServer((host, port), GoldenWebHandler)


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    load_project_env()
    settings = GoldenSettings.from_env()
    server = build_server(settings)
    if not GoldenWebHandler.token:
        logger.warning(
            "GOLDEN_WEB_TOKEN이 비어 있어 무인증으로 뜹니다. 사설망에서만 노출하세요."
        )
    logger.info(
        "골든셋 확인 웹 시작: http://%s:%s (dataset=%s, 스캔 허용=%s)",
        *server.server_address[:2],
        settings.dataset_version,
        GoldenWebHandler.allow_scan,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("종료 요청")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
