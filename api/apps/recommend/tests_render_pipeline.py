from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

import redis
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.chat.models import ChatMessage, ChatRun, ChatSession
from apps.chat.services import identity as identity_service
from apps.recommend.models import (
    OutfitComposition,
    OutfitCompositionItem,
    OutfitRenderJob,
    RecommendationResult,
)
from apps.recommend.services import render_execution, render_jobs, render_queue
from apps.recommend.services.mixed_outfit_render import RenderedOutfit
from apps.recommend.services.render_cache import RenderCacheEntry, RenderResultCache
from apps.recommend.services.render_events import RenderEventStore

FINGERPRINT = "a" * 64
RENDER_FINGERPRINT = "b" * 64
PNG = b"\x89PNG\r\n\x1a\nrendered"


class RenderFixtureMixin:
    def create_card(self, identity, *, rank: int = 1) -> OutfitComposition:
        session = ChatSession.objects.create(
            identity=identity,
            mode=ChatSession.Mode.NEW_ITEM,
        )
        message = ChatMessage.objects.create(
            session=session,
            sequence=1,
            role=ChatMessage.Role.USER,
            content="코디 추천",
        )
        run = ChatRun.objects.create(
            session=session,
            request_message=message,
            status=ChatRun.Status.SUCCEEDED,
        )
        result = RecommendationResult.objects.create(
            identity=identity,
            session=session,
            run=run,
            mode=RecommendationResult.Mode.NEW_ITEM,
            dataset_version="goldenset-v1",
        )
        card = OutfitComposition.objects.create(
            result=result,
            rank=rank,
            status=OutfitComposition.Status.VALIDATED,
            composition_fingerprint=FINGERPRINT,
        )
        OutfitCompositionItem.objects.create(
            composition=card,
            position=1,
            slot="TOP",
            source_type=OutfitCompositionItem.SourceType.PRODUCT,
            source_id=f"product-{card.pk}",
            source_collection="products_naver_v1",
            source_point_id=f"point-{card.pk}",
            template_item_point_id="golden-top",
            image_ref="products/top.png",
        )
        return card


class RenderQueueTests(TestCase):
    @patch("apps.recommend.services.render_queue.get_client")
    def test_queue_payload_contains_only_job_id(self, get_client) -> None:
        render_queue.enqueue(SimpleNamespace(pk="job-1"))

        key, raw = get_client.return_value.lpush.call_args.args
        self.assertEqual(key, settings.OUTFIT_RENDER_QUEUE_PENDING_KEY)
        self.assertEqual(json.loads(raw), {"job_id": "job-1"})

    @patch("apps.recommend.services.render_queue.get_client")
    def test_retry_then_dead_letter(self, get_client) -> None:
        client = get_client.return_value
        client.hincrby.side_effect = [1, settings.OUTFIT_RENDER_QUEUE_MAX_RETRIES]
        raw = '{"job_id":"job-1"}'

        self.assertFalse(render_queue.retry_or_dead(raw, "job-1", "TEMPORARY"))
        self.assertTrue(render_queue.retry_or_dead(raw, "job-1", "TEMPORARY"))
        client.lpush.assert_any_call(settings.OUTFIT_RENDER_QUEUE_PENDING_KEY, raw)
        self.assertTrue(
            any(
                call.args[0] == settings.OUTFIT_RENDER_QUEUE_DEAD_KEY
                for call in client.lpush.call_args_list
            )
        )


