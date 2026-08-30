from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

# indexer/ 를 import 루트로 잡아 product_indexer·util 패키지를 찾게 한다.
INDEXER_ROOT = Path(__file__).resolve().parents[2]
if str(INDEXER_ROOT) not in sys.path:
    sys.path.insert(0, str(INDEXER_ROOT))

from product_indexer import text_embedding_api

TOKEN = {"TEXT_EMBEDDING_API_TOKEN": "correct"}


class FakeEmbedder:
    """torch·bge-m3 없이 핸들러만 검증하기 위한 대역.

    실제 벡터 값은 여기서 검증할 수 있는 것이 아니다 — 골든셋과 같은 공간인지는
    모델 설정(BgeM3Embedder)이 보장하고, 이 테스트는 HTTP 계약만 본다.
    """

    dim = 4

    def encode_texts(self, texts: list[str]):
        return [[0.5, 0.5, 0.5, 0.5] for _ in texts]


def build_encoder() -> text_embedding_api.TextEncoder:
    encoder = text_embedding_api.TextEncoder(
        model_id="BAAI/bge-m3",
        version="bge-m3-v1",
        device="cpu",
    )
    encoder._embedder = FakeEmbedder()
    return encoder


class TextEmbeddingApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = text_embedding_api.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            text_embedding_api.TextEmbeddingRequestHandler,
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"
        cls.embed_url = f"{cls.base_url}{text_embedding_api.EMBED_PATH}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        text_embedding_api.encoder = build_encoder()

    def tearDown(self) -> None:
        text_embedding_api.encoder = None

    def test_health_reports_ready_model(self) -> None:
        response = requests.get(f"{self.base_url}/health", timeout=2)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["dim"], 4)

    def test_health_is_unavailable_before_model_loads(self) -> None:
        """모델이 올라오기 전에는 준비되지 않았다고 알린다 (healthcheck가 봐야 한다)."""
        text_embedding_api.encoder = None

        response = requests.get(f"{self.base_url}/health", timeout=2)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "loading")

    def test_embed_requires_server_token(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            response = requests.post(
                self.embed_url,
                json={"texts": ["토요일 성수동 데이트 룩"]},
                timeout=2,
            )

        self.assertEqual(response.status_code, 503)

    def test_embed_rejects_invalid_token(self) -> None:
        with patch.dict(os.environ, TOKEN, clear=True):
            response = requests.post(
                self.embed_url,
                json={"texts": ["토요일 성수동 데이트 룩"]},
                headers={"Authorization": "Bearer wrong"},
                timeout=2,
            )

        self.assertEqual(response.status_code, 401)

    def test_embed_returns_vectors_with_model_and_version(self) -> None:
        """호출자(text_embedding.py)가 vectors·model·version을 모두 요구한다."""
        with patch.dict(os.environ, TOKEN, clear=True):
            response = requests.post(
                self.embed_url,
                json={"texts": ["토요일 성수동 데이트 룩"]},
                headers={"Authorization": "Bearer correct"},
                timeout=2,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["vectors"], [[0.5, 0.5, 0.5, 0.5]])
        self.assertEqual(payload["model"], "BAAI/bge-m3")
        self.assertEqual(payload["version"], "bge-m3-v1")
        self.assertEqual(payload["dim"], 4)

    def test_embed_rejects_empty_and_oversized_texts(self) -> None:
        cases = [
            {"texts": []},
            {"texts": ["   "]},
            {"texts": [123]},
            {"texts": "문자열 하나"},
            {"texts": ["x" * (text_embedding_api.MAX_TEXT_CHARS + 1)]},
            {"texts": ["ok"] * (text_embedding_api.MAX_TEXTS + 1)},
            {},
        ]
        for body in cases:
            with self.subTest(body=body), patch.dict(os.environ, TOKEN, clear=True):
                response = requests.post(
                    self.embed_url,
                    json=body,
                    headers={"Authorization": "Bearer correct"},
                    timeout=2,
                )

                self.assertEqual(response.status_code, 400)

    def test_unknown_path_is_not_found(self) -> None:
        with patch.dict(os.environ, TOKEN, clear=True):
            response = requests.post(
                f"{self.base_url}/v1/nope",
                json={"texts": ["x"]},
                headers={"Authorization": "Bearer correct"},
                timeout=2,
            )

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
