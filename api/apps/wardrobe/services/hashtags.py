"""개인 옷장 전용 해시태그의 생성·연결·정렬 생명주기."""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from apps.wardrobe import taxonomy as T
from apps.wardrobe.models import WardrobeHashtag, WardrobeItem, WardrobeItemHashtag

User = get_user_model()


@dataclass(frozen=True)
class HashtagServiceError(Exception):
    code: str
    detail: str
    status_code: int = 400

    def __str__(self) -> str:
        return self.detail


def normalize_and_validate_name(value: str) -> tuple[str, str]:
    display_name, normalized_name = WardrobeHashtag.normalize_name(value)
    if not display_name:
        raise HashtagServiceError(
            "HASHTAG_NAME_REQUIRED",
            "해시태그 이름을 입력해 주세요.",
        )
    if len(display_name) > 30:
        raise HashtagServiceError(
            "HASHTAG_NAME_TOO_LONG",
            "해시태그 이름은 30자 이하여야 합니다.",
        )
    return display_name, normalized_name


def filter_payloads(user) -> dict[str, list]:
    """고정 기본 카테고리와 사용자의 옷장 해시태그를 개수와 함께 반환한다."""

    closet_filter = Q(added_to_closet_at__isnull=False)
    system_counts = {
        row["category_large"]: row["item_count"]
        for row in WardrobeItem.objects.filter(user=user)
        .filter(closet_filter)
        .values("category_large")
        .annotate(item_count=Count("id"))
    }
    system_categories = [
        {
            "id": f"system:{name}",
            "type": "SYSTEM",
            "name": name,
            "position": position,
            "item_count": system_counts.get(name, 0),
            "mutable": False,
        }
        for position, name in enumerate(T.CATEGORY_LARGE)
    ]
    hashtags = list(
        WardrobeHashtag.objects.filter(user=user).annotate(
            item_count=Count(
                "item_links",
                filter=Q(
                    item_links__wardrobe_item__user=user,
                    item_links__wardrobe_item__added_to_closet_at__isnull=False,
                ),
                distinct=True,
            )
        )
    )
    return {"system_categories": system_categories, "hashtags": hashtags}


def _validate_closet_items(*, user, item_ids: set) -> dict:
    if not item_ids:
        return {}
    items = {
        item.pk: item
        for item in WardrobeItem.objects.filter(pk__in=item_ids).only(
            "id",
            "user_id",
            "added_to_closet_at",
        )
    }
    if set(items) != item_ids:
        raise HashtagServiceError(
            "WARDROBE_ITEM_NOT_FOUND",
            "옷장 아이템을 찾을 수 없습니다.",
            404,
        )
    if any(item.user_id != user.pk for item in items.values()):
        raise HashtagServiceError(
            "WARDROBE_ITEM_FORBIDDEN",
            "이 옷장 아이템에 접근할 수 없습니다.",
            403,
        )
    if any(item.added_to_closet_at is None for item in items.values()):
        raise HashtagServiceError(
            "WARDROBE_ITEM_NOT_FOUND",
            "옷장 아이템을 찾을 수 없습니다.",
            404,
        )
    return items


def _next_position(user) -> int:
    maximum = WardrobeHashtag.objects.filter(user=user).aggregate(value=Max("position"))[
        "value"
    ]
    return 0 if maximum is None else maximum + 1


def _compact_positions(user) -> None:
    now = timezone.now()
    hashtags = list(
        WardrobeHashtag.objects.filter(user=user).order_by(
            "position",
            "created_at",
            "id",
        )
    )
    changed = []
    for position, hashtag in enumerate(hashtags):
        if hashtag.position == position:
            continue
        hashtag.position = position
        hashtag.updated_at = now
        changed.append(hashtag)
    if changed:
        WardrobeHashtag.objects.bulk_update(changed, ["position", "updated_at"])


def _get_or_create_hashtag(*, user, display_name: str, normalized_name: str):
    hashtag = WardrobeHashtag.objects.filter(
        user=user,
        normalized_name=normalized_name,
    ).first()
    if hashtag is not None:
        return hashtag, False
    return (
        WardrobeHashtag.objects.create(
            user=user,
            name=display_name,
            position=_next_position(user),
        ),
        True,
    )


