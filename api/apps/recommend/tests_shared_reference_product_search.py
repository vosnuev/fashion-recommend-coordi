from __future__ import annotations

import uuid
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from apps.recommend.services.qdrant import collection_spec
from apps.recommend.services.shared_reference_loader import (
    ReferenceSearchExclusions,
    SharedReferenceSearchBasis,
    SharedReferenceTags,
)
from apps.recommend.services.shared_reference_product_search import (
    SharedReferenceProductIndexMismatch,
    SharedReferenceProductSearchInvalid,
    SharedReferenceProductSearchRequest,
    SharedReferenceProductStoreUnavailable,
    search_similar_products,
)
from apps.recommend.services.validator import SourceEligibility


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
            layer_role="OUTER",
            layer_order=3,
        ),
        exclusions=ReferenceSearchExclusions(
            wardrobe_item_ids=(source_id,),
            qdrant_point_ids=(source_id,),
        ),
    )


def _payload(
    source: str,
    product_id: str,
    *,
    category_large: str = "아우터",
    layer_role: str = "OUTER",
    tagging_status: str = "tagged",
    price: int = 50_000,
) -> dict:
    return {
        "source": source,
        "external_product_id": product_id,
        "title": f"{source} 재킷",
        "brand": "브랜드",
        "mall_name": "쇼핑몰",
        "link": f"https://example.com/{product_id}",
        "image_url": f"https://example.com/{product_id}.jpg",
        "image_s3_key": f"products/{source}/{product_id}.webp",
        "price": price,
        "category_large": category_large,
        "category_small": "재킷",
        "layer_role": layer_role,
        "tagging_status": tagging_status,
    }


def _hit(source: str, product_id: str, *, score: float, **payload_overrides):
    payload = _payload(source, product_id)
    payload.update(payload_overrides)
    return SimpleNamespace(id=_id(), score=score, payload=payload)


class FakeQdrantClient:
    def __init__(self, hits_by_collection=None, *, error: Exception | None = None):
        self.hits_by_collection = hits_by_collection or {}
        self.error = error
        self.query_calls: list[dict] = []

    def query_points(self, **kwargs):
        self.query_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            points=self.hits_by_collection.get(kwargs["collection_name"], [])
        )


class FakeEligibilityGateway:
    def __init__(self, statuses: dict[str, SourceEligibility]) -> None:
        self.statuses = statuses
        self.checked_items = ()

    def check(self, items, *, user_id):
        self.checked_items = items
        return {
            item.identity: self.statuses.get(
                item.source_id,
                SourceEligibility(eligible=False, code="PRODUCT_NOT_ON_SALE"),
            )
            for item in items
        }


