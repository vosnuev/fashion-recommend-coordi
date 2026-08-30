from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import SimpleTestCase

from apps.chat.services.reference_recommendation_events import EVENT_NAME, STAGES
from apps.chat.services.reference_recommendation_metrics import (
    CloudWatchReferenceRecommendationEventSource,
    ReferenceRecommendationMetricsQuery,
    ReferenceRecommendationMetricsQueryTruncated,
    aggregate_reference_recommendation_metrics,
)

STARTED_AT = datetime(2026, 8, 1, tzinfo=UTC)
ENDED_AT = datetime(2026, 8, 8, tzinfo=UTC)


def _event(
    *,
    minute: int,
    mode: str,
    stylist: bool,
    status: str,
    match: str,
    similarity: float | None,
    failure_code: str | None = None,
    duration_ms: float = 100.0,
) -> dict:
    return {
        "timestamp": (STARTED_AT + timedelta(minutes=minute)).isoformat(),
        "event": EVENT_NAME,
        "recommendation_mode": mode,
        "is_stylist": stylist,
        "status": status,
        "match_result": match,
        "selected_similarity": similarity,
        "fallback": match == "STYLE_SIMILAR",
        "failure_code": failure_code,
        "duration_ms": duration_ms,
        "stage_durations_ms": {
            stage: float(index + 1) for index, stage in enumerate(STAGES)
        },
    }


class ReferenceRecommendationMetricsTests(SimpleTestCase):
    def setUp(self) -> None:
        self.events = [
            _event(
                minute=1,
                mode="WARDROBE_BASED",
                stylist=False,
                status="SUCCEEDED",
                match="VISUAL_SIMILAR",
                similarity=0.9,
                duration_ms=100,
            ),
            _event(
                minute=2,
                mode="WARDROBE_BASED",
                stylist=True,
                status="SUCCEEDED",
                match="STYLE_SIMILAR",
                similarity=0.6,
                duration_ms=200,
            ),
            _event(
                minute=3,
                mode="WARDROBE_BASED",
                stylist=False,
                status="FAILED",
                match="NO_CANDIDATE",
                similarity=None,
                failure_code="REFERENCE_ITEM_FORBIDDEN",
                duration_ms=50,
            ),
            _event(
                minute=4,
                mode="NEW_ITEM",
                stylist=True,
                status="SUCCEEDED",
                match="VISUAL_SIMILAR",
                similarity=0.8,
                duration_ms=300,
            ),
            _event(
                minute=5,
                mode="NEW_ITEM",
                stylist=False,
                status="FAILED",
                match="NO_CANDIDATE",
                similarity=None,
                failure_code="REFERENCE_VECTOR_NOT_FOUND",
                duration_ms=75,
            ),
            _event(
                minute=6,
                mode="NEW_ITEM",
                stylist=False,
                status="FAILED",
                match="NO_CANDIDATE",
                similarity=None,
                failure_code="REFERENCE_INDEX_MISMATCH",
                duration_ms=125,
            ),
            _event(
                minute=60,
                mode="WARDROBE_BASED",
                stylist=False,
                status="SUCCEEDED",
                match="VISUAL_SIMILAR",
                similarity=1.0,
            )
            | {"timestamp": (ENDED_AT + timedelta(minutes=1)).isoformat()},
        ]

    def test_aggregates_required_metrics_by_mode_and_response_mode(self) -> None:
        result = aggregate_reference_recommendation_metrics(
            self.events,
            query=ReferenceRecommendationMetricsQuery(
                started_at=STARTED_AT,
                ended_at=ENDED_AT,
            ),
        )

        overall = result["overall"]
        self.assertEqual(overall["total_count"], 6)
        self.assertEqual(overall["success_rate"], 0.5)
        self.assertEqual(overall["visual_similarity_success_rate"], 0.333333)
        self.assertEqual(overall["style_fallback_rate"], 0.166667)
        self.assertEqual(overall["no_candidate_rate"], 0.5)
        self.assertEqual(overall["average_similarity"], 0.767)
        self.assertEqual(overall["permission_error_count"], 1)
        self.assertEqual(overall["vector_missing_error_count"], 1)
        self.assertEqual(overall["index_mismatch_error_count"], 1)
        self.assertEqual(set(overall["average_stage_duration_ms"]), set(STAGES))

        self.assertEqual(result["by_mode"]["WARDROBE_BASED"]["total_count"], 3)
        self.assertEqual(result["by_mode"]["NEW_ITEM"]["total_count"], 3)
        self.assertEqual(
            result["by_response_mode"]["DEFAULT"]["success_rate"],
            0.25,
        )
        self.assertEqual(
            result["by_response_mode"]["STYLIST"]["success_rate"],
            1.0,
        )

    def test_applies_period_mode_and_response_filters(self) -> None:
        result = aggregate_reference_recommendation_metrics(
            self.events,
            query=ReferenceRecommendationMetricsQuery(
                started_at=STARTED_AT,
                ended_at=ENDED_AT,
                recommendation_mode="NEW_ITEM",
                is_stylist=False,
            ),
        )

        self.assertEqual(result["overall"]["total_count"], 2)
        self.assertEqual(result["filters"]["recommendation_mode"], "NEW_ITEM")
        self.assertEqual(result["filters"]["response_mode"], "DEFAULT")
        self.assertEqual(set(result["by_mode"]), {"NEW_ITEM"})
        self.assertEqual(set(result["by_response_mode"]), {"DEFAULT"})

    def test_zero_events_return_null_rates_instead_of_dividing_by_zero(self) -> None:
        result = aggregate_reference_recommendation_metrics(
            [],
            query=ReferenceRecommendationMetricsQuery(
                started_at=STARTED_AT,
                ended_at=ENDED_AT,
            ),
        )

        self.assertEqual(result["overall"]["total_count"], 0)
        self.assertIsNone(result["overall"]["success_rate"])
        self.assertIsNone(result["overall"]["average_similarity"])


