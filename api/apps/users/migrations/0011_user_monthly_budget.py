"""사용자의 선택형 월 의류 구매 예산 필드를 추가."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0010_system_table_comments"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="monthly_budget",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name="월 의류 구매 예산",
            ),
        ),
    ]
