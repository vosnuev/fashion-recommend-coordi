"""신체치수 추정 — 무사진(기본 정보) / 사진 두 경로를 모두 처리한다.

실제 추론은 ml/body_measurement/src/inference.py가 담당하고, 이 모듈은 Django
쪽 관심사(입력 확정, DB 저장, 트랜잭션 상태 전이, 응답 형식)만 맡는다
(CLAUDE.md §7: 모델 코드는 ml/에 두고 웹 계층은 인터페이스로 호출).

두 경로 모두 새 11개 저장 항목을 채운다. 사진 경로는 VLM이 필수 값을 모두 반환해야
성공하며, 누락된 값을 무사진 임시값으로 대체하지 않는다.

사진 처리는 백그라운드 스레드로 돌린다. 개발용 임시 수단이며 AWS 이관 시
Celery/SQS로 교체하는 것을 전제로, 호출부(views)는 start_measurement()만 사용한다.
"""

from __future__ import annotations

import logging
import math
import threading
import uuid
from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import close_old_connections, connections
from django.db import transaction as db_transaction
from django.utils import timezone

from apps.users.models import BodyMeasurement, BodyPhotoTransaction
from body_measurement.src import inference

logger = logging.getLogger(__name__)

DETAIL_FIELDS = inference.PHOTO_TARGETS

SOURCE_BASIC = "basic_info"
SOURCE_PHOTO = "photo"
ERROR_CODE_PHOTO_QUALITY_FAILED = "photo_quality_failed"

# 이 시간이 지나도 '진행중'인 트랜잭션은 죽은 것으로 본다.
# 배포·크래시로 백그라운드 스레드가 사라지면 상태를 바꿔줄 주체가 없어지는데,
# 사용자당 진행중 1건 제약 때문에 그 사용자는 영영 사진을 못 올리게 된다.
# VLM 호출은 네트워크 재시도까지 고려해 약 5분 안에 끝나는 것을 목표로 한다.
STALE_TRANSACTION_TIMEOUT_MINUTES = 5

RATIO_LIMITS = {
    "thigh_calf_ratio": (Decimal("0.1"), Decimal("9.999")),
    "torso_leg_ratio": (Decimal("0.1"), Decimal("9.999")),
}


class BodyEstimationError(Exception):
    """추정에 필요한 입력이 없거나 추론이 실패했을 때."""


class BodyPhotoQualityError(BodyEstimationError):
    """사진 구도·인원·화질이 측정 기준을 충족하지 못했을 때."""


