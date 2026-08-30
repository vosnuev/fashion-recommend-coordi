from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0003_chatrun_enqueued_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatattachment",
            name="analysis_result",
            field=models.JSONField(
                blank=True,
                db_comment="사진에서 추출한 무드·스타일·색상·핏 분석 결과 JSON",
                default=dict,
            ),
        ),
        migrations.AddField(
            model_name="chatattachment",
            name="mood_decided_at",
            field=models.DateTimeField(
                blank=True,
                db_comment="사진 무드 승인 또는 거절 확정 시각 (미결정이면 NULL)",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="chatattachment",
            name="mood_decision",
            field=models.CharField(
                choices=[
                    ("UNDECIDED", "미결정"),
                    ("APPROVED", "승인"),
                    ("REJECTED", "거절"),
                ],
                db_comment="사진 무드 반영 결정 (UNDECIDED/APPROVED/REJECTED)",
                default="UNDECIDED",
                max_length=12,
            ),
        ),
    ]
