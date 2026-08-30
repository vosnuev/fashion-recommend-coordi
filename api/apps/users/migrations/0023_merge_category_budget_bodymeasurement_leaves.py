"""중복 생성된 예산·신체치수 merge migration의 두 leaf를 합친다."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012 - Django migration contract
        ("users", "0022_merge_category_budgets_body_measurement"),
        ("users", "0022_merge_user_budget_bodymeasurement"),
    ]

    # 두 0022가 이미 같은 선행 migration을 병합했으므로 추가 스키마 연산은 없다.
    operations = []  # noqa: RUF012
