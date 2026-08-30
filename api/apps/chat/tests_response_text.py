from django.test import SimpleTestCase

from apps.chat.services.openai_adapter import (
    _ANALYZE_INSTRUCTIONS,
    _EXPLAIN_INSTRUCTIONS,
)
from apps.chat.services.persona_narration import PERSONA_NARRATION_INSTRUCTIONS
from apps.chat.services.response_text import normalize_assistant_text


class AssistantResponseTextTests(SimpleTestCase):
    def test_removes_only_supported_markdown_syntax(self) -> None:
        value = (
            "### 추천 룩\n"
            "- **아이보리 니트**를 골랐어요.\n"
            "*차분한 인상*으로 잘 어울려요."
        )

        self.assertEqual(
            normalize_assistant_text(value),
            "추천 룩\n아이보리 니트를 골랐어요.\n차분한 인상으로 잘 어울려요.",
        )

    def test_preserves_product_symbols_and_hashtags(self) -> None:
        value = (
            "#빈티지 4rab*823 EGSKOG#83\n"
            "-상품명 A*B와 A**B**\n"
            "추천 순위 #1"
        )

        self.assertEqual(normalize_assistant_text(value), value)

    def test_removes_horizontal_rule_without_collapsing_content(self) -> None:
        self.assertEqual(
            normalize_assistant_text("첫 문장\n---\n다음 문장"),
            "첫 문장\n\n다음 문장",
        )

    def test_all_user_facing_llm_prompts_share_tone_contract(self) -> None:
        for instructions in (
            _ANALYZE_INSTRUCTIONS,
            _EXPLAIN_INSTRUCTIONS,
            PERSONA_NARRATION_INSTRUCTIONS,
        ):
            with self.subTest(instructions=instructions[:30]):
                self.assertIn("친근하고 정중한 해요체", instructions)
                self.assertIn("마크다운", instructions)
                self.assertIn("이모지", instructions)
