from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0026_user_profile_image_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="bodyphototransaction",
            name="error_code",
            field=models.CharField(
                blank=True,
                db_comment="클라이언트 분기용 실패 코드 (사진 품질 실패: photo_quality_failed)",
                default="",
                max_length=50,
                verbose_name="실패 코드",
            ),
        ),
    ]
