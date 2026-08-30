"""채팅 추천 카드 이미지 생성 백엔드(Gemini / OpenRouter) 계약 테스트.

지키려는 것은 세 가지다.

- 기본 백엔드는 Gemini이고, 환경변수 하나로 Qwen(OpenRouter)으로 되돌아간다.
- 백엔드가 바뀌면 결과 캐시 지문도 함께 바뀐다 — 예전 모델로 만든 이미지를
  새 모델 결과로 돌려주면 안 된다.
- 가상 착장(virtual try-on)은 이 스위치와 무관하게 Qwen을 계속 쓴다.
"""

from __future__ import annotations

import base64
from unittest.mock import Mock

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from apps.recommend.services import render_artifacts
from apps.recommend.services.mixed_outfit_render import (
    BASE_PROMPT,
    PROMPT_VERSION,
    GeminiImageProvider,
    LoadedReferenceImage,
    OpenRouterQwenImageProvider,
    OutfitRenderRequest,
    OutfitRenderService,
    RenderItemReference,
    RenderProviderError,
    RenderSource,
    active_model,
    build_provider,
)
from apps.recommend.services.virtual_try_on import VirtualTryOnService

PNG = b"\x89PNG\r\n\x1a\n" + b"reference-bytes"
JPEG = b"\xff\xd8\xff" + b"generated-bytes"
FINGERPRINT = "a" * 64

GEMINI_SETTINGS = {
    "OUTFIT_RENDER_BACKEND": "gemini",
    "OUTFIT_RENDER_GEMINI_MODEL": "gemini-3.1-flash-image",
    "OUTFIT_RENDER_GEMINI_URL": "https://gemini.test/v1beta/interactions",
    "OUTFIT_RENDER_GEMINI_MIME_TYPE": "image/jpeg",
    "GEMINI_API_KEY": "test-key",
}


def _reference() -> LoadedReferenceImage:
    return LoadedReferenceImage(
        item=RenderItemReference(
            item_id="item-1",
            position=1,
            slot="top",
            source_type=RenderSource.WARDROBE,
            image_ref="wardrobe/top.png",
        ),
        content=PNG,
        media_type="image/png",
    )


def _session(payload: dict) -> Mock:
    response = Mock(status_code=200)
    response.json.return_value = payload
    session = Mock()
    session.post.return_value = response
    return session


class BackendSelectionTests(SimpleTestCase):
    @override_settings(**GEMINI_SETTINGS)
    def test_default_backend_is_gemini(self) -> None:
        provider = build_provider()
        self.assertIsInstance(provider, GeminiImageProvider)
        self.assertEqual(provider.provider_name, "gemini")
        self.assertEqual(provider.model_name, "gemini-3.1-flash-image")
        self.assertEqual(active_model(), "gemini-3.1-flash-image")

    @override_settings(
        OUTFIT_RENDER_BACKEND="openrouter",
        OUTFIT_RENDER_MODEL="qwen/qwen-image-3-pro",
    )
    def test_openrouter_is_one_env_var_away(self) -> None:
        """롤백 경로. Qwen 코드는 남겨 두므로 재배포 없이 되돌릴 수 있다."""
        provider = build_provider()
        self.assertIsInstance(provider, OpenRouterQwenImageProvider)
        self.assertEqual(provider.model_name, "qwen/qwen-image-3-pro")
        self.assertEqual(active_model(), "qwen/qwen-image-3-pro")

    @override_settings(**GEMINI_SETTINGS)
    def test_service_defaults_to_configured_backend(self) -> None:
        self.assertIsInstance(OutfitRenderService().provider, GeminiImageProvider)

    def test_virtual_try_on_stays_on_qwen(self) -> None:
        """가상 착장은 사람 사진을 다루는 별개 프롬프트라 함께 바꾸지 않았다."""
        with override_settings(**GEMINI_SETTINGS):
            self.assertIsInstance(
                VirtualTryOnService().provider, OpenRouterQwenImageProvider
            )


