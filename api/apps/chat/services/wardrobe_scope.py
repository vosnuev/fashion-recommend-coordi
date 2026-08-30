"""개인 옷장 해시태그·기본 카테고리 채팅 범위를 실행 시점 스냅샷으로 고정한다."""

from apps.chat.models import ChatIdentity
from apps.wardrobe.models import WardrobeHashtag, WardrobeItem


class WardrobeScopeError(ValueError):
    code = "WARDROBE_SCOPE_INVALID"
    status_code = 400


class WardrobeScopeForbidden(WardrobeScopeError):
    code = "WARDROBE_SCOPE_FORBIDDEN"
    status_code = 403


class WardrobeScopeEmpty(WardrobeScopeError):
    code = "WARDROBE_SCOPE_EMPTY"
    status_code = 409


def build_wardrobe_scope_snapshot(
    *,
    identity: ChatIdentity,
    scope: dict[str, object] | None,
) -> dict[str, object]:
    if not scope:
        return {}
    user = identity.user
    if user is None:
        raise WardrobeScopeForbidden("개인 옷장 범위 추천은 로그인이 필요합니다.")

    system_categories = tuple(dict.fromkeys(scope.get("system_categories") or []))
    hashtag_ids = tuple(dict.fromkeys(str(value) for value in (scope.get("hashtag_ids") or [])))
    match_mode = str(scope.get("match_mode") or "REQUIRED")

    hashtags = list(WardrobeHashtag.objects.filter(pk__in=hashtag_ids, user=user))
    if len(hashtags) != len(hashtag_ids):
        raise WardrobeScopeForbidden("선택한 해시태그에 접근할 수 없습니다.")

    queryset = WardrobeItem.objects.filter(
        user=user,
        confirmed=True,
        added_to_closet_at__isnull=False,
    )
    if system_categories:
        queryset = queryset.filter(category_large__in=system_categories)
    if hashtag_ids:
        queryset = queryset.filter(wardrobe_hashtags__id__in=hashtag_ids)
    candidates = list(queryset.distinct().order_by("-added_to_closet_at", "id"))
    if not candidates:
        raise WardrobeScopeEmpty("선택한 옷장 범위에 추천 가능한 옷이 없습니다.")

    return {
        "schema_version": "1.0",
        "system_categories": list(system_categories),
        "hashtags": [
            {"id": str(row.pk), "name": row.name, "position": row.position}
            for row in sorted(hashtags, key=lambda row: (row.position, str(row.pk)))
        ],
        "match_mode": match_mode,
        "candidate_item_ids": [str(item.pk) for item in candidates],
        "candidate_items": [
            {
                "id": str(item.pk),
                "name": item.item_name,
                "category_large": item.category_large,
                "category_small": item.category_small,
                "color": item.color,
            }
            for item in candidates
        ],
    }
