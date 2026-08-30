import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Max


def backfill_chat_runs(apps, schema_editor):
    ChatMessage = apps.get_model("chat", "ChatMessage")
    ChatRun = apps.get_model("chat", "ChatRun")
    RecommendationResult = apps.get_model("recommend", "RecommendationResult")

    for result in RecommendationResult.objects.all().iterator():
        if ChatRun.objects.filter(pk=result.run_id).exists():
            continue
        last_sequence = (
            ChatMessage.objects.filter(session_id=result.session_id).aggregate(
                value=Max("sequence")
            )["value"]
            or 0
        )
        request_message = ChatMessage.objects.create(
            session_id=result.session_id,
            sequence=last_sequence + 1,
            role="USER",
            content="",
            status="COMPLETED",
            metadata={
                "legacy_recommendation_result_id": str(result.id),
                "migration": "recommend.0008_link_chat_run",
            },
        )
        ChatRun.objects.create(
            id=result.run_id,
            session_id=result.session_id,
            request_message_id=request_message.id,
            status="SUCCEEDED",
            provider="legacy",
            model="",
            prompt_version="legacy-import",
            completed_at=result.created_at,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0002_chat_orchestrator"),
        ("recommend", "0007_link_chat_ownership"),
    ]

    operations = [
        migrations.RunPython(backfill_chat_runs, migrations.RunPython.noop),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE recommendation_result ADD CONSTRAINT "
                        "recommendation_result_run_id_fk "
                        "FOREIGN KEY (run_id) REFERENCES chat_run(id) "
                        "DEFERRABLE INITIALLY DEFERRED; "
                        "COMMENT ON COLUMN recommendation_result.run_id IS "
                        "'추천을 생성한 채팅 실행 FK (chat_run.id, 실행당 결과 최대 1개)';"
                    ),
                    reverse_sql=(
                        "ALTER TABLE recommendation_result DROP CONSTRAINT "
                        "IF EXISTS recommendation_result_run_id_fk; "
                        "COMMENT ON COLUMN recommendation_result.run_id IS "
                        "'추천을 생성한 채팅 실행 UUID (실행당 결과 최대 1개)';"
                    ),
                )
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name="recommendationresult",
                    name="run_id",
                ),
                migrations.AddField(
                    model_name="recommendationresult",
                    name="run",
                    field=models.OneToOneField(
                        db_column="run_id",
                        db_comment=(
                            "추천을 생성한 채팅 실행 FK "
                            "(chat_run.id, 실행당 결과 최대 1개)"
                        ),
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recommendation_result",
                        to="chat.chatrun",
                    ),
                ),
            ],
        ),
    ]
