"""캘린더 생성·조회·수정·삭제 비즈니스 로직."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Count, F, IntegerField, Prefetch, Q, QuerySet, Value
from django.utils import timezone

from apps.style_calendar.contracts import (
    CalendarProcessingErrorCode,
    CalendarSourceType,
    CalendarStatus,
)
from apps.style_calendar.models import (
    CalendarEntry,
    CalendarWardrobeItem,
)
from apps.style_calendar.services import storage
from apps.wardrobe.models import WardrobeItem, WardrobeUploadJob
from apps.wardrobe.services import storage as wardrobe_storage
from apps.wardrobe.services.items import skipped_categories_for
from apps.wardrobe.taxonomy import get_slot_key

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile

    from apps.users.models import User

logger = logging.getLogger(__name__)


class WardrobeItemsNotFoundError(Exception):
    """요청 사용자가 소유하지 않은 옷장 아이템이 포함된 경우."""


class DuplicateCategorySlotError(Exception):
    """단일 슬롯 카테고리(하의, 신발, 원피스/세트, 모자) 항목이 중복 선택된 경우."""

    def __init__(self, slot_key: str) -> None:
        self.slot_key = slot_key
        super().__init__(
            f"'{slot_key}' 카테고리 항목은 캘린더 착장당 1개만 선택할 수 있습니다."
        )


class CalendarDateConflictError(Exception):

    """사용자의 해당 날짜 캘린더가 이미 존재하는 경우."""


class CalendarStorageError(Exception):
    """캘린더 소유 S3 경로로 이미지 복사 또는 정리에 실패한 경우."""


class CalendarDeletionNotFoundError(Exception):
    """삭제 대상 캘린더가 없거나 요청 사용자 소유가 아닌 경우."""


class CalendarDeletionConflictError(Exception):
    """이미지 처리가 끝나지 않아 안전하게 삭제할 수 없는 경우."""

    def __init__(self, current_status: str) -> None:
        self.current_status = current_status
        super().__init__("이미지 처리 중인 캘린더는 삭제할 수 없습니다.")


class CalendarItemLinkNotFoundError(Exception):
    """해제하려는 옷장 아이템이 그 캘린더에 연결돼 있지 않은 경우."""


def entries_for_user(*, user) -> QuerySet[CalendarEntry]:
    """사용자 소유 캘린더와 조회 응답에 필요한 하위 데이터를 반환한다."""

    return CalendarEntry.objects.filter(user=user).select_related(
        "wardrobe_upload_job"
    ).prefetch_related(
        Prefetch(
            "wardrobe_links",
            queryset=CalendarWardrobeItem.objects.select_related(
                "wardrobe_item"
            ).order_by("sort_order", "created_at"),
        ),
    )


def entries_in_period(
    *,
    user,
    start_date: date,
    end_date: date,
) -> QuerySet[CalendarEntry]:
    """시작일과 종료일을 모두 포함하는 사용자 캘린더 목록을 반환한다."""

    return entries_for_user(user=user).filter(
        date__gte=start_date,
        date__lte=end_date,
    )


def processing_statuses_for_user(*, user) -> QuerySet[CalendarEntry]:
    """처리 상태 응답에 필요한 아이템 집계를 포함한 사용자 캘린더 QuerySet."""

    return CalendarEntry.objects.filter(user=user).annotate(
        total_item_count=Count(
            "wardrobe_links",
            filter=Q(
                wardrobe_links__wardrobe_item__job=F("wardrobe_upload_job")
            ),
            distinct=True,
        ),
        extracted_item_count=Count(
            "wardrobe_links",
            filter=Q(
                wardrobe_links__wardrobe_item__job=F("wardrobe_upload_job")
            ),
            distinct=True,
        ),
        failed_item_count=Value(0, output_field=IntegerField()),
    )


@transaction.atomic
def delete_entry(*, user, calendar_id: UUID) -> None:
    """종료된 사용자 캘린더를 삭제하고 커밋 후 S3 prefix를 정리한다.

    callback과 같은 행 잠금을 사용해 완료 처리와 삭제가 동시에 반영되지 않게
    한다. DB 삭제가 성공한 뒤 S3 정리를 수행하므로 저장소 장애가 발생해도
    사용자에게 깨진 DB 참조를 남기지 않는다.
    """

    entry = (
        CalendarEntry.objects.select_for_update()
        .filter(pk=calendar_id, user=user)
        .first()
    )
    if entry is None:
        raise CalendarDeletionNotFoundError
    if entry.status not in {
        CalendarStatus.COMPLETED.value,
        CalendarStatus.FAILED.value,
    }:
        raise CalendarDeletionConflictError(entry.status)

    user_id = entry.user_id
    entry_id = entry.pk
    entry.delete()
    transaction.on_commit(
        lambda: _cleanup_deleted_calendar_s3(
            user_id=user_id,
            calendar_id=entry_id,
        )
    )


@transaction.atomic
def clear_date_for_replacement(*, user, entry_date: date) -> None:
    """해당 날짜의 기존 캘린더를 지워 다른 기록이 그 자리를 쓰게 한다.

    룩북에서 '캘린더에도 기록'을 켰는데 그날 기록이 이미 있을 때, 사용자가
    '바꾸기'를 고른 경로에서만 호출한다. 캘린더는 하루 한 건이라 덮어쓰기가 곧
    기존 기록의 삭제이므로, 확인 없이 이 함수를 부르면 안 된다.

    이미지 처리 중인 캘린더는 삭제와 callback이 경합하므로 거절한다
    (delete_entry와 같은 규칙).
    """

    existing = (
        CalendarEntry.objects.select_for_update()
        .filter(user=user, date=entry_date)
        .first()
    )
    if existing is None:
        return
    if existing.status not in {
        CalendarStatus.COMPLETED.value,
        CalendarStatus.FAILED.value,
    }:
        raise CalendarDeletionConflictError(existing.status)

    user_id = existing.user_id
    entry_id = existing.pk
    existing.delete()
    transaction.on_commit(
        lambda: _cleanup_deleted_calendar_s3(
            user_id=user_id,
            calendar_id=entry_id,
        )
    )


@transaction.atomic
def unlink_wardrobe_item(
    *,
    user,
    calendar_id: UUID,
    wardrobe_item_id: UUID,
) -> CalendarEntry:
    """캘린더와 옷장 아이템의 **연결만** 끊는다.

    지우는 것은 calendar_wardrobe_item 연결 행 하나뿐이다. wardrobe_item 행은
    절대 건드리지 않는다 — 옷장은 사용자의 자산이고, 캘린더는 그것을 참조만 한다.
    캘린더 행도 그대로 남는다. 예전 프론트는 옷 하나를 빼려고 기록을 지우고 다시
    만들었는데, 재등록이 실패하면 기록 자체가 사라졌다 — 이 API가 그 사고를 없앤다.

    delete_entry와 같은 이유로 행 잠금을 걸고, 처리 중(REGISTERED/PROCESSING)인
    캘린더는 거절한다. 추출 callback이 연결을 만드는 중이라 경합하기 때문이다.

    대표 이미지(image_s3_key)가 이 아이템의 캘린더 복사본이었다면(옷장 직접 선택
    등록은 첫 아이템 사진이 대표다) 남은 연결의 첫 사진으로 바꿔 둔다. 그대로 두면
    지워진 사진을 가리키는 죽은 링크가 된다.
    """

    entry = (
        CalendarEntry.objects.select_for_update()
        .filter(pk=calendar_id, user=user)
        .first()
    )
    if entry is None:
        raise CalendarDeletionNotFoundError
    if entry.status not in {
        CalendarStatus.COMPLETED.value,
        CalendarStatus.FAILED.value,
    }:
        raise CalendarDeletionConflictError(entry.status)

    link = entry.wardrobe_links.filter(wardrobe_item_id=wardrobe_item_id).first()
    if link is None:
        raise CalendarItemLinkNotFoundError

    removed_copy_key = str((link.snapshot or {}).get("s3_key") or "")
    link.delete()

    if entry.image_s3_key and entry.image_s3_key == removed_copy_key:
        next_link = (
            entry.wardrobe_links.order_by("sort_order", "created_at").first()
        )
        entry.image_s3_key = (
            str((next_link.snapshot or {}).get("s3_key") or "") if next_link else ""
        )
        entry.save(update_fields=["image_s3_key", "updated_at"])

    # 캘린더 소유 경로에 복사해 둔 아이템 사진만 지운다. 연결마다 자기 복사본을
    # 가지므로(경로에 link id가 들어간다) 다른 연결의 사진을 건드릴 일이 없다.
    # 원본(wardrobe 버킷)은 옷장 소유라 여기서 절대 지우지 않는다.
    if removed_copy_key:
        transaction.on_commit(lambda: _cleanup_s3_objects([removed_copy_key]))

    return entries_for_user(user=user).get(pk=calendar_id)


@transaction.atomic
def link_wardrobe_items(
    *,
    user,
    calendar_id: UUID,
    wardrobe_item_ids: Sequence[UUID],
) -> CalendarEntry:
    """이미 있는 캘린더에 입은 옷을 **더한다**.

    unlink 의 반대편이다. 이 API 가 없던 동안 프론트는 옷 하나를 더하려고 기록을
    지우고 다시 만들었는데, 사진 기록이면 같은 사진을 다시 올려 다시 분석하는 셈이라
    같은 옷이 서로 다른 두 벌로 옷장에 쌓였다. 여기서는 연결 행만 더하므로 사진도
    분석도 다시 하지 않는다.

    이미 걸려 있는 옷은 조용히 건너뛴다 — 두 번 눌러도 같은 결과여야 하고, 프론트가
    '지금 화면의 옷 전부'를 그대로 보내도 되어야 한다.

    delete_entry·unlink 와 같은 이유로 행 잠금을 걸고, 처리 중(REGISTERED/PROCESSING)인
    캘린더는 거절한다. 추출 callback 이 sort_order 를 이어 붙이는 중이라 경합한다.
    """

    entry = (
        CalendarEntry.objects.select_for_update()
        .filter(pk=calendar_id, user=user)
        .first()
    )
    if entry is None:
        raise CalendarDeletionNotFoundError
    if entry.status not in {
        CalendarStatus.COMPLETED.value,
        CalendarStatus.FAILED.value,
    }:
        raise CalendarDeletionConflictError(entry.status)

    ordered_items = _owned_wardrobe_items(
        user=user,
        wardrobe_item_ids=wardrobe_item_ids,
    )
    linked_item_ids = set(entry.wardrobe_links.values_list("wardrobe_item_id", flat=True))
    new_items = [item for item in ordered_items if item.pk not in linked_item_ids]
    if not new_items:
        return entries_for_user(user=user).get(pk=calendar_id)

    existing_slots: set[str] = set()
    for link in entry.wardrobe_links.select_related("wardrobe_item").all():
        if link.wardrobe_item:
            slot = get_slot_key(
                link.wardrobe_item.category_large,
                link.wardrobe_item.category_small,
            )
            if slot:
                existing_slots.add(slot)

    for item in new_items:
        slot = get_slot_key(item.category_large, item.category_small)
        if slot and slot in existing_slots:
            raise DuplicateCategorySlotError(slot)


    links, destination_keys = _prepare_wardrobe_links(
        entry=entry,
        ordered_items=new_items,
        start_sort_order=entry.wardrobe_links.count(),
    )

    stored_keys: list[str] = []
    try:
        _copy_wardrobe_images(
            ordered_items=new_items,
            destination_keys=destination_keys,
            stored_keys=stored_keys,
        )
    except Exception as exc:
        _cleanup_s3_objects(stored_keys)
        raise CalendarStorageError from exc

    try:
        CalendarWardrobeItem.objects.bulk_create(links)
    except Exception:
        _cleanup_s3_objects(stored_keys)
        raise

    # 옷을 다 뺐던 기록은 대표 이미지가 비어 있다(unlink 가 그렇게 남긴다).
    # 다시 채워 두지 않으면 옷은 있는데 표지가 없는 기록이 된다.
    if not entry.image_s3_key:
        entry.image_s3_key = destination_keys[0]
        entry.save(update_fields=["image_s3_key", "updated_at"])

    return entries_for_user(user=user).get(pk=calendar_id)


def _cleanup_deleted_calendar_s3(*, user_id: int | str, calendar_id: UUID) -> None:
    try:
        storage.delete_calendar(user_id, calendar_id)
    except Exception:
        # DB 삭제는 이미 커밋됐다. 사용자 요청을 실패로 되돌리지 않고 운영 로그로
        # 남겨 고아 S3 객체를 별도로 정리할 수 있게 한다.
        logger.exception(
            "삭제된 캘린더 S3 prefix 정리 실패: user_id=%s calendar_id=%s",
            user_id,
            calendar_id,
        )


def _wardrobe_snapshot(
    item: WardrobeItem,
    *,
    calendar_s3_key: str,
) -> dict[str, object]:
    """옷장 데이터 변경과 무관하게 캘린더에 남길 연결 시점 정보."""

    return {
        "id": str(item.pk),
        "s3_key": calendar_s3_key,
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


def _cleanup_s3_objects(keys: Sequence[str]) -> None:
    if not keys:
        return
    try:
        storage.delete_objects(keys)
    except Exception:
        logger.exception("캘린더 S3 객체 정리 실패: object_count=%s", len(keys))


def _cleanup_wardrobe_s3_objects(keys: Sequence[str]) -> None:
    if not keys:
        return
    try:
        wardrobe_storage.delete_objects(keys)
    except Exception:
        logger.exception("옷장 S3 객체 정리 실패: object_count=%s", len(keys))


def validate_item_slot_conflicts(items: Sequence[WardrobeItem]) -> None:
    """단일 슬롯 카테고리(하의, 신발, 원피스/세트, 모자) 중복 여부를 검증한다."""
    seen_slots: set[str] = set()
    for item in items:
        slot = get_slot_key(item.category_large, item.category_small)
        if slot:
            if slot in seen_slots:
                raise DuplicateCategorySlotError(slot)
            seen_slots.add(slot)


def _owned_wardrobe_items(
    *,
    user: User,
    wardrobe_item_ids: Sequence[UUID],
) -> list[WardrobeItem]:
    owned_items = WardrobeItem.objects.filter(
        user=user,
        pk__in=wardrobe_item_ids,
    )
    item_by_id = {item.pk: item for item in owned_items}
    if len(item_by_id) != len(wardrobe_item_ids):
        raise WardrobeItemsNotFoundError
    items = [item_by_id[item_id] for item_id in wardrobe_item_ids]
    validate_item_slot_conflicts(items)
    return items



def _prepare_wardrobe_links(
    *,
    entry: CalendarEntry,
    ordered_items: Sequence[WardrobeItem],
    start_sort_order: int = 0,
) -> tuple[list[CalendarWardrobeItem], list[str]]:
    links: list[CalendarWardrobeItem] = []
    destination_keys: list[str] = []
    for item_offset, item in enumerate(ordered_items):
        sort_order = start_sort_order + item_offset
        link = CalendarWardrobeItem(
            calendar=entry,
            wardrobe_item=item,
            sort_order=sort_order,
        )
        destination_key = storage.selected_item_key(
            entry.user_id,
            entry.pk,
            link.pk,
            item.s3_key,
        )
        link.snapshot = _wardrobe_snapshot(
            item,
            calendar_s3_key=destination_key,
        )
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


def _save_entry_with_links(
    *,
    entry: CalendarEntry,
    links: Sequence[CalendarWardrobeItem],
    stored_keys: Sequence[str],
    upload_job: WardrobeUploadJob | None = None,
    wardrobe_stored_keys: Sequence[str] = (),
) -> None:
    try:
        with transaction.atomic():
            if upload_job is not None:
                upload_job.save(force_insert=True)
            entry.save(force_insert=True)
            CalendarWardrobeItem.objects.bulk_create(links)
    except IntegrityError as exc:
        _cleanup_s3_objects(stored_keys)
        _cleanup_wardrobe_s3_objects(wardrobe_stored_keys)
        cause = getattr(exc, "__cause__", None)
        diag = getattr(cause, "diag", None)
        if getattr(diag, "constraint_name", None) == "uq_calendar_user_date":
            raise CalendarDateConflictError from exc
        raise
    except Exception:
        _cleanup_s3_objects(stored_keys)
        _cleanup_wardrobe_s3_objects(wardrobe_stored_keys)
        raise


def create_from_wardrobe(
    *,
    user: User,
    entry_date: date,
    wardrobe_item_ids: Sequence[UUID],
    schedule: str,
    tpo: list[str],
    hashtags: list[str],
) -> CalendarEntry:
    """사용자 소유 옷장 아이템을 직접 선택해 완료 상태 캘린더를 만든다."""

    if CalendarEntry.objects.filter(user=user, date=entry_date).exists():
        raise CalendarDateConflictError

    ordered_items = _owned_wardrobe_items(
        user=user,
        wardrobe_item_ids=wardrobe_item_ids,
    )

    entry = CalendarEntry(
        user=user,
        date=entry_date,
        source_type=CalendarSourceType.WARDROBE_SELECTED.value,
        image_s3_key="",
        schedule=schedule,
        tpo=tpo,
        hashtags=hashtags,
        status=CalendarStatus.COMPLETED.value,
    )
    links, destination_keys = _prepare_wardrobe_links(
        entry=entry,
        ordered_items=ordered_items,
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
        raise CalendarStorageError from exc

    entry.image_s3_key = destination_keys[0]
    _save_entry_with_links(entry=entry, links=links, stored_keys=stored_keys)

    return entries_for_user(user=user).get(pk=entry.pk)


def create_from_photo(
    *,
    user: User,
    image: UploadedFile,
    entry_date: date,
    wardrobe_item_ids: Sequence[UUID],
    schedule: str,
    tpo: list[str],
    hashtags: list[str],
) -> CalendarEntry:
    """사용자 사진을 S3에 먼저 저장하고 조회 가능한 캘린더를 생성한다."""

    if CalendarEntry.objects.filter(user=user, date=entry_date).exists():
        raise CalendarDateConflictError

    ordered_items = _owned_wardrobe_items(
        user=user,
        wardrobe_item_ids=wardrobe_item_ids,
    )
    upload_job = WardrobeUploadJob(user=user)
    entry = CalendarEntry(
        user=user,
        wardrobe_upload_job=upload_job,
        date=entry_date,
        source_type=CalendarSourceType.PHOTO_UPLOAD.value,
        image_s3_key="",
        schedule=schedule,
        tpo=tpo,
        hashtags=hashtags,
        skipped_categories=skipped_categories_for(ordered_items),
        status=CalendarStatus.REGISTERED.value,
    )
    original_s3_key = storage.original_key(
        user.pk,
        entry.pk,
        image.name,
        image.content_type,
    )
    entry.image_s3_key = original_s3_key
    wardrobe_original_s3_key = wardrobe_storage.original_key(
        user.pk,
        upload_job.pk,
        image.name,
    )
    upload_job.source_s3_key = wardrobe_original_s3_key
    links, destination_keys = _prepare_wardrobe_links(
        entry=entry,
        ordered_items=ordered_items,
    )

    stored_keys: list[str] = []
    wardrobe_stored_keys: list[str] = []
    try:
        storage.upload_fileobj(image, original_s3_key, image.content_type)
        stored_keys.append(original_s3_key)
        storage.copy_calendar_original_to_wardrobe(
            original_s3_key,
            wardrobe_original_s3_key,
        )
        wardrobe_stored_keys.append(wardrobe_original_s3_key)
        _copy_wardrobe_images(
            ordered_items=ordered_items,
            destination_keys=destination_keys,
            stored_keys=stored_keys,
        )
    except Exception as exc:
        _cleanup_s3_objects(stored_keys)
        _cleanup_wardrobe_s3_objects(wardrobe_stored_keys)
        raise CalendarStorageError from exc

    _save_entry_with_links(
        entry=entry,
        links=links,
        stored_keys=stored_keys,
        upload_job=upload_job,
        wardrobe_stored_keys=wardrobe_stored_keys,
    )

    return entries_for_user(user=user).get(pk=entry.pk)


def create_photo_entry_for_job(
    *,
    user: User,
    entry_date: date,
    upload_job: WardrobeUploadJob,
    image_s3_key: str,
    ordered_items: Sequence[WardrobeItem],
    schedule: str,
    tpo: list[str],
    hashtags: list[str],
    entry_id: UUID | None = None,
) -> CalendarEntry:
    """이미 만들어진 옷장 job을 공유하는 사진 캘린더를 만든다.

    룩북이 '캘린더에도 기록'을 켰을 때 쓴다. 캘린더가 자기 job을 따로 만들면
    같은 사진을 GPU가 두 번 처리하게 되므로 job은 룩북과 공유하고, callback은
    apply_wardrobe_job_success가 job으로 캘린더를 찾아 그대로 반영한다.

    호출자 책임:
    - upload_job은 이미 저장돼 있어야 한다 (FK 참조).
    - image_s3_key는 **캘린더 버킷**에 이미 복사해 둔 원본 키여야 한다.
      캘린더는 자기 버킷만 presign한다.
    - ordered_items는 소유권 검증이 끝난 WardrobeItem이어야 한다.

    entry_id: 원본을 복사할 캘린더 prefix를 정하려면 호출자가 캘린더 UUID를
        먼저 알아야 한다. 여기서 새로 뽑으면 image_s3_key가 가리키는 prefix와
        어긋나 캘린더를 지워도 원본이 S3에 남는다(delete_calendar는 정확한
        prefix만 지운다).
    """

    if CalendarEntry.objects.filter(user=user, date=entry_date).exists():
        raise CalendarDateConflictError

    entry = CalendarEntry(
        **({"id": entry_id} if entry_id is not None else {}),
        user=user,
        wardrobe_upload_job=upload_job,
        date=entry_date,
        source_type=CalendarSourceType.PHOTO_UPLOAD.value,
        image_s3_key=image_s3_key,
        schedule=schedule,
        tpo=tpo,
        hashtags=hashtags,
        # 큐에 싣는 것은 룩북 쪽(같은 ordered_items 로 계산한 값)이지만, callback
        # 안전망은 캘린더도 스스로 판단해야 해서 같은 목록을 여기에도 남긴다.
        skipped_categories=skipped_categories_for(ordered_items),
        status=CalendarStatus.REGISTERED.value,
    )
    links, destination_keys = _prepare_wardrobe_links(
        entry=entry,
        ordered_items=ordered_items,
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
        raise CalendarStorageError from exc

    _save_entry_with_links(entry=entry, links=links, stored_keys=stored_keys)

    return entry


def mark_queue_enqueue_failed(entry: CalendarEntry) -> None:
    """Redis 적재 실패를 PostgreSQL의 최종 실패 상태로 기록한다."""

    completed_at = timezone.now()
    with transaction.atomic():
        entry.status = CalendarStatus.FAILED.value
        entry.processing_error_code = (
            CalendarProcessingErrorCode.QUEUE_ENQUEUE_FAILED.value
        )
        entry.processing_error_message = "옷장 이미지 처리 큐 적재 실패"
        entry.processing_completed_at = completed_at
        entry.save(
            update_fields=[
                "status",
                "processing_error_code",
                "processing_error_message",
                "processing_completed_at",
                "updated_at",
            ]
        )
        if entry.wardrobe_upload_job_id:
            WardrobeUploadJob.objects.filter(pk=entry.wardrobe_upload_job_id).update(
                status=WardrobeUploadJob.Status.FAILED,
                error_message="처리 큐 적재 실패",
                finished_at=completed_at,
            )


def is_calendar_job(*, job: WardrobeUploadJob) -> bool:
    """이 사진 처리 job 이 캘린더 기록에 걸려 있는가.

    걸려 있으면 뽑힌 옷을 옷장에 바로 들인다. 캘린더는 '그날 입은 옷'의 기록이라
    사용자가 가진 옷이 확실하고, 캘린더 상세에는 옷장에 넣는 버튼이 없어 막으면
    그 옷이 어디서도 꺼낼 수 없는 채로 남는다. 룩북과 job 을 공유하는 경우
    (룩북에서 '캘린더에도 기록')에도 같다. 옷장 쪽이 캘린더 모델을 직접 import
    하지 않도록 판별을 여기 둔다 (룩북의 is_lookbook_job 과 같은 이유).
    """

    return CalendarEntry.objects.filter(wardrobe_upload_job=job).exists()


def apply_wardrobe_job_success(
    *,
    job: WardrobeUploadJob,
    created_items: Sequence[WardrobeItem],
) -> None:
    """기존 옷장 callback 결과를 해당 사진 캘린더에 자동 연결한다."""

    entry = (
        CalendarEntry.objects.select_for_update()
        .filter(wardrobe_upload_job=job)
        .first()
    )
    if entry is None:
        return

    existing_item_ids = set(
        entry.wardrobe_links.values_list("wardrobe_item_id", flat=True)
    )
    skipped = set(entry.skipped_categories or [])
    existing_slots: set[str] = set()
    for link in entry.wardrobe_links.select_related("wardrobe_item").all():
        if link.wardrobe_item:
            slot = get_slot_key(
                link.wardrobe_item.category_large,
                link.wardrobe_item.category_small,
            )
            if slot:
                existing_slots.add(slot)

    # 제외는 워커가 열거 직후에 이미 적용한다. 여기 한 겹 더 두는 것은 구버전
    # 워커(exclude_categories 미지원)가 붙어도 입은 옷으로 이미 지정한 부위가
    # 캘린더에 두 번 걸리지 않게 하기 위한 안전망이다 (룩북과 같은 규칙).
    # 옷장 아이템 자체는 지우지 않는다 — 이미 만들어진 사용자 데이터다.
    new_items: list[WardrobeItem] = []
    for item in created_items:
        if item.pk in existing_item_ids or item.category_large in skipped:
            continue
        slot = get_slot_key(item.category_large, item.category_small)
        if slot:
            if slot in existing_slots:
                continue
            existing_slots.add(slot)
        new_items.append(item)

    start_sort_order = entry.wardrobe_links.count()
    links, destination_keys = _prepare_wardrobe_links(
        entry=entry,
        ordered_items=new_items,
        start_sort_order=start_sort_order,
    )

    stored_keys: list[str] = []
    try:
        _copy_wardrobe_images(
            ordered_items=new_items,
            destination_keys=destination_keys,
            stored_keys=stored_keys,
        )
        CalendarWardrobeItem.objects.bulk_create(links)
    except Exception:
        _cleanup_s3_objects(stored_keys)
        raise

    entry.status = CalendarStatus.COMPLETED.value
    entry.processing_error_code = ""
    entry.processing_error_message = ""
    entry.processing_completed_at = job.finished_at or timezone.now()
    entry.callback_applied_at = timezone.now()
    entry.save(
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
    """기존 옷장 callback 실패 상태를 연결된 캘린더에 반영한다."""

    entry = (
        CalendarEntry.objects.select_for_update()
        .filter(wardrobe_upload_job=job)
        .first()
    )
    if entry is None:
        return

    entry.status = CalendarStatus.FAILED.value
    entry.processing_error_code = (
        CalendarProcessingErrorCode.IMAGE_PROCESSING_FAILED.value
    )
    entry.processing_error_message = job.error_message
    entry.processing_completed_at = job.finished_at or timezone.now()
    entry.callback_applied_at = timezone.now()
    entry.save(
        update_fields=[
            "status",
            "processing_error_code",
            "processing_error_message",
            "processing_completed_at",
            "callback_applied_at",
            "updated_at",
        ]
    )
