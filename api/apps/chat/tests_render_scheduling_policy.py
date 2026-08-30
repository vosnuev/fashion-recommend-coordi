from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.chat.models import ChatSession
from apps.chat.services.recommendation_pipeline import ChatRecommendationPipeline


class RecommendationRenderSchedulingPolicyTests(SimpleTestCase):
    @patch("apps.chat.services.recommendation_pipeline.render_jobs.schedule_result")
    @patch("apps.chat.services.recommendation_pipeline.transaction.on_commit")
    def test_default_mode_schedules_render_after_commit(
        self,
        mock_on_commit: Mock,
        mock_schedule: Mock,
    ) -> None:
        mock_on_commit.side_effect = lambda callback: callback()
        run = SimpleNamespace(response_mode=ChatSession.ResponseMode.DEFAULT)

        ChatRecommendationPipeline._schedule_render_on_commit(
            run=run,
            result_id="default-result-id",
        )

        mock_on_commit.assert_called_once()
        mock_schedule.assert_called_once_with("default-result-id")

    @patch("apps.chat.services.recommendation_pipeline.render_jobs.schedule_result")
    @patch("apps.chat.services.recommendation_pipeline.transaction.on_commit")
    def test_stylist_mode_does_not_schedule_render(
        self,
        mock_on_commit: Mock,
        mock_schedule: Mock,
    ) -> None:
        run = SimpleNamespace(response_mode=ChatSession.ResponseMode.STYLIST)

        ChatRecommendationPipeline._schedule_render_on_commit(
            run=run,
            result_id="stylist-result-id",
        )

        mock_on_commit.assert_not_called()
        mock_schedule.assert_not_called()
