from __future__ import annotations

import uuid
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings
from qdrant_client import QdrantClient
from qdrant_client import models as qm

from apps.recommend.services.qdrant import collection_spec
from apps.recommend.services.shared_reference_loader import (
    ReferenceSearchExclusions,
    SharedReferenceSearchBasis,
    SharedReferenceTags,
)
from apps.recommend.services.shared_reference_visual_search import (
    WardrobeVisualIndexMismatch,
    WardrobeVisualSearchInvalid,
    WardrobeVisualSearchRequest,
    WardrobeVisualStoreUnavailable,
    search_owned_visual_matches,
)


def _id() -> str:
    return str(uuid.uuid4())


def _reference(*, layer_role: str = "OUTER") -> SharedReferenceSearchBasis:
    source_id = _id()
    return SharedReferenceSearchBasis(
        schema_version="1.0",
        shared_item_id=_id(),
        room_id=_id(),
        source_wardrobe_item_id=source_id,
        collection_name=collection_spec("wardrobe").name,
        point_id=source_id,
        embedding_version="fashionsiglip-v1",
        image_s3_key="wardrobe/friend.webp",
        image_vector=(1.0, 0.0, 0.0),
        text_vector=(0.0, 1.0, 0.0),
        tags=SharedReferenceTags(
            item_name="친구 재킷",
            category_large="아우터",
            category_small="재킷",
            season=("봄",),
            style=("미니멀",),
            color="검정",
            pattern="무지",
            fit="오버핏",
            material="울",
            sleeve="긴소매",
            length="기본",
            usage=("데이트",),
            layer_role=layer_role,
            layer_order=3,
        ),
        exclusions=ReferenceSearchExclusions(
            wardrobe_item_ids=(source_id,),
            qdrant_point_ids=(source_id,),
        ),
    )


def _payload(
    item_id: str,
    *,
    user_id: int = 7,
    confirmed: bool = True,
    category_large: str = "아우터",
    layer_role: str = "OUTER",
) -> dict:
    return {
        "user_id": user_id,
        "item_id": item_id,
        "confirmed": confirmed,
        "category_large": category_large,
        "category_small": "재킷",
        "layer_role": layer_role,
        "style": ["미니멀"],
        "color": "검정",
        "s3_key": f"wardrobe/{item_id}.webp",
        "embedding_version": "fashionsiglip-v1",
    }


class FakeQdrantClient:
    def __init__(self, *, hits=None, error: Exception | None = None) -> None:
        self.hits = list(hits or [])
        self.error = error
        self.query_calls: list[dict] = []

    def query_points(self, **kwargs):
        self.query_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(points=self.hits)


def _hit(point_id: str, *, score: float, payload=None):
    return SimpleNamespace(
        id=point_id,
        score=score,
        payload=payload if payload is not None else _payload(point_id),
    )


