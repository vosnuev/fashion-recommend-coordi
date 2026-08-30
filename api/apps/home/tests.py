"""홈 API의 오늘의 룩 선반영 훅 테스트.

트리거를 로그인에서 홈으로 옮겼다. 홈 요청에는 위경도가 실려 오므로 그날 첫
추천이 사용자 위치의 날씨를 탈 수 있고, 사용자는 로그인 직후 홈으로 오니
선반영 효과는 그대로다. 여기서 지키는 계약은 두 가지다.

- 홈 조회가 ensure_today_look을 좌표와 함께 부른다 (선반영이 실제로 걸린다)
- 홈 응답이 그 룩의 현재 상태(daily_look)를 함께 싣는다 — 프론트가 첫 프레임부터
  "생성 중 / 완성 / 없음"을 구분해야 아직 없는 추천 자리에 목업이 끼어들지 않는다
- 선반영이 죽어도 홈은 200이다 (홈 화면이 추천보다 훨씬 중요하다)
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.recommend.models import DailyLook

User = get_user_model()

WEATHER = {"region": "서울", "temperature": 25, "sky_state": "맑음"}


class HomeDailyLookTriggerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="home1")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = reverse("home:home")
        # 시리얼라이저에 실릴 실제 행. 상태 전달을 검증하려면 Mock 이 아니라
        # 진짜 모델이어야 한다(응답 본문이 곧 계약이다).
        self.look = DailyLook.objects.create(
            user=self.user,
            look_date=date(2026, 8, 18),
            status=DailyLook.Status.QUEUED,
        )

    def _ensure_returns_look(self):
        return patch(
            "apps.recommend.services.daily_look.ensure_today_look",
            return_value=(self.look, False),
        )

    @patch("apps.home.views.get_current_weather", return_value=WEATHER)
    def test_home_call_kicks_off_daily_look_with_coordinates(self, _weather):
        with self._ensure_returns_look() as ensure:
            response = self.client.get(self.url, {"lat": "37.5665", "lon": "126.9780"})
        self.assertEqual(response.status_code, 200)
        ensure.assert_called_once()
        args, kwargs = ensure.call_args
        self.assertEqual(args[0], self.user)
        # 홈이 검증한 좌표가 그대로 넘어가야 그날 추천이 사용자 위치 날씨를 탄다
        self.assertEqual(kwargs["lat"], 37.5665)
        self.assertEqual(kwargs["lon"], 126.978)

    @patch("apps.home.views.get_current_weather", return_value=WEATHER)
    def test_home_response_carries_daily_look_state(self, _weather):
        """홈 응답만으로 추천 카드의 분기가 결정돼야 한다.

        예전에는 선반영만 걸고 상태를 안 실어서, 프론트가 별도 조회를 마칠 때까지
        기온 템플릿 + 번들 목업 사진으로 카드를 '완성된 추천처럼' 그렸다.
        조회 엔드포인트와 같은 시리얼라이저를 쓰므로 프론트는 이 값을 그대로
        시드로 넣고 생성 중일 때만 폴링한다.
        """
        with self._ensure_returns_look():
            response = self.client.get(self.url)
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["daily_look"]["status"], DailyLook.Status.QUEUED)
        self.assertEqual(body["daily_look"]["look_id"], str(self.look.pk))
        # 생성 전에는 본문이 없다 — 프론트가 "아직"과 "완성"을 헷갈리지 않게.
        self.assertIsNone(body["daily_look"]["result"])
        self.assertIsNotNone(body["daily_look"]["poll_after_ms"])

    @patch(
        "apps.recommend.services.daily_look.ensure_today_look",
        side_effect=RuntimeError("db down"),
    )
    @patch("apps.home.views.get_current_weather", return_value=WEATHER)
    def test_trigger_failure_does_not_break_home(self, _weather, _ensure):
        """선반영은 부가 기능이다. 죽어도 홈 응답은 성립해야 한다."""
        response = self.client.get(self.url)
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertIn("weather", body)
        # None 은 "룩이 없음"이 아니라 "상태를 알 수 없음"이다.
        # 프론트는 이때 조회 엔드포인트로 직접 물어본다.
        self.assertIsNone(body["daily_look"])

    @patch("apps.recommend.services.daily_look.ensure_today_look")
    @patch("apps.home.views.get_current_weather", return_value=WEATHER)
    def test_anonymous_request_is_rejected_before_trigger(self, _weather, ensure):
        response = APIClient().get(self.url)
        self.assertEqual(response.status_code, 401)
        ensure.assert_not_called()
