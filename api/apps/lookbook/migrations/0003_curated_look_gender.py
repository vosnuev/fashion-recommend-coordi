from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("lookbook", "0002_curated_look")]

    operations = [
        migrations.AddField(
            model_name="curatedlook",
            name="gender",
            field=models.CharField(
                choices=[("WOMAN", "여성"), ("MAN", "남성")],
                db_comment="룩 노출 성별 구분 (WOMAN/MAN)",
                default="WOMAN",
                max_length=10,
            ),
        ),
    ]
