"""이메일·비밀번호 인증 지원에 맞춰 사용자 테이블 설명을 갱신."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0012_bodyphototransaction_error_message"),
    ]

    operations = [
        migrations.AlterModelTableComment(
            name="user",
            table_comment="서비스 사용자 (이메일·비밀번호 또는 소셜 로그인 계정)",
        ),
    ]
