"""코디 평가 유스케이스 — 접수(동기)와 분석(워커)을 분리한다.

Gemini 호출이 30초를 넘겨 gunicorn 워커를 붙잡고 있던 문제 때문에 둘로 갈랐다.
설계: Confluence > 설계 > "코디 평가 비동기화 설계(접수·워커 분리 · 익명 폴링)"

    accept_analysis()  요청 스레드에서: 컨텍스트 스냅샷 → S3 업로드 → 행 생성 → 큐 적재
    claim() / run_analysis()  워커에서:  S3 다운로드 → 축소 → Gemini → 결과 기록

원칙
- **S3는 이제 필수 경로다.** 워커가 사진을 S3에서만 읽으므로 업로드에 실패하면
  분석이 불가능하다. best-effort로 넘기지 않고 접수를 거절한다.
- **컨텍스트는 접수 시점에 굳힌다.** 큐에서 대기하는 사이 날씨가 바뀌거나 사용자가
  추구미를 수정해도, 사진을 올린 그 순간의 조건으로 평가해야 결과와 기록이 맞는다.
  워커는 `analysis.llm_context()`를 쓰고 컨텍스트를 다시 만들지 않는다.
- **업로드 파일은 맨 앞에서 한 번만 읽는다.** boto3 upload_fileobj가 넘겨받은 파일
  객체를 닫아버려, 파일 객체를 돌려쓰면 두 번째 읽기가 ValueError로 죽는다.
- **옷장 등록은 곁가지다.** save_to_wardrobe로 요청하면 평가 큐에 넣은 직후 같은
  사진을 옷장 파이프라인에도 넘긴다. 실패해도 평가 접수는 성공으로 둔다
  (services/wardrobe_link.py).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from io import BytesIO
from typing import Any

import redis
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import F, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.weather.services import resolve_coordinates

from ..models import OutfitAnalysis
from . import gemini, imaging, queue, storage, wardrobe_link
from .outfit_context import build_analysis_context

logger = logging.getLogger(__name__)


class AnalysisAcceptError(Exception):
    """접수 자체가 불가능한 경우 (S3·큐 장애). 뷰가 503으로 변환한다."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _update(analysis: OutfitAnalysis | None, **fields: Any) -> None:
    if analysis is None:
        return
    for name, value in fields.items():
        setattr(analysis, name, value)
    analysis.save(update_fields=list(fields))


# ──────────────────────────────────────────────────────────────
# 접수 (요청 스레드)
# ──────────────────────────────────────────────────────────────


def accept_analysis(
    user,
    image: UploadedFile,
    *,
    lat: float | None,
    lon: float | None,
    save_to_wardrobe: bool = False,
) -> OutfitAnalysis:
    """사진을 접수하고 분석을 큐에 넣는다. Gemini를 호출하지 않는다.

    save_to_wardrobe: 같은 사진을 옷장 아이템 등록에도 넘긴다. 옷장은 사용자 소유
        데이터라 **로그인 요청에만 적용**되고, 비로그인이면 조용히 무시한다.

    Raises: AnalysisAcceptError — 버킷 미설정, S3 업로드 실패, 평가 큐 적재 실패
    """
    if not storage.is_configured():
        # 설정 실수를 조용히 넘기면 "접수는 되는데 영원히 분석 중"이 된다
        logger.error("OUTFIT_S3_BUCKET/WARDROBE_S3_BUCKET 미설정 — 코디 평가 접수 불가")
        raise AnalysisAcceptError("코디 평가 서비스가 설정되지 않았습니다.")

    image.seek(0)
    image_data = image.read()
    mime_type = image.content_type or ""

    context = build_analysis_context(user, lat=lat, lon=lon)
    resolved_lat, resolved_lon = resolve_coordinates(lat, lon)

    is_authenticated = bool(user and user.is_authenticated)
    analysis = OutfitAnalysis(
        user=user if is_authenticated else None,
        status=OutfitAnalysis.Status.QUEUED,
        # 나중에 소유권을 넘겨받아도 "개인화 없이 평가된 건"임을 구분할 수 있게 남긴다
        accepted_anonymously=not is_authenticated,
        # 옷장은 사용자 소유 데이터다 — 비로그인 요청의 요청값은 여기서 버린다
        save_to_wardrobe=bool(save_to_wardrobe) and is_authenticated,
        image_content_type=mime_type,
        image_bytes=len(image_data),
        requested_lat=lat,
        requested_lon=lon,
        resolved_lat=resolved_lat,
        resolved_lon=resolved_lon,
        weather=context.get("weather") or {},
        body=context.get("body"),
        pursuit=context.get("pursuit"),
        personalized=bool(context.get("personalized")),
        llm_model=settings.GEMINI_MODEL,
    )

    # pk는 UUID 기본값이라 save 전에도 정해져 있어 키를 먼저 만들 수 있다.
    # 업로드가 실패하면 행을 만들지 않는다 (분석 불가능한 기록을 남기지 않는다).
    key = storage.original_key(analysis.user_id, str(analysis.pk), image.name or "")
    try:
        storage.upload_fileobj(BytesIO(image_data), key, mime_type)
    except Exception as exc:  # noqa: BLE001
        logger.exception("코디 사진 S3 업로드 실패")
        raise AnalysisAcceptError(
            "사진 저장에 실패했습니다. 잠시 후 다시 시도해주세요."
        ) from exc

    analysis.image_s3_key = key
    analysis.save()

    try:
        queue.enqueue(analysis)
    except redis.RedisError as exc:
        # 사진은 S3에 남아 있으므로 행을 FAILED로 마킹해 흔적을 남긴다 (wardrobe와 동일)
        logger.exception("코디 평가 큐 적재 실패: analysis=%s", analysis.pk)
        _update(
            analysis,
            status=OutfitAnalysis.Status.FAILED,
            error_message="처리 큐 적재 실패",
            finished_at=timezone.now(),
        )
        raise AnalysisAcceptError(
            "처리 대기열 등록에 실패했습니다. 잠시 후 다시 시도해주세요."
        ) from exc

    # 평가 큐에 넣은 직후 옷장 등록을 이어서 요청한다 (사용자가 원한 경우에만).
    # 실패해도 평가 접수는 성공이다 — 곁가지가 본류를 막지 않는다.
    if analysis.save_to_wardrobe:
        job = wardrobe_link.register_outfit_photo(analysis)
        if job is not None:
            _update(analysis, wardrobe_job=job)

    logger.info(
        "코디 평가 접수: analysis=%s user=%s 원본=%dKB 옷장연계=%s",
        analysis.pk,
        analysis.user_id,
        len(image_data) // 1024,
        analysis.wardrobe_job_id or analysis.save_to_wardrobe,
    )
    return analysis


