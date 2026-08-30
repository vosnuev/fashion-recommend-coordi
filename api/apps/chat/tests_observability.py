from __future__ import annotations

import json
import logging
from unittest.mock import patch

from config.observability import (
    JsonFormatter,
    RequestContextFilter,
    bind_request_id,
    reset_request_id,
)
from django.test import SimpleTestCase, override_settings

from apps.recommend.checks import chat_recommend_deployment_checks


class HealthAndRequestTracingTests(SimpleTestCase):
    def test_liveness_returns_request_id(self) -> None:
        response = self.client.get(
            "/health/live/",
            HTTP_X_REQUEST_ID="mobile-request-17",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response["X-Request-ID"], "mobile-request-17")

    def test_invalid_request_id_is_replaced(self) -> None:
        response = self.client.get("/health/live/", HTTP_X_REQUEST_ID="bad id\nvalue")

        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response["X-Request-ID"], "bad id\nvalue")
        self.assertEqual(len(response["X-Request-ID"]), 32)

    @patch("config.health._check_qdrant")
    @patch("config.health._check_redis")
    @patch("config.health._check_postgres")
    def test_readiness_reports_dependency_status(self, postgres, redis, qdrant) -> None:
        redis.side_effect = ConnectionError("redis://secret-host unavailable")

        response = self.client.get("/health/ready/")

        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload["status"], "not_ready")
        self.assertEqual(payload["checks"]["postgres"]["status"], "ok")
        self.assertEqual(payload["checks"]["redis"]["status"], "unavailable")
        self.assertEqual(payload["checks"]["redis"]["error"], "ConnectionError")
        self.assertNotIn("secret-host", response.content.decode())
        qdrant.assert_called_once()


class StructuredLoggingTests(SimpleTestCase):
    def test_json_log_contains_request_and_business_context(self) -> None:
        token = bind_request_id("request-42")
        try:
            record = logging.LogRecord(
                "apps.chat.worker",
                logging.INFO,
                __file__,
                1,
                "처리 완료",
                (),
                None,
            )
            record.run_id = "run-7"
            record.duration_ms = 321
            RequestContextFilter().filter(record)
            payload = json.loads(JsonFormatter().format(record))
        finally:
            reset_request_id(token)

        self.assertEqual(payload["request_id"], "request-42")
        self.assertEqual(payload["run_id"], "run-7")
        self.assertEqual(payload["duration_ms"], 321)
        self.assertEqual(payload["message"], "처리 완료")


class DeploymentCheckTests(SimpleTestCase):
    @override_settings(
        REDIS_PASSWORD="redis-secret",
        QDRANT_API_KEY="qdrant-secret",
        OPENAI_API_KEY="openai-secret",
        CHAT_GOLDENSET_DATASET_VERSION="goldenset-v1",
        CHAT_GOLDENSET_DATASET_STATUSES=("ACTIVE",),
        OUTFIT_RENDER_ENABLED=True,
        OPENROUTER_API_KEY="openrouter-secret",
        OUTFIT_RENDER_RESULT_BUCKET="private-render-bucket",
    )
    def test_complete_production_contract_passes(self) -> None:
        self.assertEqual(chat_recommend_deployment_checks(None), [])

    @override_settings(
        REDIS_PASSWORD="",
        QDRANT_API_KEY="",
        OPENAI_API_KEY="",
        CHAT_GOLDENSET_DATASET_VERSION="",
        CHAT_GOLDENSET_DATASET_STATUSES=(),
        OUTFIT_RENDER_ENABLED=True,
        OPENROUTER_API_KEY="",
        OUTFIT_RENDER_RESULT_BUCKET="",
    )
    def test_missing_production_contract_reports_stable_codes(self) -> None:
        errors = chat_recommend_deployment_checks(None)
        self.assertEqual(
            {error.id for error in errors},
            {
                "recommend.E001",
                "recommend.E002",
                "recommend.E003",
                "recommend.E004",
                "recommend.E005",
                "recommend.E006",
                "recommend.E007",
            },
        )

    @override_settings(
        REDIS_PASSWORD="redis-secret",
        QDRANT_API_KEY="qdrant-secret",
        OPENAI_API_KEY="openai-secret",
        CHAT_GOLDENSET_DATASET_VERSION="goldenset-v1",
        CHAT_GOLDENSET_DATASET_STATUSES=("PUBLISHED",),
        OUTFIT_RENDER_ENABLED=False,
    )
    def test_unknown_goldenset_status_is_rejected(self) -> None:
        errors = chat_recommend_deployment_checks(None)

        self.assertEqual([error.id for error in errors], ["recommend.E008"])
        self.assertIn("PUBLISHED", errors[0].msg)
