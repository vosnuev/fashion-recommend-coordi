"""추천 결과의 논리 UUID 소유권을 실제 채팅 identity·session FK로 연결한다.

0005에서 만들어진 ``identity_id``와 ``session_id`` PostgreSQL 컬럼은 UUID라
그대로 사용한다. 컬럼을 삭제·재생성하면 저장된 추천 결과가 유실될 수 있으므로,
state에서는 ForeignKey로 전환하고 DB에는 FK constraint만 추가한다.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0001_initial"),
        ("recommend", "0006_recommendationresult_outfitcomposition_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        'ALTER TABLE "recommendation_result" '
                        'ADD CONSTRAINT "fk_reco_result_identity" '
                        'FOREIGN KEY ("identity_id") REFERENCES "chat_identity" ("id") '
                        "DEFERRABLE INITIALLY DEFERRED;"
                    ),
                    reverse_sql=(
                        'ALTER TABLE "recommendation_result" '
                        'DROP CONSTRAINT IF EXISTS "fk_reco_result_identity";'
                    ),
                ),
                migrations.RunSQL(
                    sql=(
                        'ALTER TABLE "recommendation_result" '
                        'ADD CONSTRAINT "fk_reco_result_session" '
                        'FOREIGN KEY ("session_id") REFERENCES "chat_session" ("id") '
                        "DEFERRABLE INITIALLY DEFERRED;"
                    ),
                    reverse_sql=(
                        'ALTER TABLE "recommendation_result" '
                        'DROP CONSTRAINT IF EXISTS "fk_reco_result_session";'
                    ),
                ),
                migrations.RunSQL(
                    sql=(
                        'COMMENT ON COLUMN "recommendation_result"."identity_id" IS '
                        "'추천 결과를 소유한 회원 또는 게스트 채팅 identity FK "
                        "(chat_identity.id)';"
                    ),
                    reverse_sql=(
                        'COMMENT ON COLUMN "recommendation_result"."identity_id" IS '
                        "'추천 결과를 소유한 회원 또는 게스트 채팅 identity UUID';"
                    ),
                ),
                migrations.RunSQL(
                    sql=(
                        'COMMENT ON COLUMN "recommendation_result"."session_id" IS '
                        "'추천이 생성된 채팅 세션 FK (chat_session.id)';"
                    ),
                    reverse_sql=(
                        'COMMENT ON COLUMN "recommendation_result"."session_id" IS '
                        "'추천이 생성된 채팅 세션 UUID';"
                    ),
                ),
            ],
            state_operations=[
                migrations.RemoveIndex(
                    model_name="recommendationresult",
                    name="ix_reco_result_identity",
                ),
                migrations.RemoveIndex(
                    model_name="recommendationresult",
                    name="ix_reco_result_session",
                ),
                migrations.RemoveField(
                    model_name="recommendationresult",
                    name="identity_id",
                ),
                migrations.RemoveField(
                    model_name="recommendationresult",
                    name="session_id",
                ),
                migrations.AddField(
                    model_name="recommendationresult",
                    name="identity",
                    field=models.ForeignKey(
                        db_column="identity_id",
                        db_comment=(
                            "추천 결과를 소유한 회원 또는 게스트 채팅 identity FK "
                            "(chat_identity.id)"
                        ),
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recommendation_results",
                        to="chat.chatidentity",
                    ),
                ),
                migrations.AddField(
                    model_name="recommendationresult",
                    name="session",
                    field=models.ForeignKey(
                        db_column="session_id",
                        db_comment="추천이 생성된 채팅 세션 FK (chat_session.id)",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recommendation_results",
                        to="chat.chatsession",
                    ),
                ),
                migrations.AddIndex(
                    model_name="recommendationresult",
                    index=models.Index(
                        fields=["identity", "-created_at"],
                        name="ix_reco_result_identity",
                    ),
                ),
                migrations.AddIndex(
                    model_name="recommendationresult",
                    index=models.Index(
                        fields=["session", "-created_at"],
                        name="ix_reco_result_session",
                    ),
                ),
            ],
        ),
    ]
