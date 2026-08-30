from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("wardrobe", "0004_item_batch")]

    operations = [
        migrations.AddField(
            model_name="wardrobeuploadjob",
            name="input_metadata",
            field=models.JSONField(
                default=dict,
                blank=True,
                db_comment="외부 수집 시 클라이언트가 제공한 옷장 부분 태그 JSON",
            ),
        ),
    ]
