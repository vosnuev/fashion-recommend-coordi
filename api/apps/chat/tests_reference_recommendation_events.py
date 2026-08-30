from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

from config.observability import JsonFormatter
from django.test import SimpleTestCase

from apps.chat.models import ChatSession
from apps.chat.services.recommendation_pipeline import ChatRecommendationPipeline
from apps.chat.services.reference_recommendation_events import (
    EVENT_NAME,
    MATCH_NO_CANDIDATE,
    MATCH_STYLE_SIMILAR,
    MATCH_VISUAL_SIMILAR,
    STAGE_COMPOSER,
    STAGE_SIMILAR_SEARCH,
    STAGE_SNAPSHOT_VALIDATION,
    STAGE_VALIDATOR,
    STAGE_VECTOR_LOADING,
    STAGES,
    ReferenceRecommendationEventRecorder,
)
from apps.recommend.services.shared_reference_loader import ReferenceVectorNotFound


class ReferenceRecommendationEventRecorderTests(SimpleTestCase):
    @patch("apps.chat.services.reference_recommendation_events.logger.info")
    def test_success_event_contains_only_operational_fields(self, log_info: Mock) -> None:
        ticks = iter((10.0, 10.125))
        recorder = ReferenceRecommendationEventRecorder(
            run_id="run-17",
            recommendation_mode=ChatSession.Mode.WARDROBE_BASED,
            is_stylist=False,
            clock=lambda: next(ticks),
        )
        recorder.add_stage_duration(STAGE_SNAPSHOT_VALIDATION, 1.2)
        recorder.add_stage_duration(STAGE_VECTOR_LOADING, 8.3)
        recorder.add_stage_duration(STAGE_SIMILAR_SEARCH, 12.4)
        recorder.add_stage_duration(STAGE_COMPOSER, 19.5)
        recorder.add_stage_duration(STAGE_VALIDATOR, 2.6)
        recorder.select_match(match_result=MATCH_VISUAL_SIMILAR, similarity=0.91234567)

        recorder.success()
        recorder.success()

        log_info.assert_called_once()
        payload = log_info.call_args.kwargs["extra"]
        self.assertEqual(payload["event"], EVENT_NAME)
        self.assertEqual(payload["status"], "SUCCEEDED")
        self.assertEqual(payload["recommendation_mode"], "WARDROBE_BASED")
        self.assertEqual(payload["match_result"], MATCH_VISUAL_SIMILAR)
        self.assertEqual(payload["selected_similarity"], 0.912346)
        self.assertFalse(payload["fallback"])
        self.assertIsNone(payload["failure_code"])
        self.assertFalse(payload["is_stylist"])
        self.assertEqual(set(payload["stage_durations_ms"]), set(STAGES))
        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in (
            "reference_snapshot",
            "image_vector",
            "text_vector",
            "friend_name",
        ):
            self.assertNotIn(forbidden, serialized)

    @patch("apps.chat.services.reference_recommendation_events.logger.info")
    def test_failure_uses_stable_code_without_exception_message(self, log_info: Mock) -> None:
        ticks = iter((20.0, 20.05))
        recorder = ReferenceRecommendationEventRecorder(
            run_id="run-18",
            recommendation_mode=ChatSession.Mode.NEW_ITEM,
            is_stylist=True,
            clock=lambda: next(ticks),
        )

        recorder.failure(ReferenceVectorNotFound("친구 이름이나 저장소 주소가 섞일 수 있음"))

        payload = log_info.call_args.kwargs["extra"]
        self.assertEqual(payload["status"], "FAILED")
        self.assertEqual(payload["match_result"], MATCH_NO_CANDIDATE)
        self.assertEqual(payload["failure_code"], "REFERENCE_VECTOR_NOT_FOUND")
        self.assertIsNone(payload["selected_similarity"])
        self.assertTrue(payload["is_stylist"])
        self.assertNotIn("저장소 주소", json.dumps(payload, ensure_ascii=False))

    @patch("apps.chat.services.reference_recommendation_events.logger.info")
    def test_style_match_marks_fallback(self, log_info: Mock) -> None:
        ticks = iter((30.0, 30.01))
        recorder = ReferenceRecommendationEventRecorder(
            run_id="run-19",
            recommendation_mode=ChatSession.Mode.WARDROBE_BASED,
            is_stylist=False,
            clock=lambda: next(ticks),
        )
        recorder.select_match(match_result=MATCH_STYLE_SIMILAR, similarity=0.54)

        recorder.success()

        self.assertTrue(log_info.call_args.kwargs["extra"]["fallback"])


