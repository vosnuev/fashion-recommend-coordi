"""이메일 인증 갈래(0014_emailverification)와 main의 신체치수 갈래(0016)를 합친다.

main 머지 이후 users 앱의 리프 노드가 둘로 갈려 `migrate`가
"Conflicting migrations detected"로 멈춘다. 두 갈래는 서로 다른 테이블
(email_verifications / body_measurements)만 건드려 실제 충돌은 없으므로
스키마 변경 없이 리프만 하나로 모은다.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0014_emailverification"),
        ("users", "0016_align_body_ratio_ranges"),
    ]

    operations = []
