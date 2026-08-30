"""확인용 웹의 데이터 경로와 라우팅 검증.

S3·Qdrant는 붙이지 않는다. service가 s3io만 통해 읽도록 만들어 뒀으므로
그 함수들만 메모리 dict로 갈아끼우면 전체 경로가 그대로 돈다.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from ml.golden_set import s3io as s3io_module
from ml.golden_set.config import GoldenSettings
from ml.golden_set.items import ITEM_SCHEMA_VERSION
from ml.golden_set.web import server as server_module
from ml.golden_set.web import service

BUCKET = "test-bucket"
VERSION = "web-v1"


def _settings(**overrides) -> GoldenSettings:
    base = dict(
        gemini_api_key="",
        gemini_api_base_url="https://example.test",
        gemini_model="m",
        gemini_timeout_seconds=1,
        fashion_model_id="f",
        text_model_id="t",
        device="cpu",
        embedding_batch_size=1,
        max_multimodal_calls=1,
        s3_bucket=BUCKET,
        dataset_version=VERSION,
    )
    base.update(overrides)
    return GoldenSettings(**base)


def _manifest(golden_id: str, *, items: int = 2, failed: int = 1) -> dict:
    rows = [
        {
            "golden_id": golden_id,
            "item_index": index,
            "item_key": f"{golden_id}#{index:03d}",
            "item_name": f"상의 {index}",
            "label_ko": f"라벨 {index}",
            "category_large": "상의",
            "category_small": "티셔츠",
            "layer_role": "기본 상의",
            "color": "화이트",
            "season": ["봄"],
            "style": ["미니멀"],
            "status": "SUCCEEDED",
            "error_message": "",
            "s3_key": f"goldenset/derived/{VERSION}/{golden_id}/item_{index:03d}.png",
        }
        for index in range(items)
    ]
    for index in range(failed):
        rows.append(
            {
                "golden_id": golden_id,
                "item_index": items + index,
                "item_key": f"{golden_id}#{items + index:03d}",
                "category_large": "가방",
                "status": "FAILED",
                "error_message": "boom",
                "s3_key": "",
            }
        )
    return {
        "golden_id": golden_id,
        "image_sha256": "a" * 64,
        "schema_version": ITEM_SCHEMA_VERSION,
        "pipeline_key": "stub-crop",
        "embedding_version": "stub-embed-v1",
        "num_items": len(rows),
        "num_failed": failed,
        "latency_seconds": 1.5,
        "items": rows,
    }


class _FakeS3:
    """s3io의 읽기 함수만 대체한다 (쓰기는 put_json만 쓴다)."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def seed_source(self, *names: str) -> None:
        for name in names:
            self.objects[f"goldenset/source/{name}.jpg"] = b"jpeg"

    def seed_manifest(self, payload: dict) -> None:
        key = f"goldenset/derived/{VERSION}/{payload['golden_id']}/manifest.json"
        self.objects[key] = json.dumps(payload).encode()

    # ── s3io 대체 ──
    def list_source_keys(self, bucket, prefix):
        return sorted(
            key
            for key in self.objects
            if key.startswith(prefix) and key.endswith(".jpg")
        )

    def list_keys(self, bucket, prefix):
        return sorted(key for key in self.objects if key.startswith(prefix))

    def get_json(self, bucket, key):
        raw = self.objects.get(key)
        return None if raw is None else json.loads(raw)

    def put_json(self, bucket, key, value):
        self.objects[key] = json.dumps(value, ensure_ascii=False).encode()
        return key

    def presigned_url(self, bucket, key, *, expires_seconds=600):
        return f"https://example.test/{key}?sig=stub"


class _S3Patch:
    NAMES = (
        "list_source_keys",
        "list_keys",
        "get_json",
        "put_json",
        "presigned_url",
    )

    def __init__(self, fake: _FakeS3) -> None:
        self.fake = fake
        self.original = {name: getattr(s3io_module, name) for name in self.NAMES}

    def __enter__(self) -> _FakeS3:
        for name in self.NAMES:
            setattr(s3io_module, name, getattr(self.fake, name))
        return self.fake

    def __exit__(self, *exc) -> None:
        for name, value in self.original.items():
            setattr(s3io_module, name, value)


