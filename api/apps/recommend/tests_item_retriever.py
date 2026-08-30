from __future__ import annotations

import uuid
from types import SimpleNamespace

from django.test import SimpleTestCase
from qdrant_client import QdrantClient
from qdrant_client import models as qm

from apps.recommend.services.item_retriever import (
    ItemCandidateRetriever,
    ItemRetrievalRequest,
    ItemSource,
    TemplateItemNotFound,
)
from apps.recommend.services.qdrant import (
    GOLDEN_ITEM_COLLECTION,
    collection_spec,
    product_collection_names,
)


def _point_id() -> str:
    return str(uuid.uuid4())


def _create_item_collection(client: QdrantClient, name: str) -> None:
    client.create_collection(
        collection_name=name,
        vectors_config={
            "image": qm.VectorParams(size=3, distance=qm.Distance.COSINE),
            "text": qm.VectorParams(size=3, distance=qm.Distance.COSINE),
        },
    )


def _upsert(
    client: QdrantClient,
    collection_name: str,
    *,
    point_id: str,
    payload: dict,
    image: list[float] | None = None,
    text: list[float] | None = None,
) -> None:
    vectors = {}
    if image is not None:
        vectors["image"] = image
    if text is not None:
        vectors["text"] = text
    client.upsert(
        collection_name=collection_name,
        points=[qm.PointStruct(id=point_id, vector=vectors, payload=payload)],
    )


class ItemCandidateRetrieverIntegrationTests(SimpleTestCase):
    def setUp(self) -> None:
        self.client = QdrantClient(":memory:")
        self.wardrobe_collection = collection_spec("wardrobe").name
        self.product_collections = product_collection_names()
        for collection_name in (
            GOLDEN_ITEM_COLLECTION,
            self.wardrobe_collection,
            *self.product_collections,
        ):
            _create_item_collection(self.client, collection_name)

        self.template_id = _point_id()
        _upsert(
            self.client,
            GOLDEN_ITEM_COLLECTION,
            point_id=self.template_id,
            image=[1.0, 0.0, 0.0],
            text=[0.0, 1.0, 0.0],
            payload={
                "golden_id": "outfit-1",
                "category_large": "상의",
                "category_small": "니트",
                "layer_role": "TOP",
                "dataset_version": "v1",
                "dataset_status": "approved",
            },
        )

    def test_retrieves_three_logical_sources_with_ownership_and_budget(self) -> None:
        owned_id = _point_id()
        _upsert(
            self.client,
            self.wardrobe_collection,
            point_id=owned_id,
            image=[0.99, 0.01, 0.0],
            text=[0.0, 1.0, 0.0],
            payload={
                "user_id": 7,
                "item_id": "wardrobe-7",
                "category_large": "상의",
                "category_small": "셔츠",
                "layer_role": "TOP",
                "confirmed": True,
                "s3_key": "wardrobe/7.jpg",
            },
        )
        _upsert(
            self.client,
            self.wardrobe_collection,
            point_id=_point_id(),
            image=[1.0, 0.0, 0.0],
            text=[0.0, 1.0, 0.0],
            payload={
                "user_id": 8,
                "item_id": "wardrobe-other-user",
                "category_large": "상의",
                "layer_role": "TOP",
                "confirmed": True,
            },
        )

        golden_alternative_id = _point_id()
        _upsert(
            self.client,
            GOLDEN_ITEM_COLLECTION,
            point_id=golden_alternative_id,
            image=[0.9, 0.1, 0.0],
            text=[0.0, 1.0, 0.0],
            payload={
                "golden_id": "outfit-2",
                "category_large": "상의",
                "category_small": "셔츠",
                "layer_role": "TOP",
                "dataset_version": "v1",
                "dataset_status": "approved",
            },
        )

        _upsert(
            self.client,
            self.product_collections[0],
            point_id=_point_id(),
            image=[0.85, 0.15, 0.0],
            text=[0.0, 1.0, 0.0],
            payload={
                "external_product_id": "naver-cheap",
                "category_large": "상의",
                "category_small": "셔츠",
                "layer_role": "TOP",
                "tagging_status": "tagged",
                "price": 49_000,
                "image_url": "https://example.com/naver.jpg",
            },
        )
        _upsert(
            self.client,
            self.product_collections[0],
            point_id=_point_id(),
            image=[1.0, 0.0, 0.0],
            text=[0.0, 1.0, 0.0],
            payload={
                "external_product_id": "naver-expensive",
                "category_large": "상의",
                "layer_role": "TOP",
                "tagging_status": "tagged",
                "price": 200_000,
            },
        )
        _upsert(
            self.client,
            self.product_collections[1],
            point_id=_point_id(),
            image=[0.95, 0.05, 0.0],
            text=[0.0, 1.0, 0.0],
            payload={
                "external_product_id": "eleven-cheap",
                "category_large": "상의",
                "layer_role": "TOP",
                "tagging_status": "tagged",
                "price": 59_000,
            },
        )

        result = ItemCandidateRetriever(client=self.client).retrieve(
            ItemRetrievalRequest(
                template_item_point_id=self.template_id,
                user_id=7,
                max_price=60_000,
                dataset_version="v1",
                dataset_statuses=("approved",),
                limit_per_source=5,
            )
        )

        wardrobe = result.for_source(ItemSource.WARDROBE)
        golden = result.for_source(ItemSource.GOLDENSET_ITEM)
        products = result.for_source(ItemSource.PRODUCT)

        self.assertEqual(
            [candidate.source_id for candidate in wardrobe], ["wardrobe-7"]
        )
        self.assertTrue(wardrobe[0].is_owned)
        self.assertEqual(wardrobe[0].image_ref, "wardrobe/7.jpg")
        self.assertEqual(
            [candidate.point_id for candidate in golden], [golden_alternative_id]
        )
        self.assertNotIn(self.template_id, [candidate.point_id for candidate in golden])
        self.assertEqual(
            [candidate.source_id for candidate in products],
            ["eleven-cheap", "naver-cheap"],
        )
        self.assertTrue(all(candidate.is_purchasable for candidate in products))
        self.assertTrue(all(candidate.price <= 60_000 for candidate in products))
        self.assertEqual(result.vector_name, "image")

    def test_wardrobe_whitelist_includes_shared_item_owned_by_another_user(self) -> None:
        shared_id = _point_id()
        blocked_id = _point_id()
        for point_id, user_id in ((shared_id, 8), (blocked_id, 9)):
            _upsert(
                self.client,
                self.wardrobe_collection,
                point_id=point_id,
                image=[1.0, 0.0, 0.0],
                payload={
                    "user_id": user_id,
                    "item_id": point_id,
                    "category_large": "상의",
                    "layer_role": "TOP",
                    "confirmed": True,
                },
            )

        result = ItemCandidateRetriever(client=self.client).retrieve(
            ItemRetrievalRequest(
                template_item_point_id=self.template_id,
                sources=(ItemSource.WARDROBE,),
                user_id=7,
                allowed_wardrobe_item_ids=(shared_id,),
            )
        )

        self.assertEqual(
            [candidate.point_id for candidate in result.candidates], [shared_id]
        )

    def test_empty_wardrobe_whitelist_skips_qdrant_search(self) -> None:
        result = ItemCandidateRetriever(client=self.client).retrieve(
            ItemRetrievalRequest(
                template_item_point_id=self.template_id,
                sources=(ItemSource.WARDROBE,),
                user_id=7,
                allowed_wardrobe_item_ids=(),
            )
        )

        self.assertEqual(result.candidates, ())


