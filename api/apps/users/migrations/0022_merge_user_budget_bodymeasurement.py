"""카테고리 예산과 신체치수 migration branch를 합친다."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012 - Django migration contract
        ("users", "0018_user_category_budgets"),
        ("users", "0021_alter_bodymeasurement_leg_length_and_more"),
    ]

    operations = []  # noqa: RUF012 - 두 branch의 스키마 연산은 서로 독립적이다.