def _to_decimal(value: float, field_name: str = "measurement") -> Decimal:
    """모델이 준 float를 DecimalField 형식으로 맞춘다.

    비율 필드는 소수점 3자리(0.001), 그 외 치수 필드는 1자리(0.1)로 quantize한다.
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError) as error:
        raise BodyEstimationError(
            f"{field_name} 추정값을 숫자로 변환할 수 없습니다: {value!r}"
        ) from error

    if not math.isfinite(numeric):
        raise BodyEstimationError(
            f"{field_name} 추정값이 유효하지 않습니다: {value!r}"
        )

    try:
        if field_name.endswith("_ratio"):
            result = Decimal(str(round(numeric, 3))).quantize(Decimal("0.001"))
            minimum, maximum = RATIO_LIMITS.get(
                field_name, (Decimal("0.1"), Decimal("99.999"))
            )
        else:
            result = Decimal(str(round(numeric, 1))).quantize(Decimal("0.1"))
            minimum = Decimal("1")
            maximum = Decimal("999.9")
    except (InvalidOperation, ValueError) as error:
        raise BodyEstimationError(f"{field_name} 추정값을 저장할 수 없습니다: {value!r}") from error

    if result < minimum:
        raise BodyEstimationError(
            f"{field_name} 추정값이 허용 범위보다 작습니다: {result}"
        )
    if result > maximum:
        raise BodyEstimationError(
            f"{field_name} 추정값이 허용 범위를 초과합니다: {result}"
        )
    return result


def resolve_basic_info(
    measurement: BodyMeasurement,
    *,
    gender: str | None = None,
    height: Decimal | None = None,
    weight: Decimal | None = None,
) -> tuple[str, Decimal, Decimal]:
    """추정에 쓸 성별·키·몸무게를 확정한다. 요청값이 없으면 저장된 값을 쓴다."""
    gender = gender or measurement.gender
    height = height if height is not None else measurement.height
    weight = weight if weight is not None else measurement.weight

    missing = [
        label
        for label, value in (("성별", gender), ("키", height), ("몸무게", weight))
        if not value
    ]
    if missing:
        # 이 문구는 400 응답의 detail 로 나가 앱 화면에 그대로 표시된다.
        # 엔드포인트 경로 같은 개발자용 안내를 넣으면 사용자가 그걸 읽게 되므로
        # 무엇이 빠졌는지와 무엇을 하면 되는지만 남긴다.
        raise BodyEstimationError(
            f"{', '.join(missing)} 정보가 필요합니다. 체형 정보를 먼저 입력해 주세요."
        )
    return gender, height, weight


def save_measurements(
    measurement: BodyMeasurement,
    *,
    gender: str,
    height: Decimal,
    weight: Decimal,
    estimated: dict[str, float],
) -> BodyMeasurement:
    """기본 정보와 추정된 상세 치수/비율들을 저장한다.

    상세 값은 덮어쓴다 — 사용자가 직접 입력한 값이 있어도 최신 추정으로 교체된다.
    """
    if not isinstance(estimated, Mapping):
        raise BodyEstimationError("추정 결과 형식이 올바르지 않습니다.")

    measurement.gender = gender
    measurement.height = height
    measurement.weight = weight
    missing = [field for field in DETAIL_FIELDS if field not in estimated]
    if missing:
        raise BodyEstimationError(
            f"추정 결과에 필수 값이 없습니다: {', '.join(missing)}"
        )

    for field in DETAIL_FIELDS:
        setattr(measurement, field, _to_decimal(estimated[field], field))
    measurement.save(
        update_fields=["gender", "height", "weight", *DETAIL_FIELDS, "updated_at"]
    )
    return measurement


def estimate_from_basic_info(
    user,
    *,
    gender: str | None = None,
    height: Decimal | None = None,
    weight: Decimal | None = None,
) -> BodyMeasurement:
    """성별·키·몸무게만으로 11개 항목을 추정해 저장한다 (동기)."""
    measurement, _ = BodyMeasurement.objects.get_or_create(user=user)
    gender, height, weight = resolve_basic_info(
        measurement, gender=gender, height=height, weight=weight
    )
    try:
        estimated = inference.estimate_from_basic(gender, float(height), float(weight))
    except inference.BodyEstimationError as error:
        raise BodyEstimationError(str(error)) from error
    except Exception as error:
        logger.exception("기본 정보 신체 측정 실패 (user=%s)", getattr(user, "pk", None))
        raise BodyEstimationError(
            "기본 정보로 신체치수를 추정하는 중 오류가 발생했습니다."
        ) from error

    return save_measurements(
        measurement, gender=gender, height=height, weight=weight, estimated=estimated
    )


# ---------------------------------------------------------------------------
# 사진 기반 (비동기)
# ---------------------------------------------------------------------------


def expire_stale_transactions(user) -> int:
    """죽은 채로 남은 '진행중' 트랜잭션을 실패로 닫는다. 닫은 건수를 반환한다.

    프로세스가 재시작되면 백그라운드 스레드가 사라져 상태가 영원히 '진행중'으로
    남는다. 사진 접수·조회 시점에 정리해서 사용자가 막히지 않게 한다.
    """
    deadline = timezone.now() - timedelta(minutes=STALE_TRANSACTION_TIMEOUT_MINUTES)
    return BodyPhotoTransaction.objects.filter(
        user=user,
        status=BodyPhotoTransaction.Status.IN_PROGRESS,
        created_at__lt=deadline,
    ).update(
        status=BodyPhotoTransaction.Status.FAILED,
        error_message="측정이 시간 내에 끝나지 않았습니다. 다시 시도해주세요.",
        error_code="",
    )


def start_measurement(
    transaction_id: uuid.UUID,
    *,
    gender: str,
    height: Decimal,
    weight: Decimal,
    front_image: bytes,
    side_image: bytes,
) -> None:
    """사진 측정을 백그라운드에서 시작한다.

    업로드된 파일은 요청이 끝나면 사라지므로 바이트를 미리 읽어 넘긴다.
    """
    thread = threading.Thread(
        target=_run_measurement,
        args=(transaction_id,),
        kwargs={
            "gender": gender,
            "height": height,
            "weight": weight,
            "front_image": front_image,
            "side_image": side_image,
        },
        name=f"body-photo-tx-{transaction_id}",
        daemon=True,
    )
    thread.start()


def _run_measurement(transaction_id: uuid.UUID, **kwargs) -> None:
    """스레드 본체. 실패해도 트랜잭션을 반드시 종료 상태로 남긴다."""
    # 스레드마다 DB 커넥션이 새로 열리므로 만료 커넥션 정리 후 시작, 끝나면 닫는다.
    close_old_connections()
    try:
        complete_measurement(transaction_id, **kwargs)
    except Exception as error:
        logger.exception("사진 측정 실패 (tx=%s)", transaction_id)
        error_message = str(error).strip() or "사진 측정 중 알 수 없는 오류가 발생했습니다."
        BodyPhotoTransaction.objects.filter(
            pk=transaction_id,
            status=BodyPhotoTransaction.Status.IN_PROGRESS,
        ).update(
            status=BodyPhotoTransaction.Status.FAILED,
            error_message=error_message[:500],
            error_code=(
                ERROR_CODE_PHOTO_QUALITY_FAILED
                if isinstance(error, BodyPhotoQualityError)
                else ""
            ),
        )
    finally:
        connections.close_all()


def complete_measurement(
    transaction_id: uuid.UUID,
    *,
    gender: str,
    height: Decimal,
    weight: Decimal,
    front_image: bytes,
    side_image: bytes,
) -> None:
    """사진으로 상세 수치를 추정해 저장하고 트랜잭션을 성공 처리한다.

    스레드 없이도 검증할 수 있도록 분리했다 (tests에서 직접 호출).
    추론은 DB 트랜잭션 밖에서 끝낸다 — VLM 호출이 수 초 걸리므로 그 시간 동안
    행 잠금을 들고 있으면 안 된다.
    """
    try:
        estimated = inference.estimate_from_photos(
            gender, float(height), float(weight), front_image, side_image
        )
    except inference.PhotoValidationError as error:
        raise BodyPhotoQualityError(str(error)) from error
    except inference.BodyEstimationError as error:
        raise BodyEstimationError(str(error)) from error
    except Exception as error:
        logger.exception("사진 신체 측정 실패 (tx=%s)", transaction_id)
        raise BodyEstimationError(
            "사진으로 신체치수를 추정하는 중 오류가 발생했습니다."
        ) from error

    with db_transaction.atomic():
        tx = BodyPhotoTransaction.objects.select_for_update().get(pk=transaction_id)
        # 이미 끝난 트랜잭션은 건드리지 않는다 (중복 실행 방지).
        if tx.status != BodyPhotoTransaction.Status.IN_PROGRESS:
            return

        measurement, _ = BodyMeasurement.objects.get_or_create(user_id=tx.user_id)
        save_measurements(
            measurement,
            gender=gender,
            height=height,
            weight=weight,
            estimated=estimated,
        )

        tx.status = BodyPhotoTransaction.Status.SUCCEEDED
        tx.error_message = ""
        tx.error_code = ""
        tx.save(update_fields=["status", "error_message", "error_code", "updated_at"])


# ---------------------------------------------------------------------------
# 응답 형식 (두 API 공통)
# ---------------------------------------------------------------------------


def build_result(
    *,
    status: str,
    source: str,
    measurement: BodyMeasurement | None,
    transaction_id: uuid.UUID | None = None,
    error_message: str = "",
    error_code: str = "",
) -> dict:
    """무사진 추정과 사진 측정 조회가 공유하는 결과 payload를 만든다."""
    if status == BodyPhotoTransaction.Status.FAILED and not error_message:
        error_message = "신체 측정에 실패했습니다. 다시 시도해주세요."

    return {
        "status": status,
        "source": source,
        "transaction_id": transaction_id,
        "measurement": measurement or BodyMeasurement(),
        "error_message": error_message or None,
        "error_code": error_code or None,
    }
