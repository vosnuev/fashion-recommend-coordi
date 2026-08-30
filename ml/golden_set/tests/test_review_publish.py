"""검수 결과를 sha256으로 발행하고 코디 payload에 얹는 경로.

검수표의 정규화 golden_id와 S3의 원본 파일명 golden_id는 규칙 없이 다르다.
이름으로 이으면 한 건도 안 붙고 **에러도 안 난다** — 그 조용한 실패를 막는 게
이 테스트의 목적이다.
"""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from ml.golden_set.config import GoldenSettings
from ml.golden_set.review_publish import (
    ReviewIndex,
    build_review_payload,
    human_review_key,
)
from ml.golden_set.sync_qdrant import build_points

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


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


def _run_dir(tmp: str, *, anchors, accepted) -> Path:
    run_dir = Path(tmp)
    with (run_dir / "anchor_scores.jsonl").open("w", encoding="utf-8") as handle:
        for row in anchors:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (run_dir / "review_validation.json").write_text(
        json.dumps({"accepted_images": accepted}, ensure_ascii=False),
        encoding="utf-8",
    )
    metadata = run_dir / "metadata.csv"
    with metadata.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["golden_id", "image_sha256"])
        writer.writeheader()
        writer.writerow({"golden_id": "shj-m-casual-001", "image_sha256": SHA_A})
        writer.writerow({"golden_id": "jhy-w-na-001", "image_sha256": SHA_B})
    return run_dir


def _anchor(golden_id: str, **overrides) -> dict:
    row = {
        "golden_id": golden_id,
        "anchor_graph": "men",
        "anchor_scope": "Q_OVERALL_STYLE_EXECUTION",
        "human_score": 82.5,
        "score_band": "high",
        "score_confidence": 0.61,
        "comparison_count": 8,
        "reviewer_count": 2,
        "reviewer_agreement": 0.75,
    }
    row.update(overrides)
    return row


class BuildPayloadTests(unittest.TestCase):
    def _build(self, *, anchors, accepted):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _run_dir(tmp, anchors=anchors, accepted=accepted)
            return build_review_payload(
                run_dir=run_dir,
                metadata_csv=run_dir / "metadata.csv",
                dataset_version="v1",
            )

    def test_images_are_keyed_by_sha_not_golden_id(self) -> None:
        payload = self._build(
            anchors=[_anchor("shj-m-casual-001")], accepted=["shj-m-casual-001"]
        )
        self.assertEqual(payload["join_key"], "image_sha256")
        (image,) = payload["images"]
        self.assertEqual(image["image_sha256"], SHA_A)
        self.assertEqual(image["review_golden_id"], "shj-m-casual-001")

    def test_anchor_and_verified_merge_into_one_row(self) -> None:
        payload = self._build(
            anchors=[_anchor("shj-m-casual-001")], accepted=["shj-m-casual-001"]
        )
        (image,) = payload["images"]
        self.assertTrue(image["human_verified"])
        self.assertEqual(image["human_score"], 82.5)
        self.assertEqual(payload["num_verified"], 1)
        self.assertEqual(payload["num_anchored"], 1)

    def test_verified_without_anchor_has_no_score(self) -> None:
        """관찰 검수는 통과했지만 쌍대 비교에 안 들어간 코디."""
        payload = self._build(anchors=[], accepted=["jhy-w-na-001"])
        (image,) = payload["images"]
        self.assertTrue(image["human_verified"])
        self.assertNotIn("human_score", image)
        self.assertEqual(payload["num_anchored"], 0)

    def test_anchor_without_verified_is_not_marked_verified(self) -> None:
        payload = self._build(anchors=[_anchor("jhy-w-na-001")], accepted=[])
        (image,) = payload["images"]
        self.assertFalse(image["human_verified"])
        self.assertIn("human_score", image)

    def test_unknown_golden_id_is_reported_not_dropped_silently(self) -> None:
        payload = self._build(anchors=[_anchor("lkw-m-na-999")], accepted=[])
        self.assertEqual(payload["images"], [])
        self.assertEqual(payload["unmatched_golden_ids"], ["lkw-m-na-999"])

    def test_key_follows_the_versioned_derived_prefix(self) -> None:
        self.assertEqual(
            human_review_key(_settings(dataset_version="v2")),
            "goldenset/derived/v2/human_review.json",
        )


class ReviewIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = ReviewIndex(
            by_sha={
                SHA_A: {
                    "image_sha256": SHA_A,
                    "review_golden_id": "shj-m-casual-001",
                    "human_verified": True,
                    "human_score": 82.5,
                    "score_band": "high",
                    "score_confidence": 0.61,
                    "anchor_graph": "men",
                },
                SHA_B: {
                    "image_sha256": SHA_B,
                    "review_golden_id": "jhy-w-na-001",
                    "human_verified": True,
                },
            }
        )

    def test_unknown_sha_returns_nothing(self) -> None:
        self.assertEqual(self.index.payload_for(SHA_C), {})

    def test_blank_sha_returns_nothing(self) -> None:
        self.assertEqual(self.index.payload_for(""), {})

    def test_missing_score_is_absent_not_zero(self) -> None:
        """0은 최하점이라는 뜻이 된다. 미검수와 구분되어야 한다."""
        payload = self.index.payload_for(SHA_B)
        self.assertNotIn("human_score", payload)
        self.assertTrue(payload["human_verified"])

    def test_score_fields_are_carried(self) -> None:
        payload = self.index.payload_for(SHA_A)
        self.assertEqual(payload["human_score"], 82.5)
        self.assertEqual(payload["score_band"], "high")
        self.assertEqual(payload["anchor_graph"], "men")


class OutfitPayloadTests(unittest.TestCase):
    def _outfit(self, *, sha, review=None):
        outfit, _items = build_points(
            manifest={
                "golden_id": "001",
                "image_sha256": sha,
                "items": [],
                "split": "KNOWLEDGE",
            },
            manifest_key="goldenset/derived/v1/001/manifest.json",
            settings=_settings(),
            image_vector=[0.1],
            text_vector=[0.2],
            item_vectors={},
            image_model="fashion-test",
            text_model="text-test",
            review=review,
        )
        return outfit

    def test_review_is_joined_by_sha_across_different_golden_ids(self) -> None:
        """S3는 001, 검수표는 shj-m-casual-001 — sha가 이 둘을 잇는다."""
        index = ReviewIndex(
            by_sha={
                SHA_A: {
                    "image_sha256": SHA_A,
                    "review_golden_id": "shj-m-casual-001",
                    "human_verified": True,
                    "human_score": 82.5,
                    "score_band": "high",
                }
            }
        )
        outfit = self._outfit(sha=SHA_A, review=index)
        self.assertEqual(outfit.payload["golden_id"], "001")
        self.assertEqual(outfit.payload["human_review_golden_id"], "shj-m-casual-001")
        self.assertEqual(outfit.payload["human_score"], 82.5)
        self.assertTrue(outfit.payload["human_verified"])

    def test_unreviewed_outfit_has_no_score_key(self) -> None:
        index = ReviewIndex(by_sha={})
        outfit = self._outfit(sha=SHA_C, review=index)
        self.assertNotIn("human_score", outfit.payload)
        self.assertNotIn("human_verified", outfit.payload)

    def test_missing_review_keeps_previous_behaviour(self) -> None:
        outfit = self._outfit(sha=SHA_A, review=None)
        self.assertNotIn("human_score", outfit.payload)
        self.assertEqual(outfit.payload["golden_id"], "001")


if __name__ == "__main__":
    unittest.main()