@override_settings(
    **GEMINI_SETTINGS,
    OUTFIT_RENDER_ASPECT_RATIO="9:16",
    OUTFIT_RENDER_RESOLUTION="1K",
)
class GeminiProviderTests(SimpleTestCase):
    def test_request_carries_prompt_images_and_output_contract(self) -> None:
        session = _session(
            {
                "steps": [
                    {
                        "content": [
                            {
                                "type": "image",
                                "data": base64.b64encode(JPEG).decode(),
                            }
                        ]
                    }
                ],
                "usage": {"total_tokens": 7},
            }
        )
        provider = GeminiImageProvider(session=session)

        image, media_type, usage = provider.generate(
            prompt="코디 프롬프트", references=(_reference(),)
        )

        self.assertEqual(image, JPEG)
        self.assertEqual(media_type, "image/jpeg")
        self.assertEqual(usage, {"total_tokens": 7})

        args, kwargs = session.post.call_args
        self.assertEqual(args[0], "https://gemini.test/v1beta/interactions")
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "test-key")
        body = kwargs["json"]
        self.assertEqual(body["model"], "gemini-3.1-flash-image")
        self.assertEqual(body["input"][0], {"type": "text", "text": "코디 프롬프트"})
        self.assertEqual(
            body["input"][1],
            {
                "type": "image",
                "mime_type": "image/png",
                "data": base64.b64encode(PNG).decode(),
            },
        )
        self.assertEqual(
            body["response_format"],
            {
                "type": "image",
                "mime_type": "image/jpeg",
                "aspect_ratio": "9:16",
                "image_size": "1K",
            },
        )

    def test_generate_content_shape_is_also_accepted(self) -> None:
        """엔드포인트 설정만 바꿔도 동작하도록 두 응답 모양을 모두 읽는다."""
        session = _session(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "data": base64.b64encode(JPEG).decode()
                                    }
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {"totalTokenCount": 3},
            }
        )

        image, _, usage = GeminiImageProvider(session=session).generate(
            prompt="p", references=(_reference(),)
        )

        self.assertEqual(image, JPEG)
        self.assertEqual(usage, {"totalTokenCount": 3})

    def test_http_error_is_a_provider_error(self) -> None:
        response = Mock(status_code=429, text="rate limited")
        session = Mock()
        session.post.return_value = response
        with self.assertRaises(RenderProviderError):
            GeminiImageProvider(session=session).generate(
                prompt="p", references=(_reference(),)
            )

    def test_image_missing_from_response_is_a_provider_error(self) -> None:
        session = _session({"steps": [{"content": [{"type": "text", "text": "..."}]}]})
        with self.assertRaises(RenderProviderError):
            GeminiImageProvider(session=session).generate(
                prompt="p", references=(_reference(),)
            )

    @override_settings(GEMINI_API_KEY="")
    def test_missing_api_key_fails_before_calling_provider(self) -> None:
        session = Mock()
        with self.assertRaises(RenderProviderError):
            GeminiImageProvider(session=session).generate(
                prompt="p", references=(_reference(),)
            )
        session.post.assert_not_called()


class PromptAndFingerprintTests(SimpleTestCase):
    def test_prompt_contract_is_unchanged_by_the_backend_switch(self) -> None:
        """두 백엔드가 같은 프롬프트를 받는다 — 그래서 버전도 그대로다."""
        request = OutfitRenderRequest(
            composition_id="c-1",
            composition_fingerprint=FINGERPRINT,
            items=(_reference().item,),
            subject_presentation="woman",
        )
        prompt = OutfitRenderService._prompt(request)
        self.assertTrue(prompt.startswith(BASE_PROMPT))
        self.assertIn("여성 모델", prompt)
        self.assertEqual(PROMPT_VERSION, "mixed-outfit-render-v2")

    def test_fingerprint_follows_the_active_model(self) -> None:
        with override_settings(**GEMINI_SETTINGS):
            gemini = render_artifacts.fingerprint(FINGERPRINT, "woman")
        with override_settings(
            OUTFIT_RENDER_BACKEND="openrouter",
            OUTFIT_RENDER_MODEL="qwen/qwen-image-3-pro",
        ):
            openrouter = render_artifacts.fingerprint(FINGERPRINT, "woman")
        self.assertNotEqual(gemini, openrouter)

    @override_settings(**GEMINI_SETTINGS)
    def test_active_model_is_what_the_provider_reports(self) -> None:
        self.assertEqual(active_model(), build_provider().model_name)
        self.assertEqual(settings.OUTFIT_RENDER_BACKEND, "gemini")
