from __future__ import annotations

import uuid
from dataclasses import replace
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
from apps.recommend.services.shared_reference_style_fallback import (
    StyleFallbackIndexMismatch,
    StyleFallbackRequest,
    StyleFallbackStoreUnavailable,
    search_style_fallback,
)
from apps.recommend.services.shared_reference_visual_search import (
    WardrobeVisualSearchResult,
)


def _id() -> str:
    return str(uuid.uuid4())


def _reference() -> SharedReferenceSearchBasis:
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
        text_vector=(1.0, 0.0, 0.0),
        tags=SharedReferenceTags(
            item_name="친구 재킷",
            category_large="아우터",
            category_small="재킷",
            season=("봄",),
            style=("미니멀", "클래식"),
            color="검정",
            pattern="무지",
            fit="오버핏",
            material="울",
            sleeve="긴소매",
            length="기본",
            usage=("데이트",),
            layer_role="OUTER",
            layer_order=3,
        ),
        exclusions=ReferenceSearchExclusions(
            wardrobe_item_ids=(source_id,),
            qdrant_point_ids=(source_id,),
        ),
    )


def _visual_result(
    reference: SharedReferenceSearchBasis,
    *,
    matched: bool = False,
) -> WardrobeVisualSearchResult:
    return WardrobeVisualSearchResult(
        reference_point_id=reference.point_id,
        min_similarity=0.75,
        candidates=(SimpleNamespace(),) if matched else (),
    )


def _payload(
    item_id: str,
    *,
    user_id: int = 7,
    confirmed: bool = True,
    style=None,
    color: str = "검정",
    fit: str = "레귤러핏",
    material: str = "울",
) -> dict:
    return {
        "user_id": user_id,
        "item_id": item_id,
        "confirmed": confirmed,
        "category_large": "아우터",
        "category_small": "재킷",
        "layer_role": "OUTER",
        "style": ["미니멀"] if style is None else style,
        "color": color,
        "fit": fit,
        "material": material,
        "s3_key": f"wardrobe/{item_id}.webp",
        "embedding_version": "fashionsiglip-v1",
    }


def _hit(item_id: str, *, score: float = 0.8, payload=None):
    return SimpleNamespace(
        id=item_id,
        score=score,
        payload=_payload(item_id) if payload is None else payload,
    )


class FakeQdrantClient:
    def __init__(
        self,
        *,
        hits=None,
        query_hits=None,
        scroll_hits=None,
        error: Exception | None = None,
    ) -> None:
        default_hits = list(hits or [])
        self.query_hits = (
            list(query_hits) if query_hits is not None else default_hits
        )
        self.scroll_hits = (
            list(scroll_hits) if scroll_hits is not None else default_hits
        )
        self.error = error
        self.query_calls: list[dict] = []
        self.scroll_calls: list[dict] = []

    def query_points(self, **kwargs):
        self.query_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(points=self.query_hits)

    def scroll(self, **kwargs):
        self.scroll_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.scroll_hits, None


