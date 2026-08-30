from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0010_chatrunpersona_error_history"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatrunpersona",
            name="alternative_count",
            field=models.PositiveSmallIntegerField(
                db_comment="해당 스타일리스트의 다른 추천 요청 횟수",
                default=0,
            ),
        ),
        migrations.AddField(
            model_name="chatrunpersona",
            name="alternative_error_code",
            field=models.CharField(
                blank=True,
                db_comment=(
                    "마지막 다른 추천 실패 오류 코드 (성공 또는 미요청이면 빈 문자열)"
                ),
                default="",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="chatrunpersona",
            name="alternative_error_message",
            field=models.CharField(
                blank=True,
                db_comment=(
                    "마지막 다른 추천 실패 안내 (성공 또는 미요청이면 빈 문자열)"
                ),
                default="",
                max_length=500,
            ),
        ),
        migrations.AddField(
            model_name="chatrunpersona",
            name="alternative_status",
            field=models.CharField(
                choices=[
                    ("IDLE", "요청 없음"),
                    ("PENDING", "다른 추천 대기"),
                    ("RUNNING", "다른 추천 처리 중"),
                    ("SUCCEEDED", "다른 추천 성공"),
                    ("FAILED", "다른 추천 실패"),
                ],
                db_comment=(
                    "다른 추천 요청 상태 (IDLE/PENDING/RUNNING/SUCCEEDED/FAILED)"
                ),
                default="IDLE",
                max_length=16,
            ),
        ),
        migrations.AddConstraint(
            model_name="chatrunpersona",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "alternative_status__in",
                        ["IDLE", "PENDING", "RUNNING", "SUCCEEDED", "FAILED"],
                    )
                ),
                name="ck_chat_run_persona_alt_status",
            ),
        ),
    ]
