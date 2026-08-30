"""monthly_budget에 db_comment를 채운다.

0011에서 필드를 추가할 때 db_comment가 빠져 컬럼 주석만 비어 있었다
(CLAUDE.md 5장: 테이블·컬럼 주석은 모델이 소유한다). 컬럼 주석만 바꾸는
AlterField라 데이터 변경도, 테이블 재작성도 없다.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0012_bodyphototransaction_error_message"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="monthly_budget",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                db_comment="월 의류 구매 예산(원). 1만원 단위, 미설정이면 NULL",
                verbose_name="월 의류 구매 예산",
            ),
        ),
    ]
