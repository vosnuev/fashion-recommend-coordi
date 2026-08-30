"""recommend 앱 leaf 둘을 합친다 — 스키마 변경은 없다.

    0017_dailylook_alternatives              (daily_look.alternatives 추가, '다른 룩' 갈래)
    0018_merge_wishlist_reference_match      (wishlist / reference_match 두 0017을 합친 merge)

'다른 룩' 갈래가 0016 위에서 갈라져 나간 사이에 main 쪽에서 0017 둘이 0018로
합쳐졌다. 서로를 모른 채 같은 0016을 부모로 두었으니 leaf가 다시 둘이 된다.
건드리는 테이블이 daily_look / wishlist_item / outfit_composition 으로 모두 달라
실행 순서에 제약이 없으므로 빈 merge로 leaf만 모은다.

기존 migration의 dependencies를 고쳐 한 줄로 세우지 않은 이유는, 이미 한쪽을
적용한 DB에서 "의존성보다 먼저 적용됨"(InconsistentMigrationHistory)이 나기
때문이다. merge migration은 이력을 건드리지 않는다.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012 - Django migration contract
        ("recommend", "0017_dailylook_alternatives"),
        ("recommend", "0018_merge_wishlist_reference_match"),
    ]

    operations = []  # noqa: RUF012 - 두 갈래의 스키마 연산은 서로 독립적이다.
