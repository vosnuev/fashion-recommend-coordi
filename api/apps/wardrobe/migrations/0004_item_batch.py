import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("wardrobe", "0003_table_column_comments"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="WardrobeItemBatch",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False,
                                        serialize=False, db_comment="배치 UUID (외부 노출 식별자)")),
                ("status", models.CharField(max_length=16, default="PENDING",
                                            choices=[("PENDING", "대기"), ("PROCESSING", "처리중"),
                                                     ("DONE", "완료"), ("PARTIAL", "일부실패"),
                                                     ("FAILED", "실패")],
                                            db_comment="배치 상태 (PENDING/PROCESSING/DONE/PARTIAL/FAILED)")),
                ("total_count", models.PositiveSmallIntegerField(default=0, db_comment="접수된 이미지 장수")),
                ("done_count", models.PositiveSmallIntegerField(default=0, db_comment="태깅 성공 job 수")),
                ("failed_count", models.PositiveSmallIntegerField(default=0, db_comment="태깅 실패 job 수")),
                ("source", models.CharField(max_length=20, default="onboarding",
                                            db_comment="등록 경로 (onboarding/manual 등)")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_comment="배치 접수 시각")),
                ("finished_at", models.DateTimeField(null=True, blank=True, db_comment="모든 job 종료 시각")),
                ("user", models.ForeignKey(to=settings.AUTH_USER_MODEL,
                                            on_delete=django.db.models.deletion.CASCADE,
                                            related_name="wardrobe_batches",
                                            db_comment="등록 사용자 FK (users.id)")),
            ],
            options={
                "db_table": "wardrobe_item_batch",
                "db_table_comment": "옷장 아이템 일괄 등록 요청과 처리 진행률",
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["user", "status"], name="wardrobe_b_user_status_idx")],
            },
        ),
        migrations.AddField(
            model_name="wardrobeuploadjob", name="batch",
            field=models.ForeignKey(to="wardrobe.wardrobeitembatch", null=True, blank=True,
                                    on_delete=django.db.models.deletion.CASCADE, related_name="jobs",
                                    db_comment="일괄 등록 배치 FK (wardrobe_item_batch.id, 단건 업로드는 NULL)"),
        ),
        migrations.AddField(
            model_name="wardrobeuploadjob", name="pipeline",
            field=models.CharField(max_length=20, default="gemini-edit",
                                   db_comment="처리 파이프라인 식별자 (gemini-edit/qwen-tag)"),
        ),
        migrations.AddField(
            model_name="wardrobeuploadjob", name="original_file_name",
            field=models.CharField(max_length=255, default="", blank=True,
                                   db_comment="업로드 원본 파일명"),
        ),
    ]
