from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.wardrobe.services import vectors


def _item():
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=7,
        item_name="검정 재킷",
        category_large="아우터",
        category_small="재킷",
        season=["봄"],
        style=["미니멀"],
        color="검정",
        pattern="무지",
        fit="오버핏",
        material="울",
        sleeve="긴소매",
        length="기본",
        usage=["데이트"],
        layer_role="OUTER",
        layer_order=3,
        confirmed=True,
        s3_key="wardrobe/jacket.webp",
        embedding_version="fashionsiglip-v1",
    )


class WardrobeVectorPayloadTests(SimpleTestCase):
    @patch("apps.wardrobe.services.vectors.ensure_collection")
    @patch("apps.wardrobe.services.vectors._client")
    def test_upsert_payload_contains_style_fallback_tags(
        self,
        client_factory,
        _ensure_collection,
    ) -> None:
        client = Mock()
        client_factory.return_value = client

        saved = vectors.upsert_item(
            _item(),
            image_vector=[1.0, 0.0],
            text_vector=[0.0, 1.0],
        )

        self.assertTrue(saved)
        point = client.upsert.call_args.kwargs["points"][0]
        self.assertEqual(point.payload["style"], ["미니멀"])
        self.assertEqual(point.payload["color"], "검정")
        self.assertEqual(point.payload["fit"], "오버핏")
        self.assertEqual(point.payload["material"], "울")

    @patch("apps.wardrobe.services.vectors.ensure_collection")
    @patch("apps.wardrobe.services.vectors._client")
    def test_payload_update_keeps_style_fallback_tags_current(
        self,
        client_factory,
        _ensure_collection,
    ) -> None:
        client = Mock()
        client_factory.return_value = client
        item = _item()

        vectors.update_payload(item)

        payload = client.set_payload.call_args.kwargs["payload"]
        self.assertEqual(payload["style"], ["미니멀"])
        self.assertEqual(payload["color"], "검정")
        self.assertEqual(payload["fit"], "오버핏")
        self.assertEqual(payload["material"], "울")
