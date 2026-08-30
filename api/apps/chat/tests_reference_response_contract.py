from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase

from apps.chat.models import ChatMessage
from apps.chat.serializers import (
    ChatMessageSerializer,
    ChatRunPersonaCardSerializer,
)


class SharedReferenceResponseContractTests(SimpleTestCase):
    @patch(
        "apps.chat.serializers.wardrobe_storage.presigned_get",
        return_value="https://images.example/reference-jacket.webp",
    )
    def test_user_message_exposes_only_safe_reference_summary(
        self,
        _presigned_get_mock,
    ) -> None:
        shared_item_id = str(uuid4())
        message = SimpleNamespace(
            pk=uuid4(),
            role=ChatMessage.Role.USER,
            run=SimpleNamespace(
                reference_snapshot={
                    "schema_version": "1.0",
                    "type": "SHARED_WARDROBE_ITEM",
                    "shared_item_id": shared_item_id,
                    "room_id": str(uuid4()),
                    "wardrobe_item_id": str(uuid4()),
                    "qdrant_collection": "wardrobe-v1",
                    "qdrant_point_id": str(uuid4()),
                    "image_s3_key": "wardrobe/reference-jacket.webp",
                    "owner_name": "하영",
                    "room_name": "친구 옷장",
                    "item": {
                        "item_name": "친구의 검정 재킷",
                        "category_large": "아우터",
                    },
                }
            ),
        )

        summary = ChatMessageSerializer().get_reference_summary(message)

        self.assertEqual(
            summary,
            {
                "schema_version": "1.0",
                "type": "SHARED_WARDROBE_ITEM",
                "shared_item_id": shared_item_id,
                "item_name": "친구의 검정 재킷",
                "category_large": "아우터",
                "owner_name": "하영",
                "room_name": "친구 옷장",
                "image_url": "https://images.example/reference-jacket.webp",
            },
        )
        self.assertNotIn("qdrant_collection", summary)
        self.assertNotIn("qdrant_point_id", summary)
        self.assertNotIn("image_s3_key", summary)
        self.assertNotIn("wardrobe_item_id", summary)

    def test_message_without_reference_returns_null_summary(self) -> None:
        message = SimpleNamespace(
            pk=uuid4(),
            role=ChatMessage.Role.USER,
            run=SimpleNamespace(reference_snapshot={}),
        )

        self.assertIsNone(
            ChatMessageSerializer().get_reference_summary(message)
        )

    def test_stylist_card_serializer_exposes_reference_match(self) -> None:
        reference_match = {
            "schema_version": "1.0",
            "match_type": "VISUAL_SIMILAR",
            "selection_role": "PINNED_REFERENCE_ANCHOR",
            "source_type": "PRODUCT",
            "source_id": "product-1",
            "source_collection": "products-v1",
            "source_point_id": "point-1",
            "template_item_point_id": "template-1",
            "score": 0.91,
            "reasons": ["공유 옷 이미지와 유사함"],
        }
        serializer = ChatRunPersonaCardSerializer()

        self.assertIn("reference_match", serializer.fields)
        self.assertEqual(
            serializer.fields["reference_match"].to_representation(
                reference_match
            ),
            reference_match,
        )
