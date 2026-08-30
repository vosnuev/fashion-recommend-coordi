from django.db import migrations, models

# Django migration 클래스는 프레임워크 계약상 클래스 수준 list를 사용한다.
# ruff: noqa: RUF012


class Migration(migrations.Migration):
    dependencies = [
        ("recommend", "0016_productclickevent_engagement_duration_ms_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="outfitcomposition",
            name="reference_match",
            field=models.JSONField(
                blank=True,
                db_comment=(
                    "공유 옷 참고 매칭 근거 JSON "
                    "(match_type/source_type/source_id/score/reasons, 미사용 시 빈 객체)"
                ),
                default=dict,
            ),
        ),
    ]
