"""'다른 룩' — 차순위 후보와 그 착용 이미지.

후보는 화면이 그대로 그리는 값이라, 어긋나면 사용자가 겪는 것은 다음 넷이다.

- '다른 룩'을 눌러도 같은 룩이 나온다 (후보를 안 담았거나 대표 룩이 섞임)
- 후보 카드가 종일 사진 없이 남는다 (이미지 작업이 안 걸림)
- 다른 룩을 보다 저장했더니 대표 룩이 담긴다 (golden_id 미전달)
- 남의 코디를 담을 수 있다 (golden_id 검증 누락)
"""

from __future__ import annotations

import base64
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.lookbook.models import LookbookPost
from apps.recommend.models import DailyLook
from apps.recommend.services import daily_look as service
from apps.recommend.services import daily_look_save
from apps.recommend.tests_daily_look import CONTEXT, _FakeCandidate

User = get_user_model()

GOLDEN_BUCKET = "skn28-cozy3"
#: 진짜 1x1 PNG. 시리얼라이저의 ImageField 가 Pillow 로 열어보므로 흉내만 낸
#: 바이트열은 400 으로 떨어진다.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _alternative(golden_id: str, *, render: bool = False) -> dict:
    """result 와 같은 스키마의 후보 하나."""
    return {
        "golden_id": golden_id,
        "headline": "가볍게",
        "rationale_ko": "...",
        "tags": [],
        "styling_tips": [],
        "generated_by": "template",
        "render_image": (
            {
                "s3_bucket": GOLDEN_BUCKET,
                "s3_key": f"goldenset/derived/v1/{golden_id}/render_frontal_men.jpg",
            }
            if render
            else None
        ),
        "outfit_image": None,
        "items": [
            {
                "item_key": f"{golden_id}#000",
                "name": "화이트 셔츠",
                "category": "상의",
                "s3_bucket": GOLDEN_BUCKET,
                "s3_key": f"goldenset/derived/v1/{golden_id}/item_000.png",
            }
        ],
    }


class _FakeRef:
    def __init__(self, bucket: str, key: str) -> None:
        self.s3_bucket = bucket
        self.s3_key = key

    def as_dict(self) -> dict:
        return {"s3_bucket": self.s3_bucket, "s3_key": self.s3_key}


class BuildAlternativesTests(TestCase):
    """추천을 만들 때 후보도 함께 굳힌다."""

    def setUp(self):
        self.user = User.objects.create(username="alt1")
        self.look = DailyLook.objects.create(
            user=self.user,
            look_date=service.today(),
            weather=CONTEXT["weather"],
            body=CONTEXT["body"],
            body_profile={"silhouette": "inverted", "bmi_band": "normal", "ratios": {}},
            pursuit=CONTEXT["pursuit"],
        )
        # 이미지·문장·큐는 여기서 볼 대상이 아니다.
        for target in ("_attach_render", "_enrich_with_copy", "_schedule_alternative_renders"):
            patcher = patch.object(service, target)
            self.addCleanup(patcher.stop)
            patcher.start()

    @patch("apps.recommend.services.daily_look.retrieve_outfits")
    def test_alternatives_follow_the_chosen_look(self, retrieve):
        retrieve.return_value = [
            _FakeCandidate("095", score=88.0),
            _FakeCandidate("096", score=60.0),
            _FakeCandidate("097", score=42.0),
        ]

        service.run(self.look)

        self.look.refresh_from_db()
        self.assertEqual(self.look.result["golden_id"], "095")
        # 채택된 1위는 후보에 다시 담기지 않는다 — 담으면 '다른 룩'이 같은 룩을 돈다.
        self.assertEqual(
            [a["golden_id"] for a in self.look.alternatives], ["096", "097"]
        )

    @patch("apps.recommend.services.daily_look.retrieve_outfits")
    def test_alternatives_use_the_same_schema_as_result(self, retrieve):
        """프론트가 카드 한 벌 그리는 코드를 그대로 쓴다."""
        retrieve.return_value = [_FakeCandidate("095"), _FakeCandidate("096")]

        service.run(self.look)

        self.look.refresh_from_db()
        alternative = self.look.alternatives[0]
        self.assertEqual(sorted(alternative), sorted(self.look.result))
        self.assertTrue(alternative["items"][0]["s3_key"])
        # 문장은 템플릿이다 — LLM 호출이 후보 수만큼 늘지 않는다.
        self.assertEqual(alternative["generated_by"], "template")
        # 이미지는 별도 작업이 채운다.
        self.assertIsNone(alternative["render_image"])

    @patch("apps.recommend.services.daily_look.retrieve_outfits")
    def test_limit_bounds_the_render_cost(self, retrieve):
        """후보 수 = 만들 이미지 수. 환경변수로 조일 수 있어야 한다."""
        retrieve.return_value = [_FakeCandidate(str(i)) for i in range(5)]

        with patch.object(service, "ALTERNATIVE_LIMIT", 1):
            service.run(self.look)

        self.look.refresh_from_db()
        self.assertEqual(len(self.look.alternatives), 1)

    @patch("apps.recommend.services.daily_look.retrieve_outfits")
    def test_single_candidate_has_no_alternatives(self, retrieve):
        retrieve.return_value = [_FakeCandidate("095")]

        service.run(self.look)

        self.look.refresh_from_db()
        self.assertEqual(self.look.alternatives, [])


