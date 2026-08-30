from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0014_bodymeasurement_neck_length_and_more'),
    ]

    operations = [
        migrations.AlterModelTableComment(
            name='bodymeasurement',
            table_comment='사용자 신체치수 (기본 정보·상세 둘레·체형 지표, 사용자당 1행)',
        ),
    ]
