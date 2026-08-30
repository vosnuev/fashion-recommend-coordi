"""recommend 앱의 0017 leaf 둘을 합친다 — 스키마 변경은 없다.

    0017_wishlistitem                        (wishlist_item 테이블 생성)
    0017_outfitcomposition_reference_match   (outfit_composition.reference_match 추가)

두 갈래가 서로를 모른 채 각자 0016 위에서 만들어져 main에 함께 들어왔다.
건드리는 테이블이 달라 실행 순서에 제약이 없으므로 빈 merge로 leaf만 모은다.

기존 migration의 dependencies를 고쳐 한 줄로 세우지 않은 이유는, 이미 한쪽을
적용한 DB에서 "의존성보다 먼저 적용됨"(InconsistentMigrationHistory)이 나기
때문이다. merge migration은 이력을 건드리지 않는다.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012 - Django migration contract
        ("recommend", "0017_wishlistitem"),
        ("recommend", "0017_outfitcomposition_reference_match"),
    ]

    operations = []  # noqa: RUF012 - 두 갈래의 스키마 연산은 서로 독립적이다.
