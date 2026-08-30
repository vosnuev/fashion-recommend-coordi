"""
상품 태깅 공용 패키지 (provider 중립).

- base            : 태그 체계 상수, JSON 스키마, 프롬프트, 병합/파싱 유틸 (외부 의존 없음)
- openai_tagger   : OpenAI 동기 태거 (이미지 auto 재시도 지원)
- openai_batch    : OpenAI Batch API 요청/응답 헬퍼 (DB 의존 없음)
- claude_tagger   : Claude Agent SDK 태거 (구독제 계정 OAuth 토큰 사용)

provider 선택은 각 collector의 환경변수로 제어한다.
모든 태거는 동일한 인터페이스를 가진다:
    tag(title, category_large, category_small, naver_categories, rule_attrs, image_url=None)
      -> (tags: dict, meta: dict)
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from util.tagging.claude_tagger import ClaudeTagger
    from util.tagging.openai_tagger import OpenAITagger

from util.tagging.base import (  # noqa: F401 (re-export)
    LAYER_ROLES,
    SEASONS,
    STYLES,
    merge_tags,
    parse_json_text,
)

PROVIDER_OPENAI = "openai"
PROVIDER_CLAUDE = "claude"


def get_provider(
    env_name: str = "NAVER_TAGGING_PROVIDER",
    default: str = PROVIDER_OPENAI,
) -> str:
    """환경변수에서 태깅 provider를 읽고 검증한다."""
    provider = os.getenv(env_name, default).strip().lower()
    if provider not in (PROVIDER_OPENAI, PROVIDER_CLAUDE):
        raise ValueError(
            f"{env_name} 값이 올바르지 않습니다: {provider} (openai | claude)"
        )
    return provider


def get_sync_tagger(provider: str | None = None) -> OpenAITagger | ClaudeTagger:
    """지정한 provider 또는 네이버 기본 환경변수에 맞는 동기 태거를 반환한다."""
    if provider is None:
        provider = get_provider()
    else:
        provider = provider.strip().lower()
        if provider not in (PROVIDER_OPENAI, PROVIDER_CLAUDE):
            raise ValueError(
                f"tagging provider 값이 올바르지 않습니다: {provider} "
                "(openai | claude)"
            )
    if provider == PROVIDER_CLAUDE:
        from util.tagging.claude_tagger import ClaudeTagger

        return ClaudeTagger()
    from util.tagging.openai_tagger import OpenAITagger

    return OpenAITagger()
