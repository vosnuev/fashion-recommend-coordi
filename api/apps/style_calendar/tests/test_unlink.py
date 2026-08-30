"""입은 옷 연결 해제(unlink) API — 지워지는 건 연결 행 하나뿐이어야 한다.

이 파일이 지키는 계약: 캘린더에서 옷을 빼도 **wardrobe_item 행과 캘린더 기록은
그대로 남는다.** 예전 프론트는 옷 하나를 빼려고 기록을 지우고 다시 만들었고,
재등록이 실패하면 기록이 통째로 사라졌다.
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.style_calendar.contracts import CalendarSourceType, CalendarStatus
from apps.style_calendar.models import (
    CalendarEntry,
    CalendarWardrobeItem,
)
from apps.users.models import User
from apps.wardrobe.models import WardrobeItem


class CalendarItemUnlinkApiTests(TestCase):
    def setUp(self) -> None:
        # 연결 해제는 캘린더 소유 복사본만 지운다 — S3 는 흉내로 막는다.
        self.delete_objects_patcher = patch(
            "apps.style_calendar.services.calendar_service.storage.delete_objects"
        )
        self.mock_delete_objects = self.delete_objects_patcher.start()
        self.addCleanup(self.delete_objects_patcher.stop)
        # 응답 직렬화가 이미지 URL 을 서명한다 — 테스트에는 버킷이 없다.
        presigned_patcher = patch(
            "apps.style_calendar.serializers.storage.presigned_get",
            return_value="https://signed.example/get",
        )
        presigned_patcher.start()
        self.addCleanup(presigned_patcher.stop)

        self.user = User.objects.create(username="unlink-user")
        self.other_user = User.objects.create(username="unlink-other")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def create_entry(
        self,
        *,
        status: str = CalendarStatus.COMPLETED.value,
        source_type: str = CalendarSourceType.PHOTO_UPLOAD.value,
        image_s3_key: str = "calendar/u/original.jpg",
        entry_date: date = date(2026, 8, 10),
        user=None,
    ) -> CalendarEntry:
        return CalendarEntry.objects.create(
            user=user or self.user,
            date=entry_date,
            source_type=source_type,
            image_s3_key=image_s3_key,
            status=status,
        )

    def create_item(self, name: str = "옷장 아이템", user=None) -> WardrobeItem:
        return WardrobeItem.objects.create(
            user=user or self.user,
            job=None,
            s3_key=f"wardrobe/{uuid.uuid4()}.png",
            item_name=name,
            category_large="상의",
        )

    def link(
        self,
        entry: CalendarEntry,
        item: WardrobeItem,
        *,
        sort_order: int = 0,
        copy_key: str | None = None,
    ) -> CalendarWardrobeItem:
        return CalendarWardrobeItem.objects.create(
            calendar=entry,
            wardrobe_item=item,
            sort_order=sort_order,
            snapshot={"s3_key": copy_key or f"calendar/copy/{uuid.uuid4()}.png"},
        )

    @staticmethod
    def url_for(entry: CalendarEntry, item: WardrobeItem) -> str:
        return reverse(
            "style_calendar:calendar-item-unlink",
            kwargs={"calendar_id": entry.pk, "wardrobe_item_id": item.pk},
        )

    def test_unlink_removes_only_the_link_row(self) -> None:
        """연결 행만 사라진다. 옷장 아이템도, 캘린더 기록도 그대로다."""
        entry = self.create_entry()
        keep = self.create_item("남는 옷")
        remove = self.create_item("빼는 옷")
        self.link(entry, keep, sort_order=0)
        removed_link = self.link(entry, remove, sort_order=1)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(self.url_for(entry, remove))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            CalendarWardrobeItem.objects.filter(pk=removed_link.pk).exists()
        )
        # 계약의 핵심 — 옷장 아이템 행은 절대 지워지면 안 된다.
        self.assertTrue(WardrobeItem.objects.filter(pk=remove.pk).exists())
        self.assertTrue(CalendarEntry.objects.filter(pk=entry.pk).exists())
        # 응답은 갱신된 기록 전체 — 남은 옷만 담겨 있다.
        linked_ids = [
            row["wardrobe_item_id"] for row in response.data["wardrobe_items"]
        ]
        self.assertEqual(linked_ids, [str(keep.pk)])

    def test_unlink_cleans_up_calendar_copy_not_wardrobe_original(self) -> None:
        """지우는 S3 객체는 캘린더 소유 복사본뿐이다 — 옷장 원본은 남는다."""
        entry = self.create_entry()
        item = self.create_item()
        self.link(entry, item, copy_key="calendar/copy/target.png")

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(self.url_for(entry, item))

        self.assertEqual(response.status_code, 200)
        self.mock_delete_objects.assert_called_once_with(["calendar/copy/target.png"])

    def test_representative_image_moves_to_the_next_item(self) -> None:
        """옷장 직접 선택 기록은 첫 아이템 사진이 대표다 — 그 옷을 빼면 다음 옷으로."""
        entry = self.create_entry(
            source_type=CalendarSourceType.WARDROBE_SELECTED.value,
            image_s3_key="calendar/copy/first.png",
        )
        first = self.create_item("첫 옷")
        second = self.create_item("둘째 옷")
        self.link(entry, first, sort_order=0, copy_key="calendar/copy/first.png")
        self.link(entry, second, sort_order=1, copy_key="calendar/copy/second.png")

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(self.url_for(entry, first))

        self.assertEqual(response.status_code, 200)
        entry.refresh_from_db()
        self.assertEqual(entry.image_s3_key, "calendar/copy/second.png")

    def test_unlinking_the_last_item_clears_the_representative_image(self) -> None:
        entry = self.create_entry(
            source_type=CalendarSourceType.WARDROBE_SELECTED.value,
            image_s3_key="calendar/copy/only.png",
        )
        only = self.create_item()
        self.link(entry, only, copy_key="calendar/copy/only.png")

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(self.url_for(entry, only))

        self.assertEqual(response.status_code, 200)
        entry.refresh_from_db()
        self.assertEqual(entry.image_s3_key, "")
        self.assertTrue(WardrobeItem.objects.filter(pk=only.pk).exists())

    def test_photo_entry_keeps_its_original_photo(self) -> None:
        """사진 등록 기록의 대표는 원본 사진 — 옷을 빼도 바뀌면 안 된다."""
        entry = self.create_entry(image_s3_key="calendar/original/photo.jpg")
        item = self.create_item()
        self.link(entry, item)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.delete(self.url_for(entry, item))

        entry.refresh_from_db()
        self.assertEqual(entry.image_s3_key, "calendar/original/photo.jpg")

    def test_processing_calendar_is_rejected(self) -> None:
        """추출 callback 이 연결을 만드는 중이라 경합한다 — 409 로 거절."""
        for entry_status in (
            CalendarStatus.REGISTERED.value,
            CalendarStatus.PROCESSING.value,
        ):
            with self.subTest(status=entry_status):
                entry = self.create_entry(
                    status=entry_status,
                    entry_date=date(2026, 8, int(entry_status == "PROCESSING") + 11),
                )
                item = self.create_item()
                link = self.link(entry, item)

                response = self.client.delete(self.url_for(entry, item))

                self.assertEqual(response.status_code, 409)
                self.assertTrue(
                    CalendarWardrobeItem.objects.filter(pk=link.pk).exists()
                )

    def test_unlinked_item_returns_404(self) -> None:
        """그 캘린더에 연결돼 있지 않은 옷 — 404, 아무것도 안 지운다."""
        entry = self.create_entry()
        stranger_item = self.create_item("연결 안 된 옷")

        response = self.client.delete(self.url_for(entry, stranger_item))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(WardrobeItem.objects.filter(pk=stranger_item.pk).exists())

    def test_requires_authentication_and_ownership(self) -> None:
        other_entry = self.create_entry(user=self.other_user)
        other_item = self.create_item(user=self.other_user)
        link = self.link(other_entry, other_item)

        unauthenticated = APIClient().delete(self.url_for(other_entry, other_item))
        other_response = self.client.delete(self.url_for(other_entry, other_item))

        self.assertEqual(unauthenticated.status_code, 401)
        # 남의 캘린더는 존재 자체를 알려주지 않는다.
        self.assertEqual(other_response.status_code, 404)
        self.assertTrue(CalendarWardrobeItem.objects.filter(pk=link.pk).exists())
