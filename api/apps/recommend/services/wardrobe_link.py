"""코디 평가 → 옷장 아이템 등록 연계.

로그인 사용자가 `save_to_wardrobe=true`로 접수하면, 평가에 올린 사진을 기존 옷장
파이프라인(Redis `wardrobe:jobs` → image-processor → 콜백)에도 그대로 흘려보낸다.

crossing-app 호출을 이 모듈 하나로 몰아 둔다. analysis.py가 wardrobe 모델·서비스를
직접 import하면 두 도메인이 얽혀서, 나중에 옷장 파이프라인 계약이 바뀔 때 평가 쪽까지
읽어야 한다.

설계 결정
- **사진을 다시 올리지 않는다.** 접수 때 이미 `outfits/{user}/{analysis}/original.jpg`로
  업로드했으므로 그 키를 옷장 job의 원본으로 그대로 쓴다. 복사하면 같은 사진이 S3에
  두 벌 쌓인다 (무료 플랜을 이미 한 번 소진한 적이 있다).
  대신 큐 페이로드에 source·output 버킷을 명시해, 결과물은 옷장 버킷에 쌓이게 한다.
- **실패해도 평가를 막지 않는다.** 사용자가 요청한 주된 작업은 코디 평가다. 옷장 등록은
  곁가지이므로 job을 FAILED로 남기고 넘어간다 — 무엇이 실패했는지는 job 조회로 보인다.
- **읽기도 이 모듈을 거친다.** 평가 상세 응답에 옷장 진행 상황·아이템을 실어야 해서
  `job_summary()`를 여기 둔다. serializers.py가 WardrobeItem을 직접 import하면
  옷장 태그 스키마가 바뀔 때마다 평가 시리얼라이저까지 따라 깨진다.
"""

from __future__ import annotations

import logging

import redis
from django.utils import timezone

from apps.wardrobe.models import WardrobeUploadJob
from apps.wardrobe.services import jobs as wardrobe_jobs
from apps.wardrobe.services import storage as wardrobe_storage

from . import storage

logger = logging.getLogger(__name__)

#: 평가 상세 응답에 실을 아이템 필드. 전체 태그(season/style/fit/seg_meta 등)는
#: 옷장 API의 일이라 여기서는 칩·썸네일을 그릴 만큼만 내려준다.
ITEM_SUMMARY_FIELDS = (
    "item_name",
    "category_large",
    "category_small",
    "color",
    "confirmed",
)


def register_outfit_photo(analysis) -> WardrobeUploadJob | None:
    """평가에 쓴 사진으로 옷장 등록 job을 만들고 큐에 넣는다.

    Returns: 생성한 job. 생성 자체가 불가능하면 None (평가는 계속된다).
             큐 적재에 실패한 경우에도 job은 FAILED 상태로 반환한다 —
             클라이언트가 조회했을 때 "왜 안 들어왔는지" 보여야 한다.
    """
    if analysis.user_id is None:
        # 옷장은 사용자 소유 데이터라 익명 요청에는 적용할 수 없다 (뷰에서도 걸러진다)
        return None
    if not analysis.image_s3_key:
        logger.warning("옷장 연계 생략: 사진 키 없음 analysis=%s", analysis.pk)
        return None

    try:
        job = WardrobeUploadJob.objects.create(
            user_id=analysis.user_id,
            source_s3_key=analysis.image_s3_key,
        )
    except Exception:  # noqa: BLE001 — 연계 실패가 평가를 막지 않는다
        logger.exception("옷장 job 생성 실패: analysis=%s", analysis.pk)
        return None

    try:
        # 원본은 코디 평가 버킷에 있다. 옷장 버킷과 같을 수도, 다를 수도 있다.
        wardrobe_jobs.enqueue(job, source_bucket=storage.bucket())
    except redis.RedisError:
        logger.exception("옷장 job 큐 적재 실패: job=%s analysis=%s", job.pk, analysis.pk)
        job.status = WardrobeUploadJob.Status.FAILED
        job.error_message = "처리 큐 적재 실패"
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error_message", "finished_at"])
        return job

    logger.info(
        "옷장 등록 연계: analysis=%s job=%s user=%s",
        analysis.pk,
        job.pk,
        analysis.user_id,
    )
    return job