class AlternativeRenderTests(TestCase):
    """후보 착용 이미지를 만드는 큐 작업."""

    def setUp(self):
        self.user = User.objects.create(username="alt2")
        self.look = DailyLook.objects.create(
            user=self.user,
            look_date=service.today(),
            body=CONTEXT["body"],
            status=DailyLook.Status.SUCCEEDED,
            result={"golden_id": "095", "headline": "가볍게", "items": []},
            alternatives=[_alternative("096"), _alternative("097")],
        )

    @patch("apps.recommend.services.daily_look.settings.OUTFIT_RENDER_RESULT_BUCKET", "")
    @patch("apps.recommend.services.daily_look.outfit_render.ensure_render")
    def test_fills_every_missing_alternative(self, ensure):
        ensure.side_effect = lambda **kw: _FakeRef(kw["bucket"], "render/" + kw["items"][0]["s3_key"])

        filled = service.run_alternative_renders(str(self.look.pk))

        self.assertEqual(filled, 2)
        self.look.refresh_from_db()
        self.assertTrue(all(a["render_image"] for a in self.look.alternatives))
        # 대표 룩은 건드리지 않는다 — 조회 경로가 같은 순간 result 를 다시 쓴다.
        self.assertEqual(self.look.result["golden_id"], "095")

    @patch("apps.recommend.services.daily_look.settings.OUTFIT_RENDER_RESULT_BUCKET", "")
    @patch("apps.recommend.services.daily_look.outfit_render.ensure_render")
    def test_one_failure_does_not_stop_the_rest(self, ensure):
        """부가 기능이라 전부-아니면-전무로 다룰 이유가 없다."""
        def _render(**kw):
            if "096" in kw["items"][0]["s3_key"]:
                raise RuntimeError("provider 500")
            return _FakeRef(kw["bucket"], "render/097.jpg")

        ensure.side_effect = _render

        filled = service.run_alternative_renders(str(self.look.pk))

        self.assertEqual(filled, 1)
        self.look.refresh_from_db()
        self.assertIsNone(self.look.alternatives[0]["render_image"])
        self.assertIsNotNone(self.look.alternatives[1]["render_image"])

    @patch("apps.recommend.services.daily_look.outfit_render.ensure_render")
    def test_skips_looks_that_are_not_succeeded(self, ensure):
        DailyLook.objects.filter(pk=self.look.pk).update(
            status=DailyLook.Status.QUEUED
        )

        self.assertEqual(service.run_alternative_renders(str(self.look.pk)), 0)
        ensure.assert_not_called()

    @patch("apps.recommend.services.daily_look.settings.OUTFIT_RENDER_RESULT_BUCKET", "")
    @patch("apps.recommend.services.daily_look.outfit_render.ensure_render")
    def test_already_rendered_alternatives_are_not_remade(self, ensure):
        DailyLook.objects.filter(pk=self.look.pk).update(
            alternatives=[_alternative("096", render=True), _alternative("097", render=True)]
        )

        self.assertEqual(service.run_alternative_renders(str(self.look.pk)), 0)
        ensure.assert_not_called()


