"""0023 leaf 둘을 합친다 — 스키마 변경은 없다.

브랜치 병합 과정에서 users 앱에 0023이 둘 생겼다.

    0023_merge_category_budget_bodymeasurement_leaves  (0022 두 갈래를 합친 merge)
    0023_repair_bodymeasurement_ratio_columns          (누락 컬럼 3개 복구)

둘 다 필요하다. 앞은 카테고리 예산·신체치수 갈래를 잇고, 뒤는 0020과 같은
스키마 이탈을 실제 컬럼 추가로 복구한다. 어느 한쪽을 지우면 그 기능이 깨진다.

기존 파일의 dependencies를 고쳐 한 줄로 세우지 않은 이유는, 그 방식이 이미
migration을 적용한 환경에서 "의존성보다 먼저 적용됨" 오류를 만들기 때문이다.
merge migration은 이력을 건드리지 않고 leaf만 하나로 모은다.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012 - Django migration contract
        ("users", "0023_merge_category_budget_bodymeasurement_leaves"),
        ("users", "0023_repair_bodymeasurement_ratio_columns"),
    ]

    # 양쪽 0023이 이미 각자의 작업을 마쳤으므로 추가 스키마 연산은 없다.
    operations = []  # noqa: RUF012