@transaction.atomic
def create_hashtag_with_items(*, user, name: str, item_ids: list):
    display_name, normalized_name = normalize_and_validate_name(name)
    unique_item_ids = set(item_ids)
    if not unique_item_ids:
        raise HashtagServiceError(
            "HASHTAG_ITEM_REQUIRED",
            "해시태그를 붙일 옷을 한 벌 이상 선택해 주세요.",
        )
    User.objects.select_for_update().only("pk").get(pk=user.pk)
    _validate_closet_items(user=user, item_ids=unique_item_ids)
    hashtag, created = _get_or_create_hashtag(
        user=user,
        display_name=display_name,
        normalized_name=normalized_name,
    )
    WardrobeItemHashtag.objects.bulk_create(
        [
            WardrobeItemHashtag(wardrobe_item_id=item_id, hashtag=hashtag)
            for item_id in unique_item_ids
        ],
        ignore_conflicts=True,
    )
    return hashtag, created


@transaction.atomic
def rename_hashtag(*, user, hashtag: WardrobeHashtag, name: str) -> WardrobeHashtag:
    display_name, normalized_name = normalize_and_validate_name(name)
    User.objects.select_for_update().only("pk").get(pk=user.pk)
    locked = WardrobeHashtag.objects.select_for_update().get(pk=hashtag.pk)
    if WardrobeHashtag.objects.filter(
        user=user,
        normalized_name=normalized_name,
    ).exclude(pk=locked.pk).exists():
        raise HashtagServiceError(
            "HASHTAG_NAME_DUPLICATE",
            "이미 사용 중인 해시태그 이름입니다.",
        )
    if locked.name == display_name and locked.normalized_name == normalized_name:
        return locked
    locked.name = display_name
    locked.normalized_name = normalized_name
    locked.save(update_fields=["name", "normalized_name", "updated_at"])
    return locked


@transaction.atomic
def delete_hashtag(*, user, hashtag: WardrobeHashtag) -> None:
    User.objects.select_for_update().only("pk").get(pk=user.pk)
    locked = WardrobeHashtag.objects.select_for_update().get(pk=hashtag.pk)
    locked.delete()
    _compact_positions(user)


@transaction.atomic
def update_hashtag_items(
    *,
    user,
    hashtag: WardrobeHashtag,
    add_item_ids: list,
    remove_item_ids: list,
) -> dict:
    add_ids = set(add_item_ids)
    remove_ids = set(remove_item_ids)
    if add_ids & remove_ids:
        raise HashtagServiceError(
            "HASHTAG_ASSIGNMENT_CONFLICT",
            "같은 옷을 동시에 추가하고 제거할 수 없습니다.",
        )

    User.objects.select_for_update().only("pk").get(pk=user.pk)
    locked = WardrobeHashtag.objects.select_for_update().get(pk=hashtag.pk)
    _validate_closet_items(user=user, item_ids=add_ids | remove_ids)

    existing_add_ids = set(
        WardrobeItemHashtag.objects.filter(
            hashtag=locked,
            wardrobe_item_id__in=add_ids,
        ).values_list("wardrobe_item_id", flat=True)
    )
    actual_add_ids = add_ids - existing_add_ids
    WardrobeItemHashtag.objects.bulk_create(
        [
            WardrobeItemHashtag(wardrobe_item_id=item_id, hashtag=locked)
            for item_id in actual_add_ids
        ],
        ignore_conflicts=True,
    )

    actual_remove_ids = set(
        WardrobeItemHashtag.objects.filter(
            hashtag=locked,
            wardrobe_item_id__in=remove_ids,
        ).values_list("wardrobe_item_id", flat=True)
    )
    WardrobeItemHashtag.objects.filter(
        hashtag=locked,
        wardrobe_item_id__in=actual_remove_ids,
    ).delete()

    item_count = WardrobeItemHashtag.objects.filter(hashtag=locked).count()
    deleted = item_count == 0
    hashtag_id = str(locked.pk)
    if deleted:
        locked.delete()
        _compact_positions(user)
    return {
        "hashtag_id": hashtag_id,
        "added_item_ids": sorted(str(item_id) for item_id in actual_add_ids),
        "removed_item_ids": sorted(str(item_id) for item_id in actual_remove_ids),
        "item_count": item_count,
        "deleted": deleted,
    }


