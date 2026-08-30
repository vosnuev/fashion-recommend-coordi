"""Gemini generateContent를 사용하는 페르소나 말투 어댑터."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import requests
from django.conf import settings

from apps.chat.services.openai_adapter import LLMUsage
from apps.chat.services.persona_narration import (
    PERSONA_NARRATION_INSTRUCTIONS,
    PersonaNarrationConfigurationError,
    PersonaNarrationDraft,
    PersonaNarrationProviderError,
    PersonaNarrationRequest,
    ProviderNarration,
    persona_narration_json_schema,
)

logger = logging.getLogger(__name__)

PostCallable = Callable[..., requests.Response]


class GeminiPersonaNarrator:
    provider = "gemini"

    def __init__(self, *, post: PostCallable | None = None) -> None:
        self.post = post or requests.post

    def generate_draft(
        self,
        request: PersonaNarrationRequest,
    ) -> ProviderNarration:
        api_key = settings.GEMINI_API_KEY.strip()
        model = settings.PERSONA_LLM_MODEL.strip()
        if not api_key:
            raise PersonaNarrationConfigurationError(
                "Gemini 말투 변환 API 키가 설정되지 않았습니다."
            )
        if not model:
            raise PersonaNarrationConfigurationError(
                "PERSONA_LLM_MODEL이 설정되지 않았습니다."
            )

        url = (
            f"{settings.GEMINI_API_BASE_URL.rstrip('/')}"
            f"/v1beta/models/{model}:generateContent"
        )
        body = {
            "systemInstruction": {"parts": [{"text": PERSONA_NARRATION_INSTRUCTIONS}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": json.dumps(
                                request.payload(),
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": settings.PERSONA_LLM_MAX_OUTPUT_TOKENS,
                "responseMimeType": "application/json",
                "responseSchema": persona_narration_json_schema(),
            },
        }
        try:
            response = self.post(
                url,
                headers={
                    "x-goog-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=settings.PERSONA_LLM_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
            parsed = PersonaNarrationDraft.model_validate_json(
                self._extract_text(payload)
            )
        except PersonaNarrationProviderError:
            raise
        except requests.Timeout as exc:
            raise PersonaNarrationProviderError(
                "Gemini 말투 변환 응답 시간이 초과되었습니다."
            ) from exc
        except (requests.RequestException, ValueError, TypeError) as exc:
            logger.warning("Gemini 말투 변환 실패: %s", type(exc).__name__)
            raise PersonaNarrationProviderError(
                "Gemini 말투 변환 응답을 받을 수 없습니다."
            ) from exc

        return ProviderNarration(
            provider=self.provider,
            model=model,
            draft=parsed,
            response_id=str(payload.get("responseId") or ""),
            usage=self._usage(payload),
        )

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        try:
            candidates = payload["candidates"]
            parts = candidates[0]["content"]["parts"]
            text = "".join(
                str(part.get("text") or "") for part in parts if isinstance(part, dict)
            ).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise PersonaNarrationProviderError(
                "Gemini 말투 변환 응답 본문이 없습니다."
            ) from exc
        if not text:
            raise PersonaNarrationProviderError(
                "Gemini 말투 변환 응답 본문이 없습니다."
            )
        return text

    @staticmethod
    def _usage(payload: dict[str, Any]) -> LLMUsage:
        usage = payload.get("usageMetadata") or {}
        return LLMUsage(
            input_tokens=int(usage.get("promptTokenCount") or 0),
            cached_input_tokens=int(usage.get("cachedContentTokenCount") or 0),
            output_tokens=int(usage.get("candidatesTokenCount") or 0),
        )
