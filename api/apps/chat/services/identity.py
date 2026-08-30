"""게스트 채팅 토큰 발급·검증과 회원 identity 이전."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from apps.chat.models import ChatAttachment, ChatIdentity, ChatMessage

_TOKEN_SALT = "apps.chat.guest-identity"


class ChatIdentityError(RuntimeError):
    code = "CHAT_IDENTITY_INVALID"


class GuestTokenMissing(ChatIdentityError):
    code = "CHAT_GUEST_TOKEN_MISSING"


class GuestTokenInvalid(ChatIdentityError):
    code = "CHAT_GUEST_TOKEN_INVALID"


class GuestTokenExpired(ChatIdentityError):
    code = "CHAT_GUEST_TOKEN_EXPIRED"


class GuestAlreadyClaimed(ChatIdentityError):
    code = "CHAT_GUEST_ALREADY_CLAIMED"


@dataclass(frozen=True)
class GuestCredential:
    identity: ChatIdentity
    token: str


@dataclass(frozen=True)
class GuestClaimSummary:
    guest_identity_id: str
    member_identity_id: str
    session_count: int
    message_count: int
    attachment_count: int
    recommendation_count: int


def guest_ttl() -> timedelta:
    days = settings.CHAT_GUEST_TTL_DAYS
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        raise ImproperlyConfigured("CHAT_GUEST_TTL_DAYS는 1 이상의 정수여야 합니다.")
    return timedelta(days=days)


def token_hash(raw_token: str) -> str:
    if not isinstance(raw_token, str) or not raw_token.strip():
        raise GuestTokenMissing("게스트 채팅 토큰이 없습니다.")
    return salted_hmac(
        _TOKEN_SALT,
        raw_token.strip(),
        secret=settings.SECRET_KEY,
        algorithm="sha256",
    ).hexdigest()


def issue_guest_identity() -> GuestCredential:
    """원문은 호출자에게 한 번만 주고 DB에는 해시만 저장한다."""
    now = timezone.now()
    for _ in range(3):
        raw_token = secrets.token_urlsafe(32)
        try:
            identity = ChatIdentity.objects.create(
                identity_type=ChatIdentity.IdentityType.GUEST,
                guest_token_hash=token_hash(raw_token),
                expires_at=now + guest_ttl(),
                last_active_at=now,
            )
            return GuestCredential(identity=identity, token=raw_token)
        except IntegrityError:
            continue
    raise ChatIdentityError("게스트 채팅 identity 생성에 실패했습니다.")


def get_guest_identity(raw_token: str, *, touch: bool = False) -> ChatIdentity:
    expected_hash = token_hash(raw_token)
    identity = ChatIdentity.objects.filter(
        identity_type=ChatIdentity.IdentityType.GUEST,
        guest_token_hash=expected_hash,
    ).first()
    if identity is None or not constant_time_compare(
        identity.guest_token_hash or "", expected_hash
    ):
        raise GuestTokenInvalid("유효하지 않은 게스트 채팅 토큰입니다.")
    _validate_guest(identity)
    if touch:
        touch_identity(identity)
    return identity


def get_or_create_member_identity(user) -> ChatIdentity:
    if user is None or not getattr(user, "is_authenticated", False):
        raise ChatIdentityError("인증된 회원이 필요합니다.")
    try:
        identity, _ = ChatIdentity.objects.get_or_create(
            user=user,
            defaults={"identity_type": ChatIdentity.IdentityType.MEMBER},
        )
    except IntegrityError:
        identity = ChatIdentity.objects.get(user=user)
    if identity.identity_type != ChatIdentity.IdentityType.MEMBER:
        raise ChatIdentityError("회원 identity 유형이 올바르지 않습니다.")
    return identity


def resolve_identity(*, user=None, guest_token: str = "") -> ChatIdentity:
    if user is not None and getattr(user, "is_authenticated", False):
        return get_or_create_member_identity(user)
    return get_guest_identity(guest_token, touch=True)


def touch_identity(identity: ChatIdentity) -> None:
    now = timezone.now()
    fields = {"last_active_at": now, "updated_at": now}
    if identity.identity_type == ChatIdentity.IdentityType.GUEST:
        _validate_guest(identity)
        fields["expires_at"] = now + guest_ttl()
    ChatIdentity.objects.filter(pk=identity.pk).update(**fields)
    identity.last_active_at = now
    identity.updated_at = now
    if "expires_at" in fields:
        identity.expires_at = fields["expires_at"]


def _validate_guest(identity: ChatIdentity) -> None:
    if identity.claimed_at is not None:
        raise GuestAlreadyClaimed("이미 회원에게 이전된 게스트 채팅입니다.")
    if identity.expires_at is None or identity.expires_at <= timezone.now():
        raise GuestTokenExpired("게스트 채팅 보관기간이 만료되었습니다.")


@transaction.atomic
def claim_guest_identity(user, raw_token: str) -> GuestClaimSummary:
    """게스트 세션과 추천 결과를 회원 identity로 한 번만 이전한다."""
    expected_hash = token_hash(raw_token)
    guest = (
        ChatIdentity.objects.select_for_update()
        .filter(
            identity_type=ChatIdentity.IdentityType.GUEST,
            guest_token_hash=expected_hash,
        )
        .first()
    )
    if guest is None or not constant_time_compare(
        guest.guest_token_hash or "", expected_hash
    ):
        raise GuestTokenInvalid("유효하지 않은 게스트 채팅 토큰입니다.")
    _validate_guest(guest)

    member = get_or_create_member_identity(user)
    session_count = guest.sessions.count()
    message_count = ChatMessage.objects.filter(session__identity=guest).count()
    attachment_count = ChatAttachment.objects.filter(
        message__session__identity=guest
    ).count()

    # 순환 import를 피하고 추천 앱이 없는 관리 작업에서도 identity 모듈을 읽을 수 있게 한다.
    from apps.recommend.models import RecommendationResult

    recommendation_count = RecommendationResult.objects.filter(
        identity_id=guest.id
    ).count()
    guest.sessions.update(identity=member)
    RecommendationResult.objects.filter(identity_id=guest.id).update(
        identity_id=member.id
    )

    now = timezone.now()
    guest.claimed_at = now
    guest.claimed_by = member
    guest.last_active_at = now
    guest.save(
        update_fields=["claimed_at", "claimed_by", "last_active_at", "updated_at"]
    )

    return GuestClaimSummary(
        guest_identity_id=str(guest.id),
        member_identity_id=str(member.id),
        session_count=session_count,
        message_count=message_count,
        attachment_count=attachment_count,
        recommendation_count=recommendation_count,
    )
