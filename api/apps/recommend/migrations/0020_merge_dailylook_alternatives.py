"""recommend 앱에 남아 있던 leaf 둘을 합친다 — 스키마 변경은 없다.

    0017_dailylook_alternatives      (daily_look.alternatives 추가)
    0019_recommendation_explanations (추천 설명 구조화)

0018_merge_wishlist_reference_match가 0017 갈래 중 wishlist·reference_match만
묶고 dailylook_alternatives를 빠뜨려, 그 뒤로 leaf가 계속 둘이었다. 그래서
새 환경에서 migrate가 "Conflicting migrations detected"로 멈추고 테스트 DB도
만들어지지 않았다.

건드리는 테이블이 서로 달라 실행 순서에 제약이 없으므로 빈 merge로 leaf만 모은다.
0018과 같은 이유로 기존 migration의 dependencies는 고치지 않는다 — 한쪽만 적용된
DB에서 InconsistentMigrationHistory가 나기 때문이다.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012 - Django migration contract
        ("recommend", "0017_dailylook_alternatives"),
        ("recommend", "0019_recommendation_explanations"),
    ]

    operations = []  # noqa: RUF012 - 두 갈래의 스키마 연산은 서로 독립적이다.