@override_settings(SHARED_REFERENCE_STYLE_MIN_SCORE=0.30)
class SharedReferenceStyleFallbackTests(SimpleTestCase):
    def test_visual_match_skips_style_fallback(self) -> None:
        reference = _reference()
        client = FakeQdrantClient()

        result = search_style_fallback(
            StyleFallbackRequest(
                reference=reference,
                visual_result=_visual_result(reference, matched=True),
                user_id=7,
            ),
            client=client,
        )

        self.assertFalse(result.fallback_used)
        self.assertEqual(result.decision, "VISUAL_MATCH_FOUND")
        self.assertEqual(result.search_mode, "none")
        self.assertEqual(client.query_calls, [])
        self.assertEqual(client.scroll_calls, [])

    def test_text_search_returns_style_similar_evidence_and_breakdown(self) -> None:
        reference = _reference()
        item_id = _id()
        client = FakeQdrantClient(hits=[_hit(item_id, score=0.82)])

        result = search_style_fallback(
            StyleFallbackRequest(
                reference=reference,
                visual_result=_visual_result(reference),
                user_id=7,
            ),
            client=client,
        )

        self.assertTrue(result.fallback_used)
        self.assertEqual(result.decision, "VISUAL_THRESHOLD_NOT_MET")
        self.assertEqual(result.search_mode, "text")
        candidate = result.candidates[0]
        self.assertEqual(candidate.match_type, "STYLE_SIMILAR")
        self.assertEqual(candidate.style_score, 0.575)
        self.assertEqual(candidate.score_breakdown.style_overlap, 0.5)
        self.assertEqual(candidate.score_breakdown.color_match, 1.0)
        self.assertEqual(candidate.score_breakdown.fit_match, 0.0)
        self.assertEqual(candidate.score_breakdown.material_match, 1.0)
        self.assertIn("스타일 일치: 미니멀", candidate.evidence)
        self.assertIn("색상 일치: 검정", candidate.evidence)
        self.assertIn("소재 일치: 울", candidate.evidence)
        self.assertIn("텍스트 벡터 유사도: 0.8200", candidate.evidence)

    def test_text_query_enforces_owner_slot_category_version_and_exclusion(self) -> None:
        reference = _reference()
        client = FakeQdrantClient()

        search_style_fallback(
            StyleFallbackRequest(
                reference=reference,
                visual_result=_visual_result(reference),
                user_id=7,
                retrieval_limit=25,
            ),
            client=client,
        )

        call = client.query_calls[0]
        self.assertEqual(call["using"], "text")
        self.assertEqual(call["limit"], 25)
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

    def test_empty_text_vector_uses_tag_scroll(self) -> None:
        reference = replace(_reference(), text_vector=())
        item_id = _id()
        client = FakeQdrantClient(hits=[_hit(item_id)])

        result = search_style_fallback(
            StyleFallbackRequest(
                reference=reference,
                visual_result=_visual_result(reference),
                user_id=7,
            ),
            client=client,
        )

        self.assertEqual(result.search_mode, "tags")
        self.assertEqual(len(result.candidates), 1)
        self.assertIsNone(result.candidates[0].text_similarity)
        self.assertEqual(client.query_calls, [])
        self.assertEqual(len(client.scroll_calls), 1)

    def test_empty_text_results_degrade_to_tag_search(self) -> None:
        reference = _reference()
        item_id = _id()
        client = FakeQdrantClient(
            query_hits=[],
            scroll_hits=[_hit(item_id)],
        )

        result = search_style_fallback(
            StyleFallbackRequest(
                reference=reference,
                visual_result=_visual_result(reference),
                user_id=7,
            ),
            client=client,
        )

        self.assertEqual(result.search_mode, "text_then_tags")
        self.assertEqual(
            [candidate.wardrobe_item_id for candidate in result.candidates],
            [item_id],
        )
        self.assertIsNone(result.candidates[0].text_similarity)
        self.assertEqual(len(client.query_calls), 1)
        self.assertEqual(len(client.scroll_calls), 1)

    def test_candidate_below_style_threshold_is_removed(self) -> None:
        reference = _reference()
        item_id = _id()
        unrelated = _payload(
            item_id,
            style=["스포티"],
            color="빨강",
            fit="슬림핏",
            material="가죽",
        )

        result = search_style_fallback(
            StyleFallbackRequest(
                reference=reference,
                visual_result=_visual_result(reference),
                user_id=7,
            ),
            client=FakeQdrantClient(
                hits=[_hit(item_id, score=0.99, payload=unrelated)]
            ),
        )

        self.assertEqual(result.candidates, ())

    def test_original_shared_item_is_never_returned(self) -> None:
        reference = _reference()

        result = search_style_fallback(
            StyleFallbackRequest(
                reference=reference,
                visual_result=_visual_result(reference),
                user_id=7,
            ),
            client=FakeQdrantClient(hits=[_hit(reference.point_id)]),
        )

        self.assertEqual(result.candidates, ())

    def test_other_owner_result_is_rejected(self) -> None:
        reference = _reference()
        item_id = _id()

        with self.assertRaises(StyleFallbackIndexMismatch):
            search_style_fallback(
                StyleFallbackRequest(
                    reference=reference,
                    visual_result=_visual_result(reference),
                    user_id=7,
                ),
                client=FakeQdrantClient(
                    hits=[
                        _hit(
                            item_id,
                            payload=_payload(item_id, user_id=99),
                        )
                    ]
                ),
            )

    def test_qdrant_failure_is_wrapped(self) -> None:
        reference = _reference()

        with self.assertRaises(StyleFallbackStoreUnavailable):
            search_style_fallback(
                StyleFallbackRequest(
                    reference=reference,
                    visual_result=_visual_result(reference),
                    user_id=7,
                ),
                client=FakeQdrantClient(error=TimeoutError("qdrant timeout")),
            )

    def test_real_qdrant_text_search_returns_style_candidate(self) -> None:
        reference = _reference()
        client = QdrantClient(":memory:")
        client.create_collection(
            collection_name=reference.collection_name,
            vectors_config={
                "image": qm.VectorParams(size=3, distance=qm.Distance.COSINE),
                "text": qm.VectorParams(size=3, distance=qm.Distance.COSINE),
            },
        )
        item_id = _id()
        client.upsert(
            collection_name=reference.collection_name,
            points=[
                qm.PointStruct(
                    id=item_id,
                    vector={
                        "image": [0.0, 1.0, 0.0],
                        "text": [0.99, 0.01, 0.0],
                    },
                    payload=_payload(item_id),
                )
            ],
        )

        result = search_style_fallback(
            StyleFallbackRequest(
                reference=reference,
                visual_result=_visual_result(reference),
                user_id=7,
            ),
            client=client,
        )

        self.assertEqual(
            [candidate.wardrobe_item_id for candidate in result.candidates],
            [item_id],
        )