@transaction.atomic
def replace_item_hashtags(*, user, item_id, names: list[str]) -> tuple:
    normalized_inputs: dict[str, str] = {}
    for value in names:
        display_name, normalized_name = normalize_and_validate_name(value)
        normalized_inputs.setdefault(normalized_name, display_name)

    item = WardrobeItem.objects.filter(pk=item_id).only(
        "id",
        "user_id",
        "added_to_closet_at",
    ).first()
    if item is None:
        raise HashtagServiceError(
            "WARDROBE_ITEM_NOT_FOUND",
            "옷장 아이템을 찾을 수 없습니다.",
            404,
        )
    if item.user_id != user.pk:
        raise HashtagServiceError(
            "WARDROBE_ITEM_FORBIDDEN",
            "이 옷장 아이템에 접근할 수 없습니다.",
            403,
        )
    if item.added_to_closet_at is None:
        raise HashtagServiceError(
            "WARDROBE_ITEM_NOT_FOUND",
            "옷장 아이템을 찾을 수 없습니다.",
            404,
        )

    User.objects.select_for_update().only("pk").get(pk=user.pk)
    WardrobeItem.objects.select_for_update().only("id").get(pk=item.pk)
    existing_by_name = {
        hashtag.normalized_name: hashtag
        for hashtag in WardrobeHashtag.objects.filter(
            user=user,
            normalized_name__in=normalized_inputs,
        )
    }
    selected = []
    for normalized_name, display_name in normalized_inputs.items():
        hashtag = existing_by_name.get(normalized_name)
        if hashtag is None:
            hashtag, _created = _get_or_create_hashtag(
                user=user,
                display_name=display_name,
                normalized_name=normalized_name,
            )
        selected.append(hashtag)

    selected_ids = {hashtag.pk for hashtag in selected}
    existing_ids = set(
        WardrobeItemHashtag.objects.filter(wardrobe_item=item).values_list(
            "hashtag_id",
            flat=True,
        )
    )
    WardrobeItemHashtag.objects.bulk_create(
        [
            WardrobeItemHashtag(wardrobe_item=item, hashtag_id=hashtag_id)
            for hashtag_id in selected_ids - existing_ids
        ],
        ignore_conflicts=True,
    )
    WardrobeItemHashtag.objects.filter(
        wardrobe_item=item,
        hashtag_id__in=existing_ids - selected_ids,
    ).delete()
    _delete_orphans_and_compact(user)
    hashtags = tuple(
        WardrobeHashtag.objects.filter(pk__in=selected_ids).order_by(
            "position",
            "created_at",
            "id",
        )
    )
    return item, hashtags


def _delete_orphans_and_compact(user) -> int:
    orphan_ids = list(
        WardrobeHashtag.objects.filter(user=user, item_links__isnull=True).values_list(
            "pk",
            flat=True,
        )
    )
    if orphan_ids:
        WardrobeHashtag.objects.filter(pk__in=orphan_ids).delete()
        _compact_positions(user)
    return len(orphan_ids)


@transaction.atomic
def prune_orphan_hashtags(*, user) -> int:
    User.objects.select_for_update().only("pk").get(pk=user.pk)
    return _delete_orphans_and_compact(user)


@transaction.atomic
def reorder_hashtags(*, user, hashtag_ids: list) -> tuple:
    if len(set(hashtag_ids)) != len(hashtag_ids):
        raise HashtagServiceError(
            "HASHTAG_IDS_DUPLICATE",
            "해시태그 UUID를 중복해서 보낼 수 없습니다.",
        )
    User.objects.select_for_update().only("pk").get(pk=user.pk)
    current = list(
        WardrobeHashtag.objects.select_for_update()
        .filter(user=user)
        .order_by("position", "created_at", "id")
    )
    current_by_id = {hashtag.pk: hashtag for hashtag in current}
    if set(current_by_id) != set(hashtag_ids):
        raise HashtagServiceError(
            "HASHTAG_ORDER_SET_MISMATCH",
            "현재 해시태그 전체 순서와 요청이 일치하지 않습니다.",
        )
    now = timezone.now()
    ordered = []
    for position, hashtag_id in enumerate(hashtag_ids):
        hashtag = current_by_id[hashtag_id]
        hashtag.position = position
        hashtag.updated_at = now
        ordered.append(hashtag)
    if ordered:
        WardrobeHashtag.objects.bulk_update(ordered, ["position", "updated_at"])
    return tuple(ordered)
