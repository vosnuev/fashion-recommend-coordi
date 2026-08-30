import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("wardrobe", "0007_merge_shared_wardrobe_and_closet"),
    ]

    operations = [
        migrations.CreateModel(
            name="SharedWardrobeCategory",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        db_comment="공유 옷장 사용자 정의 카테고리 UUID",
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        db_comment="사용자 정의 카테고리명 (공유방 안에서 중복 불가)",
                        max_length=30,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, db_comment="카테고리 생성 시각"),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        db_comment="카테고리 생성 사용자 FK (탈퇴 시 NULL)",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_shared_wardrobe_categories",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "room",
                    models.ForeignKey(
                        db_comment="카테고리가 속한 공유방 FK",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="categories",
                        to="wardrobe.sharedwardroberoom",
                    ),
                ),
            ],
            options={
                "db_table": "shared_wardrobe_category",
                "db_table_comment": "공유 옷장의 사용자 정의 필터 카테고리",
                "ordering": ["created_at"],
            },
        ),
        migrations.CreateModel(
            name="SharedWardrobeItemCategory",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        db_comment="공유 아이템 카테고리 연결 UUID",
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, db_comment="아이템 카테고리 연결 시각"),
                ),
                (
                    "category",
                    models.ForeignKey(
                        db_comment="공유 아이템에 지정한 사용자 정의 카테고리 FK",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="item_links",
                        to="wardrobe.sharedwardrobecategory",
                    ),
                ),
                (
                    "shared_item",
                    models.ForeignKey(
                        db_comment="분류할 공유 아이템 FK",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="category_links",
                        to="wardrobe.sharedwardrobeitem",
                    ),
                ),
            ],
            options={
                "db_table": "shared_wardrobe_item_category",
                "db_table_comment": "공유 옷장 아이템과 사용자 정의 카테고리 연결",
            },
        ),
        migrations.AddConstraint(
            model_name="sharedwardrobecategory",
            constraint=models.UniqueConstraint(
                fields=("room", "name"),
                name="uq_shared_wardrobe_category_room_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="sharedwardrobeitemcategory",
            constraint=models.UniqueConstraint(
                fields=("shared_item", "category"),
                name="uq_shared_item_category_pair",
            ),
        ),
        migrations.AddField(
            model_name="sharedwardrobeitem",
            name="categories",
            field=models.ManyToManyField(
                related_name="shared_items",
                through="wardrobe.SharedWardrobeItemCategory",
                to="wardrobe.sharedwardrobecategory",
            ),
        ),
    ]
