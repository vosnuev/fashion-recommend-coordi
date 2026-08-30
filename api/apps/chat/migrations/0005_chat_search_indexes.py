import django.contrib.postgres.indexes
import django.contrib.postgres.operations
import django.db.models.functions.text
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0004_chatattachment_mood_analysis"),
    ]

    operations = [
        django.contrib.postgres.operations.TrigramExtension(),
        migrations.AddIndex(
            model_name="chatsession",
            index=django.contrib.postgres.indexes.GinIndex(
                django.contrib.postgres.indexes.OpClass(
                    django.db.models.functions.text.Upper("title"),
                    name="gin_trgm_ops",
                ),
                name="ix_chat_session_title_trgm",
            ),
        ),
        migrations.AddIndex(
            model_name="chatmessage",
            index=django.contrib.postgres.indexes.GinIndex(
                django.contrib.postgres.indexes.OpClass(
                    django.db.models.functions.text.Upper("content"),
                    name="gin_trgm_ops",
                ),
                name="ix_chat_message_content_trgm",
            ),
        ),
    ]
