import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0011_chatrunpersona_alternative_state"),
        ("recommend", "0012_multi_stylist_recommendation_results"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="recommendationresult",
            name="uq_reco_result_run_persona",
        ),
        migrations.RunSQL(
            sql=(
                'ALTER TABLE "recommendation_result" DROP CONSTRAINT IF EXISTS '
                '"recommendation_result_persona_execution_id_key";'
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterField(
            model_name="recommendationresult",
            name="persona_execution",
            field=models.ForeignKey(
                blank=True,
                db_column="persona_execution_id",
                db_comment=(
                    "스타일리스트별 실행 FK (chat_run_persona.id, 기본 응답이면 NULL, 재추천 이력 허용)"
                ),
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="recommendation_results",
                to="chat.chatrunpersona",
            ),
        ),
        migrations.AddField(
            model_name="recommendationresult",
            name="generation",
            field=models.PositiveSmallIntegerField(
                db_comment=(
                    "동일 run·스타일리스트 안의 추천 결과 세대 (최초 1, 다른 추천마다 증가)"
                ),
                default=1,
            ),
        ),
        migrations.AddField(
            model_name="recommendationresult",
            name="is_current",
            field=models.BooleanField(
                db_comment="동일 run·스타일리스트에서 현재 노출할 최신 결과 여부",
                default=True,
            ),
        ),
        migrations.AddField(
            model_name="recommendationresult",
            name="replaces",
            field=models.ForeignKey(
                blank=True,
                db_comment="다른 추천이 교체한 직전 추천 결과 FK (최초 추천이면 NULL)",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="replaced_by_results",
                to="recommend.recommendationresult",
            ),
        ),
        migrations.AddField(
            model_name="recommendationresult",
            name="result_type",
            field=models.CharField(
                choices=[("INITIAL", "최초 추천"), ("ALTERNATIVE", "다른 추천")],
                db_comment="추천 결과 생성 목적 (INITIAL/ALTERNATIVE)",
                default="INITIAL",
                max_length=16,
            ),
        ),
        migrations.AddConstraint(
            model_name="recommendationresult",
            constraint=models.UniqueConstraint(
                condition=models.Q(("response_mode", "STYLIST")),
                fields=("run", "persona_id", "generation"),
                name="uq_reco_result_run_persona_gen",
            ),
        ),
        migrations.AddConstraint(
            model_name="recommendationresult",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_current", True), ("response_mode", "STYLIST")),
                fields=("run", "persona_id"),
                name="uq_reco_result_current_persona",
            ),
        ),
        migrations.AddConstraint(
            model_name="recommendationresult",
            constraint=models.CheckConstraint(
                condition=models.Q(("generation__gte", 1)),
                name="ck_reco_result_generation",
            ),
        ),
        migrations.AddConstraint(
            model_name="recommendationresult",
            constraint=models.CheckConstraint(
                condition=models.Q(("result_type__in", ["INITIAL", "ALTERNATIVE"])),
                name="ck_reco_result_type",
            ),
        ),
    ]
