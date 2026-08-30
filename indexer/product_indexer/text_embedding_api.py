"""질의 텍스트를 골든셋과 같은 공간의 BGE-M3 벡터로 만들어 주는 HTTP API.

채팅 추천은 사용자 문장("토요일 성수동 데이트 룩")을 벡터로 바꿔 골든 코디를
찾는다. 그 변환을 Django API 컨테이너에서 직접 하면 CPU 서버에 2.3GB 모델을
얹어야 해서, GPU 서버에 이 서비스를 두고 API는 HTTP로 물어본다.
(호출자: api/apps/recommend/services/text_embedding.py)

⚠️ **벡터 공간이 골든셋과 같아야 한다.** 골든 코디는 ml/golden_set/embedding.py의
   BgeM3Backend가 SentenceTransformer(BAAI/bge-m3, max_seq_length=512,
   normalize_embeddings=True)로 만든 벡터로 색인돼 있다. 여기서 쓰는
   BgeM3Embedder도 같은 설정이라 두 벡터를 그대로 비교할 수 있다. 모델이나
   정규화 방식을 바꾸면 **골든셋을 다시 색인해야** 한다 — 안 그러면 검색이
   실패하지 않고 조용히 엉뚱한 코디를 고른다.

요청·응답 계약은 호출자가 소유한다 (계약을 바꾸려면 양쪽을 같이 고칠 것):
    POST {TEXT_EMBEDDING_API_URL}
    Authorization: Bearer {TEXT_EMBEDDING_API_TOKEN}
    {"texts": ["..."]}
  → 200 {"vectors": [[...1024개...]], "model": "...", "version": "...", "dim": 1024}
  → 오류는 {"detail": "..."} (호출자가 이 키를 읽어 로그에 남긴다)

product-indexer와 **같은 이미지**를 쓴다. bge-m3 의존성과 HF 캐시가 이미 그쪽에
있어서 새 이미지를 만들 이유가 없다. 다만 프로세스는 나눈다 — drain이 GPU를
길게 잡는 동안 질의 임베딩까지 막히면 채팅이 통째로 느려진다.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from util.env import load_project_env

# 리포 체크아웃이면 루트 .env를, 컨테이너면 compose가 주입한 환경변수를 쓴다.
load_project_env(__file__)

logger = logging.getLogger("text_embedding_api")

EMBED_PATH = "/v1/text-embeddings"
HEALTH_PATH = "/health"

# 호출자(Django)가 한 번에 한 문장만 보내지만, 배치 색인 재사용을 막지 않는 선에서
# 상한을 둔다. 상한이 없으면 요청 하나가 GPU를 통째로 오래 잡는다.
MAX_TEXTS = max(1, int(os.getenv("TEXT_EMBEDDING_MAX_TEXTS", "16")))
# 호출자도 2,000자에서 자른다 (text_embedding.py). 여기서도 같은 값으로 막는다.
MAX_TEXT_CHARS = 2_000
MAX_BODY_BYTES = 256 * 1024


def _model_id() -> str:
    """골든셋과 같은 모델을 쓴다.

    폴백 순서는 ml/golden_set/config.py와 일부러 같게 맞춘다. 골든셋만
    GOLDEN_TEXT_EMBED_MODEL로 바꾸고 여기를 안 바꾸면 벡터 공간이 갈라지는데,
    검색이 실패하지 않고 조용히 엉뚱한 코디를 고르기 때문에 늦게 발견된다.
    """
    return (
        os.getenv("TEXT_EMBEDDING_MODEL", "").strip()
        or os.getenv("GOLDEN_TEXT_EMBED_MODEL", "").strip()
        or os.getenv("TEXT_EMBED_MODEL", "").strip()
        or "BAAI/bge-m3"
    )


class TextEncoder:
    """모델 하나를 감싸 요청 사이에 재사용한다.

    ThreadingHTTPServer는 요청마다 스레드를 만드는데, 같은 torch 모듈에 동시에
    들어가면 GPU 메모리 사용이 요청 수만큼 늘고 순서도 보장되지 않는다.
    인코딩 구간을 잠가 한 번에 하나씩만 돌린다 — 질의는 짧아서 직렬화해도
    체감 지연이 크지 않다.
    """

    def __init__(self, *, model_id: str, version: str, device: str) -> None:
        self.model_id = model_id
        self.version = version
        self.device = device
        self._lock = threading.Lock()
        self._embedder: Any | None = None

    def load(self) -> None:
        """모델을 미리 올린다.

        첫 요청에서 로드하면 그 요청만 30초 넘게 걸려 호출자의 15초 타임아웃에
        걸린다. 기동할 때 올려두고, 그동안은 /health도 응답하지 않게 둔다
        (아직 준비되지 않은 것이 맞다).
        """
        # 무거운 임포트라 모듈 로드 경로에서 뺀다 (테스트가 torch 없이 이 모듈을
        # 임포트할 수 있어야 한다).
        from .bge_embedder import BgeM3Embedder

        self._embedder = BgeM3Embedder(self.model_id, device=self.device)
        logger.info(
            "텍스트 임베딩 모델 준비 완료: %s (dim=%s, device=%s)",
            self.model_id,
            self._embedder.dim,
            self.device,
        )

    @property
    def dim(self) -> int:
        return int(self._embedder.dim) if self._embedder is not None else 0

    def encode(self, texts: list[str]) -> list[list[float]]:
        if self._embedder is None:
            raise RuntimeError("텍스트 임베딩 모델이 아직 준비되지 않았습니다.")
        with self._lock:
            vectors = self._embedder.encode_texts(texts)
        return [[float(value) for value in row] for row in vectors]


# main()이 채운다. 테스트는 이 자리에 가짜 인코더를 넣는다.
encoder: TextEncoder | None = None


def _authorized(header_value: str | None) -> tuple[bool, bool]:
    """(인증 성공, 서버 token 설정 여부)를 반환한다."""
    expected = os.getenv("TEXT_EMBEDDING_API_TOKEN", "").strip()
    if not expected:
        return False, False
    prefix = "Bearer "
    if not header_value or not header_value.startswith(prefix):
        return False, True
    supplied = header_value[len(prefix) :].strip()
    return secrets.compare_digest(supplied, expected), True


def _valid_texts(payload: dict[str, Any]) -> list[str] | None:
    raw = payload.get("texts")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_TEXTS:
        return None
    texts: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text or len(text) > MAX_TEXT_CHARS:
            return None
        texts.append(text)
    return texts


class TextEmbeddingRequestHandler(BaseHTTPRequestHandler):
    server_version = "SKN28TextEmbedding/1.0"

    def do_GET(self) -> None:
        if self.path != HEALTH_PATH:
            self._json_response(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return
        ready = encoder is not None and encoder.dim > 0
        self._json_response(
            HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "status": "ok" if ready else "loading",
                "model": encoder.model_id if encoder else "",
                "version": encoder.version if encoder else "",
                "dim": encoder.dim if encoder else 0,
            },
        )

    def do_POST(self) -> None:
        if self.path != EMBED_PATH:
            self._json_response(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return

        authorized, configured = _authorized(self.headers.get("Authorization"))
        if not configured:
            # 무인증으로 열어두면 GPU를 남에게 그대로 내주는 셈이라 거절한다.
            self._json_response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"detail": "embedding token is not configured"},
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
        texts = _valid_texts(payload)
        if texts is None:
            self._json_response(
                HTTPStatus.BAD_REQUEST,
                {
                    "detail": (
                        f"texts must be 1..{MAX_TEXTS} non-empty strings "
                        f"of at most {MAX_TEXT_CHARS} characters"
                    )
                },
            )
            return

        if encoder is None:
            self._json_response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"detail": "embedding model is not ready"},
            )
            return

        try:
            vectors = encoder.encode(texts)
        except Exception:
            # 본문에는 모델 내부 사정을 싣지 않는다. 원인은 서버 로그로 본다.
            logger.exception("텍스트 임베딩 실패: count=%s", len(texts))
            self._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"detail": "failed to embed texts"},
            )
            return

        self._json_response(
            HTTPStatus.OK,
            {
                "vectors": vectors,
                "model": encoder.model_id,
                "version": encoder.version,
                "dim": len(vectors[0]) if vectors else 0,
            },
        )

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("HTTP %s - %s", self.address_string(), format % args)

    def _read_json(self) -> dict[str, Any] | None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if content_length < 1 or content_length > MAX_BODY_BYTES:
            self._json_response(
                HTTPStatus.BAD_REQUEST,
                {"detail": "invalid request body"},
            )
            return None
        try:
            payload = json.loads(self.rfile.read(content_length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json_response(HTTPStatus.BAD_REQUEST, {"detail": "invalid JSON"})
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


def main() -> int:
    global encoder

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    if not os.getenv("TEXT_EMBEDDING_API_TOKEN", "").strip():
        # 기동은 시키되 크게 알린다. 토큰 없이는 모든 임베딩 요청이 503이다.
        logger.warning(
            "TEXT_EMBEDDING_API_TOKEN이 비어 있어 임베딩 요청을 모두 거절한다"
        )

    encoder = TextEncoder(
        model_id=_model_id(),
        version=os.getenv("TEXT_EMBEDDING_VERSION", "bge-m3-v1").strip()
        or "bge-m3-v1",
        device=os.getenv(
            "TEXT_EMBEDDING_DEVICE",
            os.getenv("INDEXER_DEVICE", "auto"),
        ).strip()
        or "auto",
    )
    encoder.load()

    host = os.getenv("TEXT_EMBEDDING_API_HOST", "0.0.0.0")
    port = int(os.getenv("TEXT_EMBEDDING_API_PORT", "8081"))
    server = ThreadingHTTPServer((host, port), TextEmbeddingRequestHandler)
    logger.info("텍스트 임베딩 API 시작: %s:%s%s", host, port, EMBED_PATH)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("텍스트 임베딩 API 종료 요청")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