class RenderEventAndCacheTests(TestCase):
    def test_event_store_replays_last_event_id(self) -> None:
        client = Mock()
        client.xadd.return_value = "1700000000000-0"
        client.xread.return_value = [
            (
                "stream",
                [
                    (
                        "1700000000000-0",
                        {"event": "processing", "data": '{"status":"PROCESSING"}'},
                    )
                ],
            )
        ]
        store = RenderEventStore(client=client)

        event_id = store.publish("job-1", "processing", {"status": "PROCESSING"})
        events = store.read("job-1", block_milliseconds=0)

        self.assertEqual(event_id, events[0].id)
        self.assertEqual(events[0].event, "processing")
        self.assertNotIn("block", client.xread.call_args.kwargs)

    def test_cache_round_trip_and_corrupt_value(self) -> None:
        client = Mock()
        entry = RenderCacheEntry(
            render_fingerprint=RENDER_FINGERPRINT,
            output_s3_bucket="render-bucket",
            output_s3_key="render-key",
            output_media_type="image/png",
            output_bytes=len(PNG),
            provider="openrouter",
            model="qwen/qwen-image-3-pro",
            prompt_version="v1",
            reference_count=3,
            usage={"cost": 0.1},
        )
        cache = RenderResultCache(client=client)

        cache.set(entry)
        raw = client.setex.call_args.args[2]
        client.get.return_value = raw
        self.assertEqual(cache.get(RENDER_FINGERPRINT), entry)

        client.get.return_value = "not-json"
        self.assertIsNone(cache.get(RENDER_FINGERPRINT))

    def test_redis_cache_failure_is_best_effort(self) -> None:
        client = Mock()
        client.get.side_effect = redis.ConnectionError("down")
        client.setex.side_effect = redis.ConnectionError("down")
        cache = RenderResultCache(client=client)
        entry = RenderCacheEntry(
            RENDER_FINGERPRINT,
            "bucket",
            "key",
            "image/png",
            1,
            "provider",
            "model",
            "prompt",
            1,
            {},
        )

        self.assertIsNone(cache.get(RENDER_FINGERPRINT))
        cache.set(entry)


@override_settings(
    OUTFIT_RENDER_RESULT_BUCKET="render-bucket",
    OUTFIT_RENDER_RESULT_PREFIX="outfit-renders/test",
)
class RenderExecutionTests(RenderFixtureMixin, TestCase):
    def setUp(self) -> None:
        user = get_user_model().objects.create_user(username="render-executor")
        identity = identity_service.get_or_create_member_identity(user)
        self.card = self.create_card(identity)
        self.job, _ = render_jobs.prepare_job(self.card)
        self.job = render_jobs.start(self.job.pk)
        assert self.job is not None

    @patch(
        "apps.recommend.services.render_artifacts.storage.metadata_for",
        return_value=None,
    )
    @patch("apps.recommend.services.render_artifacts.storage.put_bytes_for")
    def test_generates_stores_and_caches_result(self, put_bytes, _metadata) -> None:
        renderer = Mock()
        renderer.render_request.return_value = RenderedOutfit(
            content=PNG,
            media_type="image/png",
            provider="openrouter",
            model="qwen/qwen-image-3-pro",
            prompt_version="mixed-v1",
            composition_fingerprint=FINGERPRINT,
            reference_count=1,
            usage={"cost": 0.04},
        )
        cache = Mock()
        cache.get.return_value = None

        completed = render_execution.execute(
            self.job,
            renderer=renderer,
            cache=cache,
        )

        self.assertEqual(completed.status, OutfitRenderJob.Status.SUCCEEDED)
        self.assertFalse(completed.cache_hit)
        self.assertEqual(completed.output_s3_bucket, "render-bucket")
        self.assertNotIn(str(self.card.result.identity_id), completed.output_s3_key)
        put_bytes.assert_called_once_with(
            "render-bucket",
            completed.output_s3_key,
            PNG,
            "image/png",
        )
        cache.set.assert_called_once()

    @patch(
        "apps.recommend.services.render_artifacts.storage.exists_for", return_value=True
    )
    def test_redis_cache_hit_skips_provider(self, _exists) -> None:
        entry = RenderCacheEntry(
            render_fingerprint=self.job.render_fingerprint,
            output_s3_bucket="render-bucket",
            output_s3_key="cached/render",
            output_media_type="image/png",
            output_bytes=len(PNG),
            provider="openrouter",
            model="qwen/qwen-image-3-pro",
            prompt_version="mixed-v1",
            reference_count=1,
            usage={},
        )
        cache = Mock()
        cache.get.return_value = entry
        renderer = Mock()

        completed = render_execution.execute(
            self.job,
            renderer=renderer,
            cache=cache,
        )

        self.assertTrue(completed.cache_hit)
        self.assertEqual(completed.output_s3_key, "cached/render")
        renderer.render_request.assert_not_called()