# ──────────────────────────────────────────────────────────────
# 분석 (워커)
# ──────────────────────────────────────────────────────────────


def claim(analysis_id: str) -> OutfitAnalysis | None:
    """작업을 집어 PROCESSING으로 전환한다.

    Returns: 처리할 행. 이미 완료됐거나 행이 없으면 None (호출부는 ack만 한다).

    중복 배달·재시도가 안전해야 하므로 select_for_update로 잠그고 상태를 확인한다.
    """
    with transaction.atomic():
        analysis = (
            OutfitAnalysis.objects.select_for_update().filter(pk=analysis_id).first()
        )
        if analysis is None:
            logger.warning("평가 %s: 행이 없어 건너뛴다", analysis_id)
            return None
        if analysis.status == OutfitAnalysis.Status.SUCCEEDED:
            logger.info("평가 %s: 이미 완료 — 중복 배달 무시", analysis_id)
            return None

        analysis.status = OutfitAnalysis.Status.PROCESSING
        analysis.started_at = timezone.now()
        analysis.attempts += 1
        analysis.save(update_fields=["status", "started_at", "attempts"])
        return analysis


def run_analysis(analysis: OutfitAnalysis) -> None:
    """S3에서 사진을 받아 Gemini로 평가하고 결과를 기록한다.

    실패하면 예외를 그대로 올린다 — 재시도·dead 처리는 워커 커맨드의 몫이다.
    실패 시에도 질의 본문은 기록해 무엇을 보냈는지 남긴다.
    """
    image_data = storage.download(analysis.image_s3_key)
    llm_data, llm_mime = imaging.shrink_for_llm(
        image_data, mime_type=analysis.image_content_type
    )
    context = analysis.llm_context()
    request_payload = gemini.build_request_payload(
        context, mime_type=llm_mime, image_bytes=len(llm_data)
    )
    logger.info(
        "평가 %s 시작: 원본=%dKB 전송=%dKB (attempt %s)",
        analysis.pk,
        len(image_data) // 1024,
        len(llm_data) // 1024,
        analysis.attempts,
    )

    try:
        result = gemini.evaluate_outfit(llm_data, mime_type=llm_mime, context=context)
    except gemini.GeminiServiceError as exc:
        _update(
            analysis,
            request_payload=request_payload,
            response_payload=exc.response_payload or {},
            llm_image_bytes=len(llm_data),
        )
        raise
    except gemini.GeminiConfigurationError:
        _update(
            analysis,
            request_payload=request_payload,
            llm_image_bytes=len(llm_data),
        )
        raise

    _update(
        analysis,
        status=OutfitAnalysis.Status.SUCCEEDED,
        llm_model=result.model,
        request_payload=request_payload,
        response_payload=result.response_payload,
        evaluation=result.evaluation,
        llm_image_bytes=len(llm_data),
        latency_ms=result.latency_ms,
        error_message="",
        finished_at=timezone.now(),
    )
    logger.info("평가 %s 완료: latency=%dms", analysis.pk, result.latency_ms)


def mark_failed(analysis: OutfitAnalysis, error: str) -> None:
    """재시도를 포기했을 때(dead queue) 호출한다."""
    _update(
        analysis,
        status=OutfitAnalysis.Status.FAILED,
        error_message=error[:2000],
        finished_at=timezone.now(),
    )


def sweep_stale(minutes: int | None = None) -> int:
    """워커가 죽어 방치된 행을 FAILED로 정리한다.

    이게 없으면 프론트가 영원히 폴링한다. 기준 시각은 started_at(없으면 created_at)이라
    큐에 들어간 채 잊힌 QUEUED 행도 함께 정리된다.
    """
    limit = minutes if minutes is not None else settings.OUTFIT_STALE_AFTER_MINUTES
    deadline = timezone.now() - timedelta(minutes=limit)
    stale = (
        OutfitAnalysis.objects.filter(status__in=OutfitAnalysis.PENDING_STATUSES)
        .annotate(since=Coalesce(F("started_at"), F("created_at")))
        .filter(Q(since__lt=deadline))
    )
    count = stale.update(
        status=OutfitAnalysis.Status.FAILED,
        error_message=f"{limit}분 내에 처리되지 않아 실패로 정리됨",
        finished_at=timezone.now(),
    )
    if count:
        logger.warning("방치된 코디 평가 %d건을 FAILED로 정리", count)
    return count
