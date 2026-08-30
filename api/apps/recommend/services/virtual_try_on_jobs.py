"""가상 피팅 작업 — 접수·조회·처리.

**왜 비동기인가.** 이미지 모델이 수십 초~2분을 쓰는데 그동안 HTTP 연결을 잡고
있으면, 앞단 프록시가 먼저 끊는다(Cloudflare 터널 100초 → 524). 게다가 그 연결이
곧 결과의 수명이라 화면을 나가면 만들던 것이 사라진다. 그래서 접수(202)와 조회를
나누고, 결과는 DB·S3에 남겨 언제든 다시 본다.

오늘의 룩·코디 이미지가 이미 쓰는 구조와 같다: PostgreSQL 이 작업의 사실이고,
Redis 큐에는 UUID 만 넣는다(사진·프롬프트를 큐에 넣지 않는다).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.recommend.models import DailyLook, VirtualTryOnJob
from apps.recommend.services import storage
from apps.recommend.services.virtual_try_on import (
    DIRECT_PROMPT_VERSION,
    MANNEQUIN_PROMPT_VERSION,
    VirtualTryOnService,
    body_note,
)

logger = logging.getLogger(__name__)


class VirtualTryOnUnavailable(RuntimeError):
    """결과 저장소가 설정되지 않아 접수 자체를 할 수 없다."""


def prompt_version_for(mode: str) -> str:
    return DIRECT_PROMPT_VERSION if mode == "person" else MANNEQUIN_PROMPT_VERSION


def body_note_for(look: DailyLook) -> str:
    """이 룩을 만들 때 얼려 둔 체형 판정으로 프롬프트 한 문장을 만든다.

    BodyMeasurement 를 다시 읽지 않는다. 추천이 나간 시점의 판정
    (DailyLook.body_profile 스냅샷)이 그 추천의 근거이고, 사용자가 그 뒤에 치수를
    고쳐도 **이미 나간 추천의 이미지**는 그때 기준으로 남는 편이 앞뒤가 맞는다.
    """
    profile = look.body_profile or {}
    return body_note(
        silhouette=str(profile.get("silhouette") or ""),
        bmi_band=str(profile.get("bmi_band") or ""),
    )


def build_contract(
    *, person: bytes, outfit: bytes, mode: str, body_note_text: str = ""
) -> str:
    """같은 입력이면 같은 결과 키.

    look.pk 를 넣지 않는다. 사람 사진과 코디 이미지가 같으면 결과도 같으므로,
    같은 코디를 받은 다른 사용자·다른 날짜의 작업도 이미 만들어 둔 이미지를
    그대로 쓴다. (예전 키에는 look.pk 와 golden_id 가 들어 있어 사실상 캐시가
    사용자·날짜별로 갈렸다 — 같은 걸 여러 번 만들었다는 뜻이다.)

    체형 문장은 프롬프트의 일부라 여기 들어간다. 같은 사진·같은 코디라도 체형
    판정이 다르면 옷이 앉는 모양이 달라지므로 다른 결과다.
    """
    return hashlib.sha256(
        (
            f"virtual-try-on|{mode}|{hashlib.sha256(person).hexdigest()}|"
            f"{hashlib.sha256(outfit).hexdigest()}|{settings.OUTFIT_RENDER_MODEL}|"
            f"{prompt_version_for(mode)}|{body_note_text}"
        ).encode()
    ).hexdigest()


def result_key(contract: str) -> str:
    return f"{settings.VIRTUAL_TRY_ON_RESULT_PREFIX}/{contract[:2]}/{contract}/result.png"


def person_key(job_id: uuid.UUID | str, extension: str) -> str:
    """사용자 사진을 잠시 둘 키. 수명주기 규칙이 걸린 prefix 아래에 만든다."""
    return f"{settings.VIRTUAL_TRY_ON_PERSON_PREFIX}/{job_id}/person{extension}"


def resolve_golden_id(look: DailyLook, golden_id: str = "") -> str:
    """빈 값을 **대표 룩의 실제 id** 로 편다.

    접수와 조회가 같은 키를 봐야 한다. 접수는 서버가 고른 코디의 id 를 그대로
    적는데, 조회는 "대표 룩"을 빈 문자열로 물어보는 일이 흔하다. 양쪽을 여기서
    같은 값으로 맞추지 않으면, 방금 만든 작업을 되찾지 못해 사용자는 사진을
    다시 고르게 된다.
    """
    from apps.recommend.services import daily_look as daily_look_service

    try:
        chosen = daily_look_service.pick_result(look, golden_id)
    except daily_look_service.GoldenLookNotInTodayError:
        # 오늘 없는 코디를 물었다. 그런 작업도 없으므로 그대로 두면 조회가 빈다.
        return (golden_id or "").strip()
    return str(chosen.get("golden_id") or "")


def latest_job(*, user, look: DailyLook, golden_id: str = "") -> VirtualTryOnJob | None:
    """이 사용자가 그 룩(그 후보)에 대해 마지막으로 만든 작업.

    화면을 나갔다 와도 보이게 하는 조회다. 사진을 다시 고르게 하지 않으려면
    "무엇에 대한 작업인가"로 찾아야 하고, 그 무엇은 (사용자, 룩, 후보)다.
    """
    return (
        VirtualTryOnJob.objects.filter(
            user=user, look=look, golden_id=resolve_golden_id(look, golden_id)
        )
        .order_by("-created_at")
        .first()
    )


def result_url(job: VirtualTryOnJob) -> str | None:
    """조회 시점에 서명한다. DB 에는 버킷·키만 둔다(URL 은 만료된다)."""
    if not job.result_s3_bucket or not job.result_s3_key:
        return None
    try:
        return storage.presigned_get_for(
            job.result_s3_bucket,
            job.result_s3_key,
            ttl=settings.OUTFIT_RENDER_PRESIGNED_GET_TTL_SECONDS,
        )
    except Exception:  # noqa: BLE001 — 이미지 하나가 조회를 막으면 안 된다
        logger.warning("가상 피팅 결과 서명 실패: job=%s", job.pk)
        return None


@transaction.atomic
def accept(
    *,
    user,
    look: DailyLook,
    golden_id: str,
    mode: str,
    person: bytes,
    person_extension: str,
    person_content_type: str,
    outfit: bytes,
) -> tuple[VirtualTryOnJob, bool]:
    """요청을 접수한다.

    Returns: (작업, 바로 끝났는지). 같은 입력의 결과가 이미 S3 에 있으면 생성 없이
    SUCCEEDED 로 만들어 돌려준다 — 그때는 폴링할 이유가 없다.

    Raises: VirtualTryOnUnavailable
    """
    bucket = settings.OUTFIT_RENDER_RESULT_BUCKET
    if not bucket:
        raise VirtualTryOnUnavailable

    contract = build_contract(
        person=person, outfit=outfit, mode=mode, body_note_text=body_note_for(look)
    )
    key = result_key(contract)

    job = VirtualTryOnJob(
        user=user,
        look=look,
        golden_id=golden_id or "",
        mode=mode,
        contract=contract,
    )

    if storage.exists_for(bucket, key):
        job.status = VirtualTryOnJob.Status.SUCCEEDED
        job.cache_hit = True
        job.result_s3_bucket = bucket
        job.result_s3_key = key
        job.result_media_type = "image/png"
        job.finished_at = timezone.now()
        job.save(force_insert=True)
        return job, True

    # 사진을 먼저 올린다. 워커는 DB 의 키만 보고 읽으므로, 행보다 늦게 올리면
    # 워커가 먼저 집어 파일을 못 찾는 창이 생긴다.
    job.person_s3_bucket = bucket
    job.person_s3_key = person_key(job.pk, person_extension)
    storage.put_bytes_for(bucket, job.person_s3_key, person, person_content_type)
    job.save(force_insert=True)
    return job, False


def mark_enqueued(job: VirtualTryOnJob) -> None:
    job.enqueued_at = timezone.now()
    job.save(update_fields=["enqueued_at", "updated_at"])


def start(job_id) -> VirtualTryOnJob | None:
    """워커가 집어든다. 이미 끝났거나 남이 집어간 작업은 None."""
    with transaction.atomic():
        job = (
            VirtualTryOnJob.objects.select_for_update()
            .filter(pk=job_id, status=VirtualTryOnJob.Status.QUEUED)
            .first()
        )
        if job is None:
            return None
        job.status = VirtualTryOnJob.Status.PROCESSING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at", "updated_at"])
        return job


def run(job: VirtualTryOnJob, *, service: VirtualTryOnService | None = None) -> VirtualTryOnJob:
    """실제 생성. 워커에서 부른다.

    사람 사진은 S3 에서 다시 읽는다 — 큐에 이미지를 넣지 않기 때문이다.
    코디 이미지는 그 시점의 룩에서 다시 고른다(그 사이 착용 이미지가 채워졌을 수 있다).
    """
    from apps.recommend.services import daily_look as daily_look_service

    person = storage.download_for(
        job.person_s3_bucket,
        job.person_s3_key,
        max_bytes=settings.VIRTUAL_TRY_ON_MAX_PERSON_IMAGE_BYTES,
    )
    chosen = daily_look_service.pick_result(job.look, job.golden_id)
    image = chosen.get("render_image") or chosen.get("outfit_image")
    if not image or not image.get("s3_bucket") or not image.get("s3_key"):
        raise ValueError("추천 룩 이미지가 아직 없습니다.")
    outfit = storage.download_for(
        str(image["s3_bucket"]),
        str(image["s3_key"]),
        max_bytes=settings.OUTFIT_RENDER_MAX_OUTPUT_BYTES,
    )

    bucket = settings.OUTFIT_RENDER_RESULT_BUCKET
    key = result_key(job.contract)
    cache_hit = storage.exists_for(bucket, key)
    if not cache_hit:
        runner = service or VirtualTryOnService()
        generated = (
            runner.fit_mannequin(person, outfit)
            if job.mode == "mannequin"
            # 기본 경로. 사진 속 그 사람에게 입히고, 체형 판정은 옷이 앉는 방식에만 쓴다.
            else runner.fit_person(person, outfit, body_note_for(job.look))
        )
        storage.put_bytes_for(bucket, key, generated.content, generated.media_type)
        media_type = generated.media_type
    else:
        media_type = "image/png"

    return mark_succeeded(
        job.pk,
        bucket=bucket,
        key=key,
        media_type=media_type,
        cache_hit=cache_hit,
    )


def mark_succeeded(
    job_id, *, bucket: str, key: str, media_type: str, cache_hit: bool
) -> VirtualTryOnJob:
    VirtualTryOnJob.objects.filter(pk=job_id).update(
        status=VirtualTryOnJob.Status.SUCCEEDED,
        result_s3_bucket=bucket,
        result_s3_key=key,
        result_media_type=media_type,
        cache_hit=cache_hit,
        error_code="",
        error_message="",
        finished_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return VirtualTryOnJob.objects.get(pk=job_id)


def mark_failed(job_id, *, error_code: str, error_message: str) -> VirtualTryOnJob:
    VirtualTryOnJob.objects.filter(pk=job_id).update(
        status=VirtualTryOnJob.Status.FAILED,
        error_code=error_code[:64],
        error_message=error_message[:500],
        finished_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return VirtualTryOnJob.objects.get(pk=job_id)


def reset_for_retry(job_id) -> bool:
    """워커가 죽어 PROCESSING 으로 남은 작업을 다시 대기로 돌린다."""
    return bool(
        VirtualTryOnJob.objects.filter(
            pk=job_id, status=VirtualTryOnJob.Status.PROCESSING
        ).update(
            status=VirtualTryOnJob.Status.QUEUED,
            started_at=None,
            updated_at=timezone.now(),
        )
    )


def detail_for(job: VirtualTryOnJob | None) -> str | None:
    """상태별 사용자 문구. 내부 오류 메시지를 그대로 노출하지 않는다."""
    if job is None:
        return None
    if job.status in VirtualTryOnJob.PENDING_STATUSES:
        return "가상 피팅 이미지를 생성 중입니다. 잠시만 기다려주세요."
    if job.status == VirtualTryOnJob.Status.FAILED:
        return job.error_message or "가상 착장을 만들지 못했어요. 다시 시도해 주세요."
    return None


def payload(job: VirtualTryOnJob | None) -> dict[str, Any]:
    """조회·접수 응답 공통 본문."""
    if job is None:
        return {
            "job_id": None,
            "status": None,
            "mode": "",
            "golden_id": "",
            "image_url": None,
            "cache_hit": False,
            "poll_after_ms": None,
            "detail": None,
        }
    return {
        "job_id": str(job.pk),
        "status": job.status,
        "mode": job.mode,
        "golden_id": job.golden_id,
        "image_url": result_url(job),
        "cache_hit": job.cache_hit,
        "poll_after_ms": (
            settings.VIRTUAL_TRY_ON_POLL_AFTER_MS if job.is_pending else None
        ),
        "detail": detail_for(job),
    }
