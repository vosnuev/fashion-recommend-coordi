import django.db.models.functions.datetime
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0004_eleventaggingbatch"),
    ]

    operations = [
        migrations.AddField(
            model_name="naverproduct",
            name="image_s3_key",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="naverproduct",
            name="image_checksum",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="naverproduct",
            name="embedding_status",
            field=models.CharField(
                db_default="not_requested",
                default="not_requested",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="naverproduct",
            name="embedding_version",
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
        migrations.AddField(
            model_name="naverproduct",
            name="embedding_retry_count",
            field=models.PositiveSmallIntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name="naverproduct",
            name="embedding_error",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="naverproduct",
            name="image_embedded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="naverproduct",
            name="text_embedded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="naverproduct",
            name="embedded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="elevenproduct",
            name="image_s3_key",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="elevenproduct",
            name="image_checksum",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="elevenproduct",
            name="embedding_status",
            field=models.CharField(
                db_default="not_requested",
                default="not_requested",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="elevenproduct",
            name="embedding_version",
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
        migrations.AddField(
            model_name="elevenproduct",
            name="embedding_retry_count",
            field=models.PositiveSmallIntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name="elevenproduct",
            name="embedding_error",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="elevenproduct",
            name="image_embedded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="elevenproduct",
            name="text_embedded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="elevenproduct",
            name="embedded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="ProductEmbeddingJob",
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
                ("source", models.CharField(max_length=20)),
                ("external_product_id", models.CharField(max_length=100)),
                (
                    "status",
                    models.CharField(
                        db_default="pending", default="pending", max_length=20
                    ),
                ),
                ("target_version", models.CharField(max_length=200)),
                (
                    "generation",
                    models.PositiveIntegerField(db_default=1, default=1),
                ),
                (
                    "attempt_count",
                    models.PositiveSmallIntegerField(db_default=0, default=0),
                ),
                ("last_error", models.TextField(blank=True, null=True)),
                (
                    "available_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now()
                    ),
                ),
                ("claimed_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now()
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now()
                    ),
                ),
            ],
            options={
                "verbose_name": "상품 임베딩 작업",
                "verbose_name_plural": "상품 임베딩 작업",
                "db_table": "product_embedding_job",
            },
        ),
        migrations.AddIndex(
            model_name="naverproduct",
            index=models.Index(
                fields=["embedding_status"],
                name="ix_naver_product_embed_status",
            ),
        ),
        migrations.AddIndex(
            model_name="elevenproduct",
            index=models.Index(
                fields=["embedding_status"],
                name="ix_eleven_product_embed_status",
            ),
        ),
        migrations.AddConstraint(
            model_name="productembeddingjob",
            constraint=models.UniqueConstraint(
                fields=("source", "external_product_id"),
                name="uq_product_embedding_job_source_id",
            ),
        ),
        migrations.AddIndex(
            model_name="productembeddingjob",
            index=models.Index(
                fields=["status", "available_at"],
                name="ix_product_embedding_job_ready",
            ),
        ),
        migrations.AddIndex(
            model_name="productembeddingjob",
            index=models.Index(
                fields=["source", "status"],
                name="ix_product_embed_job_source",
            ),
        ),
    ]