class FakeQdrantClient:
    def __init__(self, *, template=None, query_hits=None, scroll_points=None) -> None:
        self.template = template
        self.query_hits = list(query_hits or [])
        self.scroll_points = list(scroll_points or [])
        self.query_calls: list[dict] = []
        self.scroll_calls: list[dict] = []

    def retrieve(self, **kwargs):
        return [self.template] if self.template is not None else []

    def query_points(self, **kwargs):
        self.query_calls.append(kwargs)
        return SimpleNamespace(points=self.query_hits)

    def scroll(self, **kwargs):
        self.scroll_calls.append(kwargs)
        return self.scroll_points, None


def _template(*, point_id: str = "template", vectors=None):
    return SimpleNamespace(
        id=point_id,
        vector=vectors or {},
        payload={"category_large": "상의", "layer_role": "TOP"},
    )


class ItemCandidateRetrieverUnitTests(SimpleTestCase):
    def test_category_budget_is_used_as_product_price_filter(self) -> None:
        client = FakeQdrantClient(template=_template(vectors={"text": [0.1, 0.2]}))

        ItemCandidateRetriever(client=client).retrieve(
            ItemRetrievalRequest(
                template_item_point_id="template",
                sources=(ItemSource.PRODUCT,),
                category_budgets={"상의": 80_000},
            )
        )

        ranges = [
            condition.range.lte
            for call in client.query_calls
            for condition in call["query_filter"].must
            if getattr(condition, "range", None) is not None
        ]
        # 후보가 모자라면 좁은 조건을 하나씩 풀며 다시 찾는다. 호출 수는 그때그때
        # 달라지지만 가격 상한은 모든 호출에 똑같이 걸려야 한다.
        self.assertTrue(ranges)
        self.assertEqual(set(ranges), {80_000})

    def test_wardrobe_source_requires_positive_user_id(self) -> None:
        retriever = ItemCandidateRetriever(client=FakeQdrantClient())

        with self.assertRaisesMessage(ValueError, "user_id"):
            retriever.retrieve(ItemRetrievalRequest(template_item_point_id="item"))

    def test_missing_template_raises_domain_error(self) -> None:
        retriever = ItemCandidateRetriever(client=FakeQdrantClient())

        with self.assertRaises(TemplateItemNotFound):
            retriever.retrieve(
                ItemRetrievalRequest(
                    template_item_point_id="missing",
                    sources=(ItemSource.PRODUCT,),
                )
            )

    def test_text_vector_is_used_when_image_vector_is_missing(self) -> None:
        hit = SimpleNamespace(
            id="product",
            score=0.8,
            payload={
                "external_product_id": "product-1",
                "category_large": "상의",
                "layer_role": "TOP",
                "price": 10_000,
            },
        )
        client = FakeQdrantClient(
            template=_template(vectors={"text": [0.1, 0.2, 0.3]}),
            query_hits=[hit],
        )

        result = ItemCandidateRetriever(client=client).retrieve(
            ItemRetrievalRequest(
                template_item_point_id="template",
                sources=(ItemSource.PRODUCT,),
            )
        )

        self.assertEqual(result.vector_name, "text")
        self.assertTrue(client.query_calls)
        self.assertTrue(
            all(call["using"] == "text" for call in client.query_calls)
        )
        self.assertTrue(all(call["using"] == "text" for call in client.query_calls))

    def test_filter_search_is_used_when_template_has_no_vector(self) -> None:
        client = FakeQdrantClient(
            template=_template(),
            scroll_points=[
                SimpleNamespace(
                    id="alternative",
                    payload={"category_large": "상의", "layer_role": "TOP"},
                )
            ],
        )

        result = ItemCandidateRetriever(client=client).retrieve(
            ItemRetrievalRequest(
                template_item_point_id="template",
                sources=(ItemSource.GOLDENSET_ITEM,),
            )
        )

        self.assertEqual(result.vector_name, "filter")
        self.assertIsNone(result.candidates[0].score)
        self.assertIn("태그 조건", result.candidates[0].reasons[-1])
        self.assertTrue(client.scroll_calls)


