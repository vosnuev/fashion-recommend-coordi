from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0013_user_email_auth_comment"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailVerification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code_hash", models.CharField(blank=True, db_comment="6자리 이메일 인증 코드의 HMAC-SHA256 해시", max_length=64, verbose_name="인증 코드 해시")),
                ("expires_at", models.DateTimeField(blank=True, db_comment="현재 인증 코드 만료 시각 (기본 발송 후 10분)", null=True, verbose_name="인증 코드 만료 시각")),
                ("resend_available_at", models.DateTimeField(blank=True, db_comment="인증 메일 재발송 제한 종료 시각 (기본 발송 후 60초)", null=True, verbose_name="재발송 가능 시각")),
                ("failed_attempts", models.PositiveSmallIntegerField(db_comment="현재 코드 검증 실패 횟수 (최대 5회)", default=0, verbose_name="인증 실패 횟수")),
                ("verified_at", models.DateTimeField(blank=True, db_comment="이메일 소유 확인 완료 시각 (미인증이면 NULL)", null=True, verbose_name="이메일 인증 완료 시각")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_comment="인증 레코드 최초 생성 시각", verbose_name="생성 시각")),
                ("updated_at", models.DateTimeField(auto_now=True, db_comment="인증 레코드 마지막 수정 시각", verbose_name="수정 시각")),
                ("user", models.OneToOneField(db_comment="인증 대상 사용자 FK (users.id, 사용자당 1건)", on_delete=django.db.models.deletion.CASCADE, related_name="email_verification", to="users.user")),
            ],
            options={
                "verbose_name": "이메일 인증",
                "verbose_name_plural": "이메일 인증",
                "db_table": "email_verifications",
                "db_table_comment": "이메일 계정 소유 확인용 일회성 코드와 인증 상태",
            },
        ),
    ]
