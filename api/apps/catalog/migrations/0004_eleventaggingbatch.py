"""11번가 OpenAI Batch API 태깅 작업 추적 테이블."""

from django.db import migrations, models
from django.db.models.functions import Now


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0003_eleven_models"),
    ]

    operations = [
        migrations.CreateModel(
            name="ElevenTaggingBatch",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("batch_id", models.CharField(max_length=100, unique=True)),
                (
                    "status",
                    models.CharField(
                        db_default="submitted",
                        default="submitted",
                        max_length=30,
                    ),
                ),
                (
                    "model",
                    models.CharField(blank=True, max_length=60, null=True),
                ),
                (
                    "request_count",
                    models.IntegerField(db_default=0, default=0),
                ),
                (
                    "include_image",
                    models.BooleanField(db_default=False, default=False),
                ),
                (
                    "input_file_id",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                (
                    "output_file_id",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                (
                    "error_file_id",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                ("error", models.TextField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(db_default=Now())),
                ("updated_at", models.DateTimeField(db_default=Now())),
            ],
            options={
                "verbose_name": "11번가 태깅 배치",
                "verbose_name_plural": "11번가 태깅 배치",
                "db_table": "eleven_tagging_batch",
            },
        ),
    ]
