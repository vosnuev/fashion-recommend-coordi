from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("recommend", "0018_merge_wishlist_reference_match"),
    ]

    operations = [
        migrations.AddField(
            model_name="outfitcomposition",
            name="rationale",
            field=models.TextField(
                blank=True,
                db_comment="사용자에게 보여줄 코디 전체 추천 이유 (없으면 빈 문자열)",
                default="",
            ),
        ),
        migrations.AddField(
            model_name="outfitcompositionitem",
            name="note",
            field=models.TextField(
                blank=True,
                db_comment="사용자에게 보여줄 개별 아이템 선택 이유 (없으면 빈 문자열)",
                default="",
            ),
        ),
    ]
