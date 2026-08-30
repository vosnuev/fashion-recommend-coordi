from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

import config
import reindex_worker


class FakeEmbedder:
    version = config.EMBEDDING_VERSION

    def embed_image(self, image_bytes: bytes) -> list[float]:
        if image_bytes != b"image-bytes":
            raise AssertionError("unexpected image")
        return [1.0, 0.0]

    def embed_text(self, caption: str) -> list[float]:
        if "셔츠" not in caption:
            raise AssertionError("tags were not converted to a caption")
        return [0.0, 1.0, 0.0]


def _payload(**overrides) -> dict:
    payload = {
        "item_id": "b9ed3e3a-13c5-4af7-8722-85d236c36068",
        "user_id": 7,
        "source": {"bucket": "wardrobe", "key": "wardrobe/7/item.webp"},
        "source_updated_at": "2026-08-19T12:00:00+09:00",
        "embedding_version": config.EMBEDDING_VERSION,
        "tags": {"item_name": "셔츠", "category_large": "상의"},
        "callback_url": "https://api.example.com/reindex-callback/",
    }
    payload.update(overrides)
    return payload


class ReindexWorkerTests(TestCase):
    def test_normalize_rejects_different_embedding_space(self) -> None:
        with self.assertRaisesRegex(ValueError, "임베딩 버전"):
            reindex_worker.normalize_payload(
                _payload(embedding_version="old-version")
            )

    @patch("reindex_worker.s3io.download")
    def test_existing_crop_and_tags_create_both_vectors(self, download) -> None:
        def write_image(_bucket, _key, local_path) -> None:
            with open(local_path, "wb") as image_file:
                image_file.write(b"image-bytes")

        download.side_effect = write_image
        job = reindex_worker.normalize_payload(_payload())

        result = reindex_worker.build_success_callback(job, FakeEmbedder())

        self.assertEqual(result["image_vector"], [1.0, 0.0])
        self.assertEqual(result["text_vector"], [0.0, 1.0, 0.0])
        self.assertEqual(result["embedding_version"], config.EMBEDDING_VERSION)
