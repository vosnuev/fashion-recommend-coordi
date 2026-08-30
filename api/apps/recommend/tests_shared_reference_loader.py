from __future__ import annotations

import uuid
from copy import deepcopy
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings
from qdrant_client import QdrantClient
from qdrant_client import models as qm

from apps.recommend.services.qdrant import collection_spec
from apps.recommend.services.shared_reference_loader import (
    ReferenceIndexMismatch,
    ReferenceSnapshotInvalid,
    ReferenceVectorMissing,
    ReferenceVectorNotFound,
    ReferenceVectorStoreUnavailable,
    SharedReferenceVectorLoader,
    load_shared_reference,
)


class FakeQdrantClient:
    def __init__(self, *, points=None, error: Exception | None = None) -> None:
        self.points = list(points or [])
        self.error = error
        self.retrieve_calls: list[dict] = []

    def retrieve(self, **kwargs):
        self.retrieve_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.points


def _ids() -> tuple[str, str, str]:
    return str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())


def _snapshot() -> dict:
    shared_item_id, room_id, wardrobe_item_id = _ids()
    return {
        "schema_version": "1.0",
        "type": "SHARED_WARDROBE_ITEM",
        "shared_item_id": shared_item_id,
        "room_id": room_id,
        "wardrobe_item_id": wardrobe_item_id,
        "source_status": "available",
        "qdrant_collection": collection_spec("wardrobe").name,
        "qdrant_point_id": wardrobe_item_id,
        "embedding_version": "fashionsiglip-v1",
        "image_s3_key": "wardrobe/friend-jacket.webp",
        "captured_at": "2026-08-18T10:00:00+09:00",
        "item": {
            "item_name": "친구의 검정 재킷",
            "category_large": "아우터",
            "category_small": "재킷",
            "season": ["봄", "가을"],
            "style": ["미니멀"],
            "color": "검정",
            "pattern": "무지",
            "fit": "오버핏",
            "material": "울",
            "sleeve": "긴소매",
            "length": "기본",
            "usage": ["데이트"],
            "layer_role": "OUTER",
            "layer_order": 3,
        },
    }


def _point(snapshot: dict, *, vectors=None, payload=None):
    default_payload = {
        "user_id": 99,
        "item_id": snapshot["wardrobe_item_id"],
        "category_large": "아우터",
        "category_small": "재킷",
        "season": ["봄", "가을"],
        "style": ["미니멀"],
        "color": "검정",
        "layer_role": "OUTER",
        "confirmed": True,
        "s3_key": snapshot["image_s3_key"],
        "embedding_version": snapshot["embedding_version"],
    }
    if payload:
        default_payload.update(payload)
    return SimpleNamespace(
        id=snapshot["qdrant_point_id"],
        vector=vectors
        if vectors is not None
        else {"image": [1.0, 0.0, 0.0], "text": [0.0, 1.0, 0.0]},
        payload=default_payload,
    )


