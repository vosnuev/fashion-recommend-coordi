from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("lookbook", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="CuratedLook",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("external_id", models.CharField(db_comment="CSV에서 사용하는 운영자 룩 고유 ID", max_length=100, unique=True)),
                ("category", models.CharField(db_comment="룩북 필터 카테고리", max_length=30)),
                ("title", models.CharField(db_comment="룩 제목", max_length=200)),
                ("subtitle", models.CharField(blank=True, db_comment="룩 부제", max_length=200)),
                ("cover_image_url", models.TextField(db_comment="전신 코디 대표 이미지 URL")),
                ("tags", models.JSONField(blank=True, db_comment="룩 필터 태그 배열 JSON", default=list)),
                ("is_active", models.BooleanField(db_comment="공개 둘러보기 노출 여부", default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_comment="생성 시각")),
                ("updated_at", models.DateTimeField(auto_now=True, db_comment="수정 시각")),
            ],
            options={"db_table": "lookbook_curated_look", "db_table_comment": "운영자가 선별해 공개하는 룩북 콘텐츠", "ordering": ["category", "external_id"]},
        ),
        migrations.CreateModel(
            name="CuratedLookItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slot", models.CharField(db_comment="구성 위치 (상의/하의/신발/액세서리)", max_length=30)),
                ("name", models.CharField(db_comment="원본 상품명", max_length=500)),
                ("brand", models.CharField(blank=True, db_comment="원본 상품 브랜드 또는 판매처", max_length=200)),
                ("price", models.PositiveIntegerField(blank=True, db_comment="원본 판매가 (원)", null=True)),
                ("product_url", models.TextField(db_comment="네이버 쇼핑 원본 상품 상세 URL")),
                ("image_url", models.TextField(blank=True, db_comment="원본 상품 대표 이미지 URL")),
                ("related_keyword", models.CharField(db_comment="유사상품 네이버 검색어", max_length=200)),
                ("sort_order", models.PositiveIntegerField(db_comment="구성 아이템 표시 순서", default=0)),
                ("look", models.ForeignKey(db_comment="운영자 룩 FK", on_delete=django.db.models.deletion.CASCADE, related_name="items", to="lookbook.curatedlook")),
            ],
            options={"db_table": "lookbook_curated_item", "db_table_comment": "운영자 룩 구성 아이템과 네이버 원본 상품 연결", "ordering": ["sort_order", "id"]},
        ),
        migrations.AddConstraint(model_name="curatedlookitem", constraint=models.UniqueConstraint(fields=("look", "slot"), name="uq_curated_look_slot")),
    ]
