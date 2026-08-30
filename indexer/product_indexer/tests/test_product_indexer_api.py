from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path
import unittest.mock
from unittest.mock import patch

import requests

# indexer/ 를 import 루트로 잡아 product_indexer·util 패키지를 찾게 한다.
INDEXER_ROOT = Path(__file__).resolve().parents[2]
if str(INDEXER_ROOT) not in sys.path:
    sys.path.insert(0, str(INDEXER_ROOT))

from product_indexer import product_indexer_api


class ProductIndexerApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = product_indexer_api.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            product_indexer_api.ProductIndexerRequestHandler,
        )
        cls.thread = threading.Thread(
            target=cls.server.serve_forever,
            daemon=True,
        )
        cls.thread.start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_health_does_not_require_authentication(self) -> None:
        response = requests.get(f"{self.base_url}/health", timeout=2)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_trigger_requires_server_token(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            response = requests.post(
                f"{self.base_url}/v1/product-indexer/drain",
                json={"source": "eleven", "reason": "sync_completed"},
                timeout=2,
            )

        self.assertEqual(response.status_code, 503)

    def test_trigger_rejects_invalid_token(self) -> None:
        with patch.dict(
            os.environ,
            {"PRODUCT_INDEXER_TRIGGER_TOKEN": "correct"},
            clear=True,
        ):
            response = requests.post(
                f"{self.base_url}/v1/product-indexer/drain",
                json={"source": "eleven", "reason": "sync_completed"},
                headers={"Authorization": "Bearer wrong"},
                timeout=2,
            )

        self.assertEqual(response.status_code, 401)

    def test_trigger_starts_drain_and_returns_accepted(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"PRODUCT_INDEXER_TRIGGER_TOKEN": "correct"},
                clear=True,
            ),
            patch.object(
                product_indexer_api.manager,
                "start",
                return_value=("started", 1234),
            ) as start,
        ):
            response = requests.post(
                f"{self.base_url}/v1/product-indexer/drain",
                json={
                    "source": "naver",
                    "reason": "batch_completed",
                    "tagged_count": 4,
                },
                headers={"Authorization": "Bearer correct"},
                timeout=2,
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.json(),
            {"status": "started", "pid": 1234, "source": "naver"},
        )
        start.assert_called_once_with("naver")

    def test_manual_trigger_drains_every_source(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"PRODUCT_INDEXER_TRIGGER_TOKEN": "correct"},
                clear=True,
            ),
            patch.object(
                product_indexer_api.manager,
                "start",
                return_value=("started", 4321),
            ) as start,
        ):
            response = requests.post(
                f"{self.base_url}/v1/product-indexer/drain",
                json={"source": "manual", "reason": "manual"},
                headers={"Authorization": "Bearer correct"},
                timeout=2,
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["source"], "all")
        start.assert_called_once_with("all")


class DrainProcessManagerTests(unittest.TestCase):
    """쇼핑몰별 drain이 동시에 실행될 수 있어야 한다."""

    def setUp(self) -> None:
        self.manager = product_indexer_api.DrainProcessManager()
        self.spawned: list[list[str]] = []

    def _fake_popen(self, poll_value=None):
        def factory(command, cwd=None):
            self.spawned.append(command)
            process = unittest.mock.Mock()
            process.pid = 1000 + len(self.spawned)
            process.poll.return_value = poll_value
            process.wait.return_value = 0
            return process

        return factory

    def test_different_sources_run_concurrently(self) -> None:
        with (
            patch.object(
                product_indexer_api.subprocess, "Popen", self._fake_popen()
            ),
            patch.object(product_indexer_api.threading, "Thread"),
        ):
            naver_status, naver_pid = self.manager.start("naver")
            eleven_status, eleven_pid = self.manager.start("eleven")

        self.assertEqual(naver_status, "started")
        self.assertEqual(eleven_status, "started")
        self.assertNotEqual(naver_pid, eleven_pid)
        self.assertEqual(
            sorted(self.manager.running()),
            ["eleven", "naver"],
        )
        self.assertIn(["--source", "naver"], [cmd[-2:] for cmd in self.spawned])
        self.assertIn(["--source", "eleven"], [cmd[-2:] for cmd in self.spawned])

    def test_same_source_is_not_started_twice(self) -> None:
        with (
            patch.object(
                product_indexer_api.subprocess, "Popen", self._fake_popen()
            ),
            patch.object(product_indexer_api.threading, "Thread"),
        ):
            first_status, first_pid = self.manager.start("naver")
            second_status, second_pid = self.manager.start("naver")

        self.assertEqual(first_status, "started")
        self.assertEqual(second_status, "already_running")
        self.assertEqual(first_pid, second_pid)
        self.assertEqual(len(self.spawned), 1)

    def test_manual_drain_omits_source_flag(self) -> None:
        with (
            patch.object(
                product_indexer_api.subprocess, "Popen", self._fake_popen()
            ),
            patch.object(product_indexer_api.threading, "Thread"),
        ):
            self.manager.start(product_indexer_api.ALL_SOURCES_KEY)

        self.assertNotIn("--source", self.spawned[0])
        self.assertIn("--drain", self.spawned[0])


if __name__ == "__main__":
    unittest.main()
