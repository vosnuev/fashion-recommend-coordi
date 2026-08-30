"""코디 평가 → 옷장 등록 연계 필드 추가.

로그인 사용자가 원하면 평가에 올린 사진을 옷장 아이템 등록 파이프라인에도 넘긴다.
사진은 이미 S3에 있으므로 재업로드 없이 키만 재사용한다 (services/wardrobe_link.py).
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("recommend", "0002_async_analysis"),
        ("wardrobe", "0003_table_column_comments"),
    ]

    operations = [
        migrations.AddField(
            model_name="outfitanalysis",
            name="save_to_wardrobe",
            field=models.BooleanField(
                db_comment="이 사진을 옷장 아이템 등록에도 넘길지 여부 (비로그인 요청이면 항상 false)",
                default=False,
            ),
        ),
        migrations.AddField(
            model_name="outfitanalysis",
            name="wardrobe_job",
            field=models.ForeignKey(
                blank=True,
                db_comment="연계 생성한 옷장 등록 job FK (wardrobe_upload_job.id, 미요청·적재 실패 시 NULL)",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="outfit_analyses",
                to="wardrobe.wardrobeuploadjob",
            ),
        ),
    ]
