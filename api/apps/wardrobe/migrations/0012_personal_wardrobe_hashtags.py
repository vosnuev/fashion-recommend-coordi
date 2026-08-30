import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def migrate_categories_to_hashtags(apps, schema_editor):
    Hashtag = apps.get_model("wardrobe", "WardrobeHashtag")
    Link = apps.get_model("wardrobe", "WardrobeItemHashtag")

    # 개인 옷장 밖의 임시 아이템에는 사용자 정리 해시태그를 유지하지 않는다.
    Link.objects.filter(wardrobe_item__added_to_closet_at__isnull=True).delete()

    user_ids = list(Hashtag.objects.values_list("user_id", flat=True).distinct())
    for user_id in user_ids:
        canonical_by_name = {}
        hashtags = list(
            Hashtag.objects.filter(user_id=user_id).order_by("created_at", "id")
        )
        for hashtag in hashtags:
            display_name = " ".join(hashtag.name.strip().split())
            if display_name.startswith("#"):
                display_name = display_name[1:].lstrip()
            normalized_name = display_name.casefold()
            if not display_name:
                hashtag.delete()
                continue

            canonical = canonical_by_name.get(normalized_name)
            if canonical is None:
                hashtag.name = display_name
                hashtag.normalized_name = normalized_name
                hashtag.save(update_fields=["name", "normalized_name"])
                canonical_by_name[normalized_name] = hashtag
                continue

            item_ids = Link.objects.filter(hashtag=hashtag).values_list(
                "wardrobe_item_id",
                flat=True,
            )
            Link.objects.bulk_create(
                [
                    Link(wardrobe_item_id=item_id, hashtag=canonical)
                    for item_id in item_ids
                ],
                ignore_conflicts=True,
            )
            hashtag.delete()

        Hashtag.objects.filter(user_id=user_id, item_links__isnull=True).delete()
        remaining = list(
            Hashtag.objects.filter(user_id=user_id).order_by(
                "position",
                "created_at",
                "id",
            )
        )
        for position, hashtag in enumerate(remaining):
            hashtag.position = position
        if remaining:
            Hashtag.objects.bulk_update(remaining, ["position"])


