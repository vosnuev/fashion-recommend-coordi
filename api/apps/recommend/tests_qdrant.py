from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.recommend.services.qdrant import (
    CollectionSpec,
    QdrantContractError,
    collection_spec,
    collection_specs,
    ensure_collection_contract,
    inspect_collection,
    product_collection_names,
)
from apps.wardrobe.services import vectors as wardrobe_vectors


class FakeQdrantClient:
    def __init__(self) -> None:
        self.collections: dict[str, SimpleNamespace] = {}
        self.created_indexes: list[tuple[str, str, str]] = []

    def collection_exists(self, name: str) -> bool:
        return name in self.collections

    def get_collection(self, name: str) -> SimpleNamespace:
        return self.collections[name]

    def create_collection(self, *, collection_name: str, vectors_config) -> None:
        self.collections[collection_name] = self.info(vectors_config, {})

    def delete_collection(self, name: str) -> None:
        self.collections.pop(name, None)

    def create_payload_index(
        self, *, collection_name: str, field_name: str, field_schema: str
    ) -> None:
        self.created_indexes.append((collection_name, field_name, field_schema))
        self.collections[collection_name].payload_schema[field_name] = SimpleNamespace(
            data_type=field_schema
        )

    @staticmethod
    def info(
        vectors: dict[str, object],
        payload_schema: dict[str, object],
        *,
        points_count: int = 0,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            config=SimpleNamespace(params=SimpleNamespace(vectors=vectors)),
            payload_schema=payload_schema,
            points_count=points_count,
        )


@override_settings(
    QDRANT_IMAGE_VECTOR_DIM=768,
    QDRANT_TEXT_VECTOR_DIM=1024,
    QDRANT_GOLDEN_OUTFIT_COLLECTION="outfit_goldenset",
    QDRANT_GOLDEN_ITEM_COLLECTION="goldenset_items",
    QDRANT_WARDROBE_COLLECTION="wardrobe_items",
    PRODUCT_NAVER_QDRANT_COLLECTION="products_naver_v1",
    PRODUCT_ELEVEN_QDRANT_COLLECTION="products_eleven_v1",
    QDRANT_KNOWLEDGE_COLLECTION="knowledge",
)
class QdrantCollectionContractTests(SimpleTestCase):
    def test_collection_roles_use_actual_writer_collection_names(self) -> None:
        by_role = {spec.role: spec for spec in collection_specs()}

        self.assertEqual(by_role["golden_outfits"].name, "outfit_goldenset")
        self.assertEqual(by_role["golden_items"].name, "goldenset_items")
        self.assertEqual(by_role["wardrobe"].name, "wardrobe_items")
        self.assertEqual(
            product_collection_names(),
            ("products_naver_v1", "products_eleven_v1"),
        )

    def test_cross_source_item_contract_has_shared_filter_indexes(self) -> None:
        roles = ("golden_items", "wardrobe", "products_naver", "products_eleven")
        shared = {
            "category_large",
            "category_small",
            "layer_role",
            "season",
            "style",
            "occasion",
            "color",
            "fit",
            "pattern",
            "material",
        }

        for role in roles:
            self.assertTrue(
                shared.issubset(collection_spec(role).payload_indexes),
                msg=role,
            )

    @override_settings(PRODUCT_ELEVEN_QDRANT_COLLECTION="products_naver_v1")
    def test_duplicate_collection_names_are_rejected(self) -> None:
        with self.assertRaises(QdrantContractError):
            collection_specs()

    def test_missing_payload_index_is_reported_and_added(self) -> None:
        client = FakeQdrantClient()
        spec = CollectionSpec(
            role="test",
            name="test_items",
            vectors={"image": 3, "text": 4},
            payload_indexes={"style": "keyword", "price": "integer"},
        )
        client.collections[spec.name] = client.info(
            {
                "image": SimpleNamespace(size=3),
                "text": SimpleNamespace(size=4),
            },
            {"style": SimpleNamespace(data_type="keyword")},
        )

        before = inspect_collection(client, spec)
        created = ensure_collection_contract(client, spec)
        after = inspect_collection(client, spec)

        self.assertEqual(before.missing_payload_indexes, ("price",))
        self.assertFalse(created)
        self.assertTrue(after.valid)
        self.assertEqual(
            client.created_indexes,
            [("test_items", "price", "integer")],
        )

    def test_incompatible_vector_dimension_is_not_changed_automatically(self) -> None:
        client = FakeQdrantClient()
        spec = CollectionSpec(
            role="test",
            name="test_items",
            vectors={"image": 3},
        )
        client.collections[spec.name] = client.info(
            {"image": SimpleNamespace(size=99)},
            {},
        )

        with self.assertRaisesMessage(QdrantContractError, "expected=3, actual=99"):
            ensure_collection_contract(client, spec)

    def test_missing_collection_is_created_with_full_contract(self) -> None:
        client = FakeQdrantClient()
        spec = CollectionSpec(
            role="test",
            name="test_items",
            vectors={"image": 3, "text": 4},
            payload_indexes={"style": "keyword", "price": "integer"},
        )

        created = ensure_collection_contract(client, spec)

        self.assertTrue(created)
        self.assertTrue(inspect_collection(client, spec).valid)
        self.assertEqual(len(client.created_indexes), 2)


class WardrobeQdrantContractIntegrationTests(SimpleTestCase):
    @patch.object(wardrobe_vectors, "ensure_collection_contract")
    @patch.object(wardrobe_vectors, "collection_spec")
    @patch.object(wardrobe_vectors, "_client")
    def test_wardrobe_uses_shared_collection_contract(
        self,
        client_mock,
        collection_spec_mock,
        ensure_contract_mock,
    ) -> None:
        client = object()
        spec = object()
        client_mock.return_value = client
        collection_spec_mock.return_value = spec

        wardrobe_vectors.ensure_collection()

        collection_spec_mock.assert_called_once_with("wardrobe")
        ensure_contract_mock.assert_called_once_with(client, spec)
