from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.lookbook.models import CuratedLook, CuratedLookItem
from apps.wardrobe.taxonomy import is_valid_pair

LOOK_COLUMNS = {
    "external_id",
    "gender",
    "category",
    "title",
    "subtitle",
    "cover_image",
    "tags",
    "is_active",
}
ITEM_COLUMNS = {
    "look_external_id",
    "slot",
    "category_small",
    "name",
    "brand",
    "price",
    "product_url",
    "image_url",
    "related_keyword",
    "sort_order",
}
GENDERS = ("WOMAN", "MAN")
EXPECTED_CATEGORIES = (
    "출근",
    "데이트",
    "나들이",
    "여행",
    "미니멀",
    "캐주얼",
    "빈티지",
    "스트릿",
)
EXPECTED_CATEGORIES_BY_GENDER = {
    "WOMAN": (*EXPECTED_CATEGORIES, "하객룩"),
    "MAN": EXPECTED_CATEGORIES,
}


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise CommandError(f"CSV 파일을 찾을 수 없습니다: {path}")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _validate_columns(rows: list[dict[str, str]], required: set[str], filename: str) -> None:
    columns = set(rows[0]) if rows else set()
    missing = required - columns
    if missing:
        raise CommandError(f"{filename} 필수 컬럼 누락: {', '.join(sorted(missing))}")


class Command(BaseCommand):
    help = "운영자 룩과 네이버 원본 상품 CSV를 DB에 멱등 반영한다."

    def add_arguments(self, parser) -> None:
        parser.add_argument("directory", type=Path)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--require-50",
            action="store_true",
            help="성별·카테고리 조합마다 정확히 50개인지 검증합니다.",
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        directory: Path = options["directory"]
        looks = _rows(directory / "admin_looks.csv")
        items = _rows(directory / "admin_look_items.csv")
        _validate_columns(looks, LOOK_COLUMNS, "admin_looks.csv")
        _validate_columns(items, ITEM_COLUMNS, "admin_look_items.csv")
        invalid_genders = {
            row["gender"].strip().upper()
            for row in looks
            if row["gender"].strip().upper() not in GENDERS
        }
        if invalid_genders:
            raise CommandError("gender는 WOMAN 또는 MAN만 가능합니다.")
        duplicate_ids = [
            external_id
            for external_id, count in Counter(row["external_id"] for row in looks).items()
            if count > 1
        ]
        if duplicate_ids:
            raise CommandError("중복 external_id: " + ", ".join(sorted(duplicate_ids)))
        if options["require_50"]:
            counts = Counter(
                (row["gender"].strip().upper(), row["category"].strip())
                for row in looks
            )
            invalid_counts = [
                f"{gender}/{category}={counts[(gender, category)]}개"
                for gender in GENDERS
                for category in EXPECTED_CATEGORIES_BY_GENDER[gender]
                if counts[(gender, category)] != 50
            ]
            if invalid_counts:
                raise CommandError(
                    "각 성별·카테고리는 정확히 50개여야 합니다: "
                    + ", ".join(invalid_counts)
                )
        known = {row["external_id"] for row in looks}
        unknown = {row["look_external_id"] for row in items} - known
        if unknown:
            raise CommandError("존재하지 않는 룩 ID: " + ", ".join(sorted(unknown)))
        missing_categories = [
            f"{row['look_external_id']}:{row['slot']}"
            for row in items
            if not row["category_small"].strip()
        ]
        if missing_categories:
            raise CommandError(
                "관리자 검수 소분류(category_small) 누락: "
                + ", ".join(missing_categories)
            )
        invalid_categories = [
            f"{row['look_external_id']}:{row['slot']}>{row['category_small']}"
            for row in items
            if not is_valid_pair(row["slot"].strip(), row["category_small"].strip())
        ]
        if invalid_categories:
            raise CommandError(
                "대분류와 맞지 않는 관리자 검수 소분류: "
                + ", ".join(invalid_categories)
            )

        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS(f"검증 완료: 룩 {len(looks)}개, 아이템 {len(items)}개"))
            transaction.set_rollback(True)
            return

        look_map = {}
        for row in looks:
            look, _ = CuratedLook.objects.update_or_create(
                external_id=row["external_id"],
                defaults={
                    "gender": row["gender"].strip().upper(),
                    "category": row["category"],
                    "title": row["title"],
                    "subtitle": row["subtitle"],
                    "cover_image_url": row["cover_image"],
                    "tags": [tag.strip() for tag in row["tags"].split("|") if tag.strip()],
                    "is_active": row["is_active"].strip().lower() in {"1", "true", "yes"},
                },
            )
            look_map[look.external_id] = look

        for row in items:
            CuratedLookItem.objects.update_or_create(
                look=look_map[row["look_external_id"]],
                slot=row["slot"],
                defaults={
                    "category_small": row["category_small"].strip(),
                    "name": row["name"],
                    "brand": row["brand"],
                    "price": int(row["price"]) if row["price"] else None,
                    "product_url": row["product_url"],
                    "image_url": row["image_url"],
                    "related_keyword": row["related_keyword"],
                    "sort_order": int(row["sort_order"]),
                },
            )
        self.stdout.write(self.style.SUCCESS(f"반영 완료: 룩 {len(looks)}개, 아이템 {len(items)}개"))
