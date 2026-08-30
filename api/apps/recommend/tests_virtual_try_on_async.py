"""가상 피팅 비동기 — 접수·폴링·재진입.

동기 시절에는 요청 스레드가 이미지 모델을 기다렸다. 앞단 프록시가 100초에서 끊어
524가 났고(터널), 그 연결이 곧 결과의 수명이라 화면을 나가면 만들던 것이 사라졌다.
여기서 지키는 것은 넷이다.

- 접수는 즉시 끝난다 (202) — 모델을 기다리지 않는다
- 화면을 나갔다 와도 그 룩의 마지막 작업이 조회된다
- 같은 사진·같은 코디는 다시 만들지 않는다 (캐시 적중이면 바로 완료)
- 남의 작업·오늘 나가지 않은 코디는 손댈 수 없다
"""

from __future__ import annotations

import base64
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.recommend.models import DailyLook, VirtualTryOnJob
from apps.recommend.services import daily_look as service
from apps.recommend.services import virtual_try_on_jobs
from apps.recommend.tests_daily_look_alternatives import _alternative

User = get_user_model()

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg=="
)


class _Generated:
    content = PNG
    media_type = "image/png"


class VirtualTryOnAsyncTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="vton-async")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.look = DailyLook.objects.create(
            user=self.user,
            look_date=service.today(),
            status=DailyLook.Status.SUCCEEDED,
            result=_alternative("095", render=True),
            alternatives=[_alternative("096", render=True)],
        )
        self.url = reverse(
            "recommend:daily-look-virtual-try-on", kwargs={"look_id": self.look.pk}
        )

        self.exists = self._patch("apps.recommend.services.storage.exists_for", False)
        self.put = self._patch("apps.recommend.services.storage.put_bytes_for", None)
        self.download = self._patch(
            "apps.recommend.services.storage.download_for", PNG
        )
        self.presign = self._patch(
            "apps.recommend.services.storage.presigned_get_for", "https://signed"
        )
        self.enqueue = self._patch(
            "apps.recommend.views.render_queue.enqueue_virtual_try_on", None
        )
        self._patch_ctx(
            "apps.recommend.services.virtual_try_on_jobs.settings"
            ".OUTFIT_RENDER_RESULT_BUCKET",
            "result-bucket",
        )

    def _patch(self, target, value):
        patcher = patch(target, return_value=value)
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def _patch_ctx(self, target, value):
        patcher = patch(target, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, **extra):
        return self.client.post(
            self.url,
            {
                "person_image": SimpleUploadedFile("me.png", PNG, content_type="image/png"),
                "mode": "mannequin",
                **extra,
            },
            format="multipart",
        )

    # ── 접수 ──────────────────────────────────────────────
    def test_request_is_accepted_without_generating(self):
        """모델을 기다리지 않는다 — 이게 524를 없앤 핵심이다."""
        with patch(
            "apps.recommend.services.virtual_try_on_jobs.VirtualTryOnService"
        ) as runner:
            response = self._post()

        self.assertEqual(response.status_code, 202)
        runner.assert_not_called()
        body = response.json()
        self.assertEqual(body["status"], "QUEUED")
        self.assertIsNotNone(body["poll_after_ms"])
        self.assertIn("생성 중", body["detail"])
        self.enqueue.assert_called_once()

    def test_person_photo_is_stored_for_the_worker(self):
        """워커가 나중에 읽어야 하므로 사진은 잠시 S3에 둔다(수명주기로 만료)."""
        self._post()

        job = VirtualTryOnJob.objects.get()
        self.assertTrue(job.person_s3_key)
        self.assertIn("person-tmp", job.person_s3_key)
        self.put.assert_called_once()

    def test_cache_hit_finishes_immediately(self):
        """같은 사진·같은 코디는 다시 만들지 않는다 — 폴링할 이유도 없다."""
        self.exists.return_value = True

        response = self._post()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "SUCCEEDED")
        self.assertTrue(body["cache_hit"])
        self.assertIsNone(body["poll_after_ms"])
        self.enqueue.assert_not_called()

    def test_queue_failure_is_reported_not_silently_lost(self):
        import redis

        self.enqueue.side_effect = redis.RedisError("down")

        response = self._post()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            VirtualTryOnJob.objects.get().status, VirtualTryOnJob.Status.FAILED
        )

    # ── 조회(재진입) ──────────────────────────────────────
    def test_reentry_shows_the_job_in_progress(self):
        """화면을 나갔다 와도 사진을 다시 고를 필요가 없다."""
        self._post()

        body = self.client.get(self.url).json()

        self.assertEqual(body["status"], "QUEUED")
        self.assertIsNotNone(body["job_id"])

    def test_reentry_shows_the_finished_image(self):
        self._post()
        job = VirtualTryOnJob.objects.get()
        virtual_try_on_jobs.mark_succeeded(
            job.pk, bucket="result-bucket", key="k.png",
            media_type="image/png", cache_hit=False,
        )

        body = self.client.get(self.url).json()

        self.assertEqual(body["status"], "SUCCEEDED")
        self.assertEqual(body["image_url"], "https://signed")
        self.assertIsNone(body["poll_after_ms"])

    def test_never_requested_look_reports_no_job(self):
        body = self.client.get(self.url).json()

        self.assertIsNone(body["status"])
        self.assertIsNone(body["job_id"])

    def test_alternative_has_its_own_job(self):
        """'다른 룩'마다 결과가 다르므로 조회 기준에 golden_id 가 들어간다."""
        self._post(golden_id="096")

        primary = self.client.get(self.url).json()
        alternative = self.client.get(f"{self.url}?golden_id=096").json()

        self.assertIsNone(primary["status"])
        self.assertEqual(alternative["status"], "QUEUED")
        self.assertEqual(alternative["golden_id"], "096")

    def test_another_users_job_is_not_visible(self):
        self._post()
        other = User.objects.create(username="vton-other")
        self.client.force_authenticate(other)

        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_requires_authentication(self):
        self.assertEqual(APIClient().get(self.url).status_code, 401)

    # ── 워커 ──────────────────────────────────────────────
    def test_worker_generates_and_stores_the_result(self):
        self._post()
        job = virtual_try_on_jobs.start(VirtualTryOnJob.objects.get().pk)
        runner = patch.object(
            virtual_try_on_jobs, "VirtualTryOnService"
        ).start()
        self.addCleanup(patch.stopall)
        runner.return_value.fit_mannequin.return_value = _Generated()

        done = virtual_try_on_jobs.run(job)

        self.assertEqual(done.status, VirtualTryOnJob.Status.SUCCEEDED)
        self.assertTrue(done.result_s3_key)
        runner.return_value.fit_mannequin.assert_called_once()

    def test_worker_only_picks_queued_jobs_once(self):
        """두 워커가 같은 작업을 두 번 만들지 않는다."""
        self._post()
        job_id = VirtualTryOnJob.objects.get().pk

        first = virtual_try_on_jobs.start(job_id)
        second = virtual_try_on_jobs.start(job_id)

        self.assertIsNotNone(first)
        self.assertIsNone(second)


