"""기존 OpenAI Responses 클라이언트를 재사용하는 페르소나 말투 어댑터."""

from __future__ import annotations

import json
import logging

from django.conf import settings

from apps.chat.services.openai_adapter import (
    ChatLLMConfigurationError,
    LLMUsage,
    OpenAIChatAdapter,
)
from apps.chat.services.persona_narration import (
    PERSONA_NARRATION_INSTRUCTIONS,
    PersonaNarrationConfigurationError,
    PersonaNarrationDraft,
    PersonaNarrationProviderError,
    PersonaNarrationRequest,
    ProviderNarration,
)

logger = logging.getLogger(__name__)


class OpenAIPersonaNarrator:
    provider = "openai"

    def __init__(self, *, chat_adapter: OpenAIChatAdapter | None = None) -> None:
        self.chat_adapter = chat_adapter or OpenAIChatAdapter()

    def generate_draft(
        self,
        request: PersonaNarrationRequest,
    ) -> ProviderNarration:
        model = settings.PERSONA_LLM_MODEL.strip()
        if not model:
            raise PersonaNarrationConfigurationError(
                "PERSONA_LLM_MODEL이 설정되지 않았습니다."
            )
        try:
            response = self.chat_adapter.client.responses.parse(
                model=model,
                instructions=PERSONA_NARRATION_INSTRUCTIONS,
                input=[
                    {
                        "role": "user",
                        "content": json.dumps(
                            request.payload(),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    }
                ],
                text_format=PersonaNarrationDraft,
                max_output_tokens=settings.PERSONA_LLM_MAX_OUTPUT_TOKENS,
                prompt_cache_key=(
                    "fashion-persona-narration:"
                    f"{settings.PERSONA_LLM_PROMPT_VERSION}:"
                    f"{request.persona_id}"
                ),
                store=False,
            )
        except PersonaNarrationConfigurationError:
            raise
        except ChatLLMConfigurationError as exc:
            raise PersonaNarrationConfigurationError(str(exc)) from exc
        except Exception as exc:
            logger.warning("OpenAI 말투 변환 실패: %s", type(exc).__name__)
            raise PersonaNarrationProviderError(
                "OpenAI 말투 변환 응답을 받을 수 없습니다."
            ) from exc

        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise PersonaNarrationProviderError(
                "OpenAI 말투 변환 구조화 응답이 비어 있습니다."
            )
        if not isinstance(parsed, PersonaNarrationDraft):
            try:
                parsed = PersonaNarrationDraft.model_validate(parsed)
            except Exception as exc:
                raise PersonaNarrationProviderError(
                    "OpenAI 말투 변환 응답 구조가 올바르지 않습니다."
                ) from exc
        return ProviderNarration(
            provider=self.provider,
            model=model,
            draft=parsed,
            response_id=str(getattr(response, "id", "") or ""),
            usage=self._usage(response),
        )

    @staticmethod
    def _usage(response) -> LLMUsage:
        usage = getattr(response, "usage", None)
        details = getattr(usage, "input_tokens_details", None)
        return LLMUsage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            cached_input_tokens=int(getattr(details, "cached_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        )
