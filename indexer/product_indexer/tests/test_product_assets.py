from __future__ import annotations

import sys
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

# indexer/ 를 import 루트로 잡아 product_indexer·util 패키지를 찾게 한다.
INDEXER_ROOT = Path(__file__).resolve().parents[2]
if str(INDEXER_ROOT) not in sys.path:
    sys.path.insert(0, str(INDEXER_ROOT))

from product_indexer.product_assets import (
    InvalidProductImage,
    StoredProductImageUnavailable,
    download_and_store_image,
    load_stored_image,
)


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body
        self.headers = {"Content-Length": str(len(body))}

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        yield self.body


class FakeSession:
    def __init__(self, body: bytes):
        self.body = body

    def get(self, url: str, timeout: int, stream: bool):
        return FakeResponse(self.body)


class FakeS3:
    def __init__(self):
        self.puts = []
        self.objects = {}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = {
            "body": kwargs["Body"],
            "metadata": kwargs.get("Metadata", {}),
        }

    def get_object(self, *, Bucket, Key):
        stored = self.objects[(Bucket, Key)]
        return {
            "Body": BytesIO(stored["body"]),
            "ContentLength": len(stored["body"]),
            "Metadata": stored["metadata"],
        }


def png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGBA", (4, 4), (255, 0, 0, 128)).save(output, format="PNG")
    return output.getvalue()


class ProductAssetTests(unittest.TestCase):
    def test_download_normalizes_and_uploads_deterministic_jpeg(self) -> None:
        s3 = FakeS3()

        prepared = download_and_store_image(
            session=FakeSession(png_bytes()),
            s3_client=s3,
            source="naver",
            external_product_id="product/1",
            image_url="https://example.com/product.png",
            bucket="product-bucket",
            key_prefix="products/naver",
            timeout=10,
            max_bytes=1024 * 1024,
        )

        self.assertEqual(prepared.image.mode, "RGB")
        self.assertEqual(len(prepared.checksum), 64)
        self.assertIn("products/naver/product%2F1/", prepared.s3_key)
        self.assertTrue(prepared.s3_key.endswith(".jpg"))
        self.assertEqual(s3.puts[0]["ContentType"], "image/jpeg")
        prepared.image.close()

    def test_reuses_stored_image_when_checksum_matches(self) -> None:
        s3 = FakeS3()
        first = download_and_store_image(
            session=FakeSession(png_bytes()),
            s3_client=s3,
            source="eleven",
            external_product_id="100",
            image_url="https://example.com/product.png",
            bucket="product-bucket",
            key_prefix="products/eleven",
            timeout=10,
            max_bytes=1024 * 1024,
        )
        first.image.close()

        reused = load_stored_image(
            s3_client=s3,
            bucket="product-bucket",
            s3_key=first.s3_key,
            expected_checksum=first.checksum,
            max_bytes=1024 * 1024,
        )

        self.assertEqual(reused.checksum, first.checksum)
        self.assertEqual(reused.s3_key, first.s3_key)
        self.assertEqual(reused.image.mode, "RGB")
        self.assertEqual(len(s3.puts), 1)
        reused.image.close()

    def test_rejects_stored_image_when_checksum_differs(self) -> None:
        s3 = FakeS3()
        first = download_and_store_image(
            session=FakeSession(png_bytes()),
            s3_client=s3,
            source="naver",
            external_product_id="100",
            image_url="https://example.com/product.png",
            bucket="product-bucket",
            key_prefix="products/naver",
            timeout=10,
            max_bytes=1024 * 1024,
        )
        first.image.close()

        with self.assertRaises(StoredProductImageUnavailable):
            load_stored_image(
                s3_client=s3,
                bucket="product-bucket",
                s3_key=first.s3_key,
                expected_checksum="0" * 64,
                max_bytes=1024 * 1024,
            )

    def test_rejects_empty_image_url(self) -> None:
        with self.assertRaises(InvalidProductImage):
            download_and_store_image(
                session=FakeSession(b""),
                s3_client=FakeS3(),
                source="eleven",
                external_product_id="1",
                image_url="",
                bucket="product-bucket",
                key_prefix="products/eleven",
                timeout=10,
                max_bytes=1024,
            )


if __name__ == "__main__":
    unittest.main()
