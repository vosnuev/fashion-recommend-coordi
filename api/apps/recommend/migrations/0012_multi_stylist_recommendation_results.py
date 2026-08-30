import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


def backfill_default_results(apps, schema_editor):
    RecommendationResult = apps.get_model("recommend", "RecommendationResult")
    RecommendationResult.objects.update(
        response_mode="DEFAULT",
        persona_id="",
        persona_version=None,
        persona_explanation="",
        validated_reason_codes=[],
        strategy_snapshot={},
        persona_execution_id=None,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0009_chat_run_persona"),
        ("recommend", "0011_dailylook_execution_metadata"),
    ]

    operations = [
        migrations.AlterField(
            model_name="recommendationresult",
            name="run",
            field=models.ForeignKey(
                db_column="run_id",
                db_comment="추천을 생성한 채팅 실행 FK (chat_run.id)",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="recommendation_results",
                to="chat.chatrun",
            ),
        ),
        migrations.AddField(
            model_name="recommendationresult",
            name="persona_execution",
            field=models.OneToOneField(
                blank=True,
                db_column="persona_execution_id",
                db_comment=(
                    "스타일리스트별 실행 FK (chat_run_persona.id, 기본 응답이면 NULL)"
                ),
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="recommendation_result",
                to="chat.chatrunpersona",
            ),
        ),
        migrations.AddField(
            model_name="recommendationresult",
            name="persona_explanation",
            field=models.CharField(
                blank=True,
                db_comment="확정된 코디를 설명하는 스타일리스트 핵심 문장",
                default="",
                max_length=500,
            ),
        ),
        migrations.AddField(
            model_name="recommendationresult",
            name="persona_id",
            field=models.CharField(
                blank=True,
                db_comment=(
                    "결과를 생성한 스타일리스트 고정 ID "
                    "(minimal/experimental/practical, 기본 응답이면 빈 문자열)"
                ),
                default="",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="recommendationresult",
            name="persona_version",
            field=models.PositiveIntegerField(
                blank=True,
                db_comment="결과 생성 당시 스타일리스트 설정 버전 (기본 응답이면 NULL)",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="recommendationresult",
            name="response_mode",
            field=models.CharField(
                choices=[
                    ("DEFAULT", "기본 통합 응답"),
                    ("STYLIST", "스타일리스트별 응답"),
                ],
                db_comment="추천 결과 응답 모드 (DEFAULT/STYLIST)",
                default="DEFAULT",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="recommendationresult",
            name="strategy_snapshot",
            field=models.JSONField(
                blank=True,
                db_comment="결과 선택에 사용한 스타일리스트 추천 전략 JSON 스냅샷",
                default=dict,
            ),
        ),
        migrations.AddField(
            model_name="recommendationresult",
            name="validated_reason_codes",
            field=models.JSONField(
                blank=True,
                db_comment="Validator를 통과한 추천 근거 코드 문자열 JSON 배열",
                default=list,
            ),
        ),
        migrations.RunPython(
            backfill_default_results,
            migrations.RunPython.noop,
        ),
        migrations.AlterModelTableComment(
            name="recommendationresult",
            table_comment=(
                "채팅과 독립적으로 조회하는 기본 또는 스타일리스트별 추천 결과 "
                "(소유권·실행·전략·근거·골든셋 버전 보관)"
            ),
        ),
        migrations.AddIndex(
            model_name="recommendationresult",
            index=models.Index(
                fields=["run", "response_mode"],
                name="ix_reco_result_run_mode",
            ),
        ),
        migrations.AddConstraint(
            model_name="recommendationresult",
            constraint=models.CheckConstraint(
                condition=Q(response_mode__in=["DEFAULT", "STYLIST"]),
                name="ck_reco_result_response_mode",
            ),
        ),
        migrations.AddConstraint(
            model_name="recommendationresult",
            constraint=models.CheckConstraint(
                condition=(
                    Q(
                        response_mode="DEFAULT",
                        persona_id="",
                        persona_version__isnull=True,
                        persona_execution__isnull=True,
                    )
                    | Q(
                        response_mode="STYLIST",
                        persona_id__in=["minimal", "experimental", "practical"],
                        persona_version__gte=1,
                        persona_execution__isnull=False,
                    )
                ),
                name="ck_reco_result_persona_fields",
            ),
        ),
        migrations.AddConstraint(
            model_name="recommendationresult",
            constraint=models.UniqueConstraint(
                condition=Q(response_mode="DEFAULT"),
                fields=("run",),
                name="uq_reco_result_default_run",
            ),
        ),
        migrations.AddConstraint(
            model_name="recommendationresult",
            constraint=models.UniqueConstraint(
                condition=Q(response_mode="STYLIST"),
                fields=("run", "persona_id"),
                name="uq_reco_result_run_persona",
            ),
        ),
    ]
