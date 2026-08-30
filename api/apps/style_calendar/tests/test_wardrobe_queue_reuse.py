import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from django.test import SimpleTestCase

from apps.wardrobe.services import jobs


class CalendarWardrobeQueueReuseTests(SimpleTestCase):
    @patch.object(jobs, "CALLBACK_URL", "https://api.example/internal/wardrobe/callback/")
    @patch.object(jobs, "QUEUE_KEY", "wardrobe:jobs")
    @patch.object(jobs.storage, "BUCKET", "wardrobe-bucket")
    @patch.object(jobs, "_redis")
    def test_existing_wardrobe_payload_contract_is_used(self, mock_redis) -> None:
        redis_client = MagicMock()
        mock_redis.return_value = redis_client
        job_id = UUID("11111111-1111-1111-1111-111111111111")
        job = SimpleNamespace(
            id=job_id,
            user_id=7,
            source_s3_key=f"wardrobe/7/{job_id}/original.jpg",
        )

        jobs.enqueue(job)

        queue_key, raw_payload = redis_client.lpush.call_args.args
        payload = json.loads(raw_payload)
        self.assertEqual(queue_key, "wardrobe:jobs")
        self.assertEqual(payload["job_id"], str(job_id))
        self.assertEqual(payload["user_id"], 7)
        self.assertEqual(
            payload["source"],
            {
                "bucket": "wardrobe-bucket",
                "key": f"wardrobe/7/{job_id}/original.jpg",
            },
        )
        self.assertEqual(payload["output_prefix"], f"wardrobe/7/{job_id}/")
        self.assertEqual(
            payload["callback_url"],
            "https://api.example/internal/wardrobe/callback/",
        )
