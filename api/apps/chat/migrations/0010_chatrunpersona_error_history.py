from django.db import migrations, models

import apps.chat.models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0009_chat_run_persona"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatrunpersona",
            name="error_history",
            field=models.JSONField(
                blank=True,
                db_comment=(
                    "스타일리스트 재시도 전 오류 이력 JSON 배열 (시각·코드·메시지)"
                ),
                default=list,
                validators=[apps.chat.models.validate_persona_error_history],
            ),
        ),
    ]
