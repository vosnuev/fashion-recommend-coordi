"""단일 슬롯 카테고리(하의, 신발, 원피스/세트, 모자) 중복 제한 테스트."""

from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.style_calendar.contracts import CalendarSourceType, CalendarStatus
from apps.style_calendar.models import CalendarEntry
from apps.style_calendar.services import calendar_service
from apps.wardrobe.models import WardrobeItem, WardrobeUploadJob

User = get_user_model()


class CalendarSingleSlotTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="slot_user@example.com",
            password="password123!",
        )
        self.client.force_authenticate(user=self.user)

        self.bottom1 = WardrobeItem.objects.create(
            user=self.user,
            s3_key="bottom1.jpg",
            category_large="하의",
            category_small="데님 팬츠",
            item_name="청바지1",
        )
        self.bottom2 = WardrobeItem.objects.create(
            user=self.user,
            s3_key="bottom2.jpg",
            category_large="하의",
            category_small="슬랙스",
            item_name="슬랙스1",
        )
        self.top1 = WardrobeItem.objects.create(
            user=self.user,
            s3_key="top1.jpg",
            category_large="상의",
            category_small="티셔츠",
            item_name="반팔티",
        )
        self.top2 = WardrobeItem.objects.create(
            user=self.user,
            s3_key="top2.jpg",
            category_large="상의",
            category_small="셔츠/블라우스",
            item_name="셔츠",
        )
        self.hat1 = WardrobeItem.objects.create(
            user=self.user,
            s3_key="hat1.jpg",
            category_large="액세서리",
            category_small="모자",
            item_name="볼캡",
        )
        self.hat2 = WardrobeItem.objects.create(
            user=self.user,
            s3_key="hat2.jpg",
            category_large="액세서리",
            category_small="모자",
            item_name="비니",
        )

    @patch("apps.style_calendar.services.calendar_service.storage.copy_wardrobe_item")
    def test_create_from_wardrobe_duplicate_bottom_fails(self, mock_copy):
        """하의 2개 동시 선택 시 DuplicateCategorySlotError 에러가 발생한다."""
        with self.assertRaises(calendar_service.DuplicateCategorySlotError):
            calendar_service.create_from_wardrobe(
                user=self.user,
                entry_date=date(2026, 8, 18),
                wardrobe_item_ids=[self.bottom1.pk, self.bottom2.pk],
                schedule="",
                tpo=[],
                hashtags=[],
            )

    @patch("apps.style_calendar.services.calendar_service.storage.copy_wardrobe_item")
    def test_create_from_wardrobe_duplicate_hat_fails(self, mock_copy):
        """소분류 모자 2개 동시 선택 시 DuplicateCategorySlotError 에러가 발생한다."""
        with self.assertRaises(calendar_service.DuplicateCategorySlotError):
            calendar_service.create_from_wardrobe(
                user=self.user,
                entry_date=date(2026, 8, 18),
                wardrobe_item_ids=[self.hat1.pk, self.hat2.pk],
                schedule="",
                tpo=[],
                hashtags=[],
            )

    @patch("apps.style_calendar.services.calendar_service.storage.copy_wardrobe_item")
    def test_create_from_wardrobe_multiple_tops_allowed(self, mock_copy):
        """상의 2개 선택(레이어드)은 허용된다."""
        entry = calendar_service.create_from_wardrobe(
            user=self.user,
            entry_date=date(2026, 8, 18),
            wardrobe_item_ids=[self.top1.pk, self.top2.pk],
            schedule="",
            tpo=[],
            hashtags=[],
        )
        self.assertEqual(entry.wardrobe_links.count(), 2)

    @patch("apps.style_calendar.services.calendar_service.storage.copy_wardrobe_item")
    def test_api_wardrobe_create_duplicate_bottom_returns_400(self, mock_copy):
        """API 호출 시 하의 중복 선택 시 400 Bad Request를 반환한다."""
        response = self.client.post(
            "/api/v1/calendars/wardrobe/",
            data={
                "date": "2026-08-18",
                "wardrobe_item_ids": [str(self.bottom1.pk), str(self.bottom2.pk)],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("wardrobe_item_ids", response.data)

    @patch("apps.style_calendar.services.calendar_service.storage.copy_wardrobe_item")
    def test_apply_wardrobe_job_success_filters_duplicate_slots(self, mock_copy):
        """AI 콜백 처리 시 이미 착장에 존재하는 단일 슬롯 아이템은 추가 제외된다."""
        job = WardrobeUploadJob.objects.create(user=self.user)
        entry = CalendarEntry.objects.create(
            user=self.user,
            wardrobe_upload_job=job,
            date=date(2026, 8, 18),
            source_type=CalendarSourceType.PHOTO_UPLOAD.value,
            image_s3_key="photo.jpg",
            status=CalendarStatus.PROCESSING.value,
        )
        # 이미 하의 1개가 수동 지정됨
        calendar_service.CalendarWardrobeItem.objects.create(
            calendar=entry,
            wardrobe_item=self.bottom1,
            sort_order=0,
            snapshot={"category_large": "하의", "category_small": "데님 팬츠"},
        )

        # AI가 하의2, 상의1을 새로 추출함
        calendar_service.apply_wardrobe_job_success(
            job=job,
            created_items=[self.bottom2, self.top1],
        )

        entry.refresh_from_db()
        linked_items = [link.wardrobe_item for link in entry.wardrobe_links.all()]
        # 하의2는 필터링되고 수동 하의1 + AI 상의1 만 남아 2개여야 함
        self.assertEqual(len(linked_items), 2)
        self.assertIn(self.bottom1, linked_items)
        self.assertIn(self.top1, linked_items)
        self.assertNotIn(self.bottom2, linked_items)
