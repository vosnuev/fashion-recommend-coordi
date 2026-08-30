from __future__ import annotations

from django.test import SimpleTestCase

from apps.recommend.services.retriever import RetrievalRequest, build_filter


class SharedReferenceGoldenRetrieverTests(SimpleTestCase):
    def test_reference_category_and_slot_are_required_outfit_filters(self) -> None:
        built = build_filter(
            RetrievalRequest(
                required_item_categories=("아우터",),
                required_item_layer_roles=("OUTER",),
            )
        )

        self.assertIsNotNone(built)
        conditions = {condition.key: condition.match.any for condition in built.must}
        self.assertEqual(conditions["item_categories"], ["아우터"])
        self.assertEqual(conditions["item_layer_roles"], ["OUTER"])
