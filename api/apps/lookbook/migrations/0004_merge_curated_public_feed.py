from django.db import migrations


class Migration(migrations.Migration):
    """관리자 룩과 사용자 공개 룩의 병렬 마이그레이션 이력을 합친다."""

    dependencies = [
        ("lookbook", "0002_lookbookpost_is_public_and_more"),
        ("lookbook", "0003_curated_look_gender"),
    ]

    operations = []
