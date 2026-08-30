"""회원에게 노출할 선택형 스타일리스트 목록 응답을 구성한다."""

from __future__ import annotations

from typing import Any

from apps.chat.services import member_stylist_selections
from apps.chat.services.stylist_personas import load_stylist_personas


def get_member_stylist_catalog(user) -> dict[str, Any]:
    """내부 전략·프롬프트를 제외한 회원용 스타일리스트 목록을 반환한다."""

    catalog = load_stylist_personas()
    return {
        "schema_version": catalog.schema_version,
        "min_select": catalog.min_select,
        "max_select": catalog.max_select,
        "default_persona_ids": list(
            member_stylist_selections.default_member_persona_ids()
        ),
        "last_selected_persona_ids": list(
            member_stylist_selections.get_member_last_persona_ids(user)
        ),
        "stylists": [
            {
                "id": persona.id,
                "display_name": persona.display_name,
                "description": persona.description,
                "display_order": persona.display_order,
            }
            for persona in catalog.enabled_personas()
        ],
    }
