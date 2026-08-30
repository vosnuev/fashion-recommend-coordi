"""회원 탈퇴 — 계정과 그에 딸린 데이터를 지운다.

DB 행은 FK 의 CASCADE 가 알아서 따라온다(옷장·룩북·채팅·캘린더·추천).
**따라오지 않는 것이 둘 있다.**

  1. S3 객체 — 사진은 DB 밖에 있다. 행만 지우면 버킷에 영영 남는다.
  2. Qdrant 벡터 — 지우지 않으면 삭제된 옷이 다른 사람 추천 후보로 계속 잡힌다.

그래서 지우기 **전에** 키를 모아 두었다가 트랜잭션이 커밋된 뒤에 실제 삭제를 한다.
순서를 뒤집으면(먼저 S3 삭제 → DB 롤백) 사진만 사라지고 행은 남아 깨진 계정이 된다.

공유 옷장은 혼자 쓰는 데이터가 아니라 따로 다룬다 — 기존 '방 나가기'를 그대로 부른다.
방장 위임과 빈 방 폐쇄가 이미 거기 들어 있어, 탈퇴자가 방장이어도 남은 사람들의 방은 살아남는다.
"""

from __future__ import annotations

import logging
from typing import Iterable

from django.db import transaction

from apps.chat.models import ChatAttachment
from apps.chat.services import attachment_storage
from apps.lookbook.models import LookbookPost
from apps.lookbook.services import storage as lookbook_storage
from apps.wardrobe.models import (
    SharedWardrobeMember,
    WardrobeItem,
    WardrobeUploadJob,
)
from apps.wardrobe.services import shared_wardrobe as shared_service
from apps.wardrobe.services import storage as wardrobe_storage
from apps.wardrobe.services import vectors

logger = logging.getLogger(__name__)


@transaction.atomic
def withdraw(user) -> None:
    """계정을 삭제한다. 되돌릴 수 없다.

    실패하면 아무것도 지워지지 않는다(atomic) — 절반만 지워진 계정으로 남는 것이
    가장 나쁘다. 다만 커밋 뒤의 S3·벡터 정리는 실패해도 되돌리지 않는다:
    계정은 이미 사라졌고, 남은 것은 주인 없는 파일이라 다시 살릴 이유가 없다.
    """
    # 1. 공유 옷장에서 먼저 나간다. 내가 올린 옷은 함께 지운다 —
    #    화면이 "옷장에 등록한 옷과 사진이 사라진다"고 약속하고 받은 동의다.
    room_ids = list(
        SharedWardrobeMember.objects.filter(user=user).values_list("room_id", flat=True)
    )
    for room_id in room_ids:
        try:
            shared_service.leave_shared_room(user, str(room_id), delete_my_items=True)
        except ValueError:
            # 그 사이 방이 사라졌거나 이미 나간 상태 — 탈퇴를 막을 이유는 아니다.
            logger.warning("탈퇴 중 공유 옷장 퇴장 건너뜀 user=%s room=%s", user.pk, room_id)

    # 2. 지울 것을 미리 적어 둔다. user.delete() 뒤에는 조회할 방법이 없다.
    item_ids = list(WardrobeItem.objects.filter(user=user).values_list("id", flat=True))
    wardrobe_keys = _keys(WardrobeItem.objects.filter(user=user), "s3_key")
    # 업로드 원본은 아이템이 아니라 작업(job)에 달려 있다. 확정 전에 탈퇴하면
    # 원본만 버킷에 남으므로 함께 지운다.
    wardrobe_keys += _keys(
        WardrobeUploadJob.objects.filter(user=user), "source_s3_key"
    )
    lookbook_keys = _keys(LookbookPost.objects.filter(user=user), "image_s3_key")
    # 채팅은 user 를 직접 달지 않는다 — identity(게스트도 갖는 신원)를 거쳐 붙는다.
    attachment_keys = _keys(
        ChatAttachment.objects.filter(message__session__identity__user=user), "s3_key"
    )

    # 3. 벡터부터 끊는다. 남으면 지워진 옷이 남의 추천 후보로 계속 잡힌다.
    #    delete_item 은 이미 best-effort 라(내부에서 예외를 삼킨다) 여기서 또 감싸지 않는다.
    for item_id in item_ids:
        vectors.delete_item(item_id)

    # 4. 계정 삭제. 나머지 행은 FK CASCADE 가 따라온다.
    #    refresh 토큰(OutstandingToken)도 user FK 라 함께 사라지므로 따로 블랙리스트에
    #    올리지 않는다 — 살아 있는 access 토큰은 사용자 조회에서 걸려 401 이 된다.
    user_pk = user.pk
    user.delete()

    # 5. 파일은 커밋된 뒤에 지운다. 롤백되면 사진만 사라지고 계정은 남기 때문이다.
    transaction.on_commit(
        lambda: _delete_files(user_pk, wardrobe_keys, lookbook_keys, attachment_keys)
    )


def _keys(queryset, field: str) -> list[str]:
    """비어 있지 않은 S3 키만 뽑는다."""
    return [key for key in queryset.values_list(field, flat=True) if key]


def _delete_files(
    user_pk: int,
    wardrobe_keys: Iterable[str],
    lookbook_keys: Iterable[str],
    attachment_keys: Iterable[str],
) -> None:
    """버킷별로 지운다. 한 곳이 실패해도 나머지는 계속 지운다."""
    wardrobe_keys = list(wardrobe_keys)
    lookbook_keys = list(lookbook_keys)
    attachment_keys = list(attachment_keys)

    if wardrobe_keys:
        _swallow(lambda: wardrobe_storage.delete_objects(wardrobe_keys), "옷장", user_pk)
    if lookbook_keys:
        _swallow(lambda: lookbook_storage.delete_objects(lookbook_keys), "룩북", user_pk)
    for key in attachment_keys:
        # 첨부는 한 건씩 지우는 헬퍼밖에 없다.
        _swallow(lambda k=key: attachment_storage.delete_object(k), "채팅 첨부", user_pk)


def _swallow(run, label: str, user_pk: int) -> None:
    try:
        run()
    except Exception:  # noqa: BLE001 - 계정은 이미 지워졌다. 파일 정리 실패는 기록만 한다.
        logger.exception("탈퇴 후 %s 파일 삭제 실패 user=%s", label, user_pk)
