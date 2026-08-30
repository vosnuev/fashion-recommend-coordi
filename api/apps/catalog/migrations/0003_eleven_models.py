"""11번가 ProductSearch 수집용 응답, 카테고리, 상품 테이블."""

import django.contrib.postgres.fields
import django.contrib.postgres.indexes
import django.db.models.deletion
from django.db import migrations, models
from django.db.models.functions import Now


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0002_navertaggingbatch"),
    ]

    operations = [
        migrations.CreateModel(
            name="ElevenApiResponse",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("api_name", models.CharField(max_length=30)),
                ("endpoint", models.TextField()),
                (
                    "http_method",
                    models.CharField(db_default="GET", default="GET", max_length=10),
                ),
                ("request_params", models.JSONField(blank=True, default=dict)),
                ("response_status", models.IntegerField(blank=True, null=True)),
                (
                    "content_type",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                ("raw_body", models.TextField(blank=True, default="")),
                (
                    "response_hash",
                    models.CharField(blank=True, max_length=64, null=True),
                ),
                ("error_message", models.TextField(blank=True, null=True)),
                ("fetched_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(db_default=Now())),
            ],
            options={
                "verbose_name": "11번가 API 응답",
                "verbose_name_plural": "11번가 API 응답",
                "db_table": "eleven_api_response",
            },
        ),
        migrations.CreateModel(
            name="ElevenCategory",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("disp_no", models.CharField(max_length=30, unique=True)),
                ("disp_nm", models.CharField(max_length=200)),
                (
                    "parent_disp_no",
                    models.CharField(blank=True, max_length=30, null=True),
                ),
                ("depth", models.SmallIntegerField()),
                (
                    "leaf_yn",
                    models.BooleanField(db_default=False, default=False),
                ),
                ("gbl_dlv_yn", models.BooleanField(blank=True, null=True)),
                ("eng_disp_yn", models.BooleanField(blank=True, null=True)),
                (
                    "is_active",
                    models.BooleanField(db_default=True, default=True),
                ),
                (
                    "missing_count",
                    models.PositiveIntegerField(db_default=0, default=0),
                ),
                ("raw_data", models.JSONField(blank=True, default=dict)),
                ("collected_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(db_default=Now())),
                ("updated_at", models.DateTimeField(db_default=Now())),
                (
                    "api_response",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="categories",
                        to="catalog.elevenapiresponse",
                    ),
                ),
            ],
            options={
                "verbose_name": "11번가 카테고리",
                "verbose_name_plural": "11번가 카테고리",
                "db_table": "eleven_category",
            },
        ),
        migrations.CreateModel(
            name="ElevenProduct",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("eleven_product_id", models.CharField(max_length=40, unique=True)),
                ("title", models.CharField(max_length=500)),
                ("title_raw", models.CharField(max_length=500)),
                ("link", models.TextField(blank=True, null=True)),
                ("image_url", models.TextField(blank=True, null=True)),
                ("product_price", models.IntegerField(blank=True, null=True)),
                ("sale_price", models.IntegerField(blank=True, null=True)),
                (
                    "mall_name",
                    models.CharField(blank=True, max_length=200, null=True),
                ),
                (
                    "rating",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=6, null=True
                    ),
                ),
                ("review_count", models.IntegerField(blank=True, null=True)),
                (
                    "buy_satisfy",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=6, null=True
                    ),
                ),
                (
                    "delivery",
                    models.CharField(blank=True, max_length=200, null=True),
                ),
                ("benefit", models.JSONField(blank=True, default=dict)),
                (
                    "eleven_category1",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                (
                    "eleven_category2",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                (
                    "eleven_category3",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                (
                    "eleven_category4",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                (
                    "eleven_category_disp_no",
                    models.CharField(blank=True, max_length=30, null=True),
                ),
                ("category_large", models.CharField(max_length=30)),
                ("category_small", models.CharField(max_length=50)),
                (
                    "category_source",
                    models.CharField(
                        db_default="keyword", default="keyword", max_length=20
                    ),
                ),
                (
                    "category_mapping_version",
                    models.CharField(blank=True, max_length=30, null=True),
                ),
                (
                    "season",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.TextField(),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
                (
                    "style",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.TextField(),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
                (
                    "color",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.TextField(),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
                (
                    "pattern",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.TextField(),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
                (
                    "fit",
                    models.CharField(blank=True, max_length=30, null=True),
                ),
                (
                    "material",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.TextField(),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
                (
                    "sleeve",
                    models.CharField(blank=True, max_length=20, null=True),
                ),
                (
                    "length",
                    models.CharField(blank=True, max_length=20, null=True),
                ),
                (
                    "usage",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.TextField(),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
                (
                    "layer_role",
                    models.CharField(blank=True, max_length=30, null=True),
                ),
                ("layer_order", models.SmallIntegerField(blank=True, null=True)),
                ("tag_source", models.JSONField(blank=True, default=dict)),
                (
                    "tagging_status",
                    models.CharField(
                        db_default="pending", default="pending", max_length=20
                    ),
                ),
                (
                    "tagging_model",
                    models.CharField(blank=True, max_length=60, null=True),
                ),
                (
                    "tagging_used_image",
                    models.BooleanField(db_default=False, default=False),
                ),
                ("tagged_at", models.DateTimeField(blank=True, null=True)),
                (
                    "search_keyword",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                (
                    "search_sort",
                    models.CharField(blank=True, max_length=20, null=True),
                ),
                ("search_rank", models.IntegerField(blank=True, null=True)),
                ("page_num", models.IntegerField(blank=True, null=True)),
                ("raw_data", models.JSONField(blank=True, default=dict)),
                ("collected_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(db_default=Now())),
                ("updated_at", models.DateTimeField(db_default=Now())),
                (
                    "api_response",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="products",
                        to="catalog.elevenapiresponse",
                    ),
                ),
            ],
            options={
                "verbose_name": "11번가 상품",
                "verbose_name_plural": "11번가 상품",
                "db_table": "eleven_product",
            },
        ),
        migrations.AddIndex(
            model_name="elevenapiresponse",
            index=models.Index(
                fields=["api_name", "fetched_at"],
                name="ix_eleven_response_api_time",
            ),
        ),
        migrations.AddIndex(
            model_name="elevencategory",
            index=models.Index(
                fields=["parent_disp_no"], name="ix_eleven_category_parent"
            ),
        ),
        migrations.AddIndex(
            model_name="elevencategory",
            index=models.Index(
                fields=["depth", "leaf_yn"],
                name="ix_eleven_category_depth_leaf",
            ),
        ),
        migrations.AddIndex(
            model_name="elevenproduct",
            index=models.Index(
                fields=["category_large", "category_small"],
                name="ix_eleven_product_category",
            ),
        ),
        migrations.AddIndex(
            model_name="elevenproduct",
            index=models.Index(
                fields=["tagging_status"], name="ix_eleven_product_tag_status"
            ),
        ),
        migrations.AddIndex(
            model_name="elevenproduct",
            index=models.Index(
                fields=["collected_at"], name="ix_eleven_product_collected"
            ),
        ),
        migrations.AddIndex(
            model_name="elevenproduct",
            index=models.Index(
                fields=["search_keyword"], name="ix_eleven_product_keyword"
            ),
        ),
        migrations.AddIndex(
            model_name="elevenproduct",
            index=models.Index(
                fields=["eleven_category_disp_no"],
                name="ix_eleven_product_disp_no",
            ),
        ),
        migrations.AddIndex(
            model_name="elevenproduct",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["season"], name="ix_eleven_product_season"
            ),
        ),
        migrations.AddIndex(
            model_name="elevenproduct",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["style"], name="ix_eleven_product_style"
            ),
        ),
    ]
