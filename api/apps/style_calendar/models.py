"""사용자 착장 캘린더 도메인 모델.

CalendarWardrobeItem은 CalendarEntry와 WardrobeItem 사이의 N:N 관계를
표현하는 명시적 연결 모델이다. 사진 업로드 캘린더는 기존 옷장 업로드 job을
통해 생성된 WardrobeItem을 이 관계에 자동 연결한다.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.style_calendar.contracts import CalendarSourceType, CalendarStatus


class CalendarEntry(models.Model):
    """사용자별 하루 한 건의 착장 캘린더."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="캘린더 UUID (외부 노출 식별자)",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="calendar_entries",
        db_comment="캘린더 소유 사용자 FK (users.id)",
    )
    wardrobe_upload_job = models.OneToOneField(
        "wardrobe.WardrobeUploadJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="calendar_entry",
        db_comment=(
            "사진 업로드 처리에 재사용한 옷장 job FK "
            "(wardrobe_upload_job.id, 직접 선택 등록은 NULL)"
        ),
    )
    wardrobe_items = models.ManyToManyField(
        "wardrobe.WardrobeItem",
        through="CalendarWardrobeItem",
        through_fields=("calendar", "wardrobe_item"),
        related_name="calendar_entries",
        blank=True,
    )
    date = models.DateField(db_comment="착장 날짜 (사용자별 하루 한 건)")
    source_type = models.CharField(
        max_length=24,
        choices=[
            (CalendarSourceType.PHOTO_UPLOAD.value, "사진 업로드"),
            (CalendarSourceType.WARDROBE_SELECTED.value, "옷장 직접 선택"),
        ],
        db_comment="캘린더 등록 경로 (PHOTO_UPLOAD/WARDROBE_SELECTED)",
    )
    image_s3_key = models.CharField(
        max_length=512,
        db_comment="캘린더 대표 이미지 S3 키 (캘린더 소유 경로)",
    )
    schedule = models.TextField(
        blank=True,
        default="",
        db_comment="사용자가 입력한 일정 설명",
    )
    tpo = models.JSONField(
        default=list,
        blank=True,
        db_comment="착장 상황(TPO) 코드 또는 문자열 목록 JSON",
    )
    weather_snapshot = models.JSONField(
        null=True,
        blank=True,
        db_comment="등록 당시 날씨 스냅샷 JSON (위치 정보 없으면 NULL)",
    )
    hashtags = models.JSONField(
        default=list,
        blank=True,
        db_comment="캘린더 해시태그 문자열 목록 JSON",
    )
    skipped_categories = models.JSONField(
        default=list,
        blank=True,
        db_comment=(
            "입은 옷 지정과 겹쳐 사진 등록에서 제외한 옷장 대분류 목록 JSON "
            "(예: 상의/하의)"
        ),
    )
    status = models.CharField(
        max_length=16,
        choices=[
            (CalendarStatus.REGISTERED.value, "등록"),
            (CalendarStatus.PROCESSING.value, "처리중"),
            (CalendarStatus.COMPLETED.value, "완료"),
            (CalendarStatus.FAILED.value, "실패"),
        ],
        default=CalendarStatus.REGISTERED.value,
        db_comment="이미지 처리 상태 (REGISTERED/PROCESSING/COMPLETED/FAILED)",
    )
    processing_error_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_comment="전체 이미지 처리 실패 오류 코드",
    )
    processing_error_message = models.TextField(
        blank=True,
        default="",
        db_comment="전체 이미지 처리 실패 오류 메시지",
    )
    processing_started_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="이미지 프로세서 작업 시작 시각",
    )
    processing_completed_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="이미지 프로세서 작업 종료 시각",
    )
    callback_applied_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="최종 callback을 DB에 최초 반영한 시각",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_comment="캘린더 생성 시각")
    updated_at = models.DateTimeField(auto_now=True, db_comment="캘린더 수정 시각")

    class Meta:
        db_table = "calendar_entry"
        db_table_comment = "사용자별 하루 착장 캘린더 (대표 사진 한 장과 이미지 처리 상태)"
        ordering = ["-date", "-created_at"]  # noqa: RUF012 - Django Meta option
        constraints = [  # noqa: RUF012 - Django Meta option
            models.UniqueConstraint(
                fields=["user", "date"],
                name="uq_calendar_user_date",
            )
        ]
        indexes = [  # noqa: RUF012 - Django Meta option
            models.Index(
                fields=["status", "created_at"],
                name="cal_entry_status_created_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.date} ({self.status})"


class CalendarWardrobeItem(models.Model):
    """캘린더와 직접 선택·자동 등록된 옷장 아이템의 N:N 연결 행."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="캘린더-옷장 아이템 연결 UUID",
    )
    calendar = models.ForeignKey(
        CalendarEntry,
        on_delete=models.CASCADE,
        related_name="wardrobe_links",
        db_comment="연결 대상 캘린더 FK (calendar_entry.id)",
    )
    wardrobe_item = models.ForeignKey(
        "wardrobe.WardrobeItem",
        on_delete=models.CASCADE,
        related_name="calendar_links",
        db_comment="연결 대상 옷장 아이템 FK (wardrobe_item.id)",
    )
    sort_order = models.PositiveIntegerField(
        default=0,
        db_comment="캘린더 안의 옷장 아이템 표시 순서 (0부터 시작)",
    )
    snapshot = models.JSONField(
        default=dict,
        blank=True,
        db_comment="연결 당시 옷장 아이템의 이미지·이름·카테고리·태그 스냅샷 JSON",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="캘린더-옷장 아이템 연결 생성 시각",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="캘린더-옷장 아이템 연결 수정 시각",
    )

    class Meta:
        db_table = "calendar_wardrobe_item"
        db_table_comment = "캘린더와 옷장 아이템의 N:N 연결 정보"
        ordering = ["sort_order", "created_at"]  # noqa: RUF012 - Django Meta option
        constraints = [  # noqa: RUF012 - Django Meta option
            models.UniqueConstraint(
                fields=["calendar", "wardrobe_item"],
                name="uq_cal_wardrobe_link",
            )
        ]
        indexes = [  # noqa: RUF012 - Django Meta option
            models.Index(
                fields=["calendar", "sort_order"],
                name="cal_wardrobe_order_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.calendar_id}:{self.wardrobe_item_id}"
