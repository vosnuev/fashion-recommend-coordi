"""ChatRun 접수 시점의 개인화 데이터 기준 정보를 생성한다."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from django.db.models import Count, Max
from django.utils import timezone

from apps.chat.models import ChatIdentity
from apps.recommend.models import (
    ProductClickEvent,
    RecommendationFeedback,
    RecommendationResult,
    SavedOutfit,
)
from apps.style_calendar.models import CalendarEntry, CalendarWardrobeItem
from apps.users.constants import effective_category_budgets
from apps.users.models import BodyMeasurement, Pursuit
from apps.wardrobe.models import WardrobeItem

PERSONALIZATION_SNAPSHOT_SCHEMA_VERSION = "1.0"


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _fingerprint(value: object) -> str:
    canonical = json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_personalization_source_versions(
    *,
    identity: ChatIdentity,
    as_of: date,
) -> dict[str, Any]:
    """개인화 원천별 변경 기준을 원본 데이터 없이 조회한다."""

    profile: dict[str, Any] = {
        "identity_type": identity.identity_type,
        "personalized": identity.user_id is not None,
        "category_budgets_fingerprint": _fingerprint(
            effective_category_budgets(None)
        ),
        "pursuit_updated_at": None,
        "body_updated_at": None,
    }
    wardrobe: dict[str, Any] = {"count": 0, "updated_at": None}
    behavior: dict[str, Any] = {
        "as_of_date": as_of,
        "recommendations": {"count": 0, "updated_at": None},
        "recommendation_feedback": {"count": 0, "updated_at": None},
        "saved_outfits": {"count": 0, "updated_at": None},
        "product_clicks": {
            "count": 0,
            "created_at": None,
            "engagement_recorded_at": None,
        },
        "calendar_entries": {"count": 0, "updated_at": None},
        "calendar_item_links": {"count": 0, "updated_at": None},
    }
    if identity.user_id is None:
        return _json_safe(
            {"profile": profile, "wardrobe": wardrobe, "behavior": behavior}
        )

    budgets = effective_category_budgets(identity.user.category_budgets)
    profile.update(
        {
            "category_budgets_fingerprint": _fingerprint(budgets),
            "pursuit_updated_at": (
                Pursuit.objects.filter(user_id=identity.user_id)
                .values_list("updated_at", flat=True)
                .first()
            ),
            "body_updated_at": (
                BodyMeasurement.objects.filter(user_id=identity.user_id)
                .values_list("updated_at", flat=True)
                .first()
            ),
        }
    )
    wardrobe = WardrobeItem.objects.filter(user_id=identity.user_id).aggregate(
        count=Count("id"),
        updated_at=Max("updated_at"),
    )
    behavior.update(
        {
            "recommendations": RecommendationResult.objects.filter(
                identity=identity
            ).aggregate(count=Count("id"), updated_at=Max("updated_at")),
            "recommendation_feedback": RecommendationFeedback.objects.filter(
                composition__result__identity=identity
            ).aggregate(count=Count("id"), updated_at=Max("updated_at")),
            "saved_outfits": SavedOutfit.objects.filter(
                user_id=identity.user_id
            ).aggregate(count=Count("id"), updated_at=Max("created_at")),
            "product_clicks": ProductClickEvent.objects.filter(
                user_id=identity.user_id
            ).aggregate(
                count=Count("id"),
                created_at=Max("created_at"),
                engagement_recorded_at=Max("engagement_recorded_at"),
            ),
            "calendar_entries": CalendarEntry.objects.filter(
                user_id=identity.user_id
            ).aggregate(count=Count("id"), updated_at=Max("updated_at")),
            "calendar_item_links": CalendarWardrobeItem.objects.filter(
                calendar__user_id=identity.user_id,
                wardrobe_item__user_id=identity.user_id,
            ).aggregate(count=Count("id"), updated_at=Max("updated_at")),
        }
    )
    return _json_safe(
        {"profile": profile, "wardrobe": wardrobe, "behavior": behavior}
    )


def build_personalization_snapshot(
    *,
    identity: ChatIdentity,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    """실행 생성 트랜잭션에서 저장할 불변 개인화 기준을 만든다."""

    captured_at = captured_at or timezone.now()
    as_of = timezone.localdate(captured_at)
    return {
        "schema_version": PERSONALIZATION_SNAPSHOT_SCHEMA_VERSION,
        "captured_at": captured_at.isoformat(),
        "as_of_date": as_of.isoformat(),
        "identity_type": identity.identity_type,
        "personalized": identity.user_id is not None,
        "sources": load_personalization_source_versions(
            identity=identity,
            as_of=as_of,
        ),
    }
