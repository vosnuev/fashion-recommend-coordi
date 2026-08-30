from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from apps.recommend.services.gender import normalize_gender
from apps.users.models import BodyMeasurement
from apps.users.services.pursuit import get_pursuit
from apps.weather.services import get_current_weather, resolve_coordinates


def _json_safe(value: Any) -> Any:
    """컨텍스트를 순수 JSON 타입으로 변환한다.

    weather의 observed_at은 datetime이라 응답 직렬화(JSONField)를 통과하지 못한다.
    실황 데이터가 없는 환경에서는 None이라 드러나지 않지만, weather-collector가
    도는 서버에서는 값이 채워져 503으로 이어진다.
    """
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _serialize_measurement(measurement: BodyMeasurement | None) -> dict | None:
    """체형 행을 스냅샷 JSON으로 만든다.

    성별만 따로 다룬다. 아래 수치 필드는 "값이 없으면 None"이 맞지만, 성별에
    같은 규칙을 적용했다가 사고가 났다. 미입력 성별("")이 ``value or None``에
    걸려 None이 되고, 리트리버 호출부의 ``str(...)``을 지나며 문자열 "None"으로
    굳어, 성별 하드 필터가 아무 예외 없이 사라졌다. 성별은 언제나 문자열이다.
    """
    if measurement is None:
        return None
    fields = (
        "height",
        "weight",
        "chest",
        "waist",
        "hip",
        "shoulder",
        "thigh_length",
        "calf_length",
        "torso_length",
        "leg_length",
        "neck_length",
        "thigh_calf_ratio",
        "torso_leg_ratio",
    )
    data: dict[str, Any] = {
        field: (
            float(value)
            if isinstance(value, Decimal)
            else (value or None)
        )
        for field in fields
        if (value := getattr(measurement, field, None)) is not None
    }
    data["gender"] = normalize_gender(measurement.gender)
    return data


def build_profile_context(user) -> dict[str, Any]:
    """사용자 프로필(체형·추구미)만 스냅샷으로 만든다. 날씨는 보지 않는다.

    build_analysis_context에서 날씨를 뺀 것이 필요한 자리가 있다. "프로필이
    바뀌었는가"를 판단할 때다(daily_look._requeue_if_profile_changed). 날씨는
    분 단위로 바뀌므로 그것까지 섞으면 매번 "바뀌었다"가 되고, 판단하려고
    실황 조회를 한 번 더 하는 것도 낭비다.
    """
    if not (user and user.is_authenticated):
        return {"body": None, "pursuit": None}
    return _json_safe(
        {
            "body": _serialize_measurement(
                BodyMeasurement.objects.filter(user=user).first()
            ),
            "pursuit": get_pursuit(user),
        }
    )


def build_analysis_context(
    user,
    *,
    lat: float | None,
    lon: float | None,
) -> dict[str, Any]:
    resolved_lat, resolved_lon = resolve_coordinates(lat, lon)
    is_authenticated = bool(user and user.is_authenticated)
    # 프로필 부분은 build_profile_context와 **같은 함수**로 만든다. 두 군데서
    # 각자 직렬화하면 한쪽만 필드가 늘어났을 때 지문 비교가 영영 어긋난다.
    profile = build_profile_context(user)

    return _json_safe(
        {
            "weather": get_current_weather(resolved_lat, resolved_lon),
            "pursuit": profile["pursuit"],
            "body": profile["body"],
            "personalized": is_authenticated,
        }
    )