class ItemCandidateRetrieverAvoidedTagTests(SimpleTestCase):
    """기피 태그는 검증이 아니라 후보 검색에서 걸러야 한다."""

    def setUp(self) -> None:
        self.client = QdrantClient(":memory:")
        self.product_collections = product_collection_names()
        for collection_name in (GOLDEN_ITEM_COLLECTION, *self.product_collections):
            _create_item_collection(self.client, collection_name)

        self.template_id = _point_id()
        _upsert(
            self.client,
            GOLDEN_ITEM_COLLECTION,
            point_id=self.template_id,
            image=[1.0, 0.0, 0.0],
            text=[0.0, 1.0, 0.0],
            payload={
                "golden_id": "outfit-1",
                "category_large": "상의",
                "layer_role": "TOP",
            },
        )
        for external_id, style in (("naver-casual", "캐주얼"), ("naver-classic", "클래식")):
            _upsert(
                self.client,
                self.product_collections[0],
                point_id=_point_id(),
                image=[0.9, 0.1, 0.0],
                text=[0.0, 1.0, 0.0],
                payload={
                    "external_product_id": external_id,
                    "category_large": "상의",
                    "layer_role": "TOP",
                    "style": [style],
                    "tagging_status": "tagged",
                    "price": 10000,
                    "image_url": "https://example.test/item.jpg",
                },
            )

    def _retrieve(self, avoided_tags):
        return ItemCandidateRetriever(client=self.client).retrieve(
            ItemRetrievalRequest(
                template_item_point_id=self.template_id,
                sources=(ItemSource.PRODUCT,),
                avoided_tags=avoided_tags,
                limit_per_source=10,
            )
        )

    def test_returns_every_candidate_without_avoided_tags(self) -> None:
        result = self._retrieve({})

        self.assertEqual(len(result.candidates), 2)

    def test_excludes_candidates_carrying_an_avoided_tag(self) -> None:
        result = self._retrieve({"style": ("캐주얼",)})

        self.assertEqual(
            [candidate.payload["external_product_id"] for candidate in result.candidates],
            ["naver-classic"],
        )

    def test_empty_label_set_does_not_filter(self) -> None:
        """빈 라벨로 must_not을 만들면 Qdrant가 전부 떨어뜨린다 — 조건 자체를 걸지 않는다."""

        result = self._retrieve({"style": ()})

        self.assertEqual(len(result.candidates), 2)
