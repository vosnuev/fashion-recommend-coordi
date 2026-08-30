"""착용 이미지 생성 테스트.

핵심은 두 가지다.

- **코디당 한 번만 만든다.** 같은 골든 코디가 여러 사용자·여러 날에 추천되므로,
  이미 있으면 생성 없이 그 키를 쓴다. 여기가 새면 요금이 사용자 수만큼 붙는다.
- **실패해도 추천은 살아남는다.** 이미지가 없으면 아이템 카드로 화면이 성립한다.
"""

from __future__ import annotations

import base64
import unittest
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings

from apps.recommend.services import outfit_render
from apps.recommend.services.outfit_render import (
    BACKEND_GEMINI,
    BACKEND_OPENROUTER,
    DEFAULT_RENDER_EXTENSION,
    GEMINI_MAX_REFERENCES,
    OPENROUTER_MAX_REFERENCES,
    RenderError,
    RenderRef,
    _extract_image,
    _generate,
    _reference_keys,
    _sniff,
    ensure_render,
    existing_render,
    plan_references,
    prompt_for,
    render_key_for,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()
BUCKET = "skn28-cozy3"
ITEMS = [
    {"item_key": "095#000", "s3_key": "goldenset/derived/v1/095/item_000.png"},
    {"item_key": "095#001", "s3_key": "goldenset/derived/v1/095/item_001.png"},
    {"item_key": "095#002"},  # 이미지가 없는 아이템 (분리 실패 등)
]
RENDER_KEY = "goldenset/derived/v1/095/render_frontal.png"


class RenderKeyTests(unittest.TestCase):
    def test_render_sits_next_to_item_images(self) -> None:
        """골든셋 산출물과 같은 위치. api가 derived prefix를 따로 알 필요가 없다."""
        self.assertEqual(render_key_for(ITEMS[0]["s3_key"]), RENDER_KEY)

    def test_key_is_the_same_from_any_item(self) -> None:
        """어느 아이템에서 유도해도 같은 키라 별도 캐시 테이블이 필요 없다."""
        self.assertEqual(
            render_key_for("goldenset/derived/v1/095/item_000.png"),
            render_key_for("goldenset/derived/v1/095/item_007.png"),
        )

    def test_different_outfits_do_not_collide(self) -> None:
        self.assertNotEqual(
            render_key_for("goldenset/derived/v1/095/item_000.png"),
            render_key_for("goldenset/derived/v1/096/item_000.png"),
        )

    def test_items_without_image_are_skipped(self) -> None:
        self.assertEqual(len(_reference_keys(ITEMS)), 2)

    @override_settings(DAILY_LOOK_RENDER_MAX_REFERENCES=3)
    def test_reference_count_is_capped(self) -> None:
        """참조 장수만큼 입력 토큰과 요금이 오른다."""
        many = [{"s3_key": f"goldenset/derived/v1/095/item_{n:03d}.png"} for n in range(20)]
        self.assertEqual(len(_reference_keys(many)), 3)

    @override_settings(
        DAILY_LOOK_RENDER_MAX_REFERENCES=99, DAILY_LOOK_RENDER_GEMINI_THRESHOLD=999
    )
    def test_openrouter_limit_wins_over_settings(self) -> None:
        """.env에 큰 값이 남아 있어도 OpenRouter에는 4장을 넘기지 않는다.

            Provider rejections: Alibaba: input_references:
            must have between 0 and 4 items

        qwen/qwen-image-3-pro는 제공자가 Alibaba 하나뿐이라 5장을 보내면 다른
        곳으로 넘어가지 못하고 요청 자체가 실패한다. (threshold를 아주 크게 둬
        Gemini로 넘어가지 않는 상황을 만든다.)
        """
        many = [{"s3_key": f"k/item_{n:03d}.png"} for n in range(20)]
        self.assertEqual(len(_reference_keys(many)), OPENROUTER_MAX_REFERENCES)
        self.assertLessEqual(OPENROUTER_MAX_REFERENCES, 4)

    @override_settings(DAILY_LOOK_RENDER_MAX_REFERENCES=4)
    def test_silhouette_survives_when_accessories_are_dropped(self) -> None:
        """자리가 모자라면 가방·액세서리를 버리고 옷을 남긴다.

        예전엔 payload 순서대로 앞에서 잘랐다. 그 순서엔 의미가 없어서 가방이
        남고 바지가 빠지면, 생성된 사진이 그 코디가 아니게 된다.
        """
        items = [
            {"s3_key": "a.png", "category_large": "가방", "item_name": "토트백"},
            {"s3_key": "b.png", "category_large": "액세서리", "item_name": "모자"},
            {"s3_key": "c.png", "category_large": "상의", "item_name": "셔츠"},
            {"s3_key": "d.png", "category_large": "하의", "item_name": "슬랙스"},
            {"s3_key": "e.png", "category_large": "신발", "item_name": "로퍼"},
            {"s3_key": "f.png", "category_large": "아우터", "item_name": "코트"},
        ]
        keys = _reference_keys(items)
        self.assertEqual(len(keys), 4)
        self.assertEqual(set(keys), {"c.png", "d.png", "e.png", "f.png"})
        # 전달 순서는 원래 순서를 지킨다 (모델에 주는 순서가 결과에 영향을 준다)
        self.assertEqual(keys, ["c.png", "d.png", "e.png", "f.png"])

    @override_settings(DAILY_LOOK_RENDER_MAX_REFERENCES=4)
    def test_selection_is_deterministic_for_ties(self) -> None:
        """같은 코디는 매번 같은 참조 조합이어야 한다 (착용 이미지는 재사용된다)."""
        items = [
            {"s3_key": f"{n}.png", "category_large": "액세서리"} for n in range(6)
        ]
        self.assertEqual(_reference_keys(items), _reference_keys(items))
        self.assertEqual(_reference_keys(items), ["0.png", "1.png", "2.png", "3.png"])

    @override_settings(DAILY_LOOK_RENDER_MAX_REFERENCES=4)
    def test_unknown_category_is_not_dropped_before_clothing(self) -> None:
        """분류가 비어도 옷일 수 있다. 가방·액세서리보다는 앞에 둔다."""
        items = [
            {"s3_key": "bag.png", "category_large": "가방"},
            {"s3_key": "acc.png", "category_large": "액세서리"},
            {"s3_key": "unknown.png"},
            {"s3_key": "top.png", "category_large": "상의"},
            {"s3_key": "bottom.png", "category_large": "하의"},
        ]
        keys = _reference_keys(items)
        self.assertIn("unknown.png", keys)
        self.assertNotIn("acc.png", keys)


class EnsureRenderTests(TestCase):
    @patch("apps.recommend.services.outfit_render.storage.exists_for", return_value=True)
    @patch("apps.recommend.services.outfit_render._generate")
    def test_existing_render_is_reused_without_generating(self, generate, _exists):
        """이미 만들어 둔 코디면 모델을 부르지 않는다 — 요금이 걸린 지점이다."""
        reference = ensure_render(bucket=BUCKET, items=ITEMS)
        self.assertEqual(reference, RenderRef(BUCKET, RENDER_KEY))
        generate.assert_not_called()

    @patch("apps.recommend.services.outfit_render.storage.put_bytes_for")
    @patch("apps.recommend.services.outfit_render.storage.exists_for", return_value=False)
    @patch("apps.recommend.services.outfit_render._generate", return_value=PNG)
    def test_missing_render_is_generated_and_stored(self, generate, _exists, put):
        reference = ensure_render(bucket=BUCKET, items=ITEMS)
        self.assertEqual(reference.s3_key, RENDER_KEY)
        generate.assert_called_once()
        put.assert_called_once_with(BUCKET, RENDER_KEY, PNG, "image/png")

    @patch("apps.recommend.services.outfit_render.storage.exists_for", return_value=False)
    @patch("apps.recommend.services.outfit_render._generate")
    @override_settings(DAILY_LOOK_RENDER_ENABLED=False)
    def test_disabled_switch_skips_generation(self, generate, _exists):
        self.assertIsNone(ensure_render(bucket=BUCKET, items=ITEMS))
        generate.assert_not_called()

    @patch("apps.recommend.services.outfit_render._generate")
    def test_no_reference_images_skips_generation(self, generate):
        self.assertIsNone(ensure_render(bucket=BUCKET, items=[{"item_key": "x"}]))
        self.assertIsNone(ensure_render(bucket="", items=ITEMS))
        generate.assert_not_called()

    @patch("apps.recommend.services.outfit_render.storage.exists_for", return_value=False)
    @patch(
        "apps.recommend.services.outfit_render._generate",
        side_effect=RenderError("모델 응답에 이미지가 없습니다"),
    )
    def test_generation_failure_propagates_as_render_error(self, _generate, _exists):
        """호출부(daily_look)가 잡아 추천은 살리고 이미지만 비운다."""
        with self.assertRaises(RenderError):
            ensure_render(bucket=BUCKET, items=ITEMS)


class ExtractImageTests(unittest.TestCase):
    """제공자마다 이미지를 담는 자리가 달라 두 형태를 모두 본다."""

    def test_images_array(self) -> None:
        payload = {"choices": [{"message": {"images": [{"image_url": {"url": DATA_URL}}]}}]}
        self.assertEqual(_extract_image(payload), PNG)

    def test_content_string(self) -> None:
        self.assertEqual(
            _extract_image({"choices": [{"message": {"content": DATA_URL}}]}), PNG
        )

    def test_content_list(self) -> None:
        payload = {
            "choices": [
                {"message": {"content": [{"type": "image_url", "image_url": {"url": DATA_URL}}]}}
            ]
        }
        self.assertEqual(_extract_image(payload), PNG)

    def test_text_only_response_is_not_mistaken_for_an_image(self) -> None:
        """모델이 거절문만 돌려주는 경우. 조용히 빈 파일을 저장하면 안 된다."""
        payload = {"choices": [{"message": {"content": "죄송하지만 만들 수 없습니다."}}]}
        self.assertIsNone(_extract_image(payload))

    def test_corrupt_base64(self) -> None:
        payload = {"choices": [{"message": {"content": "data:image/png;base64,@@@@"}}]}
        self.assertIsNone(_extract_image(payload))

    def test_empty_payload(self) -> None:
        self.assertIsNone(_extract_image({}))
        self.assertIsNone(_extract_image({"choices": []}))


class ImageApiResponseTests(unittest.TestCase):
    """OpenRouter 이미지 전용 API(POST /api/v1/images) 응답 형태.

    처음엔 채팅 API에 modalities=["image","text"]를 붙였다가 404를 받았다.
    "No endpoints found that support the requested output modalities" — 이미지
    생성은 별도 엔드포인트를 쓴다.
    """

    def test_data_b64_json(self) -> None:
        payload = {
            "created": 1748372400,
            "data": [{"b64_json": base64.b64encode(PNG).decode(), "media_type": "image/png"}],
            "usage": {"total_tokens": 4175, "cost": 0.04},
        }
        self.assertEqual(_extract_image(payload), PNG)

    def test_data_url_variant(self) -> None:
        self.assertEqual(_extract_image({"data": [{"url": DATA_URL}]}), PNG)

    def test_chat_shape_still_parsed(self) -> None:
        """모델을 바꾸면 채팅 형태로 오는 경우가 있어 둘 다 본다."""
        payload = {"choices": [{"message": {"images": [{"image_url": {"url": DATA_URL}}]}}]}
        self.assertEqual(_extract_image(payload), PNG)

    def test_empty_data_array(self) -> None:
        self.assertIsNone(_extract_image({"data": []}))

    def test_corrupt_b64_json(self) -> None:
        self.assertIsNone(_extract_image({"data": [{"b64_json": "@@@@"}]}))


@override_settings(OPENROUTER_API_KEY="test-key")
class RequestShapeTests(TestCase):
    """요청이 이미지 API 규약대로 나가는지."""

    @patch("apps.recommend.services.outfit_render.storage.download_for", return_value=PNG)
    @patch("apps.recommend.services.outfit_render.requests.post")
    def test_uses_images_endpoint_with_input_references(self, post, _download):
        post.return_value = type("R", (), {
            "status_code": 200,
            "json": lambda self: {"data": [{"b64_json": base64.b64encode(PNG).decode()}]},
        })()
        outfit_render._generate(
                bucket=BUCKET,
                reference_keys=[ITEMS[0]["s3_key"]],
                backend=BACKEND_OPENROUTER,
            )

        url = post.call_args.args[0]
        body = post.call_args.kwargs["json"]
        self.assertTrue(url.endswith("/api/v1/images"), url)
        # 채팅 API 규약이 남아 있으면 다시 404가 난다
        self.assertNotIn("messages", body)
        self.assertNotIn("modalities", body)
        self.assertIn("prompt", body)
        self.assertEqual(len(body["input_references"]), 1)
        self.assertTrue(
            body["input_references"][0]["image_url"]["url"].startswith("data:image/png;base64,")
        )

    @patch("apps.recommend.services.outfit_render.storage.download_for", return_value=PNG)
    @patch("apps.recommend.services.outfit_render.requests.post")
    def test_http_error_body_is_kept_in_the_message(self, post, _download):
        """404의 실제 사유는 본문에만 담긴다. 삼키면 원인을 못 찾는다."""
        post.return_value = type("R", (), {
            "status_code": 404,
            "text": '{"error":{"message":"No endpoints found...","code":404}}',
            "json": lambda self: {},
        })()
        with self.assertRaises(RenderError) as ctx:
            outfit_render._generate(
                bucket=BUCKET,
                reference_keys=[ITEMS[0]["s3_key"]],
                backend=BACKEND_OPENROUTER,
            )
        self.assertIn("404", str(ctx.exception))
        self.assertIn("No endpoints found", str(ctx.exception))

class BackendChoiceTests(unittest.TestCase):
    """참조 장수만 보고 백엔드를 고른다.

    OpenRouter(qwen/qwen-image-3-pro)가 더 싸서 기본이지만 참조가 4장까지다.

        Provider rejections: Alibaba: input_references:
        must have between 0 and 4 items

    아이템이 다섯 이상인 코디는 넷만 남기면 무엇을 버려도 그 코디가 아니게
    되므로, 그때만 참조 14장을 받는 Gemini로 넘긴다.
    """

    def _plan(self, count: int, **kwargs):
        items = [{"s3_key": f"{n}.png", "category_large": "상의"} for n in range(count)]
        return plan_references(items)

    @override_settings(
        DAILY_LOOK_RENDER_MAX_REFERENCES=8, DAILY_LOOK_RENDER_GEMINI_THRESHOLD=5
    )
    def test_four_or_fewer_stays_on_openrouter(self) -> None:
        for count in (1, 2, 3, 4):
            plan = self._plan(count)
            self.assertEqual(plan.backend, BACKEND_OPENROUTER, f"{count}장")
            self.assertEqual(len(plan.keys), count)

    @override_settings(
        DAILY_LOOK_RENDER_MAX_REFERENCES=8, DAILY_LOOK_RENDER_GEMINI_THRESHOLD=5
    )
    def test_five_or_more_switches_to_gemini(self) -> None:
        for count in (5, 6, 8):
            plan = self._plan(count)
            self.assertEqual(plan.backend, BACKEND_GEMINI, f"{count}장")
            # Gemini로 갔으면 버리지 않는다 — 넘긴 이유가 그거다.
            self.assertEqual(len(plan.keys), count)

    @override_settings(
        DAILY_LOOK_RENDER_MAX_REFERENCES=99, DAILY_LOOK_RENDER_GEMINI_THRESHOLD=5
    )
    def test_gemini_has_its_own_ceiling(self) -> None:
        plan = self._plan(30)
        self.assertEqual(plan.backend, BACKEND_GEMINI)
        self.assertEqual(len(plan.keys), GEMINI_MAX_REFERENCES)

    @override_settings(
        DAILY_LOOK_RENDER_MAX_REFERENCES=99, DAILY_LOOK_RENDER_GEMINI_THRESHOLD=999
    )
    def test_openrouter_never_receives_more_than_four(self) -> None:
        """설정을 아무리 키워도 400을 다시 만들지 않는다."""
        plan = self._plan(30)
        self.assertEqual(plan.backend, BACKEND_OPENROUTER)
        self.assertEqual(len(plan.keys), OPENROUTER_MAX_REFERENCES)

    @override_settings(
        DAILY_LOOK_RENDER_MAX_REFERENCES=8, DAILY_LOOK_RENDER_GEMINI_THRESHOLD=4
    )
    def test_threshold_is_configurable(self) -> None:
        """'4장 이상은 Gemini'로 바꾸고 싶으면 env 한 줄이면 된다."""
        self.assertEqual(self._plan(4).backend, BACKEND_GEMINI)
        self.assertEqual(self._plan(3).backend, BACKEND_OPENROUTER)

    @override_settings(
        DAILY_LOOK_RENDER_MAX_REFERENCES=4, DAILY_LOOK_RENDER_GEMINI_THRESHOLD=5
    )
    def test_budget_is_an_operator_ceiling(self) -> None:
        """MAX_REFERENCES는 '돈을 얼마까지 쓸까'다. 그 아래로 잘리면 Gemini로 가지 않는다."""
        plan = self._plan(10)
        self.assertEqual(plan.backend, BACKEND_OPENROUTER)
        self.assertEqual(len(plan.keys), 4)


class GeminiRequestTests(TestCase):
    ITEMS = [
        {"s3_key": f"k/item_{n}.png", "category_large": "상의"} for n in range(5)
    ]

    @override_settings(
        GEMINI_API_KEY="test-key",
        DAILY_LOOK_RENDER_MAX_REFERENCES=8,
        DAILY_LOOK_RENDER_GEMINI_THRESHOLD=5,
        DAILY_LOOK_RENDER_ASPECT_RATIO="9:16",
        DAILY_LOOK_RENDER_RESOLUTION="1K",
    )
    @patch("apps.recommend.services.outfit_render.storage.download_for",
           return_value=PNG)
    @patch("apps.recommend.services.outfit_render.requests.post")
    def test_five_items_go_to_gemini_with_all_references(self, post, _download) -> None:
        post.return_value = Mock(
            status_code=200,
            json=Mock(return_value={
                "steps": [{"type": "model_output", "content": [
                    {"type": "image", "data": base64.b64encode(b"rendered").decode()}
                ]}]
            }),
        )
        plan = plan_references(self.ITEMS)
        image = _generate(bucket=BUCKET, reference_keys=plan.keys, backend=plan.backend)

        self.assertEqual(image, b"rendered")
        body = post.call_args.kwargs["json"]
        # OpenRouter가 아니라 Google API로 갔는가
        self.assertIn("interactions", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["headers"]["x-goog-api-key"], "test-key")
        # 다섯 장을 하나도 버리지 않았는가 (텍스트 1 + 이미지 5)
        images = [p for p in body["input"] if p["type"] == "image"]
        self.assertEqual(len(images), 5)
        self.assertEqual(body["input"][0]["type"], "text")
        # 전신이 담기려면 세로 비율이어야 한다
        self.assertEqual(body["response_format"]["aspect_ratio"], "9:16")
        self.assertEqual(body["response_format"]["image_size"], "1K")

    @override_settings(GEMINI_API_KEY="")
    def test_missing_key_is_reported_clearly(self) -> None:
        with self.assertRaises(RenderError) as caught:
            _generate(bucket=BUCKET, reference_keys=["a.png"], backend=BACKEND_GEMINI)
        self.assertIn("GEMINI_API_KEY", str(caught.exception))

    def test_generate_content_shape_is_also_understood(self) -> None:
        """엔드포인트 설정만 바꿔도 동작하도록 두 응답 모양을 모두 읽는다."""
        payload = {"candidates": [{"content": {"parts": [
            {"inlineData": {"mimeType": "image/png",
                            "data": base64.b64encode(b"alt").decode()}}
        ]}}]}
        self.assertEqual(_extract_image(payload), b"alt")

    def test_openrouter_shape_still_works(self) -> None:
        payload = {"data": [{"b64_json": base64.b64encode(b"legacy").decode()}]}
        self.assertEqual(_extract_image(payload), b"legacy")


JPEG = b"\xff\xd8\xff\xe0" + b"fake-jpeg-bytes"
RENDER_KEY_JPG = "goldenset/derived/v1/095/render_frontal.jpg"


class RenderFormatTests(TestCase):
    """착용 이미지의 형식은 백엔드가 정한다.

    Gemini는 JPEG만 내준다:

        The value 'image/png' is not supported for 'response_format.mime_type'.
        Supported values: 'image/jpeg'.

    (입력은 PNG 그대로 받는다. 이 제약은 출력에만 걸린다.)

    그래서 확장자를 .png로 못 박을 수 없다. .png 키에 JPEG를 넣어 두면 S3가
    Content-Type: image/png으로 내려보내고 일부 클라이언트가 못 연다.
    """

    ITEMS_5 = [
        {"s3_key": f"goldenset/derived/v1/095/item_{n:03d}.png",
         "category_large": "상의"}
        for n in range(5)
    ]

    def test_sniff_reads_the_bytes_not_the_header(self) -> None:
        self.assertEqual(_sniff(PNG), (".png", "image/png"))
        self.assertEqual(_sniff(JPEG), (".jpg", "image/jpeg"))

    def test_unknown_bytes_fall_back_without_crashing(self) -> None:
        """형식을 몰라도 추천은 살아남아야 한다."""
        self.assertEqual(_sniff(b"???")[0], DEFAULT_RENDER_EXTENSION)

    @override_settings(DAILY_LOOK_RENDER_GEMINI_THRESHOLD=5,
                       DAILY_LOOK_RENDER_MAX_REFERENCES=8)
    @patch("apps.recommend.services.outfit_render.storage.put_bytes_for")
    @patch("apps.recommend.services.outfit_render.storage.exists_for",
           return_value=False)
    @patch("apps.recommend.services.outfit_render._generate", return_value=JPEG)
    def test_jpeg_is_stored_as_jpg_with_the_right_content_type(
        self, _generate_mock, _exists, put
    ) -> None:
        ref = ensure_render(bucket=BUCKET, items=self.ITEMS_5)
        put.assert_called_once_with(BUCKET, RENDER_KEY_JPG, JPEG, "image/jpeg")
        self.assertEqual(ref.s3_key, RENDER_KEY_JPG)

    @patch("apps.recommend.services.outfit_render._generate")
    @patch("apps.recommend.services.outfit_render.storage.put_bytes_for")
    def test_existing_jpg_is_reused_even_though_png_is_the_default(
        self, put, generate
    ) -> None:
        """확장자 후보를 하나만 보면 이미 만든 이미지를 못 찾고 매번 다시 만든다.

        착용 이미지는 코디당 한 번만 만드는 게 이 기능의 요금 설계다. 여기가
        새면 비용이 사용자 수만큼 붙는다.
        """
        with patch(
            "apps.recommend.services.outfit_render.storage.exists_for",
            side_effect=lambda bucket, key: key.endswith(".jpg"),
        ):
            ref = ensure_render(bucket=BUCKET, items=ITEMS)

        self.assertEqual(ref.s3_key, RENDER_KEY_JPG)
        generate.assert_not_called()
        put.assert_not_called()

    def test_key_helper_takes_an_extension(self) -> None:
        self.assertEqual(render_key_for(ITEMS[0]["s3_key"], ".jpg"), RENDER_KEY_JPG)
        # 기본값은 그대로 .png — 기존에 저장된 이미지를 계속 찾을 수 있어야 한다.
        self.assertEqual(render_key_for(ITEMS[0]["s3_key"]), RENDER_KEY)


class GeminiOutputFormatTests(TestCase):
    @override_settings(
        GEMINI_API_KEY="test-key",
        DAILY_LOOK_RENDER_GEMINI_MIME_TYPE="image/jpeg",
    )
    @patch("apps.recommend.services.outfit_render.storage.download_for",
           return_value=PNG)
    @patch("apps.recommend.services.outfit_render.requests.post")
    def test_output_is_requested_as_jpeg_while_input_stays_png(
        self, post, _download
    ) -> None:
        """실제로 난 400을 그대로 못 박는다.

            The value 'image/png' is not supported for
            'response_format.mime_type'. Supported values: 'image/jpeg'.
        """
        post.return_value = Mock(
            status_code=200,
            json=Mock(return_value={"steps": [{"content": [
                {"type": "image", "data": base64.b64encode(JPEG).decode()}
            ]}]}),
        )
        _generate(bucket=BUCKET, reference_keys=["a.png"], backend=BACKEND_GEMINI)

        body = post.call_args.kwargs["json"]
        self.assertEqual(body["response_format"]["mime_type"], "image/jpeg")
        # 입력은 PNG 그대로다 — 400은 출력에만 걸린 제약이었다.
        images = [p for p in body["input"] if p["type"] == "image"]
        self.assertEqual(images[0]["mime_type"], "image/png")


class RenderGenderTests(TestCase):
    """유니섹스 코디는 남녀 모두에게 추천된다. 그러면 이미지도 갈려야 한다.

    키가 하나면 먼저 만든 쪽의 이미지가 반대 성별에게 그대로 나간다 — 남성
    사용자가 여성 모델 사진을 보게 된다. 성별 하드 룰을 검색에서만 지키고
    화면에서 깨뜨리는 셈이다.
    """

    ITEM = "goldenset/derived/v1/095/item_000.png"

    def test_key_differs_by_gender(self) -> None:
        men = render_key_for(self.ITEM, ".jpg", "male")
        women = render_key_for(self.ITEM, ".jpg", "female")
        self.assertNotEqual(men, women)
        self.assertTrue(men.endswith("render_frontal_men.jpg"), men)
        self.assertTrue(women.endswith("render_frontal_women.jpg"), women)

    def test_unknown_gender_uses_the_legacy_key(self) -> None:
        self.assertTrue(render_key_for(self.ITEM, ".png").endswith("render_frontal.png"))

    @patch("apps.recommend.services.outfit_render.storage.exists_for")
    def test_legacy_image_is_not_reused_when_gender_is_known(self, exists) -> None:
        """옛 키의 이미지는 모델 성별을 알 수 없다. 재사용하면 사고가 반복된다."""
        exists.side_effect = lambda bucket, key: key.endswith("render_frontal.png")
        self.assertIsNone(existing_render(BUCKET, self.ITEM, "male"))
        # 성별을 모를 때는 그대로 쓴다 (기존 자산을 버리지 않는다)
        self.assertIsNotNone(existing_render(BUCKET, self.ITEM, ""))

    @patch("apps.recommend.services.outfit_render.storage.exists_for")
    def test_each_gender_reuses_its_own_image(self, exists) -> None:
        exists.side_effect = lambda bucket, key: key.endswith("render_frontal_men.jpg")
        self.assertIsNotNone(existing_render(BUCKET, self.ITEM, "male"))
        self.assertIsNone(existing_render(BUCKET, self.ITEM, "female"))

    def test_prompt_names_the_model_gender(self) -> None:
        self.assertIn("남성", prompt_for("male"))
        self.assertNotIn("여성 모델", prompt_for("male"))
        self.assertIn("여성", prompt_for("female"))
        # 성별을 모르면 사람을 특정하지 않는다
        self.assertNotIn("남성", prompt_for(""))
        self.assertNotIn("여성", prompt_for(""))

    @override_settings(
        DAILY_LOOK_RENDER_MAX_REFERENCES=8,
        DAILY_LOOK_RENDER_GEMINI_THRESHOLD=99,
        OPENROUTER_API_KEY="test-key",
    )
    @patch("apps.recommend.services.outfit_render.storage.put_bytes_for")
    @patch("apps.recommend.services.outfit_render.storage.exists_for",
           return_value=False)
    @patch("apps.recommend.services.outfit_render.storage.download_for",
           return_value=PNG)
    @patch("apps.recommend.services.outfit_render.requests.post")
    def test_generated_image_is_stored_under_the_gender_key(
        self, post, _download, _exists, put
    ) -> None:
        post.return_value = Mock(
            status_code=200,
            json=Mock(return_value={"data": [{"b64_json": base64.b64encode(PNG).decode()}]}),
        )
        ref = ensure_render(bucket=BUCKET, items=ITEMS, gender="male")

        self.assertTrue(ref.s3_key.endswith("render_frontal_men.png"), ref.s3_key)
        self.assertTrue(put.call_args.args[1].endswith("render_frontal_men.png"))
        # 프롬프트에도 성별이 실렸는가
        self.assertIn("남성", post.call_args.kwargs["json"]["prompt"])
