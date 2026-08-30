"""룩북 API 테스트 공통 픽스처.

S3·Redis는 전부 mock한다. 검증 대상은 "어떤 키에 무엇을 올리고, 실패하면
무엇을 되돌리는가"이지 boto3 자체가 아니다.
"""

from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.users.models import User
from apps.wardrobe.models import WardrobeItem


def make_image_file(
    name: str = "look.jpg",
    *,
    content_type: str = "image/jpeg",
    image_format: str = "JPEG",
    extra_size: int = 0,
) -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", (10, 10), "white").save(buffer, format=image_format)
    content = buffer.getvalue() + (b"\0" * extra_size)
    return SimpleUploadedFile(name, content, content_type=content_type)


class LookbookApiTestCase(TestCase):
    """룩북 API 테스트가 공유하는 mock·사용자·옷장 아이템."""

    def setUp(self) -> None:
        service = "apps.lookbook.services.lookbook_service"
        calendar_service = "apps.style_calendar.services.calendar_service"
        patchers = {
            "upload_fileobj": patch(f"{service}.storage.upload_fileobj"),
            "copy_wardrobe_item": patch(f"{service}.storage.copy_wardrobe_item"),
            "copy_original_to_wardrobe": patch(
                f"{service}.storage.copy_original_to_wardrobe"
            ),
            "copy_original_to_calendar": patch(
                f"{service}.storage.copy_original_to_calendar"
            ),
            "delete_objects": patch(f"{service}.storage.delete_objects"),
            "delete_lookbook": patch(f"{service}.storage.delete_lookbook"),
            "wardrobe_delete_objects": patch(
                f"{service}.wardrobe_storage.delete_objects"
            ),
            "calendar_copy_wardrobe_item": patch(
                f"{calendar_service}.storage.copy_wardrobe_item"
            ),
            "calendar_delete_calendar": patch(
                f"{calendar_service}.storage.delete_calendar"
            ),
            "calendar_delete_objects": patch(
                f"{service}.calendar_storage.delete_objects"
            ),
            # 시리얼라이저는 버킷을 함께 넘긴다 — 오늘의 룩에서 담은 골든 코디는
            # 룩북 버킷이 아니라 골든셋 버킷을 가리키기 때문이다(빈 값 = 룩북 버킷).
            "presigned_get": patch(
                "apps.lookbook.serializers.storage.presigned_get_in",
                side_effect=(
                    lambda bucket, key: f"https://lookbook.example/{key}" if key else ""
                ),
            ),
            "enqueue": patch("apps.lookbook.views.wardrobe_jobs.enqueue"),
            "logger_exception": patch("apps.lookbook.views.logger.exception"),
        }
        self.mocks = {}
        for name, patcher in patchers.items():
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

        self.client = APIClient()
        self.user = User.objects.create(username="lookbook-user")
        self.other_user = User.objects.create(username="lookbook-other")
        self.client.force_authenticate(self.user)

        self.top = WardrobeItem.objects.create(
            user=self.user,
            job=None,
            s3_key="wardrobe/user/top.png",
            item_name="흰색 반팔",
            category_large="상의",
            category_small="티셔츠",
            confirmed=True,
            added_to_closet_at=timezone.now(),
        )
        self.bottom = WardrobeItem.objects.create(
            user=self.user,
            job=None,
            s3_key="wardrobe/user/bottom.png",
            item_name="검정 바지",
            category_large="하의",
            category_small="슬랙스",
            confirmed=True,
            added_to_closet_at=timezone.now(),
        )
        self.other_item = WardrobeItem.objects.create(
            user=self.other_user,
            job=None,
            s3_key="wardrobe/other/item.png",
            item_name="다른 사용자 옷",
            category_large="상의",
            confirmed=True,
            added_to_closet_at=timezone.now(),
        )
