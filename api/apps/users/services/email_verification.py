import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from rest_framework.exceptions import ValidationError

from apps.users.models import EmailVerification, User


def _code_hash(user_id: int, code: str) -> str:
    return salted_hmac("users.email_verification", f"{user_id}:{code}").hexdigest()


def issue_code(user: User, *, enforce_cooldown: bool = False) -> int:
    now = timezone.now()
    verification, _ = EmailVerification.objects.select_for_update().get_or_create(user=user)
    if verification.verified_at:
        raise ValidationError({"email": "이미 인증된 이메일입니다."})
    if enforce_cooldown and verification.resend_available_at and now < verification.resend_available_at:
        seconds = max(1, int((verification.resend_available_at - now).total_seconds()))
        raise ValidationError({"retry_after": seconds, "detail": "잠시 후 인증 메일을 다시 요청해 주세요."})

    code = f"{secrets.randbelow(1_000_000):06d}"
    verification.code_hash = _code_hash(user.pk, code)
    verification.expires_at = now + timedelta(seconds=settings.EMAIL_VERIFICATION_CODE_TTL_SECONDS)
    verification.resend_available_at = now + timedelta(seconds=settings.EMAIL_VERIFICATION_RESEND_SECONDS)
    verification.failed_attempts = 0
    verification.save()

    send_mail(
        subject="[COZY] 이메일 인증 코드",
        message=(
            f"COZY 회원가입 인증 코드는 {code}입니다.\n\n"
            f"인증 코드는 {settings.EMAIL_VERIFICATION_CODE_TTL_SECONDS // 60}분 동안 유효합니다."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )
    return settings.EMAIL_VERIFICATION_RESEND_SECONDS


def verify_code(email: str, code: str) -> User:
    error = None
    with transaction.atomic():
        user = User.objects.filter(email__iexact=email).exclude(password__startswith="!").first()
        if user is None:
            raise ValidationError({"email": "가입 정보를 찾을 수 없습니다."})

        try:
            verification = EmailVerification.objects.select_for_update().get(user=user)
        except EmailVerification.DoesNotExist as exc:
            raise ValidationError({"email": "인증 메일을 먼저 요청해 주세요."}) from exc

        # 이미 인증된 계정을 무조건 통과시키면, 코드를 모르는 사람도 이 API로
        # '검증 성공' 상태를 얻는다. 인증이 끝났으면 로그인으로 안내한다.
        if verification.verified_at:
            raise ValidationError({"email": "이미 인증된 이메일입니다. 로그인해 주세요."})
        now = timezone.now()
        if not verification.expires_at or now >= verification.expires_at:
            raise ValidationError({"code": "인증 코드가 만료되었습니다. 다시 발송해 주세요."})
        if verification.failed_attempts >= settings.EMAIL_VERIFICATION_MAX_ATTEMPTS:
            raise ValidationError({"code": "인증 시도 횟수를 초과했습니다. 코드를 다시 발송해 주세요."})
        if not constant_time_compare(verification.code_hash, _code_hash(user.pk, code)):
            verification.failed_attempts += 1
            verification.save(update_fields=["failed_attempts", "updated_at"])
            error = {"code": "인증 코드가 올바르지 않습니다."}
        else:
            verification.verified_at = now
            verification.code_hash = ""
            verification.failed_attempts = 0
            verification.save(update_fields=["verified_at", "code_hash", "failed_attempts", "updated_at"])
            user.is_active = True
            user.save(update_fields=["is_active"])

    if error:
        raise ValidationError(error)
    return user


def resend_code(email: str) -> int:
    user = User.objects.filter(email__iexact=email).exclude(password__startswith="!").first()
    if user is None:
        raise ValidationError({"email": "가입 정보를 찾을 수 없습니다."})
    return issue_code(user, enforce_cooldown=True)
