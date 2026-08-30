"""회원의 마지막 스타일리스트 선택 저장과 최초 선택 폴백."""

from __future__ import annotations

from collections.abc import Sequence

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.chat.models import (
    MemberStylistSelection,
    validate_member_last_selected_persona_ids,
)
from apps.chat.services.stylist_personas import load_stylist_personas


class MemberStylistSelectionError(RuntimeError):
    code = "MEMBER_STYLIST_SELECTION_INVALID"


class MemberAuthenticationRequired(MemberStylistSelectionError):
    code = "MEMBER_AUTHENTICATION_REQUIRED"


def _member_id(user) -> object:
    if (
        user is None
        or not getattr(user, "is_authenticated", False)
        or getattr(user, "pk", None) is None
    ):
        raise MemberAuthenticationRequired(
            "마지막 스타일리스트 선택을 저장하려면 로그인이 필요합니다."
        )
    return user.pk


def default_member_persona_ids() -> tuple[str, ...]:
    """선택 이력이 없는 회원에게 적용할 제품 기본값을 반환한다."""

    catalog = load_stylist_personas()
    catalog.get("minimal")
    return ("minimal",)


def get_member_last_persona_ids(user) -> tuple[str, ...]:
    """저장 행을 만들지 않고 회원의 마지막 선택 또는 최초 기본값을 조회한다."""

    user_id = _member_id(user)
    stored = (
        MemberStylistSelection.objects.filter(user_id=user_id)
        .values_list("last_selected_persona_ids", flat=True)
        .first()
    )
    if stored is None:
        return default_member_persona_ids()
    return tuple(stored)


@transaction.atomic
def save_member_last_persona_ids(
    user,
    persona_ids: Sequence[str],
) -> tuple[MemberStylistSelection, bool]:
    """회원당 한 행을 생성하거나 갱신하고 생성 여부를 함께 반환한다."""

    user_id = _member_id(user)
    if isinstance(persona_ids, (str, bytes)) or not isinstance(
        persona_ids,
        Sequence,
    ):
        raise MemberStylistSelectionError(
            "스타일리스트 선택값은 문자열 배열이어야 합니다."
        )

    normalized_ids = list(persona_ids)
    try:
        validate_member_last_selected_persona_ids(normalized_ids)
    except ValidationError as exc:
        raise MemberStylistSelectionError("; ".join(exc.messages)) from exc

    selection, created = MemberStylistSelection.objects.update_or_create(
        user_id=user_id,
        defaults={"last_selected_persona_ids": normalized_ids},
    )
    return selection, created
