"""개인 옷장 해시태그 보기 설정과 즐겨찾기 migration leaf를 병합한다."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012 - Django migration contract
        ("wardrobe", "0011_wardrobeitem_is_favorite"),
        ("wardrobe", "0013_wardrobe_view_preference"),
    ]

    operations = []  # noqa: RUF012
