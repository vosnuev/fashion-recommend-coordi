"""동일한 0023 leaf를 합친 중복 0024 migration 두 개를 다시 병합한다.

브랜치별로 같은 충돌을 해결하면서 파일명만 다른 빈 0024 merge가 각각 생성됐다.
이미 어느 한쪽 또는 양쪽을 적용한 환경의 이력을 고치지 않고 새 merge leaf를
추가해야 모든 DB에서 안전하게 단일 migration graph로 수렴한다.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012 - Django migration contract
        ("users", "0024_merge_body_measurement_repair_and_budget_leaves"),
        ("users", "0024_merge_bodymeasurement_repair_leaves"),
    ]

    # 두 0024 모두 동일한 두 0023을 잇는 빈 merge라 추가 스키마 연산은 없다.
    operations = []  # noqa: RUF012
