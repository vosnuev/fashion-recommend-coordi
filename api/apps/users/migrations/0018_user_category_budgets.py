"""단일 월 예산을 대분류별 상품 1개 예산으로 교체한다."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("users", "0017_merge_email_auth_body_ratio")]

    operations = [
        migrations.RenameField(
            model_name="user",
            old_name="monthly_budget",
            new_name="legacy_monthly_budget",
        ),
        migrations.AlterField(
            model_name="user",
            name="legacy_monthly_budget",
            field=models.PositiveIntegerField(
                blank=True,
                db_comment="이전 단일 월 의류 구매 예산(원), 신규 추천에서는 사용하지 않음",
                editable=False,
                null=True,
                verbose_name="이전 월 의류 구매 예산",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="category_budgets",
            field=models.JSONField(
                blank=True,
                default=dict,
                db_comment="대분류별 상품 1개 최대 가격(원) JSON, 미설정 카테고리는 키 없음",
                verbose_name="카테고리별 상품 예산",
            ),
        ),
    ]