class ScheduleAlternativeRenderTests(TestCase):
    """폴링마다 작업이 쌓이면 요금이 폭주한다 — 쿨다운 락."""

    def setUp(self):
        self.user = User.objects.create(username="alt3")
        self.look = DailyLook.objects.create(
            user=self.user,
            look_date=service.today(),
            status=DailyLook.Status.SUCCEEDED,
            result={"golden_id": "095"},
            alternatives=[_alternative("096")],
        )

    @patch("apps.recommend.services.daily_look.queue_service.push")
    @patch("apps.recommend.services.daily_look.queue_service.get_client")
    def test_pushes_once_within_the_cooldown(self, get_client, push):
        client = get_client.return_value
        client.set.side_effect = [True, None]

        service._schedule_alternative_renders(self.look)
        service._schedule_alternative_renders(self.look)

        self.assertEqual(push.call_count, 1)
        self.assertEqual(push.call_args.args[0]["job"], service.JOB_RENDER_ALTERNATIVES)

    @patch("apps.recommend.services.daily_look.queue_service.push")
    @patch("apps.recommend.services.daily_look.queue_service.get_client")
    def test_nothing_missing_means_no_job(self, get_client, push):
        DailyLook.objects.filter(pk=self.look.pk).update(
            alternatives=[_alternative("096", render=True)]
        )
        self.look.refresh_from_db()

        service._schedule_alternative_renders(self.look)

        push.assert_not_called()
        get_client.assert_not_called()

    @patch("apps.recommend.services.daily_look.queue_service.get_client",
           side_effect=RuntimeError("redis down"))
    def test_redis_failure_does_not_raise(self, _client):
        """예약 실패가 조회나 추천을 되돌리면 안 된다."""
        service._schedule_alternative_renders(self.look)


class RefreshAlternativesTests(TestCase):
    """조회 시점 보정 — 생성하지 않고 이미 있는 것만 붙인다."""

    def setUp(self):
        self.user = User.objects.create(username="alt4")
        self.look = DailyLook.objects.create(
            user=self.user,
            look_date=service.today(),
            body=CONTEXT["body"],
            status=DailyLook.Status.SUCCEEDED,
            result={"golden_id": "095", "items": []},
            alternatives=[_alternative("096"), _alternative("097")],
        )

    @patch("apps.recommend.services.daily_look.settings.OUTFIT_RENDER_RESULT_BUCKET", "")
    @patch("apps.recommend.services.daily_look._schedule_alternative_renders")
    @patch("apps.recommend.services.daily_look.outfit_render.ensure_render")
    @patch("apps.recommend.services.daily_look.outfit_render.existing_render")
    def test_attaches_what_already_exists_without_generating(
        self, existing, ensure, _schedule
    ):
        existing.side_effect = lambda bucket, key, gender: (
            _FakeRef(bucket, "render/096.jpg") if "096" in key else None
        )

        changed = service.refresh_alternatives(self.look)

        self.assertTrue(changed)
        ensure.assert_not_called()
        self.look.refresh_from_db()
        self.assertIsNotNone(self.look.alternatives[0]["render_image"])
        self.assertIsNone(self.look.alternatives[1]["render_image"])

    @patch("apps.recommend.services.daily_look.settings.OUTFIT_RENDER_RESULT_BUCKET", "")
    @patch("apps.recommend.services.daily_look._schedule_alternative_renders")
    @patch("apps.recommend.services.daily_look.outfit_render.existing_render",
           return_value=None)
    def test_schedules_generation_when_still_missing(self, _existing, schedule):
        service.refresh_alternatives(self.look)

        schedule.assert_called_once()

    @patch("apps.recommend.services.daily_look.outfit_render.existing_render")
    def test_no_work_when_all_rendered(self, existing):
        DailyLook.objects.filter(pk=self.look.pk).update(
            alternatives=[_alternative("096", render=True)]
        )
        self.look.refresh_from_db()

        self.assertFalse(service.refresh_alternatives(self.look))
        existing.assert_not_called()


class AlternativeApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="alt5")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.look = DailyLook.objects.create(
            user=self.user,
            look_date=service.today(),
            status=DailyLook.Status.SUCCEEDED,
            result=_alternative("095", render=True),
            alternatives=[_alternative("096", render=True), _alternative("097")],
        )
        for target in ("refresh_render", "refresh_alternatives"):
            patcher = patch.object(service, target, return_value=False)
            self.addCleanup(patcher.stop)
            patcher.start()

    def test_today_response_carries_alternatives(self):
        body = self.client.get(reverse("recommend:daily-look-today")).json()

        self.assertEqual(
            [a["golden_id"] for a in body["alternatives"]], ["096", "097"]
        )
        # 대표 룩과 같은 스키마여야 프론트가 카드 코드를 재사용한다.
        self.assertEqual(sorted(body["alternatives"][0]), sorted(body["result"]))
        # 이미지가 아직 없는 후보는 null — 그때는 items[].image_url 로 카드가 선다.
        self.assertIsNone(body["alternatives"][1]["render_image_url"])

    def test_broken_alternative_rows_are_dropped(self):
        """golden_id 없는 행을 내려보내면 프론트가 저장할 수 없는 카드를 그린다."""
        DailyLook.objects.filter(pk=self.look.pk).update(
            alternatives=[{"headline": "깨진 행"}, _alternative("096")]
        )

        body = self.client.get(reverse("recommend:daily-look-today")).json()

        self.assertEqual([a["golden_id"] for a in body["alternatives"]], ["096"])


class SaveAlternativeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="alt6")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = reverse("recommend:daily-look-save")
        self.look = DailyLook.objects.create(
            user=self.user,
            look_date=service.today(),
            status=DailyLook.Status.SUCCEEDED,
            result={
                "golden_id": "095",
                "headline": "가볍게",
                "items": [],
                "render_image": {"s3_bucket": GOLDEN_BUCKET, "s3_key": "r/095.jpg"},
            },
            alternatives=[_alternative("096", render=True)],
        )
        for target in ("refresh_render", "refresh_alternatives"):
            patcher = patch.object(service, target, return_value=False)
            self.addCleanup(patcher.stop)
            patcher.start()

    def test_saves_the_look_being_viewed(self):
        response = self.client.post(self.url, {"golden_id": "096"}, format="json")

        self.assertEqual(response.status_code, 201)
        post = LookbookPost.objects.get(user=self.user)
        self.assertEqual(post.golden_id, "096")
        # 표지도 그 후보의 것이어야 한다 — 대표 룩 사진이 서면 다른 룩이 담긴 셈이다.
        self.assertIn("096", post.image_s3_key)

    def test_blank_golden_id_saves_the_primary(self):
        response = self.client.post(self.url, {"golden_id": ""}, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(LookbookPost.objects.get(user=self.user).golden_id, "095")

    def test_body_can_be_omitted(self):
        self.assertEqual(self.client.post(self.url).status_code, 201)

    def test_unknown_golden_id_is_refused(self):
        """오늘 이 사용자에게 나가지 않은 코디는 담을 수 없다."""
        response = self.client.post(self.url, {"golden_id": "999"}, format="json")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "GOLDEN_LOOK_NOT_IN_TODAY")
        self.assertEqual(LookbookPost.objects.count(), 0)

    def test_another_users_alternative_is_not_savable(self):
        """후보 목록은 **그 사용자의 행**에서만 읽는다."""
        other = User.objects.create(username="alt7")
        DailyLook.objects.create(
            user=other,
            look_date=service.today(),
            status=DailyLook.Status.SUCCEEDED,
            result={"golden_id": "555", "headline": "x", "items": []},
        )

        response = self.client.post(self.url, {"golden_id": "555"}, format="json")

        self.assertEqual(response.status_code, 404)

    def test_primary_and_alternative_are_separate_lookbooks(self):
        self.client.post(self.url, format="json")
        self.client.post(self.url, {"golden_id": "096"}, format="json")

        self.assertEqual(
            sorted(
                LookbookPost.objects.filter(user=self.user).values_list(
                    "golden_id", flat=True
                )
            ),
            ["095", "096"],
        )

    def test_service_raises_for_unknown_golden_id(self):
        with self.assertRaises(daily_look_save.GoldenLookNotInTodayError) as ctx:
            daily_look_save.save_to_lookbook(self.user, golden_id="999")

        self.assertEqual(ctx.exception.golden_id, "999")