@override_settings(SHARED_REFERENCE_VISUAL_MIN_SCORE=0.75)
class SharedReferenceWardrobeVisualSearchTests(SimpleTestCase):
    def test_returns_only_visual_candidates_above_threshold(self) -> None:
        reference = _reference()
        high_id = _id()
        low_id = _id()
        client = FakeQdrantClient(
            hits=[
                _hit(low_id, score=0.74),
                _hit(high_id, score=0.91),
                _hit(reference.point_id, score=1.0),
            ]
        )

        result = search_owned_visual_matches(
            WardrobeVisualSearchRequest(reference=reference, user_id=7),
            client=client,
        )

        self.assertEqual(
            [candidate.wardrobe_item_id for candidate in result.candidates],
            [high_id],
        )
        self.assertEqual(result.candidates[0].match_type, "VISUAL_SIMILAR")
        self.assertEqual(result.candidates[0].visual_score, 0.91)
        self.assertNotIn(
            reference.source_wardrobe_item_id,
            [candidate.wardrobe_item_id for candidate in result.candidates],
        )

    def test_query_enforces_owner_confirmation_slot_category_and_exclusion(self) -> None:
        reference = _reference()
        client = FakeQdrantClient()

        search_owned_visual_matches(
            WardrobeVisualSearchRequest(
                reference=reference,
                user_id=7,
                limit=5,
            ),
            client=client,
        )

        call = client.query_calls[0]
        self.assertEqual(call["using"], "image")
        self.assertEqual(call["score_threshold"], 0.75)
        self.assertEqual(call["limit"], 5)
        conditions = {
            condition.key: condition.match.value
            for condition in call["query_filter"].must
        }
        self.assertEqual(
            conditions,
            {
                "user_id": 7,
                "confirmed": True,
                "category_large": "아우터",
                "layer_role": "OUTER",
                "embedding_version": "fashionsiglip-v1",
            },
        )
        self.assertEqual(
            call["query_filter"].must_not[0].has_id,
            [reference.point_id],
        )

    def test_custom_similarity_threshold_is_applied(self) -> None:
        reference = _reference()
        client = FakeQdrantClient(hits=[_hit(_id(), score=0.87)])

        result = search_owned_visual_matches(
            WardrobeVisualSearchRequest(
                reference=reference,
                user_id=7,
                min_similarity=0.9,
            ),
            client=client,
        )

        self.assertEqual(result.min_similarity, 0.9)
        self.assertEqual(result.candidates, ())

    def test_result_from_another_owner_is_rejected(self) -> None:
        reference = _reference()
        point_id = _id()
        hit = _hit(
            point_id,
            score=0.9,
            payload=_payload(point_id, user_id=99),
        )

        with self.assertRaises(WardrobeVisualIndexMismatch):
            search_owned_visual_matches(
                WardrobeVisualSearchRequest(reference=reference, user_id=7),
                client=FakeQdrantClient(hits=[hit]),
            )

    def test_unconfirmed_result_is_rejected(self) -> None:
        reference = _reference()
        point_id = _id()
        hit = _hit(
            point_id,
            score=0.9,
            payload=_payload(point_id, confirmed=False),
        )

        with self.assertRaises(WardrobeVisualIndexMismatch):
            search_owned_visual_matches(
                WardrobeVisualSearchRequest(reference=reference, user_id=7),
                client=FakeQdrantClient(hits=[hit]),
            )

    def test_different_embedding_version_is_rejected(self) -> None:
        reference = _reference()
        point_id = _id()
        payload = _payload(point_id)
        payload["embedding_version"] = "old-image-space"

        with self.assertRaisesMessage(WardrobeVisualIndexMismatch, "임베딩 버전"):
            search_owned_visual_matches(
                WardrobeVisualSearchRequest(reference=reference, user_id=7),
                client=FakeQdrantClient(
                    hits=[_hit(point_id, score=0.9, payload=payload)]
                ),
            )

    def test_missing_reference_slot_is_rejected_before_query(self) -> None:
        client = FakeQdrantClient()

        with self.assertRaises(WardrobeVisualSearchInvalid):
            search_owned_visual_matches(
                WardrobeVisualSearchRequest(
                    reference=_reference(layer_role=""),
                    user_id=7,
                ),
                client=client,
            )

        self.assertEqual(client.query_calls, [])

    def test_invalid_similarity_threshold_is_rejected(self) -> None:
        with self.assertRaises(WardrobeVisualSearchInvalid):
            search_owned_visual_matches(
                WardrobeVisualSearchRequest(
                    reference=_reference(),
                    user_id=7,
                    min_similarity=1.1,
                ),
                client=FakeQdrantClient(),
            )

    def test_missing_original_exclusion_contract_is_rejected(self) -> None:
        reference = _reference()
        reference = SharedReferenceSearchBasis(
            **{
                **reference.__dict__,
                "exclusions": ReferenceSearchExclusions(
                    wardrobe_item_ids=(),
                    qdrant_point_ids=(),
                ),
            }
        )

        with self.assertRaisesMessage(WardrobeVisualSearchInvalid, "제외 계약"):
            search_owned_visual_matches(
                WardrobeVisualSearchRequest(reference=reference, user_id=7),
                client=FakeQdrantClient(),
            )

    def test_qdrant_failure_is_wrapped(self) -> None:
        with self.assertRaises(WardrobeVisualStoreUnavailable):
            search_owned_visual_matches(
                WardrobeVisualSearchRequest(reference=_reference(), user_id=7),
                client=FakeQdrantClient(error=TimeoutError("qdrant timeout")),
            )

    def test_real_qdrant_returns_own_confirmed_same_slot_and_excludes_source(self) -> None:
        reference = _reference()
        client = QdrantClient(":memory:")
        client.create_collection(
            collection_name=reference.collection_name,
            vectors_config={
                "image": qm.VectorParams(size=3, distance=qm.Distance.COSINE),
                "text": qm.VectorParams(size=3, distance=qm.Distance.COSINE),
            },
        )
        expected_id = _id()
        other_user_id = _id()
        points = [
            qm.PointStruct(
                id=expected_id,
                vector={"image": [0.99, 0.01, 0.0], "text": [0.0, 1.0, 0.0]},
                payload=_payload(expected_id),
            ),
            qm.PointStruct(
                id=reference.point_id,
                vector={"image": [1.0, 0.0, 0.0], "text": [0.0, 1.0, 0.0]},
                payload=_payload(reference.point_id),
            ),
            qm.PointStruct(
                id=other_user_id,
                vector={"image": [1.0, 0.0, 0.0], "text": [0.0, 1.0, 0.0]},
                payload=_payload(other_user_id, user_id=99),
            ),
        ]
        client.upsert(collection_name=reference.collection_name, points=points)

        result = search_owned_visual_matches(
            WardrobeVisualSearchRequest(reference=reference, user_id=7),
            client=client,
        )

        self.assertEqual(
            [candidate.wardrobe_item_id for candidate in result.candidates],
            [expected_id],
        )