class RenderApiTests(RenderFixtureMixin, TestCase):
    def setUp(self) -> None:
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="render-owner")
        self.other_user = user_model.objects.create_user(username="render-other")
        self.identity = identity_service.get_or_create_member_identity(self.user)
        self.other_identity = identity_service.get_or_create_member_identity(
            self.other_user
        )
        self.card = self.create_card(self.identity)
        self.url = reverse(
            "recommend:recommendation-card-render",
            args=[self.card.result_id, self.card.pk],
        )
        self.client.force_authenticate(self.user)

    @patch("apps.recommend.services.render_events.RenderEventStore")
    @patch("apps.recommend.services.render_queue.enqueue")
    def test_post_is_idempotent_and_enqueues_job_reference(
        self, enqueue, events
    ) -> None:
        first = self.client.post(self.url, {}, format="json")
        second = self.client.post(self.url, {}, format="json")

        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(second.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(first.data["job_id"], second.data["job_id"])
        self.assertEqual(OutfitRenderJob.objects.count(), 1)
        self.assertIsNotNone(OutfitRenderJob.objects.get().enqueued_at)
        self.assertEqual(enqueue.call_count, 2)
        self.assertEqual(events.return_value.publish.call_count, 2)

    @patch("apps.recommend.services.render_events.RenderEventStore")
    @patch("apps.recommend.services.render_queue.enqueue")
    def test_post_creates_job_only_for_selected_card(
        self,
        enqueue,
        _events,
    ) -> None:
        other_card = OutfitComposition.objects.create(
            result=self.card.result,
            rank=2,
            status=OutfitComposition.Status.VALIDATED,
            composition_fingerprint="c" * 64,
        )

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(OutfitRenderJob.objects.filter(composition=self.card).exists())
        self.assertFalse(
            OutfitRenderJob.objects.filter(composition=other_card).exists()
        )
        enqueue.assert_called_once()

    def test_render_failure_does_not_change_recommendation_or_run_status(self) -> None:
        job, _ = render_jobs.prepare_job(self.card)
        started = render_jobs.start(job.pk)
        self.assertIsNotNone(started)

        failed = render_jobs.mark_failed(
            job.pk,
            error_code="PROVIDER_FAILED",
            error_message="이미지 제공자 호출 실패",
        )
        self.card.result.refresh_from_db()
        self.card.result.run.refresh_from_db()

        self.assertEqual(failed.status, OutfitRenderJob.Status.FAILED)
        self.assertEqual(self.card.result.run.status, ChatRun.Status.SUCCEEDED)
        self.assertTrue(self.card.result.is_current)

    @patch("apps.recommend.services.render_queue.enqueue")
    def test_queue_failure_is_terminal_and_retryable_by_new_post(self, enqueue) -> None:
        enqueue.side_effect = redis.ConnectionError("down")

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["status"], OutfitRenderJob.Status.FAILED)
        self.assertEqual(
            response.data["error"]["code"], "OUTFIT_RENDER_QUEUE_UNAVAILABLE"
        )

    @patch("apps.recommend.services.render_queue.enqueue")
    def test_other_identity_cannot_create_or_read_render(self, enqueue) -> None:
        self.client.force_authenticate(self.other_user)

        post = self.client.post(self.url, {}, format="json")
        get = self.client.get(self.url)

        self.assertEqual(post.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(get.status_code, status.HTTP_404_NOT_FOUND)
        enqueue.assert_not_called()

    @patch("apps.recommend.services.render_events.RenderEventStore")
    @patch("apps.recommend.services.render_queue.enqueue")
    def test_result_scheduler_creates_render_job_for_each_card(
        self, enqueue, _events
    ) -> None:
        second = OutfitComposition.objects.create(
            result=self.card.result,
            rank=2,
            status=OutfitComposition.Status.VALIDATED,
            composition_fingerprint="c" * 64,
        )

        jobs = render_jobs.schedule_result(self.card.result_id)

        self.assertEqual(
            {job.composition_id for job in jobs}, {self.card.pk, second.pk}
        )
        self.assertEqual(enqueue.call_count, 2)

    @patch(
        "apps.recommend.serializers.storage.presigned_get_for",
        return_value="https://signed.example/render",
    )
    def test_success_response_exposes_signed_url_but_not_s3_location(
        self, _signed
    ) -> None:
        job, _ = render_jobs.prepare_job(self.card)
        job.status = OutfitRenderJob.Status.SUCCEEDED
        job.output_s3_bucket = "private-bucket"
        job.output_s3_key = "private/key"
        job.output_media_type = "image/png"
        job.output_bytes = len(PNG)
        job.save()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["image_url"], "https://signed.example/render")
        self.assertNotIn("output_s3_bucket", response.data)
        self.assertNotIn("output_s3_key", response.data)

    @patch(
        "apps.recommend.serializers.storage.presigned_get_for",
        return_value="https://signed.example/render",
    )
    @patch("apps.recommend.views.RenderEventStore")
    def test_terminal_sse_falls_back_to_owned_database_job(
        self, events, _signed
    ) -> None:
        job, _ = render_jobs.prepare_job(self.card)
        job.status = OutfitRenderJob.Status.SUCCEEDED
        job.output_s3_bucket = "private-bucket"
        job.output_s3_key = "private/key"
        job.output_media_type = "image/png"
        job.save()
        events.return_value.read.return_value = []

        response = self.client.get(
            reverse("recommend:outfit-render-events", args=[job.pk]),
            HTTP_ACCEPT="text/event-stream",
        )
        body = b"".join(response.streaming_content).decode()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["X-Accel-Buffering"], "no")
        self.assertIn("event: completed", body)
        self.assertIn("https://signed.example/render", body)

    def test_other_identity_cannot_subscribe_to_job_events(self) -> None:
        job, _ = render_jobs.prepare_job(self.card)
        self.client.force_authenticate(self.other_user)

        response = self.client.get(
            reverse("recommend:outfit-render-events", args=[job.pk]),
            HTTP_ACCEPT="text/event-stream",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class RenderWorkerTests(RenderFixtureMixin, TestCase):
    def setUp(self) -> None:
        user = get_user_model().objects.create_user(username="render-worker")
        identity = identity_service.get_or_create_member_identity(user)
        self.card = self.create_card(identity)
        self.job, _ = render_jobs.prepare_job(self.card)

    @patch(
        "apps.recommend.management.commands.run_outfit_render_worker.RenderEventStore"
    )
    @patch(
        "apps.recommend.management.commands.run_outfit_render_worker.render_execution.execute"
    )
    @patch("apps.recommend.management.commands.run_outfit_render_worker.render_queue")
    def test_worker_processes_and_acks_success(self, queue, execute, _events) -> None:
        raw = json.dumps({"job_id": str(self.job.pk)})
        queue.recover_processing.return_value = []
        queue.fetch.return_value = raw

        def complete(job):
            return render_jobs.mark_succeeded(
                job.pk,
                values={
                    "output_s3_bucket": "bucket",
                    "output_s3_key": "key",
                    "output_media_type": "image/png",
                    "output_bytes": len(PNG),
                    "provider": "openrouter",
                    "model": "qwen/qwen-image-3-pro",
                    "prompt_version": "v1",
                    "reference_count": 1,
                    "usage": {},
                },
                cache_hit=False,
            )

        execute.side_effect = complete

        call_command("run_outfit_render_worker", "--once", stdout=StringIO())

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, OutfitRenderJob.Status.SUCCEEDED)
        queue.ack.assert_called_once_with(raw, str(self.job.pk))

    @patch(
        "apps.recommend.management.commands.run_outfit_render_worker.RenderEventStore"
    )
    @patch("apps.recommend.management.commands.run_outfit_render_worker.render_queue")
    def test_worker_recovery_resets_processing_job(self, queue, _events) -> None:
        OutfitRenderJob.objects.filter(pk=self.job.pk).update(
            status=OutfitRenderJob.Status.PROCESSING
        )
        raw = json.dumps({"job_id": str(self.job.pk)})
        queue.recover_processing.return_value = [raw]
        queue.fetch.return_value = None

        call_command("run_outfit_render_worker", "--once", stdout=StringIO())

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, OutfitRenderJob.Status.QUEUED)
