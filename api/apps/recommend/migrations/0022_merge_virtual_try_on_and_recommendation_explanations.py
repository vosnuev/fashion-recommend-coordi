"""가상 피팅과 추천 설명 migration 갈래를 단일 leaf로 합친다.

가상 피팅 migration이 추천 설명 merge 이전의 공통 조상을 부모로 생성된 채
나중에 main에서 합쳐져 새 leaf가 생겼다. 이미 적용됐을 수 있는 기존 migration의
dependency는 바꾸지 않고 빈 merge migration으로 두 이력을 안전하게 연결한다.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012 - Django migration contract
        ("recommend", "0020_virtual_try_on_job"),
        ("recommend", "0021_merge_main_and_recommendation_explanations"),
    ]

    operations = []  # noqa: RUF012 - 두 부모의 스키마 연산은 이미 각자 완료됐다.
