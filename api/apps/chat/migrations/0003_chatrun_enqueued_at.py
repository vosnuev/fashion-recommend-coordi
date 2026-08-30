from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0002_chat_orchestrator"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatrun",
            name="enqueued_at",
            field=models.DateTimeField(
                blank=True,
                db_comment=(
                    "Redis pending 큐 적재 확인 시각 (미적재 또는 적재 확인 전이면 NULL)"
                ),
                null=True,
            ),
        ),
    ]
