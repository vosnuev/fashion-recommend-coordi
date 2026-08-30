"""변경 버전만 확인해 재사용하는 채팅 추천 컨텍스트 서비스."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.chat.models import (
    ChatIdentity,
    ChatMessage,
    ChatRun,
    ChatSession,
    PersonaProfile,
)
from apps.chat.services.behavior_signals import load_user_behavior_signals
from apps.chat.services.context_cache import JsonCache, RedisJsonCache
from apps.chat.services.personalization_snapshot import (
    load_personalization_source_versions,
)
from apps.users.constants import effective_category_budgets
from apps.users.models import BodyMeasurement
from apps.users.services.pursuit import get_pursuit
from apps.weather.services import get_current_weather, resolve_coordinates

_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ChatContext:
    payload: dict[str, Any]
    fingerprint: str
    base_fingerprint: str
    cache_hit: bool


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _canonical(value: Any) -> str:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def normalize_request(text: str) -> str:
    return _SPACE_RE.sub(" ", text.strip()).casefold()


def _serialize_measurement(measurement: BodyMeasurement | None) -> dict | None:
    if measurement is None:
        return None
    fields = (
        "gender",
        "height",
        "weight",
        "chest",
        "waist",
        "hip",
        "thigh",
        "calf",
        "arm",
        "shoulder",
    )
    return {
        field: float(value) if isinstance(value, Decimal) else (value or None)
        for field in fields
        if (value := getattr(measurement, field)) is not None
    }


def get_active_persona(session: ChatSession) -> PersonaProfile:
    if session.persona_profile_id:
        persona = PersonaProfile.objects.filter(pk=session.persona_profile_id).first()
        if persona is not None:
            return persona
    persona = PersonaProfile.objects.filter(is_active=True).first()
    if persona is not None:
        ChatSession.objects.filter(pk=session.pk, persona_profile__isnull=True).update(
            persona_profile=persona
        )
        session.persona_profile = persona
        return persona
    persona = PersonaProfile.objects.create(
        code="default-stylist",
        name="AI 스타일리스트",
        prompt_config={
            "tone": "친절하고 근거 중심",
            "style_philosophy": "사용자 조건을 존중하고 실제 사용할 수 있는 아이템만 설명",
            "description_length": "medium",
        },
        version=1,
        is_active=True,
    )
    ChatSession.objects.filter(pk=session.pk, persona_profile__isnull=True).update(
        persona_profile=persona
    )
    session.persona_profile = persona
    return persona


class ChatContextService:
    """비싼 프로필 본문은 버전이 같을 때 Redis에서 재사용한다."""

    def __init__(self, *, cache: JsonCache | None = None) -> None:
        self.cache = cache or RedisJsonCache()

    def build(
        self,
        *,
        session: ChatSession,
        request_message: ChatMessage,
        current_run: ChatRun,
    ) -> ChatContext:
        if request_message.session_id != session.id:
            raise ValueError("요청 메시지가 채팅 세션에 속하지 않습니다.")
        if current_run.session_id != session.id:
            raise ValueError("현재 실행이 채팅 세션에 속하지 않습니다.")
        if current_run.request_message_id != request_message.id:
            raise ValueError("현재 실행의 요청 메시지가 일치하지 않습니다.")

        persona = get_active_persona(session)
        location = request_message.metadata.get("location") or {}
        lat, lon = resolve_coordinates(location.get("lat"), location.get("lon"))
        weather = _json_safe(get_current_weather(lat, lon))
        as_of = timezone.localdate()
        source_versions = self._source_versions(
            identity=session.identity,
            persona=persona,
            weather=weather,
            as_of=as_of,
        )
        base_fingerprint = fingerprint(source_versions)
        cache_key = (
            f"{settings.CHAT_CONTEXT_CACHE_PREFIX}:base:"
            f"{session.identity_id}:{base_fingerprint}"
        )
        base_context = self.cache.get(cache_key)
        cache_hit = base_context is not None
        if base_context is None:
            base_context = self._build_base_context(
                identity=session.identity,
                persona=persona,
                weather=weather,
                source_versions=source_versions,
            )
            self.cache.set(
                cache_key,
                base_context,
                settings.CHAT_CONTEXT_CACHE_TTL_SECONDS,
            )

        behavior_signals = None
        if session.identity.user_id is not None:
            behavior_signals = load_user_behavior_signals(
                identity=session.identity,
                current_run=current_run,
                as_of=as_of,
            )
        recent_messages = self._recent_messages(session, request_message)
        payload = {
            **base_context,
            "behavior_signals": behavior_signals,
            "session": {
                "id": str(session.id),
                "mode": session.mode,
                "conditions": _json_safe(session.context_state or {}),
                "conversation_summary": session.conversation_summary,
            },
            "recent_messages": recent_messages,
            "current_request": request_message.content,
        }
        complete_fingerprint = fingerprint(
            {
                "request": normalize_request(request_message.content),
                "mode": session.mode,
                "base_fingerprint": base_fingerprint,
                "session_conditions": session.context_state or {},
                "persona_version": persona.version,
                "behavior_signals": behavior_signals,
                "personalization_reference": current_run.personalization_snapshot,
            }
        )
        return ChatContext(
            payload=payload,
            fingerprint=complete_fingerprint,
            base_fingerprint=base_fingerprint,
            cache_hit=cache_hit,
        )

    @staticmethod
    def _source_versions(
        *,
        identity: ChatIdentity,
        persona: PersonaProfile,
        weather: dict[str, Any],
        as_of: date,
    ) -> dict[str, Any]:
        personalization = load_personalization_source_versions(
            identity=identity,
            as_of=as_of,
        )

        return _json_safe(
            {
                **personalization,
                "weather": {
                    "region": weather.get("region"),
                    "observed_at": weather.get("observed_at"),
                    "is_stale": weather.get("is_stale"),
                },
                "goldenset_dataset_version": settings.CHAT_GOLDENSET_DATASET_VERSION,
                "product_index_version": settings.CHAT_PRODUCT_INDEX_VERSION,
                "persona": {"id": str(persona.id), "version": persona.version},
            }
        )

    @staticmethod
    def _build_base_context(
        *,
        identity: ChatIdentity,
        persona: PersonaProfile,
        weather: dict[str, Any],
        source_versions: dict[str, Any],
    ) -> dict[str, Any]:
        pursuit = None
        body = None
        category_budgets = effective_category_budgets(None)
        if identity.user_id is not None:
            pursuit = get_pursuit(identity.user)
            body = _serialize_measurement(
                BodyMeasurement.objects.filter(user_id=identity.user_id).first()
            )
            category_budgets = effective_category_budgets(
                identity.user.category_budgets
            )
        return _json_safe(
            {
                "profile": {
                    "personalized": identity.user_id is not None,
                    "pursuit": pursuit,
                    "body": body,
                    "category_budgets": category_budgets,
                },
                "weather": weather,
                "source_versions": source_versions,
                "persona": {
                    "id": str(persona.id),
                    "code": persona.code,
                    "name": persona.name,
                    "version": persona.version,
                    "prompt_config": persona.prompt_config,
                },
            }
        )

    @staticmethod
    def _recent_messages(
        session: ChatSession,
        request_message: ChatMessage,
    ) -> list[dict[str, Any]]:
        rows = list(
            session.messages.filter(sequence__lt=request_message.sequence)
            .order_by("-sequence")
            .values("sequence", "role", "content")[
                : settings.CHAT_CONTEXT_RECENT_MESSAGES
            ]
        )
        rows.reverse()
        return rows
