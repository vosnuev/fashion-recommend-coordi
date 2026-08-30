from __future__ import annotations

import uuid
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.chat.serializers import ChatRunPersonaResultSerializer


class StylistResultSerializerTests(SimpleTestCase):
    def test_running_result_keeps_unavailable_fields_empty(self) -> None:
        execution = self._execution(
            persona_id="experimental",
            display_order=2,
            status="RUNNING",
            recommendation_result=None,
        )

        payload = ChatRunPersonaResultSerializer(execution).data

        self.assertEqual(payload["display_name"], "모험")
        self.assertIsNone(payload["result_id"])
        self.assertEqual(payload["message"], "")
        self.assertEqual(payload["validated_reason_codes"], [])
        self.assertIsNone(payload["card"])
        self.assertIsNone(payload["error"])

    def test_succeeded_and_failed_rows_have_distinct_contracts(self) -> None:
        result_id = uuid.uuid4()
        succeeded = self._execution(
            persona_id="minimal",
            display_order=1,
            status="SUCCEEDED",
            recommendation_result=SimpleNamespace(
                pk=result_id,
                persona_explanation="차분하게 정리했어요.",
                validated_reason_codes=["MINIMAL_COLOR_COHESION"],
                public_compositions=[],
            ),
        )
        failed = self._execution(
            persona_id="practical",
            display_order=3,
            status="FAILED",
            recommendation_result=None,
            error_code="PRACTICAL_FAILED",
            error_message="실용형 추천에 실패했습니다.",
        )

        succeeded_payload = ChatRunPersonaResultSerializer(succeeded).data
        failed_payload = ChatRunPersonaResultSerializer(failed).data

        self.assertEqual(str(succeeded_payload["result_id"]), str(result_id))
        self.assertEqual(succeeded_payload["message"], "차분하게 정리했어요.")
        self.assertEqual(
            succeeded_payload["validated_reason_codes"],
            ["MINIMAL_COLOR_COHESION"],
        )
        self.assertIsNone(succeeded_payload["card"])
        self.assertEqual(
            failed_payload["error"],
            {
                "code": "PRACTICAL_FAILED",
                "message": "실용형 추천에 실패했습니다.",
            },
        )

    @staticmethod
    def _execution(
        *,
        persona_id: str,
        display_order: int,
        status: str,
        recommendation_result,
        error_code: str = "",
        error_message: str = "",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            persona_id=persona_id,
            display_order=display_order,
            status=status,
            recommendation_result=recommendation_result,
            error_code=error_code,
            error_message=error_message,
            retry_count=0,
            alternative_status="IDLE",
            alternative_count=0,
            alternative_error_code="",
            alternative_error_message="",
            latency_ms=0,
            started_at=None,
            completed_at=None,
        )
