"""익명 접수 건의 소유권 이전 (claim).

비로그인 상태로 코디를 평가한 뒤 로그인하면, 앱이 들고 있던 접수 건을 계정으로
옮긴다. 평가는 **다시 하지 않는다** — 이미 끝난 결과의 주인만 바꾼다.

## 왜 UUID만으로는 안 되는가

조회는 UUID를 아는 사람에게 열어두지만(설계 문서 5.2), claim은 성격이 다르다.

- 조회는 읽기다. UUID가 새어도 평가 문구만 보인다 (사진 URL·체형은 응답에서 뺐다).
- claim은 쓰기이고 되돌리기 어렵다. 게다가 가져간 뒤에는 소유자 응답으로 바뀌어
  **사진 presigned URL과 체형 스냅샷까지 열린다.** 즉 권한 상승 경로다.

그래서 두 겹을 건다:

1. **서명 토큰** — 접수 202 응답에만 실어 보낸다. 로그·Referer에 남는 것은
   `poll_url`의 UUID뿐이라 노출 경로가 다르다. 서버에 저장하지 않는다
   (Django TimestampSigner라 서명만 대조하면 된다).
2. **짧은 TTL** — 조회는 24시간이지만 claim은 `OUTFIT_CLAIM_TTL_MINUTES`(기본 60분)만
   허용한다. 로그인 유도는 결과 직후에 일어나므로 이 정도면 충분하다.

## 개인화

익명 평가는 `personalized=false`, `body/pursuit=NULL`로 이미 끝나 있다. 소유권만
옮기므로 결과는 그대로고, 그 사실을 `accepted_anonymously`로 남긴다 (API에는 싣지 않는다).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone

from ..models import OutfitAnalysis
from . import storage

logger = logging.getLogger(__name__)

# salt를 주면 같은 SECRET_KEY를 쓰는 다른 서명(세션·비밀번호 재설정 등)과 토큰이 섞이지 않는다
_SIGNER_SALT = "apps.recommend.outfit-analysis.claim"


class SkipReason:
    INVALID_TOKEN = "invalid_token"
    EXPIRED = "expired"
    NOT_FOUND = "not_found"
    ALREADY_OWNED = "already_owned"


@dataclass
class ClaimResult:
    claimed: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    def skip(self, analysis_id: str | None, reason: str) -> None:
        self.skipped.append({"analysis_id": analysis_id, "reason": reason})


def _signer() -> signing.TimestampSigner:
    return signing.TimestampSigner(salt=_SIGNER_SALT)


def _ttl() -> timedelta:
    return timedelta(minutes=settings.OUTFIT_CLAIM_TTL_MINUTES)


def issue_token(analysis: OutfitAnalysis) -> str | None:
    """접수 응답에 실을 claim 토큰. 이미 주인이 있으면 발급하지 않는다."""
    if analysis.user_id is not None:
        return None
    return _signer().sign(str(analysis.pk))


def verify_token(token: str) -> tuple[str | None, str | None]:
    """토큰을 검증해 (analysis_id, 실패 사유)를 돌려준다.

    성공하면 (uuid, None), 실패하면 (None, 사유).
    """
    try:
        return _signer().unsign(token, max_age=_ttl()), None
    except signing.SignatureExpired:
        return None, SkipReason.EXPIRED
    except signing.BadSignature:
        # 위조·오타·다른 salt로 만든 토큰 — 어느 쪽이든 알려줄 것은 없다
        return None, SkipReason.INVALID_TOKEN


def claim_analyses(user, tokens: list[str]) -> ClaimResult:
    """토큰 목록을 검증해 소유권을 넘긴다. 건별로 독립 처리한다."""
    result = ClaimResult()
    for token in tokens:
        analysis_id, reason = verify_token(token)
        if analysis_id is None:
            result.skip(None, reason)
            continue
        _claim_one(user, analysis_id, result)
    if result.claimed:
        logger.info(
            "코디 평가 소유권 이전: user=%s count=%d ids=%s",
            user.pk,
            len(result.claimed),
            ",".join(result.claimed),
        )
    return result


def _claim_one(user, analysis_id: str, result: ClaimResult) -> None:
    deadline = timezone.now() - _ttl()

    with transaction.atomic():
        analysis = (
            OutfitAnalysis.objects.select_for_update().filter(pk=analysis_id).first()
        )
        if analysis is None:
            result.skip(analysis_id, SkipReason.NOT_FOUND)
            return

        if analysis.user_id is not None:
            # 이미 내 것이면 재요청이므로 성공으로 친다 (멱등). 남의 것이면 알려주지 않는다.
            if analysis.user_id == user.pk:
                result.claimed.append(str(analysis.pk))
            else:
                result.skip(analysis_id, SkipReason.ALREADY_OWNED)
            return

        # 토큰이 유효해도 행 자체가 오래됐으면 막는다. 토큰은 접수 시각에 발급되므로
        # 보통 같은 판정이지만, 권한 상승 경로라 DB 쪽에서도 한 번 더 본다.
        if analysis.created_at < deadline:
            result.skip(analysis_id, SkipReason.EXPIRED)
            return

        analysis.user = user
        analysis.claimed_at = timezone.now()
        analysis.save(update_fields=["user", "claimed_at"])

    # 소유권 이전을 먼저 커밋하고 사진을 옮긴다. 순서를 뒤집으면 이동은 됐는데 DB가
    # 안 바뀐 경우 키가 어긋난다. 이동이 실패해도 기존 키는 그대로 살아 있어 안전하다.
    _move_photo_to_owner(analysis)
    result.claimed.append(str(analysis.pk))


def _move_photo_to_owner(analysis: OutfitAnalysis) -> None:
    """`outfits/anonymous/...` → `outfits/{user_id}/...` 로 원본을 옮긴다.

    익명 사진은 보관 기간을 짧게 두고 정리하게 되는데, 주인이 생긴 사진이
    `anonymous/` 프리픽스에 남아 있으면 그 정리에 함께 쓸려나간다.

    이동은 best-effort다 — 실패해도 기존 키로 계속 읽을 수 있으므로 소유권 이전을
    되돌리지 않는다. 다만 위 문제 때문에 실패는 ERROR로 남긴다.
    """
    old_key = analysis.image_s3_key
    if not old_key or not storage.is_configured():
        return

    new_key = storage.owner_key(analysis.user_id, str(analysis.pk), old_key)
    if new_key == old_key:
        return

    try:
        storage.move(old_key, new_key)
    except Exception:  # noqa: BLE001
        logger.exception(
            "소유권 이전 후 사진 이동 실패 (익명 프리픽스에 남음): analysis=%s %s → %s",
            analysis.pk,
            old_key,
            new_key,
        )
        return

    analysis.image_s3_key = new_key
    analysis.save(update_fields=["image_s3_key"])
