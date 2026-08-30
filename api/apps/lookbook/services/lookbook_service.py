"""룩북 생성·조회·수정·삭제 비즈니스 로직.

등록 시나리오는 캘린더(style_calendar)와 같은 골격을 쓴다.
  룩 사진 업로드 → S3 선업로드 → 옷장 job 생성 → 큐 적재 → 202
  → 이미지 프로세서 → 옷장 callback → 룩북에 아이템 자동 연결

캘린더와 다른 점이 딱 하나 있다. **입은 옷으로 이미 지정한 대분류는 사진에서
다시 뽑지 않는다.** 사용자가 상의를 직접 골라 뒀는데 사진에서 같은 상의를 또
등록하면 옷장에 같은 옷이 두 벌 생기고, 룩북 카드에도 중복해 보인다. 제외는
큐 페이로드(exclude_categories)로 이미지 프로세서에 전달해 열거 직후에
걸러진다 — 여기서 거르면 생성·태깅·임베딩 비용 자체가 발생하지 않는다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, Q, QuerySet
from django.utils import timezone

from apps.lookbook.contracts import (
    LookbookLinkType,
    LookbookProcessingErrorCode,
    LookbookSourceType,
    LookbookStatus,
    recommendation_card_id_from_lookbook,
)
from apps.lookbook.models import LookbookPost, LookbookWardrobeItem
from apps.lookbook.services import storage
from apps.style_calendar.models import CalendarEntry
from apps.style_calendar.services import calendar_service
from apps.style_calendar.services import storage as calendar_storage
from apps.wardrobe.models import WardrobeItem, WardrobeUploadJob
from apps.wardrobe.services import storage as wardrobe_storage
from apps.wardrobe.services.items import skipped_categories_for

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile

    from apps.users.models import User

logger = logging.getLogger(__name__)


class WardrobeItemsNotFoundError(Exception):
    """요청 사용자가 소유하지 않은 옷장 아이템이 포함된 경우."""


class LookbookStorageError(Exception):
    """룩북 소유 S3 경로로 업로드·복사에 실패한 경우."""


class LookbookNotFoundError(Exception):
    """대상 룩북이 없거나 요청 사용자 소유가 아닌 경우."""


class LookbookDeletionConflictError(Exception):
    """이미지 처리가 끝나지 않아 안전하게 삭제할 수 없는 경우."""

    def __init__(self, current_status: str) -> None:
        self.current_status = current_status
        super().__init__("이미지 처리 중인 룩북은 삭제할 수 없습니다.")


class CalendarDateConflictError(Exception):
    """'캘린더에도 기록'을 켰는데 그 날짜에 이미 캘린더가 있는 경우."""

    def __init__(self, entry_date: date) -> None:
        self.entry_date = entry_date
        super().__init__("해당 날짜의 캘린더가 이미 존재합니다.")


class CalendarBusyError(Exception):
    """덮어쓰려는 캘린더가 아직 이미지 처리 중인 경우."""

    def __init__(self, current_status: str) -> None:
        self.current_status = current_status
        super().__init__("이미지 처리 중인 캘린더는 교체할 수 없습니다.")


# ── 조회 ──────────────────────────────────────────────────
def posts_for_user(*, user) -> QuerySet[LookbookPost]:
    """사용자 소유 룩북과 조회 응답에 필요한 하위 데이터를 반환한다."""

    return (
        LookbookPost.objects.filter(user=user)
        .select_related("wardrobe_upload_job", "calendar_entry")
        .prefetch_related(
            Prefetch(
                "wardrobe_links",
                queryset=LookbookWardrobeItem.objects.select_related(
                    "wardrobe_item"
                ).order_by("sort_order", "created_at"),
            ),
        )
    )


def posts_filtered(
    *,
    user,
    hashtag: str = "",
    status: str = "",
) -> QuerySet[LookbookPost]:
    """해시태그·상태로 좁힌 룩북 목록.

    hashtags는 JSONField(문자열 배열)라 `contains`로 "이 값을 포함하는 배열"을
    찾는다. PostgreSQL jsonb `@>` 연산으로 내려가므로 파이썬에서 거르지 않는다.
    """

    queryset = posts_for_user(user=user)
    if hashtag:
        queryset = queryset.filter(hashtags__contains=[hashtag])
    if status:
        queryset = queryset.filter(status=status)
    return queryset


def public_posts(*, hashtag: str = "") -> QuerySet[LookbookPost]:
    """전체 공개된 룩 피드 — 로그인 여부와 무관하게 누구나 보는 목록.

    처리 중이거나 실패한 룩은 내보내지 않는다. 사진에서 옷을 뽑는 도중인 룩은
    표지도 아이템도 아직 제자리가 아니라, 남에게 보이면 깨진 카드가 된다.
    """

    queryset = (
        LookbookPost.objects.filter(
            is_public=True,
            status=LookbookStatus.COMPLETED.value,
        )
        .select_related("wardrobe_upload_job", "calendar_entry")
        .prefetch_related(
            Prefetch(
                "wardrobe_links",
                queryset=LookbookWardrobeItem.objects.select_related(
                    "wardrobe_item"
                ).order_by("sort_order", "created_at"),
            ),
        )
    )
    if hashtag:
        queryset = queryset.filter(hashtags__contains=[hashtag])
    return queryset


def processing_statuses_for_user(*, user) -> QuerySet[LookbookPost]:
    """처리 상태 응답에 필요한 아이템 집계를 포함한 사용자 룩북 QuerySet."""

    return LookbookPost.objects.filter(user=user).annotate(
        total_item_count=Count("wardrobe_links", distinct=True),
        selected_item_count=Count(
            "wardrobe_links",
            filter=Q(wardrobe_links__link_type=LookbookLinkType.SELECTED.value),
            distinct=True,
        ),
        extracted_item_count=Count(
            "wardrobe_links",
            filter=Q(wardrobe_links__link_type=LookbookLinkType.EXTRACTED.value),
            distinct=True,
        ),
    )


# ── 내부 헬퍼 ─────────────────────────────────────────────
def _owned_wardrobe_items(
    *,
    user: User,
    wardrobe_item_ids: Sequence[UUID],
) -> list[WardrobeItem]:
    owned_items = WardrobeItem.objects.filter(user=user, pk__in=wardrobe_item_ids)
    item_by_id = {item.pk: item for item in owned_items}
    if len(item_by_id) != len(wardrobe_item_ids):
        raise WardrobeItemsNotFoundError
    return [item_by_id[item_id] for item_id in wardrobe_item_ids]


def _wardrobe_snapshot(
    item: WardrobeItem,
    *,
    lookbook_s3_key: str,
) -> dict[str, object]:
    """옷장 데이터 변경과 무관하게 룩북에 남길 연결 시점 정보."""

    return {
        "id": str(item.pk),
        "s3_key": lookbook_s3_key,
        "source_wardrobe_s3_key": item.s3_key,
        "item_name": item.item_name,
        "category_large": item.category_large,
        "category_small": item.category_small,
        "tags": {
            "season": list(item.season),
            "style": list(item.style),
            "color": item.color,
            "pattern": item.pattern,
            "fit": item.fit,
            "material": item.material,
            "sleeve": item.sleeve,
            "length": item.length,
            "usage": list(item.usage),
            "layer_role": item.layer_role,
            "layer_order": item.layer_order,
        },
    }


def _prepare_wardrobe_links(
    *,
    post: LookbookPost,
    ordered_items: Sequence[WardrobeItem],
    link_type: LookbookLinkType,
    start_sort_order: int = 0,
) -> tuple[list[LookbookWardrobeItem], list[str]]:
    links: list[LookbookWardrobeItem] = []
    destination_keys: list[str] = []
    for item_offset, item in enumerate(ordered_items):
        link = LookbookWardrobeItem(
            lookbook=post,
            wardrobe_item=item,
            link_type=link_type.value,
            sort_order=start_sort_order + item_offset,
        )
        destination_key = storage.selected_item_key(
            post.user_id,
            post.pk,
            link.pk,
            item.s3_key,
        )
        link.snapshot = _wardrobe_snapshot(item, lookbook_s3_key=destination_key)
        links.append(link)
        destination_keys.append(destination_key)
    return links, destination_keys


def _copy_wardrobe_images(
    *,
    ordered_items: Sequence[WardrobeItem],
    destination_keys: Sequence[str],
    stored_keys: list[str],
) -> None:
    for item, destination_key in zip(ordered_items, destination_keys, strict=True):
        storage.copy_wardrobe_item(item.s3_key, destination_key)
        stored_keys.append(destination_key)


def _cleanup_s3_objects(keys: Sequence[str]) -> None:
    if not keys:
        return
    try:
        storage.delete_objects(keys)
    except Exception:
        logger.exception("룩북 S3 객체 정리 실패: object_count=%s", len(keys))


def _cleanup_wardrobe_s3_objects(keys: Sequence[str]) -> None:
    if not keys:
        return
    try:
        wardrobe_storage.delete_objects(keys)
    except Exception:
        logger.exception("옷장 S3 객체 정리 실패: object_count=%s", len(keys))


def _guard_calendar_date(
    *,
    user: User,
    entry_date: date | None,
    overwrite: bool,
) -> None:
    """캘린더를 함께 만들 수 있는 상태인지 S3를 건드리기 전에 확인한다.

    업로드를 마친 뒤 409를 주면 요청마다 고아 객체가 쌓인다.
    """

    if entry_date is None:
        return
    exists = CalendarEntry.objects.filter(user=user, date=entry_date).exists()
    if exists and not overwrite:
        raise CalendarDateConflictError(entry_date)


def _cleanup_calendar_s3_objects(keys: Sequence[str]) -> None:
    if not keys:
        return
    try:
        calendar_storage.delete_objects(keys)
    except Exception:
        logger.exception("캘린더 S3 객체 정리 실패: object_count=%s", len(keys))


def _attach_calendar_for_photo(
    *,
    post: LookbookPost,
    user: User,
    entry_date: date,
    upload_job: WardrobeUploadJob,
    lookbook_original_key: str,
    ordered_items: Sequence[WardrobeItem],
    overwrite: bool,
    calendar_stored_keys: list[str],
) -> None:
    """룩북과 **같은 job**을 공유하는 사진 캘린더를 만들어 연결한다."""

    if overwrite:
        calendar_service.clear_date_for_replacement(user=user, entry_date=entry_date)

    # 캘린더 UUID를 먼저 정해 둔다. 원본 복사 경로(calendar/{user}/{id}/)와
    # 캘린더 행의 id가 같아야 캘린더를 지울 때 그 원본도 함께 정리된다.
    entry_id = uuid4()
    calendar_key = calendar_storage.original_key(
        user.pk,
        entry_id,
        lookbook_original_key,
    )
    try:
        storage.copy_original_to_calendar(lookbook_original_key, calendar_key)
    except Exception as exc:
        # 저장소 장애는 사용자에게 "잠시 후 다시"라고 안내할 수 있는 실패다.
        # raw 예외로 새어 나가면 500이 되어 재시도 안내를 못 준다.
        raise LookbookStorageError from exc
    # 뒤에서 실패하면 이 복사본도 되돌려야 한다 — 룩북 버킷이 아니라
    # 캘린더 버킷에 있어서 룩북 정리 경로가 손대지 못한다.
    calendar_stored_keys.append(calendar_key)

    entry = calendar_service.create_photo_entry_for_job(
        user=user,
        entry_date=entry_date,
        upload_job=upload_job,
        image_s3_key=calendar_key,
        ordered_items=ordered_items,
        schedule=post.schedule,
        tpo=post.tpo,
        hashtags=post.hashtags,
        entry_id=entry_id,
    )
    post.calendar_entry = entry


def _attach_calendar_for_wardrobe(
    *,
    post: LookbookPost,
    user: User,
    entry_date: date,
    wardrobe_item_ids: Sequence[UUID],
    overwrite: bool,
) -> None:
    """사진 없이 고른 옷만으로 만든 룩북에 캘린더를 붙인다."""

    if overwrite:
        calendar_service.clear_date_for_replacement(user=user, entry_date=entry_date)

    entry = calendar_service.create_from_wardrobe(
        user=user,
        entry_date=entry_date,
        wardrobe_item_ids=wardrobe_item_ids,
        schedule=post.schedule,
        tpo=post.tpo,
        hashtags=post.hashtags,
    )
    post.calendar_entry = entry


def _translate_calendar_errors(error: Exception, entry_date: date | None) -> Exception:
    if entry_date is None:
        return error
    if isinstance(error, calendar_service.CalendarDateConflictError):
        return CalendarDateConflictError(entry_date)
    if isinstance(error, calendar_service.CalendarDeletionConflictError):
        return CalendarBusyError(error.current_status)
    if isinstance(error, calendar_service.CalendarStorageError):
        return LookbookStorageError()
    return error


# ── 등록 ──────────────────────────────────────────────────
def create_from_wardrobe(
    *,
    user: User,
    wardrobe_item_ids: Sequence[UUID],
    schedule: str,
    tpo: list[str],
    hashtags: list[str],
    calendar_date: date | None = None,
    overwrite_calendar: bool = False,
    is_public: bool = False,
) -> LookbookPost:
    """옷장 아이템만 골라 올린 룩북 (사진 없음, 바로 완료 상태)."""

    ordered_items = _owned_wardrobe_items(
        user=user,
        wardrobe_item_ids=wardrobe_item_ids,
    )
    _guard_calendar_date(
        user=user,
        entry_date=calendar_date,
        overwrite=overwrite_calendar,
    )

    post = LookbookPost(
        user=user,
        source_type=LookbookSourceType.WARDROBE_SELECTED.value,
        image_s3_key="",
        schedule=schedule,
        tpo=tpo,
        hashtags=hashtags,
        skipped_categories=[],
        is_public=is_public,
        status=LookbookStatus.COMPLETED.value,
    )
    links, destination_keys = _prepare_wardrobe_links(
        post=post,
        ordered_items=ordered_items,
        link_type=LookbookLinkType.SELECTED,
    )

    stored_keys: list[str] = []
    try:
        _copy_wardrobe_images(
            ordered_items=ordered_items,
            destination_keys=destination_keys,
            stored_keys=stored_keys,
        )
    except Exception as exc:
        _cleanup_s3_objects(stored_keys)
        raise LookbookStorageError from exc

    # 룩 사진이 없으면 고른 옷의 첫 장이 표지가 된다 (프론트 LookComposer와 같은 규칙).
    post.image_s3_key = destination_keys[0]

    try:
        with transaction.atomic():
            post.save(force_insert=True)
            LookbookWardrobeItem.objects.bulk_create(links)
            if calendar_date is not None:
                _attach_calendar_for_wardrobe(
                    post=post,
                    user=user,
                    entry_date=calendar_date,
                    wardrobe_item_ids=wardrobe_item_ids,
                    overwrite=overwrite_calendar,
                )
                post.save(update_fields=["calendar_entry", "updated_at"])
    except Exception as exc:
        _cleanup_s3_objects(stored_keys)
        raise _translate_calendar_errors(exc, calendar_date) from exc

    return posts_for_user(user=user).get(pk=post.pk)


@dataclass(frozen=True)
class GoldenLookItem:
    """골든 코디 구성 아이템 한 벌치 스냅샷.

    옷장 아이템(WardrobeItem)이 아니다 — 사용자가 가진 옷이 아니라 골든셋의
    옷이므로 옷장에 넣지 않고 값만 베껴 둔다. 골든셋이 다시 적재되어 태그가
    바뀌어도 사용자가 담아 둔 룩은 담을 때 모습 그대로 남아야 한다.
    """

    item_key: str
    name: str = ""
    category: str = ""
    sub_category: str = ""
    layer_role: str = ""
    color: str = ""
    s3_bucket: str = ""
    s3_key: str = ""

    def as_snapshot(self) -> dict[str, str]:
        """옷장 아이템 스냅샷(_wardrobe_snapshot)과 **같은 키 이름**을 쓴다.

        룩북 상세는 옷장 룩과 골든 룩을 한 화면에서 그린다. 여기서만 "name"을
        쓰면 프론트가 출처별로 다른 키를 읽어야 하고, 한쪽을 고칠 때 다른 쪽이
        조용히 빈칸이 된다. s3_bucket 은 골든 룩에만 있는 추가 키다 —
        옷장 스냅샷은 룩북 버킷이 기본이라 버킷을 적을 필요가 없었다.
        """

        return {
            "item_key": self.item_key,
            "item_name": self.name,
            "category_large": self.category,
            "category_small": self.sub_category,
            "layer_role": self.layer_role,
            "color": self.color,
            "s3_bucket": self.s3_bucket,
            "s3_key": self.s3_key,
        }


def create_from_golden_look(
    *,
    user: User,
    golden_id: str,
    image_bucket: str,
    image_key: str,
    schedule: str = "",
    hashtags: Sequence[str] = (),
    items: Sequence[GoldenLookItem] = (),
) -> tuple[LookbookPost, bool]:
    """오늘의 룩에서 담은 골든 코디를 룩북에 남긴다.

    Returns: (룩북, 새로 만들었는지). 이미 담아 둔 코디면 (기존 룩북, False).

    사진 등록과 달리 **S3를 전혀 건드리지 않는다.** 골든셋 이미지는 코디당 한
    장을 모든 사용자가 공유하는 자산이라, 담을 때마다 룩북 버킷으로 복사하면
    같은 사진이 사용자 수만큼 늘어난다. 버킷과 키를 함께 저장해 두고 조회 시점에
    그 버킷으로 서명한다(storage.presigned_get_in).

    그래서 상태도 처음부터 COMPLETED다. 뽑을 옷도 만들 이미지도 없다.

    멱등은 DB가 보장한다. (user, golden_id) 유니크 제약이 있어 두 기기에서
    동시에 눌러도 행은 하나다 — select 후 insert 사이에 다른 요청이 끼면
    깨지므로 IntegrityError를 '이미 있음'으로 읽는다 (오늘의 룩 생성과 같은 관례).
    """

    golden_id = golden_id.strip()
    if not golden_id:
        raise ValueError("golden_id는 비어 있을 수 없습니다.")

    existing = LookbookPost.objects.filter(user=user, golden_id=golden_id).first()
    if existing is not None:
        return posts_for_user(user=user).get(pk=existing.pk), False

    post = LookbookPost(
        user=user,
        source_type=LookbookSourceType.GOLDEN_LOOK.value,
        golden_id=golden_id,
        image_s3_bucket=image_bucket.strip(),
        image_s3_key=image_key.strip(),
        schedule=schedule.strip(),
        tpo=[],
        hashtags=list(hashtags),
        skipped_categories=[],
        is_public=False,
        status=LookbookStatus.COMPLETED.value,
    )
    links = [
        LookbookWardrobeItem(
            lookbook=post,
            wardrobe_item=None,
            link_type=LookbookLinkType.GOLDEN.value,
            sort_order=order,
            snapshot=item.as_snapshot(),
        )
        for order, item in enumerate(items)
    ]

    try:
        with transaction.atomic():
            post.save(force_insert=True)
            if links:
                LookbookWardrobeItem.objects.bulk_create(links)
    except IntegrityError:
        # 같은 코디를 두 기기에서 동시에 담은 경우. 진 쪽은 이긴 쪽의 행을 쓴다.
        already = LookbookPost.objects.filter(user=user, golden_id=golden_id).first()
        if already is None:
            raise
        return posts_for_user(user=user).get(pk=already.pk), False

    return posts_for_user(user=user).get(pk=post.pk), True


def create_from_photo(
    *,
    user: User,
    image: UploadedFile,
    wardrobe_item_ids: Sequence[UUID],
    schedule: str,
    tpo: list[str],
    hashtags: list[str],
    calendar_date: date | None = None,
    overwrite_calendar: bool = False,
    is_public: bool = False,
) -> LookbookPost:
    """룩 사진을 올려 만든 룩북. 사진 속 아이템은 비동기로 옷장에 등록된다."""

    ordered_items = _owned_wardrobe_items(
        user=user,
        wardrobe_item_ids=wardrobe_item_ids,
    )
    _guard_calendar_date(
        user=user,
        entry_date=calendar_date,
        overwrite=overwrite_calendar,
    )

    upload_job = WardrobeUploadJob(user=user)
    post = LookbookPost(
        user=user,
        wardrobe_upload_job=upload_job,
        source_type=LookbookSourceType.PHOTO_UPLOAD.value,
        image_s3_key="",
        schedule=schedule,
        tpo=tpo,
        hashtags=hashtags,
        skipped_categories=skipped_categories_for(ordered_items),
        is_public=is_public,
        status=LookbookStatus.REGISTERED.value,
    )

    original_s3_key = storage.original_key(
        user.pk,
        post.pk,
        image.name,
        image.content_type,
    )
    post.image_s3_key = original_s3_key
    wardrobe_original_s3_key = wardrobe_storage.original_key(
        user.pk,
        upload_job.pk,
        image.name,
    )
    upload_job.source_s3_key = wardrobe_original_s3_key

    links, destination_keys = _prepare_wardrobe_links(
        post=post,
        ordered_items=ordered_items,
        link_type=LookbookLinkType.SELECTED,
    )

    stored_keys: list[str] = []
    wardrobe_stored_keys: list[str] = []
    try:
        storage.upload_fileobj(image, original_s3_key, image.content_type)
        stored_keys.append(original_s3_key)
        # 워커 입력은 옷장 버킷 기준이라 원본을 그쪽으로도 복사한다
        # (같은 사진을 두 번 업로드하지 않기 위한 서버 측 copy).
        storage.copy_original_to_wardrobe(original_s3_key, wardrobe_original_s3_key)
        wardrobe_stored_keys.append(wardrobe_original_s3_key)
        _copy_wardrobe_images(
            ordered_items=ordered_items,
            destination_keys=destination_keys,
            stored_keys=stored_keys,
        )
    except Exception as exc:
        _cleanup_s3_objects(stored_keys)
        _cleanup_wardrobe_s3_objects(wardrobe_stored_keys)
        raise LookbookStorageError from exc

    calendar_stored_keys: list[str] = []
    try:
        with transaction.atomic():
            upload_job.save(force_insert=True)
            post.save(force_insert=True)
            LookbookWardrobeItem.objects.bulk_create(links)
            if calendar_date is not None:
                _attach_calendar_for_photo(
                    post=post,
                    user=user,
                    entry_date=calendar_date,
                    upload_job=upload_job,
                    lookbook_original_key=original_s3_key,
                    ordered_items=ordered_items,
                    overwrite=overwrite_calendar,
                    calendar_stored_keys=calendar_stored_keys,
                )
                post.save(update_fields=["calendar_entry", "updated_at"])
    except Exception as exc:
        _cleanup_s3_objects(stored_keys)
        _cleanup_wardrobe_s3_objects(wardrobe_stored_keys)
        _cleanup_calendar_s3_objects(calendar_stored_keys)
        raise _translate_calendar_errors(exc, calendar_date) from exc

    return posts_for_user(user=user).get(pk=post.pk)


# ── 큐 적재 실패 ──────────────────────────────────────────
def mark_queue_enqueue_failed(post: LookbookPost) -> None:
    """Redis 적재 실패를 PostgreSQL의 최종 실패 상태로 기록한다."""

    completed_at = timezone.now()
    with transaction.atomic():
        post.status = LookbookStatus.FAILED.value
        post.processing_error_code = (
            LookbookProcessingErrorCode.QUEUE_ENQUEUE_FAILED.value
        )
        post.processing_error_message = "옷장 이미지 처리 큐 적재 실패"
        post.processing_completed_at = completed_at
        post.save(
            update_fields=[
                "status",
                "processing_error_code",
                "processing_error_message",
                "processing_completed_at",
                "updated_at",
            ]
        )
        if post.wardrobe_upload_job_id:
            WardrobeUploadJob.objects.filter(pk=post.wardrobe_upload_job_id).update(
                status=WardrobeUploadJob.Status.FAILED,
                error_message="처리 큐 적재 실패",
                finished_at=completed_at,
            )


# ── 옷장 callback 반영 ────────────────────────────────────
def is_lookbook_job(*, job: WardrobeUploadJob) -> bool:
    """이 사진 처리 job 이 룩북에서 시작된 것인가.

    옷장 callback 이 아이템을 만들 때, 룩북에서 온 사진이면 옷장에 바로 넣지 않는다
    (사용자가 고른 적 없는 옷이라 룩 상세에서 직접 '옷장에 추가'해야 한다).
    옷장 쪽이 룩북 모델을 직접 import 하지 않도록 판별을 여기 둔다.
    """
    return LookbookPost.objects.filter(wardrobe_upload_job=job).exists()


def apply_wardrobe_job_success(
    *,
    job: WardrobeUploadJob,
    created_items: Sequence[WardrobeItem],
) -> None:
    """옷장 callback 결과를 해당 룩북에 자동 연결한다."""

    post = (
        LookbookPost.objects.select_for_update()
        .filter(wardrobe_upload_job=job)
        .first()
    )
    if post is None:
        return

    existing_item_ids = set(
        post.wardrobe_links.values_list("wardrobe_item_id", flat=True)
    )
    skipped = set(post.skipped_categories or [])
    # 제외는 워커가 열거 직후에 이미 적용한다. 여기 한 겹 더 두는 것은 구버전
    # 워커(exclude_categories 미지원)가 붙어도 룩북 카드에 같은 부위가 두 번
    # 걸리지 않게 하기 위한 안전망이다. 옷장 아이템 자체는 지우지 않는다 —
    # 이미 만들어진 사용자 데이터를 룩북 사정으로 삭제할 수는 없다.
    new_items = [
        item
        for item in created_items
        if item.pk not in existing_item_ids and item.category_large not in skipped
    ]
    start_sort_order = post.wardrobe_links.count()
    links, destination_keys = _prepare_wardrobe_links(
        post=post,
        ordered_items=new_items,
        link_type=LookbookLinkType.EXTRACTED,
        start_sort_order=start_sort_order,
    )

    stored_keys: list[str] = []
    try:
        _copy_wardrobe_images(
            ordered_items=new_items,
            destination_keys=destination_keys,
            stored_keys=stored_keys,
        )
        LookbookWardrobeItem.objects.bulk_create(links)
    except Exception:
        _cleanup_s3_objects(stored_keys)
        raise

    post.status = LookbookStatus.COMPLETED.value
    post.processing_error_code = ""
    post.processing_error_message = ""
    post.processing_completed_at = job.finished_at or timezone.now()
    post.callback_applied_at = timezone.now()
    post.save(
        update_fields=[
            "status",
            "processing_error_code",
            "processing_error_message",
            "processing_completed_at",
            "callback_applied_at",
            "updated_at",
        ]
    )


def apply_wardrobe_job_failure(*, job: WardrobeUploadJob) -> None:
    """옷장 callback 실패 상태를 연결된 룩북에 반영한다."""

    post = (
        LookbookPost.objects.select_for_update()
        .filter(wardrobe_upload_job=job)
        .first()
    )
    if post is None:
        return

    post.status = LookbookStatus.FAILED.value
    post.processing_error_code = (
        LookbookProcessingErrorCode.IMAGE_PROCESSING_FAILED.value
    )
    post.processing_error_message = job.error_message
    post.processing_completed_at = job.finished_at or timezone.now()
    post.callback_applied_at = timezone.now()
    post.save(
        update_fields=[
            "status",
            "processing_error_code",
            "processing_error_message",
            "processing_completed_at",
            "callback_applied_at",
            "updated_at",
        ]
    )


# ── 삭제 ──────────────────────────────────────────────────
@transaction.atomic
def delete_post(*, user, lookbook_id: UUID) -> None:
    """종료된 룩북을 삭제하고 커밋 후 S3 prefix를 정리한다.

    연결된 캘린더는 지우지 않는다 — 캘린더는 '언제 입었는지'의 기록이고
    룩북 삭제는 '피드에서 내린다'는 뜻이라 수명이 다르다. FK는 SET_NULL이라
    캘린더 쪽에는 아무 영향이 없다.
    """

    post = (
        LookbookPost.objects.select_for_update()
        .filter(pk=lookbook_id, user=user)
        .first()
    )
    if post is None:
        raise LookbookNotFoundError
    if post.status not in {
        LookbookStatus.COMPLETED.value,
        LookbookStatus.FAILED.value,
    }:
        raise LookbookDeletionConflictError(post.status)

    user_id = post.user_id
    post_id = post.pk
    recommendation_card_id = recommendation_card_id_from_lookbook(post.golden_id)
    post.delete()
    if recommendation_card_id:
        # 추천 저장과 룩북이 서로 다른 상태로 남지 않게 룩북 삭제를 북마크에도 반영한다.
        from apps.recommend.models import SavedOutfit

        SavedOutfit.objects.filter(
            user_id=user_id,
            composition_id=recommendation_card_id,
        ).delete()
    transaction.on_commit(
        lambda: _cleanup_deleted_lookbook_s3(user_id=user_id, lookbook_id=post_id)
    )


def _cleanup_deleted_lookbook_s3(*, user_id: int | str, lookbook_id: UUID) -> None:
    try:
        storage.delete_lookbook(user_id, lookbook_id)
    except Exception:
        # DB 삭제는 이미 커밋됐다. 사용자 요청을 실패로 되돌리지 않고 운영 로그로
        # 남겨 고아 S3 객체를 별도로 정리할 수 있게 한다.
        logger.exception(
            "삭제된 룩북 S3 prefix 정리 실패: user_id=%s lookbook_id=%s",
            user_id,
            lookbook_id,
        )
