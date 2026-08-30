from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """오늘의 룩 '저장' — 골든 코디를 룩북에 담는 경로.

    골든 코디는 사용자 옷장의 옷도, 사용자가 올린 사진도 아니다. 이미지는
    골든셋 버킷에 이미 있는 것을 가리키기만 하고(image_s3_bucket), 구성
    아이템은 옷장에 넣지 않고 snapshot 으로만 남긴다(wardrobe_item NULL 허용).
    """

    dependencies = [
        ("lookbook", "0004_merge_curated_public_feed"),
    ]

    operations = [
        migrations.AddField(
            model_name="lookbookpost",
            name="image_s3_bucket",
            field=models.CharField(
                blank=True,
                db_comment=(
                    "대표 이미지가 있는 S3 버킷. 빈 값이면 룩북 버킷"
                    "(LOOKBOOK_S3_BUCKET). 오늘의 룩에서 담은 골든 코디는 "
                    "골든셋 버킷을 그대로 가리킨다"
                ),
                default="",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="lookbookpost",
            name="golden_id",
            field=models.CharField(
                blank=True,
                db_comment=(
                    "오늘의 룩에서 담은 골든 코디 id (source_type=GOLDEN_LOOK 일 때만). "
                    "사용자당 한 번만 담기도록 유니크 제약의 근거가 된다"
                ),
                default="",
                max_length=100,
            ),
        ),
        migrations.AlterField(
            model_name="lookbookpost",
            name="source_type",
            field=models.CharField(
                choices=[
                    ("PHOTO_UPLOAD", "룩 사진 업로드"),
                    ("WARDROBE_SELECTED", "옷장 직접 선택"),
                    ("GOLDEN_LOOK", "오늘의 룩 저장"),
                ],
                db_comment="룩북 등록 경로 (PHOTO_UPLOAD/WARDROBE_SELECTED/GOLDEN_LOOK)",
                max_length=24,
            ),
        ),
        migrations.AddConstraint(
            model_name="lookbookpost",
            constraint=models.UniqueConstraint(
                condition=models.Q(("golden_id", ""), _negated=True),
                fields=("user", "golden_id"),
                name="uq_lookbook_user_golden",
            ),
        ),
        migrations.AlterField(
            model_name="lookbookwardrobeitem",
            name="wardrobe_item",
            field=models.ForeignKey(
                blank=True,
                db_comment=(
                    "연결 대상 옷장 아이템 FK (wardrobe_item.id). 골든 코디 구성 "
                    "아이템은 사용자 옷장의 옷이 아니라 NULL이고 snapshot만 남는다"
                ),
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="lookbook_links",
                to="wardrobe.wardrobeitem",
            ),
        ),
        migrations.AlterField(
            model_name="lookbookwardrobeitem",
            name="link_type",
            field=models.CharField(
                choices=[
                    ("SELECTED", "사용자 직접 선택"),
                    ("EXTRACTED", "룩 사진에서 추출"),
                    ("GOLDEN", "골든 코디 구성"),
                ],
                db_comment=(
                    "아이템이 붙은 경로 (SELECTED: 직접 선택 / EXTRACTED: 사진 추출 / "
                    "GOLDEN: 골든 코디 구성)"
                ),
                default="SELECTED",
                max_length=16,
            ),
        ),
    ]
