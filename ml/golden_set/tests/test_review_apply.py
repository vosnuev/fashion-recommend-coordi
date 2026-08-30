"""재임베딩 없이 payload만 갈아끼우는 경로."""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

from ml.golden_set.config import GoldenSettings
from ml.golden_set.qdrant_index import outfit_point_id
from ml.golden_set.review_apply import REVIEW_PAYLOAD_KEYS, apply_review_payload
from ml.golden_set.review_publish import ReviewIndex

SHA_REVIEWED = "a" * 64
SHA_PLAIN = "b" * 64
SHA_NOT_INDEXED = "c" * 64


def _settings() -> GoldenSettings:
    return GoldenSettings(
        gemini_api_key="test",
        gemini_api_base_url="https://example.test",
        gemini_model="fake-gemini",
        gemini_timeout_seconds=1,
        max_multimodal_calls=1,
        fashion_model_id="fashion-test",
        text_model_id="text-test",
        device="cpu",
        embedding_batch_size=1,
        s3_bucket="golden-test",
        dataset_version="v1",
    )


#: S3에는 원본 파일명이 golden_id로 들어 있다. 검수표 이름과 겹치지 않는다.
MANIFESTS = {
    "goldenset/derived/v1/001/manifest.json": {
        "golden_id": "001",
        "image_sha256": SHA_REVIEWED,
    },
    "goldenset/derived/v1/002/manifest.json": {
        "golden_id": "002",
        "image_sha256": SHA_PLAIN,
    },
    "goldenset/derived/v1/042-2/manifest.json": {
        "golden_id": "042-2",
        "image_sha256": SHA_NOT_INDEXED,
    },
}

REVIEW = ReviewIndex(
    by_sha={
        SHA_REVIEWED: {
            "image_sha256": SHA_REVIEWED,
            "review_golden_id": "shj-m-casual-001",
            "human_verified": True,
            "human_score": 82.5,
            "score_band": "high",
        },
        SHA_NOT_INDEXED: {
            "image_sha256": SHA_NOT_INDEXED,
            "review_golden_id": "jhy-w-na-001",
            "human_verified": True,
            "human_score": 40.0,
        },
    }
)


@dataclass
class FakeClient:
    """적재된 포인트만 들고 있는 최소 Qdrant."""

    indexed: set[str]

    def __post_init__(self) -> None:
        self.set_calls: list[tuple[str, dict]] = []
        self.deleted: list[tuple[list[str], list[str]]] = []

    def retrieve(self, *, collection_name, ids, with_payload, with_vectors):
        return [SimpleNamespace(id=point) for point in ids if point in self.indexed]

    def set_payload(self, *, collection_name, payload, points, wait):
        for point in points:
            self.set_calls.append((point, payload))

    def delete_payload(self, *, collection_name, keys, points, wait):
        self.deleted.append((list(keys), list(points)))


def _point(golden_id: str) -> str:
    return outfit_point_id("v1", golden_id)


class ApplyReviewTests(unittest.TestCase):
    def _apply(self, *, indexed=None, review=REVIEW, **kwargs):
        indexed = (
            {_point("001"), _point("002")} if indexed is None else indexed
        )
        client = FakeClient(indexed=indexed)
        with (
            patch(
                "ml.golden_set.review_apply.find_manifests",
                return_value=list(MANIFESTS),
            ),
            patch(
                "ml.golden_set.review_apply.s3io.get_json",
                side_effect=lambda bucket, key: MANIFESTS[key],
            ),
            patch("ml.golden_set.review_apply.preflight"),
        ):
            summary = apply_review_payload(
                settings=_settings(), review=review, client=client, **kwargs
            )
        return summary, client

    def test_matched_outfit_gets_review_payload(self) -> None:
        summary, client = self._apply()
        self.assertEqual(summary["num_matched"], 2)
        self.assertEqual(summary["num_updated"], 1)
        (point, payload), = client.set_calls
        self.assertEqual(point, _point("001"))
        self.assertEqual(payload["human_score"], 82.5)
        self.assertEqual(payload["human_review_golden_id"], "shj-m-casual-001")

    def test_unreviewed_outfit_has_review_keys_cleared(self) -> None:
        """검수를 되돌렸는데 옛 점수가 남으면 랭킹이 안 되돌아간다."""
        summary, client = self._apply()
        (keys, points), = client.deleted
        self.assertEqual(points, [_point("002")])
        self.assertEqual(set(keys), set(REVIEW_PAYLOAD_KEYS))
        self.assertEqual(summary["num_cleared"], 1)

    def test_not_indexed_outfit_is_counted_not_silently_dropped(self) -> None:
        """set_payload는 없는 포인트에 조용히 아무것도 하지 않는다."""
        summary, client = self._apply()
        self.assertEqual(summary["num_not_indexed"], 1)
        self.assertNotIn(
            _point("042-2"), [point for point, _ in client.set_calls]
        )

    def test_dry_run_writes_nothing(self) -> None:
        summary, client = self._apply(dry_run=True)
        self.assertFalse(summary["applied"])
        self.assertEqual(client.set_calls, [])
        self.assertEqual(client.deleted, [])
        self.assertEqual(summary["num_matched"], 2)

    def test_no_sha_overlap_raises(self) -> None:
        """조인 키가 어긋나면 아무 일도 안 일어나는데 에러도 안 난다."""
        stale = ReviewIndex(
            by_sha={"d" * 64: {"image_sha256": "d" * 64, "human_verified": True}}
        )
        with self.assertRaises(ValueError) as caught:
            self._apply(review=stale)
        self.assertIn("sha256이 일치하는 코디가", str(caught.exception))

    def test_empty_review_clears_everything_without_raising(self) -> None:
        """검수 결과가 아직 없는 상태는 정상이다 — 지우기만 한다."""
        summary, client = self._apply(review=ReviewIndex(by_sha={}))
        self.assertEqual(summary["num_matched"], 0)
        self.assertEqual(client.set_calls, [])
        (_keys, points), = client.deleted
        self.assertEqual(len(points), 2)

    def test_limit_restricts_manifests(self) -> None:
        summary, _ = self._apply(limit=1, dry_run=True)
        self.assertEqual(summary["num_manifests"], 1)


if __name__ == "__main__":
    unittest.main()
