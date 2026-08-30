# 골든 코디에서 분리한 의상 아이템을 1급 엔티티로 승격한다.
# 태그 필드는 apps.wardrobe.WardrobeItem과 같은 축을 쓴다 — 코디의 상의를
# 옷장/상품 아이템으로 교체하려면 세 저장소가 같은 필터 언어를 써야 한다.

import uuid

import django.contrib.postgres.fields
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('goldenset', '0002_alter_goldenprinciple_applies_when_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='GoldenOutfitItem',
            fields=[
                ('id', models.UUIDField(db_comment='골든 코디 아이템 UUID', default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('item_index', models.PositiveSmallIntegerField(db_comment='코디 안에서의 아이템 순번 (0부터, 파이프라인 산출 순서)')),
                ('item_key', models.CharField(db_comment='Qdrant point ID 재현용 안정 식별자 (golden_id#000 형식)', max_length=120)),
                ('s3_bucket', models.CharField(blank=True, db_comment='분리된 아이템 이미지 S3 버킷', default='', max_length=200)),
                ('s3_key', models.CharField(blank=True, db_comment='분리된 아이템 이미지 S3 키', default='', max_length=512)),
                ('item_name', models.CharField(blank=True, db_comment='아이템 표시 이름', default='', max_length=120)),
                ('category_large', models.CharField(blank=True, db_comment='대분류 (상의/하의/아우터/신발/가방 등)', default='', max_length=20)),
                ('category_small', models.CharField(blank=True, db_comment='소분류 (티셔츠/데님 팬츠 등)', default='', max_length=30)),
                ('season', django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=10), blank=True, db_comment='계절 태그 배열', default=list, size=None)),
                ('style', django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=10), blank=True, db_comment='스타일 태그 배열', default=list, size=None)),
                ('usage', django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=20), blank=True, db_comment='용도(TPO) 태그 배열', default=list, size=None)),
                ('color', models.CharField(blank=True, db_comment='색상 태그', default='', max_length=10)),
                ('pattern', models.CharField(blank=True, db_comment='패턴 태그', default='', max_length=10)),
                ('fit', models.CharField(blank=True, db_comment='핏 태그', default='', max_length=10)),
                ('material', models.CharField(blank=True, db_comment='소재 태그', default='', max_length=10)),
                ('sleeve', models.CharField(blank=True, db_comment='소매 길이 태그', default='', max_length=10)),
                ('length', models.CharField(blank=True, db_comment='기장 태그', default='', max_length=10)),
                ('layer_role', models.CharField(blank=True, db_comment='레이어링 역할 태그 (아이템 교체 질의의 핵심 축)', default='', max_length=10)),
                ('layer_order', models.PositiveSmallIntegerField(blank=True, db_comment='레이어링 착용 순서 (안쪽부터 1)', null=True)),
                ('label_ko', models.CharField(blank=True, db_comment='열거 단계가 붙인 짧은 한국어 라벨', default='', max_length=120)),
                ('descriptor_en', models.TextField(blank=True, db_comment='아이템을 특정하는 영어 서술 (분리 프롬프트 입력)', default='')),
                ('view_angle', models.CharField(blank=True, db_comment='원본에서 관측된 각도 (front/side/back/three-quarter)', default='', max_length=16)),
                ('occluded_by', models.JSONField(blank=True, db_comment='이 아이템을 가리는 요소 목록', default=list)),
                ('bbox', models.JSONField(blank=True, db_comment='원본 좌표 [ymin, xmin, ymax, xmax] 0~1000 정규화', null=True)),
                ('missing_required', models.JSONField(blank=True, db_comment='taxonomy 필수 태그 중 채워지지 않은 필드 목록', default=list)),
                ('pipeline_key', models.CharField(blank=True, db_comment='아이템을 만든 파이프라인 구현 키 (gemini-image-edit/sam3-crop 등)', default='', max_length=40)),
                ('image_embedding_version', models.CharField(blank=True, db_comment='아이템 이미지 임베딩 모델 버전', default='', max_length=80)),
                ('text_embedding_version', models.CharField(blank=True, db_comment='아이템 캡션 임베딩 모델 버전', default='', max_length=80)),
                ('status', models.CharField(choices=[('SUCCEEDED', '성공'), ('FAILED', '실패')], db_comment='아이템 처리 상태 (SUCCEEDED/FAILED)', default='SUCCEEDED', max_length=16)),
                ('error_message', models.TextField(blank=True, db_comment='아이템 처리 실패 사유', default='')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_comment='아이템 생성 시각')),
                ('updated_at', models.DateTimeField(auto_now=True, db_comment='아이템 수정 시각')),
                ('image', models.ForeignKey(db_comment='아이템이 속한 골든 코디 이미지 FK (golden_image.id)', on_delete=django.db.models.deletion.CASCADE, related_name='items', to='goldenset.goldenimage')),
            ],
            options={
                'db_table': 'goldenset"."golden_outfit_item',
                'db_table_comment': '골든 코디 사진에서 분리한 의상 아이템과 태그',
                'ordering': ['image', 'item_index'],
            },
        ),
        migrations.AddIndex(
            model_name='goldenoutfititem',
            index=models.Index(fields=['category_large', 'layer_role'], name='idx_golden_item_cat_layer'),
        ),
        migrations.AddIndex(
            model_name='goldenoutfititem',
            index=models.Index(fields=['item_key'], name='idx_golden_item_key'),
        ),
        migrations.AddIndex(
            model_name='goldenoutfititem',
            index=models.Index(fields=['status'], name='idx_golden_item_status'),
        ),
        migrations.AddConstraint(
            model_name='goldenoutfititem',
            constraint=models.UniqueConstraint(fields=('image', 'item_index'), name='uq_golden_outfit_item_index'),
        ),
        migrations.AddConstraint(
            model_name='goldenoutfititem',
            constraint=models.UniqueConstraint(fields=('image', 'item_key'), name='uq_golden_outfit_item_key'),
        ),
    ]
