from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import requests
from django.test import SimpleTestCase, override_settings
from pydantic import ValidationError

from apps.chat.services.gemini_persona_narrator import GeminiPersonaNarrator
from apps.chat.services.openai_persona_narrator import OpenAIPersonaNarrator
from apps.chat.services.persona_narration import (
    PersonaNarrationConfigurationError,
    PersonaNarrationDraft,
    PersonaNarrationDraftItem,
    PersonaNarrationItem,
    PersonaNarrationRequest,
    PersonaNarrationService,
    ProviderNarration,
    RuleBasedPersonaNarrator,
    build_persona_narration_service,
)
from apps.chat.services.stylist_personas import load_stylist_personas


class PersonaNarrationTests(SimpleTestCase):
    def test_provider_payload_contains_only_approved_input_contract(self) -> None:
        request = self._request()

        payload = request.payload()

        self.assertEqual(
            set(payload),
            {
                "persona_id",
                "outfit_id",
                "items",
                "reason_codes",
                "voice_profile",
            },
        )
        self.assertEqual(
            payload["items"],
            [
                {"slot": "TOP", "name": "크루넥 니트"},
                {"slot": "BOTTOM", "name": "테이퍼드 팬츠"},
            ],
        )
        self.assertNotIn("identity_id", json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("source_id", json.dumps(payload, ensure_ascii=False))

    def test_changed_facts_or_multiple_sentences_use_template_fallback(self) -> None:
        request = self._request()
        valid = self._draft()
        invalid_drafts = (
            valid.model_copy(update={"outfit_id": "different-outfit"}),
            valid.model_copy(
                update={
                    "items": [
                        PersonaNarrationDraftItem(
                            slot="TOP",
                            name="존재하지 않는 재킷",
                        ),
                        PersonaNarrationDraftItem(
                            slot="BOTTOM",
                            name="테이퍼드 팬츠",
                        ),
                    ]
                }
            ),
            valid.model_copy(update={"reason_codes": ["UNKNOWN_REASON"]}),
            valid.model_copy(update={"attribute_claims": ["방수 소재"]}),
            valid.model_copy(update={"message": "첫 문장입니다. 두 번째 문장입니다."}),
        )

        for draft in invalid_drafts:
            with self.subTest(draft=draft):
                service = PersonaNarrationService(narrator=_DraftNarrator(draft=draft))
                result = service.generate(request)

                self.assertTrue(result.fallback_used)
                self.assertEqual(result.provider, "template")
                self.assertEqual(result.requested_provider, "fake")
                self.assertEqual(
                    result.fallback_reason,
                    "PERSONA_NARRATION_CONTRACT_FAILED",
                )

    def test_valid_draft_is_approved_as_exactly_one_sentence(self) -> None:
        result = PersonaNarrationService(
            narrator=_DraftNarrator(draft=self._draft())
        ).generate(self._request())

        self.assertFalse(result.fallback_used)
        self.assertEqual(result.provider, "fake")
        self.assertEqual(result.message, "두 아이템을 차분하게 묶은 코디예요.")

    def test_structured_output_rejects_unexpected_fact_fields(self) -> None:
        payload = self._draft().model_dump()
        payload["invented_attribute"] = "방수"

        with self.assertRaises(ValidationError):
            PersonaNarrationDraft.model_validate(payload)

    def test_rule_based_fallback_uses_fixed_minimal_reason_priority(self) -> None:
        request = replace(
            self._request(),
            reason_codes=(
                "MINIMAL_RECENT_HISTORY",
                "MINIMAL_SILHOUETTE_CONSISTENCY",
                "MINIMAL_COLOR_COHESION",
            ),
        )

        result = self._fallback().generate(
            request,
            requested_provider="openai",
            reason="PERSONA_NARRATION_PROVIDER_FAILED",
        )

        self.assertEqual(
            result.message,
            "상의 크루넥 니트, 하의 테이퍼드 팬츠 조합에서 "
            "색상 조화·실루엣 일관성 기준으로 차분하게 정리했어요.",
        )

    def test_rule_based_fallback_prioritizes_and_deduplicates_common_reason(
        self,
    ) -> None:
        catalog = load_stylist_personas()
        request = PersonaNarrationRequest(
            persona_id="practical",
            outfit_id="outfit-2",
            items=(
                PersonaNarrationItem(slot="TOP", name="셔츠"),
                PersonaNarrationItem(slot="BOTTOM", name="팬츠"),
                PersonaNarrationItem(slot="OUTER", name="재킷"),
                PersonaNarrationItem(slot="FOOTWEAR", name="스니커즈"),
            ),
            reason_codes=(
                "PRACTICAL_WEATHER_FIT",
                "STYLIST_DUPLICATE_ALLOWED_NO_DISTINCT_CANDIDATE",
                "STYLIST_DUPLICATE_ALLOWED_CANDIDATE_EXHAUSTED",
            ),
            voice_profile=catalog.get("practical").voice_profile,
        )

        result = self._fallback().generate(
            request,
            requested_provider="gemini",
            reason="PERSONA_NARRATION_NOT_CONFIGURED",
        )

        self.assertEqual(
            result.message,
            "상의 셔츠, 하의 팬츠 등 4개 아이템 조합에서 "
            "유효 후보 범위에서 품질을 우선한 판단·날씨 적합성 기준으로 "
            "활용하기 쉽게 구성했어요.",
        )
        self.assertEqual(
            result.message.count("유효 후보 범위에서 품질을 우선한 판단"),
            1,
        )

    def test_rule_based_fallback_ignores_unknown_reason_codes(self) -> None:
        request = replace(
            self._request(),
            reason_codes=("UNMAPPED_REASON",),
        )

        first = self._fallback().generate(
            request,
            requested_provider="openai",
            reason="PERSONA_NARRATION_CONTRACT_FAILED",
        )
        second = self._fallback().generate(
            request,
            requested_provider="openai",
            reason="PERSONA_NARRATION_CONTRACT_FAILED",
        )

        self.assertEqual(first.message, second.message)
        self.assertIn("검증된 추천 조건", first.message)
        self.assertNotIn("UNMAPPED_REASON", first.message)

    def test_rule_based_fallback_uses_experimental_voice_rule(self) -> None:
        catalog = load_stylist_personas()
        request = replace(
            self._request(),
            persona_id="experimental",
            reason_codes=(
                "EXPERIMENTAL_NOVELTY",
                "EXPERIMENTAL_HYPOTHESIS_ALIGNMENT",
            ),
            voice_profile=catalog.get("experimental").voice_profile,
        )

        result = self._fallback().generate(
            request,
            requested_provider="gemini",
            reason="PERSONA_NARRATION_PROVIDER_FAILED",
        )

        self.assertEqual(
            result.message,
            "상의 크루넥 니트, 하의 테이퍼드 팬츠 조합에서 "
            "변화 가설과의 정합성·새로움 기준으로 익숙함은 지키고 변화를 "
            "더했어요.",
        )

    def test_rule_based_fallback_keeps_long_item_names_within_one_sentence(
        self,
    ) -> None:
        request = replace(
            self._request(),
            items=(
                PersonaNarrationItem(slot="TOP", name="긴" * 200),
                PersonaNarrationItem(slot="BOTTOM", name="팬츠"),
                PersonaNarrationItem(slot="OUTER", name="재킷"),
            ),
        )

        result = self._fallback().generate(
            request,
            requested_provider="openai",
            reason="PERSONA_NARRATION_PROVIDER_FAILED",
        )

        self.assertLessEqual(len(result.message), 300)
        self.assertEqual(result.message.count("."), 1)
        self.assertIn("상의, 하의 팬츠 등 3개 아이템 조합", result.message)
        self.assertNotIn("긴" * 200, result.message)

    @override_settings(
        PERSONA_LLM_MODEL="gpt-4o-mini",
        PERSONA_LLM_MAX_OUTPUT_TOKENS=400,
        PERSONA_LLM_PROMPT_VERSION="persona-narration-v1",
    )
    def test_openai_adapter_reuses_responses_client_and_structured_schema(self) -> None:
        response = SimpleNamespace(
            id="resp-1",
            output_parsed=self._draft(),
            usage=SimpleNamespace(
                input_tokens=20,
                output_tokens=10,
                input_tokens_details=SimpleNamespace(cached_tokens=4),
            ),
        )
        client = SimpleNamespace(
            responses=SimpleNamespace(parse=Mock(return_value=response))
        )
        chat_adapter = SimpleNamespace(client=client)
        narrator = OpenAIPersonaNarrator(chat_adapter=chat_adapter)

        generated = narrator.generate_draft(self._request())

        self.assertEqual(generated.provider, "openai")
        self.assertEqual(generated.model, "gpt-4o-mini")
        self.assertEqual(generated.usage.cached_input_tokens, 4)
        call = client.responses.parse.call_args
        self.assertEqual(call.kwargs["model"], "gpt-4o-mini")
        self.assertIs(call.kwargs["text_format"], PersonaNarrationDraft)
        self.assertFalse(call.kwargs["store"])
        sent_payload = json.loads(call.kwargs["input"][0]["content"])
        self.assertEqual(sent_payload, self._request().payload())

    @override_settings(
        GEMINI_API_KEY="gemini-secret",
        GEMINI_API_BASE_URL="https://generativelanguage.example",
        PERSONA_LLM_MODEL="gemini-test",
        PERSONA_LLM_TIMEOUT_SECONDS=7,
        PERSONA_LLM_MAX_OUTPUT_TOKENS=400,
    )
    def test_gemini_adapter_uses_same_contract_and_structured_output(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "responseId": "gemini-response-1",
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": self._draft().model_dump_json(),
                            }
                        ]
                    }
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 18,
                "cachedContentTokenCount": 3,
                "candidatesTokenCount": 9,
            },
        }
        post = Mock(return_value=response)

        generated = GeminiPersonaNarrator(post=post).generate_draft(self._request())

        self.assertEqual(generated.provider, "gemini")
        self.assertEqual(generated.response_id, "gemini-response-1")
        self.assertEqual(generated.usage.output_tokens, 9)
        call = post.call_args
        self.assertEqual(
            call.args[0],
            "https://generativelanguage.example/v1beta/models/"
            "gemini-test:generateContent",
        )
        self.assertEqual(call.kwargs["timeout"], 7)
        body = call.kwargs["json"]
        self.assertEqual(
            body["generationConfig"]["responseMimeType"],
            "application/json",
        )
        sent_payload = json.loads(body["contents"][0]["parts"][0]["text"])
        self.assertEqual(sent_payload, self._request().payload())

    @override_settings(
        PERSONA_LLM_PROVIDER="gemini",
        PERSONA_LLM_MODEL="gemini-test",
        GEMINI_API_KEY="",
    )
    def test_unconfigured_gemini_uses_template_without_openai_switch(self) -> None:
        post = Mock(side_effect=AssertionError("Gemini 호출도 시작하면 안 됩니다."))
        service = build_persona_narration_service(gemini_post=post)

        result = service.generate(self._request())

        self.assertTrue(result.fallback_used)
        self.assertEqual(result.provider, "template")
        self.assertEqual(result.requested_provider, "gemini")
        self.assertEqual(
            result.fallback_reason,
            "PERSONA_NARRATION_NOT_CONFIGURED",
        )
        post.assert_not_called()

    @override_settings(
        PERSONA_LLM_PROVIDER="gemini",
        PERSONA_LLM_MODEL="gemini-test",
        GEMINI_API_KEY="gemini-secret",
        PERSONA_LLM_TIMEOUT_SECONDS=1,
        PERSONA_LLM_MAX_OUTPUT_TOKENS=400,
    )
    def test_gemini_call_failure_uses_template_without_provider_switch(self) -> None:
        post = Mock(side_effect=requests.Timeout("timeout"))
        service = build_persona_narration_service(gemini_post=post)

        result = service.generate(self._request())

        self.assertTrue(result.fallback_used)
        self.assertEqual(result.requested_provider, "gemini")
        self.assertEqual(result.provider, "template")
        self.assertEqual(
            result.fallback_reason,
            "PERSONA_NARRATION_PROVIDER_FAILED",
        )
        post.assert_called_once()

    @override_settings(PERSONA_LLM_PROVIDER="openai")
    def test_factory_selects_only_openai_for_openai_server_setting(self) -> None:
        service = build_persona_narration_service(
            openai_chat_adapter=SimpleNamespace(client=object())
        )

        self.assertIsInstance(service.narrator, OpenAIPersonaNarrator)
        self.assertEqual(service.narrator.provider, "openai")

    @override_settings(PERSONA_LLM_PROVIDER="unknown")
    def test_unknown_server_provider_is_rejected(self) -> None:
        with self.assertRaises(PersonaNarrationConfigurationError):
            build_persona_narration_service()

    @staticmethod
    def _request() -> PersonaNarrationRequest:
        return PersonaNarrationRequest(
            persona_id="minimal",
            outfit_id="outfit-1",
            items=(
                PersonaNarrationItem(slot="TOP", name="크루넥 니트"),
                PersonaNarrationItem(slot="BOTTOM", name="테이퍼드 팬츠"),
            ),
            reason_codes=("MINIMAL_COLOR_COHESION", "TPO_VALIDATED"),
            voice_profile=load_stylist_personas().get("minimal").voice_profile,
        )

    @staticmethod
    def _draft() -> PersonaNarrationDraft:
        return PersonaNarrationDraft(
            message="두 아이템을 차분하게 묶은 코디예요.",
            outfit_id="outfit-1",
            items=[
                {"slot": "TOP", "name": "크루넥 니트"},
                {"slot": "BOTTOM", "name": "테이퍼드 팬츠"},
            ],
            reason_codes=["MINIMAL_COLOR_COHESION", "TPO_VALIDATED"],
            attribute_claims=[],
        )

    @staticmethod
    def _fallback() -> RuleBasedPersonaNarrator:
        return RuleBasedPersonaNarrator()


class _DraftNarrator:
    provider = "fake"

    def __init__(self, *, draft: PersonaNarrationDraft) -> None:
        self.draft = draft

    def generate_draft(self, _request: PersonaNarrationRequest) -> ProviderNarration:
        return ProviderNarration(
            provider=self.provider,
            model="fake-model",
            draft=self.draft,
        )
