from __future__ import annotations

import csv
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.lookbook.management.commands.import_admin_lookbook import (
    ITEM_COLUMNS,
    LOOK_COLUMNS,
)
from apps.lookbook.models import CuratedLookItem


class ImportAdminLookbookTests(TestCase):
    def _write_csvs(self, directory: Path, category_small: str) -> None:
        look = {
            "external_id": "woman-casual-001",
            "gender": "WOMAN",
            "category": "캐주얼",
            "title": "관리자 검수 룩",
            "subtitle": "테스트",
            "cover_image": "images/woman-casual-001.png",
            "tags": "캐주얼",
            "is_active": "true",
        }
        item = {
            "look_external_id": "woman-casual-001",
            "slot": "아우터",
            "category_small": category_small,
            "name": "브라운 니트 가디건",
            "brand": "테스트 브랜드",
            "price": "30000",
            "product_url": "https://example.com/original",
            "image_url": "https://example.com/original.jpg",
            "related_keyword": "브라운 니트 가디건",
            "sort_order": "0",
        }
        for filename, columns, row in (
            ("admin_looks.csv", LOOK_COLUMNS, look),
            ("admin_look_items.csv", ITEM_COLUMNS, item),
        ):
            with (directory / filename).open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=sorted(columns))
                writer.writeheader()
                writer.writerow(row)

    def test_import_saves_manually_reviewed_small_category(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            self._write_csvs(path, "가디건")

            call_command("import_admin_lookbook", path)

        self.assertEqual(CuratedLookItem.objects.get().category_small, "가디건")

    def test_import_rejects_large_and_small_category_mismatch(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            self._write_csvs(path, "티셔츠")

            with self.assertRaisesMessage(CommandError, "대분류와 맞지 않는"):
                call_command("import_admin_lookbook", path)

    def test_import_rejects_unreviewed_small_category(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            self._write_csvs(path, "")

            with self.assertRaisesMessage(CommandError, "소분류(category_small) 누락"):
                call_command("import_admin_lookbook", path)