class CloudWatchReferenceRecommendationEventSourceTests(SimpleTestCase):
    def test_fetches_json_event_with_period_and_mode_filter(self) -> None:
        event = _event(
            minute=1,
            mode="NEW_ITEM",
            stylist=True,
            status="SUCCEEDED",
            match="VISUAL_SIMILAR",
            similarity=0.88,
        )
        client = Mock()
        client.start_query.return_value = {"queryId": "query-1"}
        client.get_query_results.return_value = {
            "status": "Complete",
            "results": [
                [
                    {"field": "@timestamp", "value": event["timestamp"]},
                    {"field": "@message", "value": json.dumps(event)},
                ]
            ],
        }
        query = ReferenceRecommendationMetricsQuery(
            started_at=STARTED_AT,
            ended_at=ENDED_AT,
            recommendation_mode="NEW_ITEM",
            is_stylist=True,
        )
        source = CloudWatchReferenceRecommendationEventSource(
            log_group_name="/aws/ecs/fashion-api",
            region_name="ap-northeast-2",
            limit=100,
            client=client,
        )

        rows = source.fetch(query)

        self.assertEqual(rows, [event])
        call = client.start_query.call_args.kwargs
        self.assertEqual(call["logGroupName"], "/aws/ecs/fashion-api")
        self.assertIn('recommendation_mode = "NEW_ITEM"', call["queryString"])
        self.assertIn("is_stylist = true", call["queryString"])

    def test_refuses_silently_truncated_cloudwatch_results(self) -> None:
        event = _event(
            minute=1,
            mode="NEW_ITEM",
            stylist=False,
            status="SUCCEEDED",
            match="VISUAL_SIMILAR",
            similarity=0.9,
        )
        client = Mock()
        client.start_query.return_value = {"queryId": "query-2"}
        client.get_query_results.return_value = {
            "status": "Complete",
            "results": [
                [{"field": "@message", "value": json.dumps(event)}],
            ],
        }
        source = CloudWatchReferenceRecommendationEventSource(
            log_group_name="/aws/ecs/fashion-api",
            region_name="ap-northeast-2",
            limit=1,
            client=client,
        )

        with self.assertRaises(ReferenceRecommendationMetricsQueryTruncated):
            source.fetch(
                ReferenceRecommendationMetricsQuery(
                    started_at=STARTED_AT,
                    ended_at=ENDED_AT,
                )
            )


class ReferenceRecommendationMetricsCommandTests(SimpleTestCase):
    @patch(
        "apps.chat.management.commands.reference_recommendation_metrics._jsonl_events"
    )
    def test_command_outputs_period_and_mode_filtered_json(self, load_events: Mock) -> None:
        load_events.return_value = [
            _event(
                minute=1,
                mode="WARDROBE_BASED",
                stylist=False,
                status="SUCCEEDED",
                match="VISUAL_SIMILAR",
                similarity=0.91,
            )
        ]
        stdout = StringIO()

        call_command(
            "reference_recommendation_metrics",
            "--start",
            STARTED_AT.isoformat(),
            "--end",
            ENDED_AT.isoformat(),
            "--mode",
            "WARDROBE_BASED",
            "--response-mode",
            "DEFAULT",
            "--input",
            "events.jsonl",
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["overall"]["total_count"], 1)
        self.assertEqual(payload["filters"]["recommendation_mode"], "WARDROBE_BASED")
        self.assertEqual(payload["filters"]["response_mode"], "DEFAULT")
