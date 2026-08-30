"""익명 접수 건의 소유권 이전(claim) 지원 필드.

평가는 다시 하지 않고 주인만 바꾸므로, 개인화 없이 나온 결과라는 사실을
accepted_anonymously로 남긴다. claimed_at은 감사 기록이다.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("recommend", "0003_save_to_wardrobe"),
    ]

    operations = [
        migrations.AddField(
            model_name="outfitanalysis",
            name="accepted_anonymously",
            field=models.BooleanField(
                db_comment="접수 시점에 비로그인이었는지 여부 (소유권 이전 후에도 유지 — 개인화 없이 평가된 기록임을 구분한다)",
                default=False,
            ),
        ),
        migrations.AddField(
            model_name="outfitanalysis",
            name="claimed_at",
            field=models.DateTimeField(
                blank=True,
                db_comment="익명 접수 건의 소유권이 사용자에게 이전된 시각 (감사용)",
                null=True,
            ),
        ),
    ]