class ServiceTests(unittest.TestCase):
    def test_progress_counts_source_versus_completed(self) -> None:
        fake = _FakeS3()
        fake.seed_source("look-a", "look-b", "look-c")
        fake.seed_manifest(_manifest("look-a"))
        with _S3Patch(fake):
            progress = service.source_progress(_settings())
        self.assertEqual(progress["source_count"], 3)
        self.assertEqual(progress["processed_count"], 1)
        self.assertEqual(progress["pending_count"], 2)
        self.assertEqual(progress["stale_schema_count"], 0)

    def test_stale_schema_is_not_counted_as_done(self) -> None:
        fake = _FakeS3()
        fake.seed_source("look-a")
        stale = _manifest("look-a") | {"schema_version": "golden-items-v0"}
        fake.seed_manifest(stale)
        with _S3Patch(fake):
            progress = service.source_progress(_settings())
            rows = service.outfit_rows(_settings())
        self.assertEqual(progress["processed_count"], 0)
        self.assertEqual(progress["stale_schema_count"], 1)
        self.assertTrue(rows[0]["stale_schema"])

    def test_outfit_rows_include_pending_sources(self) -> None:
        fake = _FakeS3()
        fake.seed_source("look-a", "look-b")
        fake.seed_manifest(_manifest("look-a"))
        with _S3Patch(fake):
            rows = {row["golden_id"]: row for row in service.outfit_rows(_settings())}
        self.assertTrue(rows["look-a"]["processed"])
        self.assertEqual(rows["look-a"]["item_count"], 3)
        self.assertEqual(rows["look-a"]["failed_count"], 1)
        self.assertEqual(rows["look-a"]["layer_roles"], ["기본 상의"])
        self.assertFalse(rows["look-b"]["processed"])

    def test_renamed_golden_id_is_not_reported_as_pending(self) -> None:
        """metadata CSV가 golden_id를 파일명과 다르게 준 경우.

        파일명(stem)만 비교하면 이미 처리된 원본이 영영 "대기"로 남고 목록에도
        두 번 나온다. manifest의 source_key로 맞춰야 한다.
        """
        fake = _FakeS3()
        fake.seed_source("look-a", "look-b")
        renamed = _manifest("golden-001") | {
            "source_key": "goldenset/source/look-a.jpg"
        }
        fake.seed_manifest(renamed)
        with _S3Patch(fake):
            progress = service.source_progress(_settings())
            rows = {row["golden_id"]: row for row in service.outfit_rows(_settings())}
        self.assertEqual(progress["pending_count"], 1)
        self.assertTrue(rows["golden-001"]["processed"])
        self.assertFalse(rows["look-b"]["processed"])
        self.assertNotIn("look-a", rows)

    def test_outfit_detail_has_previews_and_point_ids(self) -> None:
        fake = _FakeS3()
        fake.seed_source("look-a")
        fake.seed_manifest(_manifest("look-a"))
        with _S3Patch(fake):
            detail = service.outfit_detail(_settings(), "look-a")
        self.assertTrue(detail["found"])
        self.assertIn("sig=stub", detail["source_preview_url"])
        self.assertEqual(len(detail["items"]), 3)
        self.assertIsNotNone(detail["items"][0]["preview_url"])
        # 실패 아이템은 s3_key가 없어 미리보기도 없다.
        self.assertIsNone(detail["items"][-1]["preview_url"])
        self.assertTrue(detail["items"][0]["point_id"])

    def test_missing_outfit_returns_not_found_shape(self) -> None:
        with _S3Patch(_FakeS3()):
            detail = service.outfit_detail(_settings(), "없는코디")
        self.assertFalse(detail["found"])

    def test_run_summary_round_trip(self) -> None:
        fake = _FakeS3()
        with _S3Patch(fake):
            service.publish_run_summary(_settings(), {"num_items": 7, "indexed": True})
            summary = service.run_summary(_settings())
        self.assertEqual(summary["num_items"], 7)

    def test_collect_status_isolates_failing_sections(self) -> None:
        fake = _FakeS3()
        fake.seed_source("look-a")

        def boom(*args, **kwargs):
            raise RuntimeError("qdrant down")

        with _S3Patch(fake):
            original = service.qdrant_counts
            service.qdrant_counts = boom
            try:
                status = service.collect_status(_settings())
            finally:
                service.qdrant_counts = original
        # 한 구획이 죽어도 나머지는 정상이어야 한다.
        self.assertIn("error", status["qdrant"])
        self.assertEqual(status["source"]["source_count"], 1)
        self.assertEqual(status["dataset"]["version"], VERSION)