@override_settings(QDRANT_IMAGE_VECTOR_DIM=3, QDRANT_TEXT_VECTOR_DIM=3)
class SharedReferenceVectorLoaderTests(SimpleTestCase):
    def test_loads_real_in_memory_qdrant_point(self) -> None:
        snapshot = _snapshot()
        source = _point(snapshot)
        client = QdrantClient(":memory:")
        client.create_collection(
            collection_name=snapshot["qdrant_collection"],
            vectors_config={
                "image": qm.VectorParams(size=3, distance=qm.Distance.COSINE),
                "text": qm.VectorParams(size=3, distance=qm.Distance.COSINE),
            },
        )
        client.upsert(
            collection_name=snapshot["qdrant_collection"],
            points=[
                qm.PointStruct(
                    id=snapshot["qdrant_point_id"],
                    vector=source.vector,
                    payload=source.payload,
                )
            ],
        )

        basis = load_shared_reference(snapshot, client=client)

        self.assertEqual(basis.point_id, snapshot["qdrant_point_id"])
        self.assertEqual(basis.image_vector, (1.0, 0.0, 0.0))
        self.assertEqual(basis.text_vector, (0.0, 1.0, 0.0))

    def test_loads_vectors_tags_and_original_item_exclusion(self) -> None:
        snapshot = _snapshot()
        client = FakeQdrantClient(points=[_point(snapshot)])

        basis = load_shared_reference(snapshot, client=client)

        self.assertEqual(basis.image_vector, (1.0, 0.0, 0.0))
        self.assertEqual(basis.text_vector, (0.0, 1.0, 0.0))
        self.assertEqual(basis.tags.category_large, "아우터")
        self.assertEqual(basis.tags.style, ("미니멀",))
        self.assertEqual(
            basis.exclusions.wardrobe_item_ids,
            (snapshot["wardrobe_item_id"],),
        )
        self.assertEqual(
            basis.exclusions.qdrant_point_ids,
            (snapshot["qdrant_point_id"],),
        )
        self.assertEqual(
            client.retrieve_calls,
            [
                {
                    "collection_name": snapshot["qdrant_collection"],
                    "ids": [snapshot["qdrant_point_id"]],
                    "with_payload": True,
                    "with_vectors": True,
                }
            ],
        )

    def test_owned_wardrobe_snapshot_uses_the_same_vector_contract(self) -> None:
        snapshot = _snapshot()
        snapshot["type"] = "WARDROBE_ITEM"
        snapshot.pop("shared_item_id")
        snapshot.pop("room_id")
        snapshot.pop("source_status")

        basis = load_shared_reference(
            snapshot,
            client=FakeQdrantClient(points=[_point(snapshot)]),
        )

        self.assertIsNone(basis.shared_item_id)
        self.assertIsNone(basis.room_id)
        self.assertEqual(
            basis.exclusions.wardrobe_item_ids,
            (snapshot["wardrobe_item_id"],),
        )

    def test_reports_snapshot_and_vector_stage_timings(self) -> None:
        snapshot = _snapshot()
        observed: list[tuple[str, float]] = []
        loader = SharedReferenceVectorLoader(
            client=FakeQdrantClient(points=[_point(snapshot)])
        )

        loader.load(
            snapshot,
            stage_observer=lambda stage, duration_ms: observed.append(
                (stage, duration_ms)
            ),
        )

        self.assertEqual(
            [stage for stage, _duration in observed],
            ["SNAPSHOT_VALIDATION", "VECTOR_LOADING"],
        )
        self.assertTrue(all(duration >= 0 for _stage, duration in observed))

    def test_missing_qdrant_point_raises_not_found(self) -> None:
        with self.assertRaises(ReferenceVectorNotFound):
            load_shared_reference(_snapshot(), client=FakeQdrantClient())

    def test_missing_named_vector_raises_vector_missing(self) -> None:
        snapshot = _snapshot()
        point = _point(snapshot, vectors={"image": [1.0, 0.0, 0.0]})

        with self.assertRaisesMessage(ReferenceVectorMissing, "text"):
            load_shared_reference(snapshot, client=FakeQdrantClient(points=[point]))

    def test_wrong_vector_dimension_raises_index_mismatch(self) -> None:
        snapshot = _snapshot()
        point = _point(
            snapshot,
            vectors={"image": [1.0, 0.0], "text": [0.0, 1.0, 0.0]},
        )

        with self.assertRaisesMessage(ReferenceIndexMismatch, "차원"):
            load_shared_reference(snapshot, client=FakeQdrantClient(points=[point]))

    def test_collection_name_mismatch_is_rejected_before_query(self) -> None:
        snapshot = _snapshot()
        snapshot["qdrant_collection"] = "stale-wardrobe-index"
        client = FakeQdrantClient()

        with self.assertRaises(ReferenceIndexMismatch):
            load_shared_reference(snapshot, client=client)

        self.assertEqual(client.retrieve_calls, [])

    def test_embedding_version_mismatch_is_rejected(self) -> None:
        snapshot = _snapshot()
        point = _point(snapshot, payload={"embedding_version": "old-version"})

        with self.assertRaisesMessage(ReferenceIndexMismatch, "임베딩 버전"):
            load_shared_reference(snapshot, client=FakeQdrantClient(points=[point]))

    def test_original_item_id_mismatch_is_rejected(self) -> None:
        snapshot = _snapshot()
        point = _point(snapshot, payload={"item_id": str(uuid.uuid4())})

        with self.assertRaisesMessage(ReferenceIndexMismatch, "원본 옷장 아이템 ID"):
            load_shared_reference(snapshot, client=FakeQdrantClient(points=[point]))

    def test_stale_indexed_tag_is_rejected(self) -> None:
        snapshot = _snapshot()
        point = _point(snapshot, payload={"category_large": "상의"})

        with self.assertRaisesMessage(ReferenceIndexMismatch, "category_large"):
            load_shared_reference(snapshot, client=FakeQdrantClient(points=[point]))

    def test_array_tag_order_does_not_cause_false_index_mismatch(self) -> None:
        snapshot = _snapshot()
        point = _point(snapshot, payload={"season": ["가을", "봄"]})

        basis = load_shared_reference(
            snapshot,
            client=FakeQdrantClient(points=[point]),
        )

        self.assertEqual(basis.tags.season, ("가을", "봄"))

    def test_legacy_private_source_status_does_not_break_existing_snapshot(self) -> None:
        snapshot = _snapshot()
        snapshot["source_status"] = "private"
        client = FakeQdrantClient(points=[_point(snapshot)])

        basis = load_shared_reference(snapshot, client=client)

        self.assertEqual(basis.source_wardrobe_item_id, snapshot["wardrobe_item_id"])

    def test_invalid_snapshot_is_rejected(self) -> None:
        snapshot = _snapshot()
        snapshot["item"] = deepcopy(snapshot["item"])
        snapshot["item"]["category_large"] = ""

        with self.assertRaises(ReferenceSnapshotInvalid):
            load_shared_reference(snapshot, client=FakeQdrantClient())

    def test_qdrant_failure_is_wrapped(self) -> None:
        with self.assertRaises(ReferenceVectorStoreUnavailable):
            load_shared_reference(
                _snapshot(),
                client=FakeQdrantClient(error=TimeoutError("qdrant timeout")),
            )
