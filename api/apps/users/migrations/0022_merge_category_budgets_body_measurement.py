from django.db import migrations


class Migration(migrations.Migration):
    """main에서 병렬 병합된 예산·신체측정 마이그레이션 그래프를 합친다."""

    dependencies = [
        ("users", "0018_user_category_budgets"),
        ("users", "0021_alter_bodymeasurement_leg_length_and_more"),
    ]

    operations = []
