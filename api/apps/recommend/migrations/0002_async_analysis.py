"""코디 평가 비동기화 — 상태 확장과 워커용 컬럼 추가.

동기 시절의 PENDING은 "요청을 받아 지금 평가 중"이라는 한 가지 뜻이었지만,
큐를 두면 "큐에서 대기중(QUEUED)"과 "워커가 처리중(PROCESSING)"이 갈린다.
기존 PENDING 행은 QUEUED로 옮긴다 — 워커가 아직 집지 않은 상태와 같다.

설계: Confluence > 설계 > "코디 평가 비동기화 설계(접수·워커 분리 · 익명 폴링)"
"""

from django.db import migrations, models


def pending_to_queued(apps, schema_editor):
    apps.get_model("recommend", "OutfitAnalysis").objects.filter(
        status="PENDING"
    ).update(status="QUEUED")


def queued_to_pending(apps, schema_editor):
    apps.get_model("recommend", "OutfitAnalysis").objects.filter(
        status__in=("QUEUED", "PROCESSING")
    ).update(status="PENDING")


class Migration(migrations.Migration):

    dependencies = [
        ("recommend", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="outfitanalysis",
            name="attempts",
            field=models.PositiveSmallIntegerField(
                db_comment="워커 처리 시도 횟수 (재시도 포함)", default=0
            ),
        ),
        migrations.AddField(
            model_name="outfitanalysis",
            name="llm_image_bytes",
            field=models.PositiveIntegerField(
                blank=True,
                db_comment="LLM에 실제 전송한 축소본 크기 (bytes, image_bytes는 원본 크기)",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="outfitanalysis",
            name="started_at",
            field=models.DateTimeField(
                blank=True,
                db_comment="워커가 처리를 시작한 시각 (큐 대기시간 측정·좀비 정리 기준)",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="outfitanalysis",
            name="status",
            field=models.CharField(
                choices=[
                    ("QUEUED", "대기중"),
                    ("PROCESSING", "평가 진행중"),
                    ("SUCCEEDED", "평가 완료"),
                    ("FAILED", "평가 실패"),
                ],
                db_comment="평가 상태 (QUEUED/PROCESSING/SUCCEEDED/FAILED)",
                default="QUEUED",
                max_length=16,
            ),
        ),
        migrations.RunPython(pending_to_queued, queued_to_pending),
    ]