class VirtualTryOnLookSelectionTests(TestCase):
    """가상 피팅이 **화면에서 보던 그 룩**을 입히는가.

    golden_id 를 안 받던 시절에는 '다른 룩'으로 후보를 보다 입어봐도 대표 룩이
    입혀졌다. 화면에서 고른 것과 결과가 다르면 사용자에게는 그냥 오작동이다.
    """

    def setUp(self):
        self.user = User.objects.create(username="tryon1")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.look = DailyLook.objects.create(
            user=self.user,
            look_date=service.today(),
            body=CONTEXT["body"],
            status=DailyLook.Status.SUCCEEDED,
            result=_alternative("095", render=True),
            alternatives=[_alternative("096", render=True), _alternative("097")],
        )
        self.url = reverse(
            "recommend:daily-look-virtual-try-on", kwargs={"look_id": self.look.pk}
        )

        patches = {
            "download": patch("apps.recommend.views.storage.download_for", return_value=PNG),
            "exists": patch("apps.recommend.views.storage.exists_for", return_value=True),
            "put": patch("apps.recommend.views.storage.put_bytes_for"),
            "presign": patch(
                "apps.recommend.views.storage.presigned_get_for",
                return_value="https://signed",
            ),
            "service": patch("apps.recommend.views.VirtualTryOnService"),
            "bucket": patch(
                "apps.recommend.views.settings.OUTFIT_RENDER_RESULT_BUCKET", "result-bucket"
            ),
        }
        self.mocks = {}
        for name, patcher in patches.items():
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def _post(self, **extra):
        return self.client.post(
            self.url,
            {"person_image": SimpleUploadedFile("me.png", PNG, content_type="image/png"),
             "mode": "mannequin", **extra},
            format="multipart",
        )

    def _downloaded_key(self) -> str:
        return self.mocks["download"].call_args.args[1]

    def test_alternative_is_the_look_that_gets_worn(self):
        response = self._post(golden_id="096")

        self.assertEqual(response.status_code, 200)
        self.assertIn("096", self._downloaded_key())

    def test_blank_golden_id_uses_the_primary_look(self):
        response = self._post(golden_id="")

        self.assertEqual(response.status_code, 200)
        self.assertIn("095", self._downloaded_key())

    def test_golden_id_can_be_omitted(self):
        response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertIn("095", self._downloaded_key())

    def test_unknown_golden_id_is_refused(self):
        """저장 API와 같은 규칙 — 오늘 나가지 않은 코디는 입힐 수 없다."""
        response = self._post(golden_id="999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "GOLDEN_LOOK_NOT_IN_TODAY")
        self.mocks["download"].assert_not_called()

    def test_another_users_look_is_not_reachable(self):
        other = User.objects.create(username="tryon2")
        other_look = DailyLook.objects.create(
            user=other,
            look_date=service.today(),
            status=DailyLook.Status.SUCCEEDED,
            result=_alternative("555", render=True),
        )
        url = reverse(
            "recommend:daily-look-virtual-try-on", kwargs={"look_id": other_look.pk}
        )

        response = self.client.post(
            url,
            {"person_image": SimpleUploadedFile("me.png", PNG, content_type="image/png"),
             "mode": "mannequin"},
            format="multipart",
        )

        self.assertEqual(response.status_code, 404)

    @patch("apps.recommend.services.daily_look.refresh_alternatives", return_value=False)
    def test_candidate_without_render_image_is_409(self, _refresh):
        """후보 이미지는 별도 작업이 채운다 — 아직이면 '나중에'라고 말한다."""
        response = self._post(golden_id="097")

        self.assertEqual(response.status_code, 409)
        self.assertIn("완료된 뒤", response.json()["detail"])

    @patch("apps.recommend.services.daily_look.refresh_alternatives")
    def test_refresh_picks_up_a_just_finished_render(self, refresh):
        """조회 보정이 방금 만들어진 이미지를 붙이면 그대로 진행한다."""

        def _fill(look):
            look.alternatives = [_alternative("096", render=True), _alternative("097", render=True)]
            look.save(update_fields=["alternatives"])
            return True

        refresh.side_effect = _fill

        response = self._post(golden_id="097")

        self.assertEqual(response.status_code, 200)
        self.assertIn("097", self._downloaded_key())
