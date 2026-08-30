from django.db import migrations, models


class Migration(migrations.Migration):
    """'다른 룩' — 차순위 후보를 result와 같은 스키마로 함께 저장한다.

    candidates(진단용 요약)와 따로 두는 이유: candidates 는 운영자가 추천 경로를
    되짚는 값이고, alternatives 는 화면이 그대로 그리는 값이다. 한 필드에 섞으면
    화면 요구가 진단 스냅샷의 스키마를 흔든다.

    result 와 별도 필드인 이유: 조회 경로(refresh_render)가 요청 스레드에서
    result 를 통째로 다시 쓴다. 워커가 같은 순간 후보 이미지를 채우면 한쪽이
    덮인다 — update_fields 를 갈라 두면 그 경합 자체가 없다.
    """

    dependencies = [
        ("recommend", "0016_productclickevent_engagement_duration_ms_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="dailylook",
            name="alternatives",
            field=models.JSONField(
                blank=True,
                db_comment=(
                    "'다른 룩'으로 돌려볼 차순위 후보들. result와 **같은 스키마**의 배열이라 "
                    "프론트가 카드 한 벌을 그리는 코드를 그대로 쓴다. 문장은 템플릿이고 "
                    "착용 이미지는 별도 큐 작업이 나중에 채운다"
                ),
                default=list,
                verbose_name="다른 룩 후보",
            ),
        ),
    ]
