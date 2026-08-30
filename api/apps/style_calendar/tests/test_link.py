"""입은 옷 연결 추가(link) API — 기록을 지우지 않고 옷만 더한다.

이 파일이 지키는 계약: 캘린더에 옷을 더해도 **기록 id 와 사진은 그대로다.**
이 API 가 없던 동안 프론트는 옷 하나를 더하려고 기록을 지우고 다시 만들었는데,
사진 기록에서는 그것이 곧 같은 사진의 재분석이라 같은 옷이 서로 다른 두 벌로
옷장에 쌓였다.
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.style_calendar.contracts import CalendarSourceType, CalendarStatus
from apps.style_calendar.models import CalendarEntry, CalendarWardrobeItem
from apps.users.models import User
from apps.wardrobe.models import WardrobeItem


class CalendarItemLinkApiTests(TestCase):
    def setUp(self) -> None:
        copy_patcher = patch(
            "apps.style_calendar.services.calendar_service.storage.copy_wardrobe_item"
        )
        self.mock_copy = copy_patcher.start()
        self.addCleanup(copy_patcher.stop)
        delete_patcher = patch(
            "apps.style_calendar.services.calendar_service.storage.delete_objects"
        )
        self.mock_delete_objects = delete_patcher.start()
        self.addCleanup(delete_patcher.stop)
        presigned_patcher = patch(
            "apps.style_calendar.serializers.storage.presigned_get",
            return_value="https://signed.example/get",
        )
        presigned_patcher.start()
        self.addCleanup(presigned_patcher.stop)

        self.user = User.objects.create(username="link-user")
        self.other_user = User.objects.create(username="link-other")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def create_entry(
        self,
        *,
        status: str = CalendarStatus.COMPLETED.value,
        image_s3_key: str = "calendar/u/original.jpg",
        user=None,
    ) -> CalendarEntry:
        return CalendarEntry.objects.create(
            user=user or self.user,
            date=date(2026, 8, 11),
            source_type=CalendarSourceType.PHOTO_UPLOAD.value,
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

    @staticmethod
    def url_for(entry: CalendarEntry) -> str:
        return reverse(
            "style_calendar:calendar-item-link",
            kwargs={"calendar_id": entry.pk},
        )

    def post(self, entry: CalendarEntry, items: list[WardrobeItem]):
        return self.client.post(
            self.url_for(entry),
            {"wardrobe_item_ids": [str(item.pk) for item in items]},
            format="json",
        )

    def test_link_appends_items_without_touching_entry(self) -> None:
        """기록 id·사진은 그대로고 연결만 이어 붙는다."""

        entry = self.create_entry()
        first = self.create_item("이미 있는 옷")
        CalendarWardrobeItem.objects.create(
            calendar=entry,
            wardrobe_item=first,
            sort_order=0,
            snapshot={"s3_key": "calendar/copy/first.png"},
        )
        added = self.create_item("더하는 옷")

        response = self.post(entry, [added])

        self.assertEqual(response.status_code, 200)
        entry.refresh_from_db()
        self.assertEqual(entry.image_s3_key, "calendar/u/original.jpg")
        links = list(entry.wardrobe_links.order_by("sort_order"))
        self.assertEqual(
            [link.wardrobe_item_id for link in links],
            [first.pk, added.pk],
        )
        self.assertEqual([link.sort_order for link in links], [0, 1])
        # 더한 옷의 사진만 캘린더 소유 경로로 복사한다.
        self.assertEqual(self.mock_copy.call_count, 1)
        self.assertIn(
            f"calendar/{self.user.pk}/{entry.pk}/selected/",
            links[1].snapshot["s3_key"],
        )
        # 응답은 갱신된 기록 전체 — 프론트가 다시 조회하지 않아도 된다.
        self.assertEqual(
            [row["wardrobe_item_id"] for row in response.data["wardrobe_items"]],
            [str(first.pk), str(added.pk)],
        )

    def test_link_is_idempotent_for_already_linked_items(self) -> None:
        """같은 옷을 다시 보내도 연결이 늘지 않는다 — 프론트가 화면의 옷 전부를 보내도 된다."""

        entry = self.create_entry()
        item = self.create_item()

        first = self.post(entry, [item])
        second = self.post(entry, [item])

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(entry.wardrobe_links.count(), 1)
        self.assertEqual(self.mock_copy.call_count, 1)

    def test_link_restores_representative_image_when_entry_had_none(self) -> None:
        """옷을 다 뺐던 기록(대표 이미지 빈 값)은 더한 첫 옷이 표지가 된다."""

        entry = self.create_entry(image_s3_key="")
        item = self.create_item()

        response = self.post(entry, [item])

        self.assertEqual(response.status_code, 200)
        entry.refresh_from_db()
        self.assertEqual(
            entry.image_s3_key,
            entry.wardrobe_links.get().snapshot["s3_key"],
        )

    def test_link_rejects_other_users_item_and_entry(self) -> None:
        entry = self.create_entry()
        others_item = self.create_item(user=self.other_user)

        response = self.post(entry, [others_item])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(entry.wardrobe_links.count(), 0)
        self.mock_copy.assert_not_called()

        others_entry = self.create_entry(user=self.other_user)
        response = self.post(others_entry, [self.create_item()])
        self.assertEqual(response.status_code, 404)

    def test_link_is_blocked_while_photo_is_processing(self) -> None:
        """추출 callback 이 연결을 이어 붙이는 중이라 경합한다 — unlink 와 같은 규칙."""

        entry = self.create_entry(status=CalendarStatus.REGISTERED.value)

        response = self.post(entry, [self.create_item()])

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["status"], CalendarStatus.REGISTERED.value)
        self.assertEqual(entry.wardrobe_links.count(), 0)

    def test_link_rejects_empty_or_duplicate_input(self) -> None:
        entry = self.create_entry()
        item = self.create_item()

        empty = self.client.post(
            self.url_for(entry), {"wardrobe_item_ids": []}, format="json"
        )
        duplicated = self.client.post(
            self.url_for(entry),
            {"wardrobe_item_ids": [str(item.pk), str(item.pk)]},
            format="json",
        )

        self.assertEqual(empty.status_code, 400)
        self.assertEqual(duplicated.status_code, 400)
        self.assertEqual(entry.wardrobe_links.count(), 0)

    def test_storage_failure_leaves_no_half_written_links(self) -> None:
        entry = self.create_entry()
        self.mock_copy.side_effect = RuntimeError("s3 down")

        response = self.post(entry, [self.create_item(), self.create_item("둘째")])

        self.assertEqual(response.status_code, 503)
        self.assertEqual(entry.wardrobe_links.count(), 0)
