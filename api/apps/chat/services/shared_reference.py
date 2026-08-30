"""공유 옷장 아이템을 채팅 실행의 불변 참조 스냅샷으로 변환한다."""

from __future__ import annotations

import re

from django.conf import settings
from django.utils import timezone

from apps.chat.models import ChatIdentity
from apps.wardrobe.models import (
    SharedWardrobeItem,
    SharedWardrobeMember,
    WardrobeItem,
)
from apps.wardrobe.services.reference_eligibility import (
    REFERENCE_UNAVAILABLE_NOT_CONFIRMED,
    resolve_reference_eligibilities,
    resolve_wardrobe_vector_readiness,
)

REFERENCE_SCHEMA_VERSION = "1.0"
REFERENCE_TYPE_SHARED_WARDROBE_ITEM = "SHARED_WARDROBE_ITEM"
REFERENCE_TYPE_WARDROBE_ITEM = "WARDROBE_ITEM"
AUTO_USERNAME_RE = re.compile(r"^(email|naver|kakao|google|apple)_")


class SharedReferenceError(RuntimeError):
    code = "REFERENCE_ITEM_INVALID"


class SharedReferenceNotFound(SharedReferenceError):
    code = "REFERENCE_ITEM_NOT_FOUND"


class SharedReferenceForbidden(SharedReferenceError):
    code = "REFERENCE_ITEM_FORBIDDEN"


class SharedReferenceUnavailable(SharedReferenceError):
    code = "REFERENCE_ITEM_NOT_READY"


def _owner_display_name(user) -> str:
    """공유 옷 등록자의 실행 시점 표시명을 내부 로그인 ID 없이 고정한다."""

    if user is None:
        return "멤버"
    nickname = (user.nickname or "").strip()
    if nickname and not AUTO_USERNAME_RE.match(nickname):
        return nickname
    email = (user.email or "").strip()
    if email:
        return email.split("@", 1)[0]
    username = (user.username or "").strip()
    if username and not AUTO_USERNAME_RE.match(username):
        return username
    return "멤버"


def _snapshot_item(
    *,
    item: WardrobeItem,
    reference_type: str,
    owner_name: str,
    room_name: str = "",
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "schema_version": REFERENCE_SCHEMA_VERSION,
        "type": reference_type,
        "wardrobe_item_id": str(item.pk),
        "qdrant_collection": settings.QDRANT_WARDROBE_COLLECTION,
        "qdrant_point_id": str(item.pk),
        "embedding_version": item.embedding_version,
        "image_s3_key": item.s3_key,
        "owner_name": owner_name,
        "room_name": room_name,
        "captured_at": timezone.now().isoformat(),
        "item": {
            "item_name": item.item_name,
            "category_large": item.category_large,
            "category_small": item.category_small,
            "season": list(item.season),
            "style": list(item.style),
            "color": item.color,
            "pattern": item.pattern,
            "fit": item.fit,
            "material": item.material,
            "sleeve": item.sleeve,
            "length": item.length,
            "usage": list(item.usage),
            "layer_role": item.layer_role,
            "layer_order": item.layer_order,
        },
    }
    if extra:
        snapshot.update(extra)
    return snapshot


def build_reference_snapshot(
    *,
    identity: ChatIdentity,
    reference: dict[str, object] | None,
) -> dict[str, object]:
    """참조 권한을 확인하고 이후 검색이 사용할 실행 시점 메타를 고정한다.

    벡터 자체는 DB에 복제하지 않는다. Qdrant point는 WardrobeItem UUID와 같으므로
    컬렉션과 point ID, 임베딩 버전만 저장한다.
    """

    if not reference:
        return {}
    reference_type = reference.get("type")
    if reference_type not in {
        REFERENCE_TYPE_SHARED_WARDROBE_ITEM,
        REFERENCE_TYPE_WARDROBE_ITEM,
    }:
        raise SharedReferenceError("지원하지 않는 채팅 참조 유형입니다.")
    if (
        identity.identity_type != ChatIdentity.IdentityType.MEMBER
        or not identity.user_id
    ):
        raise SharedReferenceForbidden(
            "옷장 아이템 참조는 로그인한 회원만 사용할 수 있습니다."
        )

    if reference_type == REFERENCE_TYPE_WARDROBE_ITEM:
        item = (
            WardrobeItem.objects.filter(
                pk=reference.get("wardrobe_item_id"),
                user_id=identity.user_id,
            )
            .first()
        )
        if item is None:
            raise SharedReferenceNotFound("내 옷장 아이템을 찾을 수 없습니다.")
        if not item.confirmed:
            raise SharedReferenceUnavailable(
                "사용자가 확정한 옷장 아이템만 참조할 수 있습니다."
            )
        readiness = resolve_wardrobe_vector_readiness(
            [item],
            enqueue_missing=True,
        )
        if readiness.get(str(item.pk)) is not True:
            raise SharedReferenceUnavailable(
                "옷 이미지의 벡터 처리가 끝난 뒤 다시 시도해 주세요."
            )
        return _snapshot_item(
            item=item,
            reference_type=REFERENCE_TYPE_WARDROBE_ITEM,
            owner_name="내 옷",
        )

    shared_item_id = reference.get("shared_item_id")
    shared_item = (
        SharedWardrobeItem.objects.select_related(
            "room",
            "wardrobe_item",
            "registered_by",
        )
        .filter(pk=shared_item_id)
        .first()
    )
    if shared_item is None:
        raise SharedReferenceNotFound("공유 옷장 아이템을 찾을 수 없습니다.")
    if not SharedWardrobeMember.objects.filter(
        room_id=shared_item.room_id,
        user_id=identity.user_id,
    ).exists():
        raise SharedReferenceForbidden("참여 중인 공유 옷장의 아이템만 참조할 수 있습니다.")
    item = shared_item.wardrobe_item
    eligibility = resolve_reference_eligibilities(
        [shared_item],
        enqueue_missing=True,
    )[str(shared_item.pk)]
    if eligibility.unavailable_reason == REFERENCE_UNAVAILABLE_NOT_CONFIRMED:
        raise SharedReferenceUnavailable("사용자가 확정한 공유 옷만 참조할 수 있습니다.")
    if not eligibility.eligible:
        raise SharedReferenceUnavailable(
            "공유 옷 이미지의 벡터 처리가 끝난 뒤 다시 시도해 주세요."
        )

    return _snapshot_item(
        item=item,
        reference_type=REFERENCE_TYPE_SHARED_WARDROBE_ITEM,
        owner_name=_owner_display_name(shared_item.registered_by),
        room_name=shared_item.room.title,
        extra={
            "shared_item_id": str(shared_item.pk),
            "room_id": str(shared_item.room_id),
            "source_status": shared_item.status,
        },
    )
