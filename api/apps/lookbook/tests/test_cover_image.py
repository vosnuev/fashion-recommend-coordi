"""둘러보기 커버 축소본 — 목록이 원본(장당 약 2MB)을 받지 않게 하는 장치의 회귀 테스트."""

from __future__ import annotations

import shutil
import tempfile
from io import BytesIO
from pathlib import Path

from django.test import TestCase, override_settings
from PIL import Image

from apps.lookbook.models import CuratedLook
from apps.lookbook.services import cover_image


class RequestedWidthTests(TestCase):
    """화이트리스트 밖의 값은 전부 원본(None)으로 떨어져야 한다 — 캐시 폭탄 방지."""

    def test_allowed_widths_pass_through(self) -> None:
        for raw, expected in (("400", 400), ("800", 800)):
            with self.subTest(raw=raw):
                self.assertEqual(cover_image.requested_width(raw), expected)

    def test_everything_else_falls_back_to_original(self) -> None:
        for raw in (None, "", "1080", "abc", "-1", "400.5", "0"):
            with self.subTest(raw=raw):
                self.assertIsNone(cover_image.requested_width(raw))


class ThumbnailTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.source = self.tmp / "cover.png"
        Image.new("RGB", (1080, 1350), (120, 90, 60)).save(self.source)

    def test_thumbnail_is_jpeg_at_requested_width(self) -> None:
        data = cover_image.build_thumbnail(self.source, 400)

        with Image.open(BytesIO(data)) as out:
            self.assertEqual(out.format, "JPEG")
            self.assertEqual(out.width, 400)
            # 세로 비율이 유지돼야 카드에서 잘리지 않는다.
            self.assertEqual(out.height, 500)

    def test_thumbnail_is_much_smaller_than_source(self) -> None:
        data = cover_image.build_thumbnail(self.source, 400)

        self.assertLess(len(data), self.source.stat().st_size / 5)

    def test_alpha_is_flattened_onto_white(self) -> None:
        """JPEG 는 알파를 담지 못한다 — 변환에서 터지지 않고 흰 바탕으로 깔려야 한다."""
        rgba = self.tmp / "alpha.png"
        Image.new("RGBA", (800, 1000), (200, 40, 40, 128)).save(rgba)

        with Image.open(BytesIO(cover_image.build_thumbnail(rgba, 400))) as out:
            self.assertEqual(out.mode, "RGB")

    def test_cache_is_reused_and_refreshed_when_source_changes(self) -> None:
        cache = self.tmp / "thumbs"
        first = cover_image.cached_thumbnail(self.source, 400, cache, "cover")
        cached_file = cache / "cover-400.jpg"
        self.assertTrue(cached_file.is_file())

        # 원본이 그대로면 같은 바이트를 그대로 돌려준다.
        self.assertEqual(cover_image.cached_thumbnail(self.source, 400, cache, "cover"), first)

        # 원본이 새로 적재되면(운영 CSV 재적재) 낡은 캐시를 붙잡고 있으면 안 된다.
        Image.new("RGB", (1080, 1350), (10, 10, 10)).save(self.source)
        refreshed = cover_image.cached_thumbnail(self.source, 400, cache, "cover")
        self.assertNotEqual(refreshed, first)

    def test_serves_even_when_cache_dir_is_not_writable(self) -> None:
        """읽기 전용 배포에서도 응답은 나가야 한다 — 캐시는 있으면 좋은 것이지 필수가 아니다."""
        blocked = self.tmp / "readonly"
        blocked.mkdir()
        blocked.chmod(0o500)
        self.addCleanup(blocked.chmod, 0o700)

        data = cover_image.cached_thumbnail(self.source, 400, blocked / "nested", "cover")

        self.assertTrue(data)


class CoverViewTests(TestCase):
    """?w= 유무로 응답이 갈리는지 — 기존 호출(파라미터 없음)이 안 깨지는 게 핵심이다."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        images = self.root / "data" / "lookbook" / "images"
        images.mkdir(parents=True)
        Image.new("RGB", (1080, 1350), (120, 90, 60)).save(images / "man-casual-001.png")
        CuratedLook.objects.create(
            external_id="man-casual-001",
            gender=CuratedLook.Gender.MAN,
            category="캐주얼",
            title="남성 캐주얼 룩",
            subtitle="테스트",
            cover_image_url="images/man-casual-001.png",
            tags=["캐주얼"],
        )

    def _get(self, query: str = "") -> object:
        # BASE_DIR 의 부모 아래 data/lookbook 을 보므로 한 칸 아래를 가리킨다.
        with override_settings(BASE_DIR=str(self.root / "api")):
            return self.client.get(f"/api/v1/lookbooks/discover/man-casual-001/cover/{query}")

    def test_without_width_serves_original_png(self) -> None:
        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")

    def test_with_width_serves_jpeg(self) -> None:
        response = self._get("?w=400")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertIn("max-age", response["Cache-Control"])
        with Image.open(BytesIO(response.content)) as out:
            self.assertEqual(out.width, 400)

    def test_unknown_width_falls_back_to_original(self) -> None:
        response = self._get("?w=9999")

        self.assertEqual(response["Content-Type"], "image/png")