class PersonModeTests(TestCase):
    """마네킹이 아니라 **사진 속 그 사람**에게 입힌다."""

    def setUp(self):
        self.user = User.objects.create(username="vton-person")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.look = DailyLook.objects.create(
            user=self.user,
            look_date=service.today(),
            status=DailyLook.Status.SUCCEEDED,
            result=_alternative("095", render=True),
            # body-measure 로 입력한 치수의 판정 결과 (추천 시점 스냅샷)
            body_profile={"silhouette": "rectangle", "bmi_band": "normal", "ratios": {}},
        )
        self.url = reverse(
            "recommend:daily-look-virtual-try-on", kwargs={"look_id": self.look.pk}
        )
        for target, value in [
            ("apps.recommend.services.storage.exists_for", False),
            ("apps.recommend.services.storage.put_bytes_for", None),
            ("apps.recommend.services.storage.download_for", PNG),
            ("apps.recommend.services.storage.presigned_get_for", "https://signed"),
            ("apps.recommend.views.render_queue.enqueue_virtual_try_on", None),
        ]:
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        ctx = patch(
            "apps.recommend.services.virtual_try_on_jobs.settings"
            ".OUTFIT_RENDER_RESULT_BUCKET",
            "result-bucket",
        )
        ctx.start()
        self.addCleanup(ctx.stop)

    def _post(self, **extra):
        return self.client.post(
            self.url,
            {
                "person_image": SimpleUploadedFile("me.png", PNG, content_type="image/png"),
                **extra,
            },
            format="multipart",
        )

    def test_default_mode_dresses_the_person(self):
        """mode 를 안 보내면 사람에게 입힌다 — 마네킹은 이제 선택지다."""
        self._post()

        self.assertEqual(VirtualTryOnJob.objects.get().mode, "person")

    def test_worker_passes_the_body_note_to_the_person_fit(self):
        self._post()
        job = virtual_try_on_jobs.start(VirtualTryOnJob.objects.get().pk)
        runner = patch.object(virtual_try_on_jobs, "VirtualTryOnService").start()
        self.addCleanup(patch.stopall)
        runner.return_value.fit_person.return_value = _Generated()

        virtual_try_on_jobs.run(job)

        runner.return_value.fit_mannequin.assert_not_called()
        note = runner.return_value.fit_person.call_args.args[2]
        self.assertIn("rectangle", note)
        self.assertIn("Do not resize, reshape", note)

    def test_body_type_changes_the_cache_key(self):
        """같은 사진·같은 코디라도 체형이 다르면 옷이 앉는 모양이 다르다."""
        rectangle = virtual_try_on_jobs.build_contract(
            person=PNG, outfit=PNG, mode="person",
            body_note_text=virtual_try_on_jobs.body_note_for(self.look),
        )
        DailyLook.objects.filter(pk=self.look.pk).update(
            body_profile={"silhouette": "triangle", "bmi_band": "normal", "ratios": {}}
        )
        self.look.refresh_from_db()
        triangle = virtual_try_on_jobs.build_contract(
            person=PNG, outfit=PNG, mode="person",
            body_note_text=virtual_try_on_jobs.body_note_for(self.look),
        )

        self.assertNotEqual(rectangle, triangle)

    def test_look_without_body_data_still_works(self):
        """체형을 아직 안 넣은 사용자도 입어볼 수 있어야 한다 — 문장만 빠진다."""
        DailyLook.objects.filter(pk=self.look.pk).update(body_profile={})
        self.look.refresh_from_db()

        self.assertEqual(virtual_try_on_jobs.body_note_for(self.look), "")
        self.assertEqual(self._post().status_code, 202)

    def test_mannequin_is_still_available(self):
        self._post(mode="mannequin")

        self.assertEqual(VirtualTryOnJob.objects.get().mode, "mannequin")
