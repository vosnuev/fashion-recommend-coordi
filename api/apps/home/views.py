import logging

from rest_framework.response import Response
from rest_framework.views import APIView

from apps.home.services import (
    MOCK_CLOSET_COUNT,
    MOCK_SAVED_LOOK_COUNT,
    QUICK_RECOMMENDS,
    build_today_look,
)
from apps.weather.services import get_current_weather, resolve_coordinates

logger = logging.getLogger(__name__)


class HomeView(APIView):
    """GET /api/v1/home/?lat=&lon= — 홈 화면 통합 응답 (로그인 필요)."""

    def get(self, request):
        lat, lon = resolve_coordinates(
            request.query_params.get("lat"), request.query_params.get("lon")
        )
        weather = get_current_weather(lat, lon)

        # 오늘의 룩 선반영. 예전에는 로그인 응답에서 걸었는데, 그 시점에는
        # 위치가 없어 항상 서울 날씨로 만들어졌다. 홈 요청에는 위경도가 실려
        # 오므로 여기서 걸면 그날 첫 추천이 사용자 위치의 날씨를 탄다.
        # 사용자는 로그인 직후 홈으로 오니 선반영 효과는 그대로다.
        #
        # 걸어두는 김에 현재 상태를 그대로 실어 보낸다. 프론트가 홈 응답만으로
        # "생성 중 / 완성 / 없음"을 첫 프레임부터 구분할 수 있어야, 아직 없는
        # 추천 자리에 목업 카드가 끼어들지 않는다.
        daily_look = _daily_look_payload(request.user, lat, lon)

        return Response(
            {
                "nickname": request.user.nickname or request.user.username,
                "weather": weather,
                # 기온 구간 템플릿. 추천 그 자체가 아니라 '오늘 날씨엔 이런 옷'
                # 수준의 보조 문구다 — 프론트는 이걸 추천 카드로 승격시키지 않고
                # daily_look 이 생성 중일 때의 힌트로만 쓴다.
                "today_look": build_today_look(weather["temperature"]),
                "daily_look": daily_look,
                "quick_recommends": QUICK_RECOMMENDS,
                "closet_count": MOCK_CLOSET_COUNT,
                "saved_look_count": MOCK_SAVED_LOOK_COUNT,
            }
        )


def _daily_look_payload(user, lat, lon) -> dict | None:
    """그날 첫 홈 진입이면 오늘의 룩 생성을 걸고, 그 상태를 응답에 실어 보낸다.

    "첫 진입"에는 예외가 하나 있다. 이미 EMPTY로 끝난 행이 있고 그 뒤 체형·추구미가
    바뀌었으면 여기서 다시 걸린다 — 화면이 안내한 대로 프로필을 채우고 홈으로 돌아온
    사용자가 그 자리에서 결과를 보게 하려는 것이다(ensure_today_look 안에서 판단한다).

    선반영(생성 트리거)과 상태 전달을 한 번에 한다. 사용자가 추천 화면에 도착할
    때쯤 이미 완성돼 있게 하려는 선반영이고, 조회 엔드포인트
    (GET /api/v1/looks/today/)도 같은 함수를 부르므로 여기서 실패해도 기능이
    사라지지는 않는다 — 그래서 예외를 삼키고 None 을 돌려준다. 홈 화면은 추천보다
    훨씬 중요하고, 추천 생성이 홈을 막아서는 안 된다.

    본문은 조회 엔드포인트와 **같은 시리얼라이저**로 만든다. 상태 코드만 내려주면
    완성된 룩을 위해 프론트가 곧바로 한 번 더 왕복해야 하고, 그 왕복 동안 카드가
    빈 채로 남아 깜빡인다. 스키마가 같으므로 프론트는 이 값을 조회 응답과 구분 없이
    쓴다(홈 응답을 시드로 넣고, 생성 중일 때만 폴링).

    None 은 "상태를 알 수 없음"이지 "룩이 없음"이 아니다 — 프론트는 이때 조회
    엔드포인트로 직접 물어본다.
    """
    try:
        from apps.recommend.serializers import DailyLookSerializer
        from apps.recommend.services import daily_look as daily_look_service

        look, _created = daily_look_service.ensure_today_look(user, lat=lat, lon=lon)
        # 착용 이미지는 생성 시점에 실패해도 나중에 캐시에 생긴다. 조회 엔드포인트가
        # 하는 보정을 여기서도 해두지 않으면 홈 카드만 이미지 없이 남는다.
        daily_look_service.refresh_render(look)
        return DailyLookSerializer(look).data
    except Exception:  # noqa: BLE001
        logger.exception("오늘의 룩 선반영 실패 (홈 응답은 계속 진행): user=%s", user.pk)
        return None
