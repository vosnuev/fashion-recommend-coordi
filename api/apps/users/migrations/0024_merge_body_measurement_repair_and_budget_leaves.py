"""사용자 앱의 두 leaf(0023 merge / 0023 repair)를 하나로 합친다.

0023_merge_category_budget_bodymeasurement_leaves 는 중복 생성된 두 0022 merge를
합쳤고, 0023_repair_bodymeasurement_ratio_columns 는 그와 무관하게 0022 merge
위에서 컬럼 복구 SQL만 실행한다. 두 갈래가 서로를 모르는 채 main에 함께
들어오면서 leaf가 둘이 되어 `migrate`가 CommandError로 멈췄다.

기존 migration의 dependencies를 고쳐 잡지 않고 새 merge를 얹는 이유:
0023_repair 는 main에 먼저 들어가 이미 적용된 DB가 있고,
0023_merge_...leaves 는 feature/chat-main-integration 에서 먼저 적용된 DB가
있을 수 있다. 어느 쪽 의존성을 바꿔도 반대쪽 환경에서
InconsistentMigrationHistory("applied before its dependency")가 난다.
새 merge는 어떤 이력에서도 안전하다.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012 - Django migration contract
        ("users", "0023_merge_category_budget_bodymeasurement_leaves"),
        ("users", "0023_repair_bodymeasurement_ratio_columns"),
    ]

    # 두 갈래 모두 상대의 스키마 연산과 겹치지 않는다 (한쪽은 빈 merge,
    # 다른 한쪽은 IF NOT EXISTS 복구 SQL). 추가 연산 없음.
    operations = []  # noqa: RUF012
