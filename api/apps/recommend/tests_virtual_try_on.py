from __future__ import annotations

from unittest.mock import Mock

from django.test import SimpleTestCase

from apps.recommend.services.virtual_try_on import (
    DIRECT_PROMPT,
    MANNEQUIN_PROMPT,
    VirtualTryOnService,
    body_note,
)

PNG = b"\x89PNG\r\n\x1a\nimage"


class VirtualTryOnServiceTests(SimpleTestCase):
    def setUp(self) -> None:
        self.provider = Mock()
        self.provider.generate.return_value = (PNG, "image/png", {})
        self.service = VirtualTryOnService(provider=self.provider)

    def test_direct_fit_keeps_person_first_and_outfit_second(self) -> None:
        self.service.fit_person(PNG, PNG)

        call = self.provider.generate.call_args.kwargs
        self.assertEqual(call["prompt"], DIRECT_PROMPT)
        self.assertEqual(
            [ref.item.slot for ref in call["references"]],
            ["target_person", "outfit"],
        )
        self.assertIn("Do not slim, enlarge, reshape", call["prompt"])

    def test_mannequin_fit_is_one_edit_without_base_clothes(self) -> None:
        self.service.fit_mannequin(PNG, PNG)

        call = self.provider.generate.call_args.kwargs
        self.assertEqual(call["prompt"], MANNEQUIN_PROMPT)
        self.assertEqual(
            [ref.item.slot for ref in call["references"]],
            ["target_person", "outfit"],
        )
        self.assertIn("Do not add a base outfit", call["prompt"])


class BodyNoteTests(SimpleTestCase):
    """체형 정보를 프롬프트에 넣는 방식.

    사진에 이미 몸이 찍혀 있는데 치수를 주는 목적은 하나다 — **옷을 그 체형에 맞게
    앉히는 것**. 몸을 수치대로 고쳐 그리라는 뜻이 되면 그건 체형 보정이지 가상
    착장이 아니다.
    """

    def test_note_tells_the_model_to_fit_clothes_not_reshape_the_body(self) -> None:
        note = body_note(silhouette="rectangle", bmi_band="normal")

        self.assertIn("rectangle", note)
        self.assertIn("average", note)
        self.assertIn("Do not resize, reshape, or idealize the person", note)

    def test_unknown_axes_are_left_out(self) -> None:
        """모르는 값을 기본값으로 메우면 잘못된 체형으로 옷을 맞추게 된다."""
        self.assertEqual(body_note(silhouette="unknown", bmi_band="unknown"), "")
        self.assertEqual(body_note(), "")

    def test_partial_body_data_still_helps(self) -> None:
        note = body_note(silhouette="triangle", bmi_band="")

        self.assertIn("triangle", note)
        self.assertNotIn("average", note)

    def test_person_fit_appends_the_note(self) -> None:
        provider = Mock()
        provider.generate.return_value = (PNG, "image/png", {})
        service = VirtualTryOnService(provider=provider)

        service.fit_person(PNG, PNG, body_note(silhouette="hourglass"))

        prompt = provider.generate.call_args.kwargs["prompt"]
        self.assertTrue(prompt.startswith(DIRECT_PROMPT))
        self.assertIn("hourglass", prompt)

    def test_prompt_is_unchanged_without_body_data(self) -> None:
        """체형 정보가 없으면 예전과 한 글자도 다르지 않다 (캐시가 그대로 산다)."""
        provider = Mock()
        provider.generate.return_value = (PNG, "image/png", {})
        service = VirtualTryOnService(provider=provider)

        service.fit_person(PNG, PNG)

        self.assertEqual(provider.generate.call_args.kwargs["prompt"], DIRECT_PROMPT)