@override_settings(SHARED_REFERENCE_VISUAL_MIN_SCORE=0.75)
class SharedReferenceProductSearchTests(SimpleTestCase):
    def test_selects_best_sellable_product_from_naver_and_eleven(self) -> None:
        naver_collection = collection_spec("products_naver").name
        eleven_collection = collection_spec("products_eleven").name
        client = FakeQdrantClient(
            {
                naver_collection: [_hit("naver", "n-1", score=0.88)],
                eleven_collection: [_hit("eleven", "e-1", score=0.93)],
            }
        )
        gateway = FakeEligibilityGateway(
            {
                "n-1": SourceEligibility(eligible=True, current_price=45_000),
                "e-1": SourceEligibility(eligible=True, current_price=48_000),
            }
        )

        result = search_similar_products(
            SharedReferenceProductSearchRequest(
                reference=_reference(),
                total_budget=100_000,
                category_budgets={"아우터": 60_000},
            ),
            client=client,
            eligibility_gateway=gateway,
        )

        self.assertEqual(
            [candidate.external_product_id for candidate in result.candidates],
            ["e-1", "n-1"],
        )
        self.assertEqual(result.selected_anchor.external_product_id, "e-1")
        self.assertEqual(result.selected_anchor.selection_role, "NEW_ITEM_ANCHOR")
        self.assertEqual(result.selected_anchor.sale_status, "ON_SALE")
        self.assertEqual(result.selected_anchor.tagging_status, "tagged")

    def test_query_applies_tagging_slot_category_and_effective_budget(self) -> None:
        client = FakeQdrantClient()

        result = search_similar_products(
            SharedReferenceProductSearchRequest(
                reference=_reference(),
                total_budget=90_000,
                category_budgets={"아우터": 50_000},
                already_selected_total=30_000,
                limit=5,
            ),
            client=client,
            eligibility_gateway=FakeEligibilityGateway({}),
        )

        self.assertEqual(len(client.query_calls), 2)
        self.assertEqual(result.remaining_total_budget, 60_000)
        self.assertEqual(result.category_budget, 50_000)
        self.assertEqual(result.effective_max_price, 50_000)
        for call in client.query_calls:
            self.assertEqual(call["using"], "image")
            self.assertEqual(call["score_threshold"], 0.75)
            self.assertEqual(call["limit"], 5)
            values = {
                condition.key: condition.match.value
                for condition in call["query_filter"].must
                if condition.match is not None
            }
            self.assertEqual(
                values,
                {
                    "tagging_status": "tagged",
                    "category_large": "아우터",
                    "layer_role": "OUTER",
                },
            )
            price_condition = next(
                condition
                for condition in call["query_filter"].must
                if condition.key == "price"
            )
            self.assertEqual(price_condition.range.gte, 0)
            self.assertEqual(price_condition.range.lte, 50_000)

    def test_database_current_price_must_still_fit_budget(self) -> None:
        collection = collection_spec("products_naver").name
        client = FakeQdrantClient(
            {collection: [_hit("naver", "n-price-changed", score=0.95)]}
        )
        gateway = FakeEligibilityGateway(
            {
                "n-price-changed": SourceEligibility(
                    eligible=True,
                    current_price=80_000,
                )
            }
        )

        result = search_similar_products(
            SharedReferenceProductSearchRequest(
                reference=_reference(),
                total_budget=50_000,
            ),
            client=client,
            eligibility_gateway=gateway,
        )

        self.assertEqual(result.candidates, ())
        self.assertIsNone(result.selected_anchor)

    def test_not_on_sale_or_not_tagged_product_is_not_selected(self) -> None:
        collection = collection_spec("products_eleven").name
        client = FakeQdrantClient(
            {
                collection: [
                    _hit("eleven", "sold-out", score=0.96),
                    _hit("eleven", "available", score=0.85),
                ]
            }
        )
        gateway = FakeEligibilityGateway(
            {
                "sold-out": SourceEligibility(
                    eligible=False,
                    code="PRODUCT_NOT_ON_SALE",
                ),
                "available": SourceEligibility(
                    eligible=True,
                    current_price=40_000,
                ),
            }
        )

        result = search_similar_products(
            SharedReferenceProductSearchRequest(reference=_reference()),
            client=client,
            eligibility_gateway=gateway,
        )

        self.assertEqual(result.selected_anchor.external_product_id, "available")

    def test_wrong_slot_payload_is_rejected(self) -> None:
        collection = collection_spec("products_naver").name
        client = FakeQdrantClient(
            {collection: [_hit("naver", "wrong-slot", score=0.9, layer_role="TOP")]}
        )

        with self.assertRaises(SharedReferenceProductIndexMismatch):
            search_similar_products(
                SharedReferenceProductSearchRequest(reference=_reference()),
                client=client,
                eligibility_gateway=FakeEligibilityGateway({}),
            )

    def test_invalid_budget_is_rejected_before_query(self) -> None:
        client = FakeQdrantClient()

        with self.assertRaises(SharedReferenceProductSearchInvalid):
            search_similar_products(
                SharedReferenceProductSearchRequest(
                    reference=_reference(),
                    total_budget=-1,
                ),
                client=client,
                eligibility_gateway=FakeEligibilityGateway({}),
            )

        self.assertEqual(client.query_calls, [])

    def test_qdrant_failure_is_wrapped(self) -> None:
        with self.assertRaises(SharedReferenceProductStoreUnavailable):
            search_similar_products(
                SharedReferenceProductSearchRequest(reference=_reference()),
                client=FakeQdrantClient(error=TimeoutError("qdrant timeout")),
                eligibility_gateway=FakeEligibilityGateway({}),
            )
