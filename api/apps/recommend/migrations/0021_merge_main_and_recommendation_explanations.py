"""main과 추천 설명 브랜치에서 생긴 recommend migration leaf를 합친다.

두 부모 migration은 이미 각자의 선행 갈래를 합친 빈 merge migration이다.
스키마 연산 없이 의존성만 연결해 배포·테스트 환경의 migration graph를 단일 leaf로
만든다. 기존 migration의 dependencies를 수정하면 일부 DB에서 적용 이력이 꼬일 수
있으므로 새 merge migration으로 해결한다.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012 - Django migration contract
        ("recommend", "0019_merge_daily_look_alternatives"),
        ("recommend", "0020_merge_dailylook_alternatives"),
    ]

    operations = []  # noqa: RUF012 - 두 부모 모두 빈 merge migration이다.
