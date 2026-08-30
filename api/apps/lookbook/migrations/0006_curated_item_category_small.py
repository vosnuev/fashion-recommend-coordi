from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lookbook", "0005_lookbook_golden_look"),
    ]

    operations = [
        migrations.AddField(
            model_name="curatedlookitem",
            name="category_small",
            field=models.CharField(
                blank=True,
                db_comment=(
                    "관리자가 검수·확정한 서비스 소분류 "
                    "(가디건/패딩/셔츠 등, 공란이면 유사상품 미노출)"
                ),
                default="",
                max_length=50,
            ),
        ),
    ]