class ReferenceRecommendationPipelineEventTests(SimpleTestCase):
    @patch("apps.chat.services.recommendation_pipeline.ReferenceRecommendationEventRecorder")
    def test_reference_run_emits_one_success_event(self, recorder_class: Mock) -> None:
        pipeline = object.__new__(ChatRecommendationPipeline)
        expected = Mock()
        pipeline._generate_candidates = Mock(return_value=expected)
        recorder = recorder_class.return_value
        run = SimpleNamespace(
            pk="run-success",
            response_mode=ChatSession.ResponseMode.STYLIST,
            reference_snapshot={"type": "SHARED_WARDROBE_ITEM"},
            session=SimpleNamespace(mode=ChatSession.Mode.NEW_ITEM),
        )

        actual = pipeline.generate_candidates(
            run=run,
            context={},
            analysis=Mock(),
        )

        self.assertIs(actual, expected)
        recorder_class.assert_called_once_with(
            run_id="run-success",
            recommendation_mode="NEW_ITEM",
            is_stylist=True,
        )
        recorder.success.assert_called_once_with()
        recorder.failure.assert_not_called()

    @patch("apps.chat.services.recommendation_pipeline.ReferenceRecommendationEventRecorder")
    def test_reference_run_emits_one_failure_event(self, recorder_class: Mock) -> None:
        pipeline = object.__new__(ChatRecommendationPipeline)
        error = RuntimeError("민감할 수 있는 원문")
        pipeline._generate_candidates = Mock(side_effect=error)
        recorder = recorder_class.return_value
        run = SimpleNamespace(
            pk="run-failure",
            response_mode=ChatSession.ResponseMode.DEFAULT,
            reference_snapshot={"type": "SHARED_WARDROBE_ITEM"},
            session=SimpleNamespace(mode=ChatSession.Mode.WARDROBE_BASED),
        )

        with self.assertRaises(RuntimeError):
            pipeline.generate_candidates(run=run, context={}, analysis=Mock())

        recorder.failure.assert_called_once_with(error)
        recorder.success.assert_not_called()


class ReferenceRecommendationJsonFormatterTests(SimpleTestCase):
    def test_formatter_keeps_metric_fields_and_drops_sensitive_extras(self) -> None:
        record = logging.LogRecord(
            "apps.chat.reference_recommendation",
            logging.INFO,
            __file__,
            1,
            "레퍼런스 추천 운영 이벤트",
            (),
            None,
        )
        record.event = EVENT_NAME
        record.run_id = "run-20"
        record.recommendation_mode = "NEW_ITEM"
        record.match_result = MATCH_VISUAL_SIMILAR
        record.selected_similarity = 0.88
        record.fallback = False
        record.stage_durations_ms = {stage: 1.0 for stage in STAGES}
        record.failure_code = None
        record.is_stylist = False
        record.reference_snapshot = {"friend_name": "저장 금지"}
        record.raw_vector = [0.1, 0.2]

        payload = json.loads(JsonFormatter().format(record))

        self.assertEqual(payload["match_result"], MATCH_VISUAL_SIMILAR)
        self.assertEqual(payload["stage_durations_ms"][STAGE_COMPOSER], 1.0)
        self.assertNotIn("reference_snapshot", payload)
        self.assertNotIn("raw_vector", payload)

