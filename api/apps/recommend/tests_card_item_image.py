"""추천 카드 아이템 사진 URL 계약 테스트.

앱은 http(s) 주소만 그릴 수 있는데 `image_ref`는 대부분 비공개 S3 키다. 그래서
조회 시점에 presigned URL을 만들어 `image_url`로 내려준다 — 이 파일은 그 규칙과
실패해도 카드가 살아남는다는 점을 지킨다.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.recommend.serializers import RecommendationCardItemSerializer
from apps.recommend.services.item_images import image_url_for

BUCKETS = {
    "OUTFIT_RENDER_WARDROBE_BUCKET": "wardrobe-bucket",
    "OUTFIT_RENDER_PRODUCT_BUCKET": "product-bucket",
    "OUTFIT_RENDER_GOLDENSET_BUCKET": "golden-bucket",
    "OUTFIT_RENDER_PRESIGNED_GET_TTL_SECONDS": 3600,
}


def _item(*, source_type="WARDROBE", image_ref="", snapshot=None):
    return SimpleNamespace(
        source_type=source_type,
        image_ref=image_ref,
        item_snapshot=snapshot if snapshot is not None else {},
    )


@override_settings(**BUCKETS)
class ItemImageUrlTests(SimpleTestCase):
    def setUp(self) -> None:
        patcher = patch(
            "apps.recommend.services.item_images.storage.presigned_get_for",
            side_effect=lambda bucket, key, ttl=0: f"https://s3.test/{bucket}/{key}?ttl={ttl}",
        )
        self.presign = patcher.start()
        self.addCleanup(patcher.stop)

    def test_http_ref_is_passed_through(self) -> None:
        """이미 주소인 옛 데이터는 서명하지 않는다."""
        url = image_url_for(_item(image_ref="https://cdn.test/top.png"))
        self.assertEqual(url, "https://cdn.test/top.png")
        self.presign.assert_not_called()

    def test_wardrobe_key_uses_the_wardrobe_bucket(self) -> None:
        """옷장 벡터 payload에는 버킷이 없다 — 출처로 기본 버킷을 정한다."""
        url = image_url_for(_item(image_ref="wardrobe/2026/ab12.jpg"))
        self.assertEqual(url, "https://s3.test/wardrobe-bucket/wardrobe/2026/ab12.jpg?ttl=3600")

    def test_golden_item_uses_the_golden_bucket(self) -> None:
        url = image_url_for(
            _item(source_type="GOLDENSET_ITEM", image_ref="goldenset/derived/v1/095/item_000.png")
        )
        self.assertTrue(url.startswith("https://s3.test/golden-bucket/"))

    def test_snapshot_key_wins_but_the_bucket_comes_from_settings(self) -> None:
        """키는 스냅샷이, **버킷은 지금 설정이** 이긴다.

        스냅샷의 image_s3_bucket은 그 상품을 인덱싱하던 인덱서 env가 Qdrant
        payload를 거쳐 박아 둔 값이다. 버킷을 옮기고 나면 그 값으로 서명한 URL은
        404가 된다 — 실제로 그렇게 깨졌다(skn28-cozy → skn28-cozy3).
        """
        url = image_url_for(
            _item(
                source_type="PRODUCT",
                image_ref="products/legacy.jpg",
                snapshot={
                    "image_s3_bucket": "old-indexer-bucket",
                    "image_s3_key": "products/2026/xyz.jpg",
                    "image_url": "https://shop.test/xyz.jpg",
                },
            )
        )
        self.assertEqual(
            url, "https://s3.test/product-bucket/products/2026/xyz.jpg?ttl=3600"
        )

    @override_settings(OUTFIT_RENDER_PRODUCT_BUCKET="")
    def test_snapshot_bucket_is_the_fallback_when_unset(self) -> None:
        """설정이 비었을 때만 스냅샷 버킷으로 물러선다."""
        url = image_url_for(
            _item(
                source_type="PRODUCT",
                snapshot={
                    "image_s3_bucket": "old-indexer-bucket",
                    "image_s3_key": "products/2026/xyz.jpg",
                },
            )
        )
        self.assertEqual(
            url, "https://s3.test/old-indexer-bucket/products/2026/xyz.jpg?ttl=3600"
        )

    def test_falls_back_to_the_original_shop_url(self) -> None:
        """S3 사본이 없는 상품은 원본 주소라도 보여 준다."""
        url = image_url_for(
            _item(
                source_type="PRODUCT",
                snapshot={"image_url": "https://shop.test/xyz.jpg"},
            )
        )
        self.assertEqual(url, "https://shop.test/xyz.jpg")

    def test_presign_failure_does_not_break_the_card(self) -> None:
        self.presign.side_effect = RuntimeError("boto is unhappy")
        url = image_url_for(
            _item(
                source_type="PRODUCT",
                image_ref="products/2026/xyz.jpg",
                snapshot={"image_url": "https://shop.test/xyz.jpg"},
            )
        )
        self.assertEqual(url, "https://shop.test/xyz.jpg")

    def test_presign_failure_without_fallback_returns_none(self) -> None:
        self.presign.side_effect = RuntimeError("boto is unhappy")
        self.assertIsNone(image_url_for(_item(image_ref="wardrobe/ab12.jpg")))

    def test_nothing_to_show_returns_none(self) -> None:
        self.assertIsNone(image_url_for(_item()))

    @override_settings(OUTFIT_RENDER_WARDROBE_BUCKET="")
    def test_missing_bucket_setting_is_not_a_crash(self) -> None:
        """버킷 env가 비면 빈 이름으로 서명하다 botocore가 죽는다 — 아예 부르지 않는다."""
        self.assertIsNone(image_url_for(_item(image_ref="wardrobe/ab12.jpg")))
        self.presign.assert_not_called()


class SerializerContractTests(SimpleTestCase):
    def test_card_item_exposes_image_url(self) -> None:
        fields = RecommendationCardItemSerializer().fields
        self.assertIn("image_url", fields)
        # image_ref도 남긴다 — 구버전 앱이 아직 이 값을 읽는다.
        self.assertIn("image_ref", fields)
