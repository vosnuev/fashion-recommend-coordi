from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ml.golden_set.config import GoldenSettings
from ml.golden_set.sync_qdrant import build_points


def _settings(**overrides) -> GoldenSettings:
    values = {
        "gemini_api_key": "test",
        "gemini_api_base_url": "https://example.test",
        "gemini_model": "fake-gemini",
        "gemini_timeout_seconds": 1,
        "max_multimodal_calls": 1,
        "fashion_model_id": "fashion-test",
        "text_model_id": "text-test",
        "device": "cpu",
        "embedding_batch_size": 1,
        "s3_bucket": "golden-test",
        "dataset_version": "v1",
    }
    values.update(overrides)
    return GoldenSettings(**values)


class GoldenDatasetStatusTests(unittest.TestCase):
    def test_environment_status_is_normalized(self) -> None:
        with patch.dict(os.environ, {"GOLDEN_DATASET_STATUS": "active"}):
            settings = GoldenSettings.from_env()

        self.assertEqual(settings.dataset_status, "ACTIVE")

    def test_unknown_environment_status_is_rejected(self) -> None:
        with (
            patch.dict(os.environ, {"GOLDEN_DATASET_STATUS": "PUBLISHED"}),
            self.assertRaisesRegex(ValueError, "지원하지 않는 골든셋 상태"),
        ):
            GoldenSettings.from_env()

    def test_unknown_direct_status_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "지원하지 않는 골든셋 상태"):
            _settings(dataset_status="PUBLISHED")

    def test_full_sync_writes_configured_status_to_outfit_payload(self) -> None:
        outfit, _items = build_points(
            manifest={"golden_id": "look-1", "items": [], "split": "KNOWLEDGE"},
            manifest_key="goldenset/derived/v1/look-1/manifest.json",
            settings=_settings(dataset_status="ACTIVE"),
            image_vector=[0.1],
            text_vector=[0.2],
            item_vectors={},
            image_model="fashion-test",
            text_model="text-test",
        )

        # 승격 커맨드(set_goldenset_qdrant_status)가 두 키를 함께 쓰므로 적재도
        # 두 키를 함께 써야 한다. status만 쓰면 승격 뒤 재적재에서 dataset_status가
        # 사라져, 두 키를 각각 보는 도구가 서로 다른 답을 준다.
        self.assertEqual(outfit.payload["status"], "ACTIVE")
        self.assertEqual(outfit.payload["dataset_status"], "ACTIVE")
