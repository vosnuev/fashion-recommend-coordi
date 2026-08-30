import os
import unittest
from unittest.mock import Mock, patch

from util.product_indexer_trigger import trigger_product_indexer


class ProductIndexerTriggerTests(unittest.TestCase):
    def test_missing_url_disables_trigger(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("util.product_indexer_trigger.requests.post") as post,
        ):
            self.assertFalse(
                trigger_product_indexer(source="eleven", reason="sync_completed")
            )
        post.assert_not_called()

    def test_url_without_token_is_rejected(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"PRODUCT_INDEXER_TRIGGER_URL": "https://gpu.example/drain"},
                clear=True,
            ),
            patch("util.product_indexer_trigger.requests.post") as post,
        ):
            self.assertFalse(
                trigger_product_indexer(source="naver", reason="batch_completed")
            )
        post.assert_not_called()

    def test_success_sends_bearer_token_and_metadata(self) -> None:
        response = Mock(status_code=202)
        env = {
            "PRODUCT_INDEXER_TRIGGER_URL": "https://gpu.example/drain",
            "PRODUCT_INDEXER_TRIGGER_TOKEN": "secret-token",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "util.product_indexer_trigger.requests.post",
                return_value=response,
            ) as post,
        ):
            result = trigger_product_indexer(
                source="eleven",
                reason="sync_completed",
                tagged_count=3,
            )

        self.assertTrue(result)
        post.assert_called_once_with(
            "https://gpu.example/drain",
            json={
                "source": "eleven",
                "reason": "sync_completed",
                "tagged_count": 3,
            },
            headers={"Authorization": "Bearer secret-token"},
            timeout=10,
        )

    def test_transient_failure_retries_without_raising(self) -> None:
        failed = Mock(status_code=503)
        accepted = Mock(status_code=202)
        env = {
            "PRODUCT_INDEXER_TRIGGER_URL": "https://gpu.example/drain",
            "PRODUCT_INDEXER_TRIGGER_TOKEN": "secret-token",
            "PRODUCT_INDEXER_TRIGGER_MAX_RETRIES": "2",
            "PRODUCT_INDEXER_TRIGGER_RETRY_BASE_SECONDS": "1",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "util.product_indexer_trigger.requests.post",
                side_effect=[failed, accepted],
            ) as post,
            patch("util.product_indexer_trigger.time.sleep") as sleep,
        ):
            result = trigger_product_indexer(
                source="naver",
                reason="batch_completed",
            )

        self.assertTrue(result)
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
