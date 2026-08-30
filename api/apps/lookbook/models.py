"""룩북 도메인 모델.

룩북은 "룩 한 벌"의 기록이다. 캘린더(calendar_entry)와 담는 내용은 같지만
**날짜에 매이지 않는다** — 캘린더는 user+date 유니크라 하루 한 건이고, 룩북은
같은 날 여러 벌을 올릴 수 있고 날짜가 아예 없을 수도 있다. 그래서 캘린더
테이블을 늘리지 않고 별도 테이블을 뒀고, 두 기록을 잇고 싶을 때만
calendar_entry로 연결한다.

사진 등록 경로는 캘린더와 동일하게 기존 옷장 업로드 job(WardrobeUploadJob)을
재사용한다. 다른 점은 하나 — 사용자가 '입은 옷'으로 이미 지정한 대분류는
이미지 프로세서 단계에서 제외한다(skipped_categories).
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.lookbook.contracts import (
    LookbookLinkType,
    LookbookSourceType,
    LookbookStatus,
)


class LookbookPost(models.Model):
    """사용자가 올린 룩 한 벌."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="룩북 UUID (외부 노출 식별자)",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="lookbook_posts",
        db_comment="룩북 소유 사용자 FK (users.id)",
    )
    wardrobe_upload_job = models.OneToOneField(
        "wardrobe.WardrobeUploadJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lookbook_post",
        db_comment=(
            "룩 사진 처리에 재사용한 옷장 job FK "
            "(wardrobe_upload_job.id, 옷장 직접 선택 등록은 NULL)"
        ),
    )
    calendar_entry = models.OneToOneField(
        "style_calendar.CalendarEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lookbook_post",
        db_comment=(
            "'캘린더에도 기록'으로 함께 만든 캘린더 FK "
            "(calendar_entry.id, 캘린더를 남기지 않으면 NULL)"
        ),
    )
    wardrobe_items = models.ManyToManyField(
        "wardrobe.WardrobeItem",
        through="LookbookWardrobeItem",
        through_fields=("lookbook", "wardrobe_item"),
        related_name="lookbook_posts",
        blank=True,
    )
    source_type = models.CharField(
        max_length=24,
        choices=[
            (LookbookSourceType.PHOTO_UPLOAD.value, "룩 사진 업로드"),
            (LookbookSourceType.WARDROBE_SELECTED.value, "옷장 직접 선택"),
            (LookbookSourceType.GOLDEN_LOOK.value, "오늘의 룩 저장"),
        ],
        db_comment="룩북 등록 경로 (PHOTO_UPLOAD/WARDROBE_SELECTED/GOLDEN_LOOK)",
    )
    image_s3_key = models.CharField(
        max_length=512,
        db_comment="룩북 대표 이미지 S3 키 (룩북 소유 경로)",
    )
    image_s3_bucket = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_comment=(
            "대표 이미지가 있는 S3 버킷. 빈 값이면 룩북 버킷(LOOKBOOK_S3_BUCKET). "
            "오늘의 룩에서 담은 골든 코디는 골든셋 버킷을 그대로 가리킨다"
        ),
    )
    golden_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_comment=(
            "오늘의 룩에서 담은 골든 코디 id (source_type=GOLDEN_LOOK 일 때만). "
            "사용자당 한 번만 담기도록 유니크 제약의 근거가 된다"
        ),
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
    hashtags = models.JSONField(
        default=list,
        blank=True,
        db_comment="룩북 해시태그 문자열 목록 JSON",
    )
    skipped_categories = models.JSONField(
        default=list,
        blank=True,
        db_comment=(
            "입은 옷 지정과 겹쳐 사진 등록에서 제외한 옷장 대분류 목록 JSON "
            "(예: 상의/하의)"
        ),
    )
    is_public = models.BooleanField(
        "전체 공개 여부",
        default=False,
        db_comment=(
            "전체 공개 여부 (true: 앱 사용자 전체가 둘러보기에서 볼 수 있음). "
            "룩북은 친구 단위 공유를 두지 않는다 — 내 것이거나 전체 공개거나 둘 중 하나다."
        ),
    )
    status = models.CharField(
        max_length=16,
        choices=[
            (LookbookStatus.REGISTERED.value, "등록"),
            (LookbookStatus.PROCESSING.value, "처리중"),
            (LookbookStatus.COMPLETED.value, "완료"),
            (LookbookStatus.FAILED.value, "실패"),
        ],
        default=LookbookStatus.REGISTERED.value,
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
    created_at = models.DateTimeField(auto_now_add=True, db_comment="룩북 생성 시각")
    updated_at = models.DateTimeField(auto_now=True, db_comment="룩북 수정 시각")

    class Meta:
        db_table = "lookbook_post"
        db_table_comment = (
            "사용자가 올린 룩 한 벌 (대표 사진·입은 옷·일정·해시태그와 이미지 처리 상태)"
        )
        ordering = ["-created_at"]  # noqa: RUF012 - Django Meta option
        constraints = [  # noqa: RUF012 - Django Meta option
            # 같은 골든 코디를 두 번 담지 않는다. 오늘의 룩은 하루 한 벌이라
            # 두 번째 '저장'은 사용자의 의도가 아니라 눌린 것에 가깝다.
            # 골든 코디가 아닌 룩(golden_id="")은 제약에서 빠진다 — 사진 룩은
            # 같은 사진을 여러 번 올릴 수 있어야 한다.
            models.UniqueConstraint(
                fields=["user", "golden_id"],
                condition=~models.Q(golden_id=""),
                name="uq_lookbook_user_golden",
            )
        ]
        indexes = [  # noqa: RUF012 - Django Meta option
            models.Index(
                fields=["user", "-created_at"],
                name="lookbook_user_created_idx",
            ),
            models.Index(
                fields=["status", "created_at"],
                name="lookbook_status_created_idx",
            ),
            # 공개 피드 — 공개된 것만 최신순으로 훑는다.
            models.Index(
                fields=["is_public", "-created_at"],
                name="lookbook_public_created_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.pk} ({self.status})"


class LookbookWardrobeItem(models.Model):
    """룩북과 옷장 아이템의 N:N 연결 행."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="룩북-옷장 아이템 연결 UUID",
    )
    lookbook = models.ForeignKey(
        LookbookPost,
        on_delete=models.CASCADE,
        related_name="wardrobe_links",
        db_comment="연결 대상 룩북 FK (lookbook_post.id)",
    )
    wardrobe_item = models.ForeignKey(
        "wardrobe.WardrobeItem",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="lookbook_links",
        db_comment=(
            "연결 대상 옷장 아이템 FK (wardrobe_item.id). "
            "골든 코디 구성 아이템은 사용자 옷장의 옷이 아니라 NULL이고 snapshot만 남는다"
        ),
    )
    link_type = models.CharField(
        max_length=16,
        choices=[
            (LookbookLinkType.SELECTED.value, "사용자 직접 선택"),
            (LookbookLinkType.EXTRACTED.value, "룩 사진에서 추출"),
            (LookbookLinkType.GOLDEN.value, "골든 코디 구성"),
        ],
        default=LookbookLinkType.SELECTED.value,
        db_comment=(
            "아이템이 붙은 경로 "
            "(SELECTED: 직접 선택 / EXTRACTED: 사진 추출 / GOLDEN: 골든 코디 구성)"
        ),
    )
    sort_order = models.PositiveIntegerField(
        default=0,
        db_comment="룩북 안의 옷장 아이템 표시 순서 (0부터 시작)",
    )
    snapshot = models.JSONField(
        default=dict,
        blank=True,
        db_comment="연결 당시 옷장 아이템의 이미지·이름·카테고리·태그 스냅샷 JSON",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="룩북-옷장 아이템 연결 생성 시각",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="룩북-옷장 아이템 연결 수정 시각",
    )

    class Meta:
        db_table = "lookbook_wardrobe_item"
        db_table_comment = "룩북과 옷장 아이템의 N:N 연결 정보"
        ordering = ["sort_order", "created_at"]  # noqa: RUF012 - Django Meta option
        constraints = [  # noqa: RUF012 - Django Meta option
            models.UniqueConstraint(
                fields=["lookbook", "wardrobe_item"],
                name="uq_lookbook_wardrobe_link",
            )
        ]
        indexes = [  # noqa: RUF012 - Django Meta option
            models.Index(
                fields=["lookbook", "sort_order"],
                name="lookbook_wardrobe_order_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.lookbook_id}:{self.wardrobe_item_id}"


class CuratedLook(models.Model):
    """운영자가 CSV로 관리하는 공개 룩북 콘텐츠."""

    class Gender(models.TextChoices):
        WOMAN = "WOMAN", "여성"
        MAN = "MAN", "남성"

    external_id = models.CharField(
        max_length=100, unique=True, db_comment="CSV에서 사용하는 운영자 룩 고유 ID"
    )
    gender = models.CharField(
        max_length=10,
        choices=Gender.choices,
        default=Gender.WOMAN,
        db_comment="룩 노출 성별 구분 (WOMAN/MAN)",
    )
    category = models.CharField(max_length=30, db_comment="룩북 필터 카테고리")
    title = models.CharField(max_length=200, db_comment="룩 제목")
    subtitle = models.CharField(max_length=200, blank=True, db_comment="룩 부제")
    cover_image_url = models.TextField(db_comment="전신 코디 대표 이미지 URL")
    tags = models.JSONField(default=list, blank=True, db_comment="룩 필터 태그 배열 JSON")
    is_active = models.BooleanField(default=True, db_comment="공개 둘러보기 노출 여부")
    created_at = models.DateTimeField(auto_now_add=True, db_comment="생성 시각")
    updated_at = models.DateTimeField(auto_now=True, db_comment="수정 시각")

    class Meta:
        db_table = "lookbook_curated_look"
        db_table_comment = "운영자가 선별해 공개하는 룩북 콘텐츠"
        ordering = ["category", "external_id"]  # noqa: RUF012


class CuratedLookItem(models.Model):
    """운영자 룩의 원본 상품과 유사상품 검색 기준."""

    look = models.ForeignKey(
        CuratedLook,
        on_delete=models.CASCADE,
        related_name="items",
        db_comment="운영자 룩 FK",
    )
    slot = models.CharField(max_length=30, db_comment="구성 위치 (상의/하의/신발/액세서리)")
    category_small = models.CharField(
        max_length=50,
        blank=True,
        default="",
        db_comment="관리자가 검수·확정한 서비스 소분류 (가디건/패딩/셔츠 등, 공란이면 유사상품 미노출)",
    )
    name = models.CharField(max_length=500, db_comment="원본 상품명")
    brand = models.CharField(max_length=200, blank=True, db_comment="원본 상품 브랜드 또는 판매처")
    price = models.PositiveIntegerField(null=True, blank=True, db_comment="원본 판매가 (원)")
    product_url = models.TextField(db_comment="네이버 쇼핑 원본 상품 상세 URL")
    image_url = models.TextField(blank=True, db_comment="원본 상품 대표 이미지 URL")
    related_keyword = models.CharField(max_length=200, db_comment="유사상품 네이버 검색어")
    sort_order = models.PositiveIntegerField(default=0, db_comment="구성 아이템 표시 순서")

    class Meta:
        db_table = "lookbook_curated_item"
        db_table_comment = "운영자 룩 구성 아이템과 네이버 원본 상품 연결"
        ordering = ["sort_order", "id"]  # noqa: RUF012
        constraints = [  # noqa: RUF012
            models.UniqueConstraint(fields=["look", "slot"], name="uq_curated_look_slot")
        ]