class Migration(migrations.Migration):
    dependencies = [("wardrobe", "0011_personal_wardrobe_categories")]

    operations = [
        migrations.RenameModel(
            old_name="WardrobeCategory",
            new_name="WardrobeHashtag",
        ),
        migrations.RenameModel(
            old_name="WardrobeItemCategory",
            new_name="WardrobeItemHashtag",
        ),
        migrations.RenameField(
            model_name="wardrobeitem",
            old_name="custom_categories",
            new_name="wardrobe_hashtags",
        ),
        migrations.RenameField(
            model_name="wardrobeitemhashtag",
            old_name="category",
            new_name="hashtag",
        ),
        migrations.RemoveConstraint(
            model_name="wardrobehashtag",
            name="uq_wardrobe_category_user_normalized_name",
        ),
        migrations.RemoveIndex(
            model_name="wardrobehashtag",
            name="idx_wd_cat_user_pos",
        ),
        migrations.RemoveConstraint(
            model_name="wardrobeitemhashtag",
            name="uq_wardrobe_item_category_pair",
        ),
        migrations.RemoveIndex(
            model_name="wardrobeitemhashtag",
            name="idx_wd_item_cat_lookup",
        ),
        migrations.AlterModelTable(
            name="wardrobehashtag",
            table="wardrobe_hashtag",
        ),
        migrations.AlterModelTableComment(
            name="wardrobehashtag",
            table_comment="개인 옷장 아이템에 사용자가 붙이는 정리용 해시태그",
        ),
        migrations.AlterModelOptions(
            name="wardrobehashtag",
            options={"ordering": ["position", "created_at", "id"]},
        ),
        migrations.AlterModelTable(
            name="wardrobeitemhashtag",
            table="wardrobe_item_hashtag",
        ),
        migrations.AlterModelTableComment(
            name="wardrobeitemhashtag",
            table_comment="개인 옷장 아이템과 사용자 해시태그 연결",
        ),
        migrations.AlterField(
            model_name="wardrobehashtag",
            name="id",
            field=models.UUIDField(
                db_comment="개인 옷장 사용자 해시태그 UUID",
                default=uuid.uuid4,
                editable=False,
                primary_key=True,
                serialize=False,
            ),
        ),
        migrations.AlterField(
            model_name="wardrobehashtag",
            name="user",
            field=models.ForeignKey(
                db_comment="해시태그 소유 사용자 FK (users.id)",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="wardrobe_hashtags",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="wardrobehashtag",
            name="name",
            field=models.CharField(
                db_comment="사용자에게 표시할 개인 옷장 해시태그명 (# 제외)",
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name="wardrobehashtag",
            name="normalized_name",
            field=models.CharField(
                db_comment="중복 검사용 정규화 해시태그명 (#·공백 정리 및 대소문자 통합)",
                editable=False,
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name="wardrobehashtag",
            name="position",
            field=models.PositiveIntegerField(
                db_comment="사용자 해시태그 표시 순서 (0부터 오름차순)",
                default=0,
            ),
        ),
        migrations.AlterField(
            model_name="wardrobehashtag",
            name="created_at",
            field=models.DateTimeField(
                auto_now_add=True,
                db_comment="해시태그 생성 시각",
            ),
        ),
        migrations.AlterField(
            model_name="wardrobehashtag",
            name="updated_at",
            field=models.DateTimeField(
                auto_now=True,
                db_comment="해시태그 수정 시각",
            ),
        ),
        migrations.AlterField(
            model_name="wardrobeitem",
            name="wardrobe_hashtags",
            field=models.ManyToManyField(
                related_name="wardrobe_items",
                through="wardrobe.WardrobeItemHashtag",
                to="wardrobe.wardrobehashtag",
            ),
        ),
        migrations.AlterField(
            model_name="wardrobeitemhashtag",
            name="id",
            field=models.UUIDField(
                db_comment="개인 옷장 아이템 해시태그 연결 UUID",
                default=uuid.uuid4,
                editable=False,
                primary_key=True,
                serialize=False,
            ),
        ),
        migrations.AlterField(
            model_name="wardrobeitemhashtag",
            name="wardrobe_item",
            field=models.ForeignKey(
                db_comment="해시태그를 지정한 개인 옷장 아이템 FK",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="wardrobe_hashtag_links",
                to="wardrobe.wardrobeitem",
            ),
        ),
        migrations.AlterField(
            model_name="wardrobeitemhashtag",
            name="hashtag",
            field=models.ForeignKey(
                db_comment="아이템에 지정한 사용자 해시태그 FK",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="item_links",
                to="wardrobe.wardrobehashtag",
            ),
        ),
        migrations.AlterField(
            model_name="wardrobeitemhashtag",
            name="created_at",
            field=models.DateTimeField(
                auto_now_add=True,
                db_comment="아이템 해시태그 연결 시각",
            ),
        ),
        migrations.RunPython(
            migrate_categories_to_hashtags,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RunSQL(
            sql="SET CONSTRAINTS ALL IMMEDIATE",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AddConstraint(
            model_name="wardrobehashtag",
            constraint=models.UniqueConstraint(
                fields=("user", "normalized_name"),
                name="uq_wd_hashtag_user_normalized",
            ),
        ),
        migrations.AddIndex(
            model_name="wardrobehashtag",
            index=models.Index(
                fields=["user", "position"],
                name="idx_wd_hashtag_user_pos",
            ),
        ),
        migrations.AddConstraint(
            model_name="wardrobeitemhashtag",
            constraint=models.UniqueConstraint(
                fields=("wardrobe_item", "hashtag"),
                name="uq_wd_item_hashtag_pair",
            ),
        ),
        migrations.AddIndex(
            model_name="wardrobeitemhashtag",
            index=models.Index(
                fields=["hashtag", "wardrobe_item"],
                name="idx_wd_item_hashtag_lookup",
            ),
        ),
    ]
