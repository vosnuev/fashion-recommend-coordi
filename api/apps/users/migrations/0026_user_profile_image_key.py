"""사용자가 직접 올린 프로필 사진의 S3 key 를 담을 자리.

기존 profile_image(URLField)는 소셜 provider 가 준 URL 전용으로 남긴다 —
직접 올린 사진을 지웠을 때 되돌아갈 자리가 필요해서다.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0025_merge_duplicate_0024_leaves"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="profile_image_key",
            field=models.CharField(
                blank=True,
                db_comment="사용자가 올린 프로필 사진의 S3 key (있으면 profile_image URL 보다 우선)",
                max_length=255,
                verbose_name="프로필 이미지 S3 키",
            ),
        ),
    ]
