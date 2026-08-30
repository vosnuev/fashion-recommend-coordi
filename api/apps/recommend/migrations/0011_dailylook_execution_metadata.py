from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("recommend", "0010_outfitrenderjob")]

    operations = [
        migrations.AddField(
            model_name="dailylook",
            name="attempts",
            field=models.PositiveSmallIntegerField(
                db_comment="오늘의 룩 추천 워커 처리 시도 횟수 (렌더 보정 작업 제외)",
                default=0,
            ),
        ),
        migrations.AddField(
            model_name="dailylook",
            name="enqueued_at",
            field=models.DateTimeField(
                blank=True,
                db_comment="오늘의 룩 Redis 큐 적재 확인 시각 (미적재이면 NULL)",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="dailylook",
            name="finished_at",
            field=models.DateTimeField(
                blank=True,
                db_comment="오늘의 룩 추천 성공·후보 없음·최종 실패 시각",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="dailylook",
            name="started_at",
            field=models.DateTimeField(
                blank=True,
                db_comment="오늘의 룩 추천 워커 마지막 처리 시작 시각",
                null=True,
            ),
        ),
    ]