def _item_image_url(s3_key: str) -> str | None:
    """옷장 버킷은 비공개라 presigned GET으로만 노출한다. 발급 실패 시 None."""
    if not s3_key or not wardrobe_storage.BUCKET:
        return None
    try:
        return wardrobe_storage.presigned_get(s3_key)
    except Exception:  # noqa: BLE001 — URL 발급 실패가 평가 조회를 막지 않는다
        logger.warning("옷장 아이템 presigned URL 발급 실패: key=%s", s3_key, exc_info=True)
        return None


def job_summary(analysis) -> dict | None:
    """평가 상세 응답에 실을 옷장 연계 요약을 만든다.

    Returns: 연계 job이 없으면 None. 있으면 항상 상태를 내려주고, **job이 DONE일
        때만** 생성된 아이템 요약을 채운다 (그 외에는 빈 배열).

    옷장 파이프라인은 GPU 서버 → 콜백 구조라 평가가 SUCCEEDED가 된 뒤에도 job은
    아직 PROCESSING일 수 있다. 그래서 상태를 항상 실어 프론트가 이 엔드포인트만
    폴링해도 옷장 등록 완료까지 따라갈 수 있게 한다.

    N+1을 피하려면 호출부가 `select_related("wardrobe_job")` ·
    `prefetch_related("wardrobe_job__items")`로 미리 당겨야 한다.
    """
    job = analysis.wardrobe_job
    if job is None:
        return None

    items: list[dict] = []
    if job.status == WardrobeUploadJob.Status.DONE:
        items = [
            {
                "id": item.pk,
                **{name: getattr(item, name) for name in ITEM_SUMMARY_FIELDS},
                "image_url": _item_image_url(item.s3_key),
            }
            for item in job.items.all()
        ]

    return {
        "job_id": job.pk,
        "status": job.status,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        "items": items,
    }


def accessible_item_ids(user) -> list[str]:
    """로그인 사용자가 추천 검색에서 접근 가능한 옷장 아이템 UUID 리스트를 반환합니다.

    격리 규칙 (Confluence §4-2 / shared-wardrobe-spec.md):
    - 내 옷: user=user, confirmed=True
    - 공유방 옷: room__members__user=user, wardrobe_item__confirmed=True
    - 상한: settings.RETRIEVER_WARDROBE_ID_CAP
    - 정열: created_at 내림차순 (결정적 필터링)
    """
    from django.conf import settings
    from apps.wardrobe.models import SharedWardrobeItem, WardrobeItem

    if user is None or not user.is_authenticated:
        return []

    own_items = list(
        WardrobeItem.objects.filter(user=user, confirmed=True)
        .order_by("-created_at")
        .values_list("id", flat=True)
    )

    shared_items = list(
        SharedWardrobeItem.objects.filter(
            room__members__user=user,
            wardrobe_item__confirmed=True,
        )
        .order_by("-created_at")
        .values_list("wardrobe_item_id", flat=True)
    )

    seen = set()
    combined: list[str] = []
    for item_id in own_items + shared_items:
        str_id = str(item_id)
        if str_id not in seen:
            seen.add(str_id)
            combined.append(str_id)

    cap = getattr(settings, "RETRIEVER_WARDROBE_ID_CAP", 1000)
    if len(combined) > cap:
        logger.warning(
            "accessible_item_ids 상한(%d) 초과: user=%s total=%d",
            cap,
            user.pk,
            len(combined),
        )
        combined = combined[:cap]

    return combined


def owned_closet_item_ids(user) -> list[str]:
    """개인 해시태그 추천용 소유·확정·옷장 편입 아이템 UUID만 반환한다."""
    from django.conf import settings
    from apps.wardrobe.models import WardrobeItem

    if user is None or not user.is_authenticated:
        return []
    values = (
        WardrobeItem.objects.filter(
            user=user,
            confirmed=True,
            added_to_closet_at__isnull=False,
        )
        .order_by("-added_to_closet_at", "-created_at")
        .values_list("id", flat=True)
    )
    cap = getattr(settings, "RETRIEVER_WARDROBE_ID_CAP", 1000)
    return [str(value) for value in values[:cap]]