class RoutingTests(unittest.TestCase):
    """토큰·스캔 차단 등 노출 관련 동작 고정."""

    def _serve(self, *, token: str = "", allow_scan: bool = False):
        server_module.GoldenWebHandler.settings = _settings()
        server_module.GoldenWebHandler.scan_runner = server_module.ScanRunner(
            _settings()
        )
        server_module.GoldenWebHandler.token = token
        server_module.GoldenWebHandler.allow_scan = allow_scan
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_module.GoldenWebHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"

    def _get(self, url: str, headers: dict | None = None) -> int:
        request = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    def _post(self, url: str) -> tuple[int, dict]:
        request = urllib.request.Request(url, data=b"", method="POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_health_is_open_and_other_paths_need_token(self) -> None:
        httpd, base = self._serve(token="s3cr3t")
        try:
            self.assertEqual(self._get(f"{base}/health"), 200)
            self.assertEqual(self._get(f"{base}/"), 401)
            self.assertEqual(
                self._get(f"{base}/", {"Authorization": "Bearer wrong"}), 401
            )
            self.assertEqual(
                self._get(f"{base}/", {"Authorization": "Bearer s3cr3t"}), 200
            )
            self.assertEqual(self._get(f"{base}/?token=s3cr3t"), 200)
            self.assertEqual(self._get(f"{base}/nope?token=s3cr3t"), 404)
        finally:
            httpd.shutdown()

    def test_query_token_survives_a_proxy_injected_bearer(self) -> None:
        """Cloudflare Access 등이 자기 JWT를 Authorization에 끼워 넣는 경우.

        앞자리가 채워졌다고 쿼리 파라미터를 건너뛰면, 올바른 토큰을 줘도
        401이 난다. 실제로 이 증상으로 접속이 막혔다.
        """
        httpd, base = self._serve(token="s3cr3t")
        try:
            self.assertEqual(
                self._get(
                    f"{base}/?token=s3cr3t",
                    {"Authorization": "Bearer cloudflare-access-jwt"},
                ),
                200,
            )
            # 전용 헤더도 통해야 한다 (프록시가 Authorization을 점유한 환경).
            self.assertEqual(
                self._get(f"{base}/", {"X-Golden-Token": "s3cr3t"}), 200
            )
            # 어느 자리에도 맞는 값이 없으면 여전히 거부한다.
            self.assertEqual(
                self._get(
                    f"{base}/?token=nope",
                    {"Authorization": "Bearer also-nope"},
                ),
                401,
            )
        finally:
            httpd.shutdown()

    def test_non_ascii_token_is_rejected_not_crashed(self) -> None:
        """compare_digest는 비ASCII str에 TypeError를 낸다 — 500이 아니라 401이어야."""
        httpd, base = self._serve(token="s3cr3t")
        try:
            self.assertEqual(self._get(f"{base}/?token=%ED%95%9C%EA%B8%80"), 401)
        finally:
            httpd.shutdown()

    def test_scan_is_refused_unless_enabled(self) -> None:
        httpd, base = self._serve()
        try:
            status, body = self._post(f"{base}/api/scan")
            self.assertEqual(status, 403)
            self.assertIn("GOLDEN_WEB_ALLOW_SCAN", body["detail"])
        finally:
            httpd.shutdown()

    def test_scan_runs_once_at_a_time(self) -> None:
        """버튼 연타로 무거운 사이클이 겹치지 않아야 한다."""
        httpd, base = self._serve(allow_scan=True)
        release = threading.Event()
        runner = server_module.GoldenWebHandler.scan_runner
        # 실제 임베딩 대신 신호를 기다리는 작업으로 바꿔 "실행 중" 상태를 붙잡는다.
        runner._run = lambda: release.wait(timeout=5)  # noqa: SLF001 — 테스트 전용
        try:
            first, _ = self._post(f"{base}/api/scan")
            second, body = self._post(f"{base}/api/scan")
            self.assertEqual(first, 202)
            self.assertEqual(second, 409)
            self.assertFalse(body["started"])
            self.assertEqual(body["status"], "RUNNING")
        finally:
            release.set()
            httpd.shutdown()
