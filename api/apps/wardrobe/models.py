"""옷장 아이템 등록 도메인 모델.

설계 문서: Confluence > 설계 > "옷장 기능 전체 설계".
- DB가 source of truth. Qdrant 벡터는 파생 저장소 (services/vectors.py).
- 업로드 1건 = WardrobeUploadJob 1건 → 처리 결과 아이템 N건(WardrobeItem).

테이블·컬럼 comment는 db_table_comment/db_comment로 모델이 소유한다
(새 필드 추가 시 반드시 db_comment 지정).
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone


class WardrobeItemBatch(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "대기"
        PROCESSING = "PROCESSING", "처리중"
        DONE = "DONE", "완료"
        PARTIAL = "PARTIAL", "일부실패"
        FAILED = "FAILED", "실패"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False,
                          db_comment="배치 UUID (외부 노출 식별자)")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="wardrobe_batches",
                             db_comment="등록 사용자 FK (users.id)")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING,
                              db_comment="배치 상태 (PENDING/PROCESSING/DONE/PARTIAL/FAILED)")
    total_count = models.PositiveSmallIntegerField(default=0, db_comment="접수된 이미지 장수")
    done_count = models.PositiveSmallIntegerField(default=0, db_comment="태깅 성공 job 수")
    failed_count = models.PositiveSmallIntegerField(default=0, db_comment="태깅 실패 job 수")
    source = models.CharField(max_length=20, default="onboarding",
                              db_comment="등록 경로 (onboarding/manual 등)")
    created_at = models.DateTimeField(auto_now_add=True, db_comment="배치 접수 시각")
    finished_at = models.DateTimeField(null=True, blank=True, db_comment="모든 job 종료 시각")

    class Meta:
        db_table = "wardrobe_item_batch"
        db_table_comment = "옷장 아이템 일괄 등록 요청과 처리 진행률"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "status"], name="wardrobe_b_user_status_idx")]

    def refresh_status(self) -> None:
        counts = self.jobs.aggregate(
            done=Count("id", filter=Q(status="DONE")),
            failed=Count("id", filter=Q(status="FAILED")),
        )
        self.done_count, self.failed_count = counts["done"], counts["failed"]
        finished = self.done_count + self.failed_count
        if finished == self.total_count:
            self.status = (self.Status.DONE if not self.failed_count else
                           self.Status.FAILED if not self.done_count else self.Status.PARTIAL)
            self.finished_at = self.finished_at or timezone.now()
        elif finished:
            self.status = self.Status.PROCESSING
        self.save(update_fields=["done_count", "failed_count", "status", "finished_at"])


class WardrobeUploadJob(models.Model):
    """사진 업로드 → 이미지 프로세서 처리 job. 콜백 멱등성의 기준 키."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "대기"
        PROCESSING = "PROCESSING", "처리중"
        DONE = "DONE", "완료"
        FAILED = "FAILED", "실패"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="job UUID (외부 노출 식별자)",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wardrobe_jobs",
        db_comment="업로드 사용자 FK (users.id)",
    )
    batch = models.ForeignKey(
        WardrobeItemBatch, on_delete=models.CASCADE, null=True, blank=True,
        related_name="jobs",
        db_comment="일괄 등록 배치 FK (wardrobe_item_batch.id, 단건 업로드는 NULL)",
    )
    pipeline = models.CharField(max_length=20, default="gemini-edit",
                                db_comment="처리 파이프라인 식별자 (gemini-edit/qwen-tag)")
    original_file_name = models.CharField(max_length=255, blank=True, default="",
                                          db_comment="업로드 원본 파일명")
    input_metadata = models.JSONField(
        default=dict,
        blank=True,
        db_comment="외부 수집 시 클라이언트가 제공한 옷장 부분 태그 JSON",
    )
    # ── 공유 예약 ──
    # 등록 화면에서 '공유 옷장' 토글을 켜고 시작한 job. 이 시점의 옷은 아직 존재하지도
    # 않으므로 방 선택을 job 이 들고 있다가, 아이템이 만들어질 때 아이템으로 옮긴다.
    # 기기가 아니라 여기에 두는 이유: PC 에서 올리고 폰에서 확정해도 공유가 살아야 한다.
    shared_room = models.ForeignKey(
        "SharedWardrobeRoom",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pending_jobs",
        db_comment="등록 시 지정한 공유 예약 방 FK (NULL: 공유 안 함, 방 삭제 시 NULL)",
    )
    # UI/API 에서 제거됨 — 공유는 항상 available 로 동작한다. 레거시 값 보존용 컬럼.
    shared_status = models.CharField(
        "공유 예약 상태",
        max_length=15,
        blank=True,
        default="",
        db_comment="공유 예약 시 적용할 상태 (available/borrowed/private, 빈 문자열: 예약 없음)",
    )
    source_s3_key = models.CharField(
        "원본 S3 키", max_length=512, db_comment="업로드 원본 이미지 S3 키"
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_comment="처리 상태 (PENDING/PROCESSING/DONE/FAILED)",
    )
    error_message = models.TextField(
        blank=True, default="", db_comment="실패 시 오류 메시지"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_comment="job 생성(접수) 시각")
    finished_at = models.DateTimeField(
        null=True, blank=True, db_comment="처리 종료 시각 (DONE/FAILED 전환 시)"
    )

    class Meta:
        # 프로젝트 규칙: db_table 명시 (기본값이면 wardrobe_wardrobeuploadjob처럼
        # 앱 라벨과 모델명 접두사가 중복된다)
        db_table = "wardrobe_upload_job"
        db_table_comment = "옷장 사진 업로드 처리 job (이미지 프로세서 콜백 멱등성 기준)"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self) -> str:
        return f"job {self.id} ({self.status})"


class WardrobeHashtag(models.Model):
    """사용자가 개인 옷장 아이템에 직접 붙이는 옷장 전용 해시태그."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="개인 옷장 사용자 해시태그 UUID",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wardrobe_hashtags",
        db_comment="해시태그 소유 사용자 FK (users.id)",
    )
    name = models.CharField(
        max_length=30,
        db_comment="사용자에게 표시할 개인 옷장 해시태그명 (# 제외)",
    )
    normalized_name = models.CharField(
        max_length=30,
        editable=False,
        db_comment="중복 검사용 정규화 해시태그명 (#·공백 정리 및 대소문자 통합)",
    )
    position = models.PositiveIntegerField(
        default=0,
        db_comment="사용자 해시태그 표시 순서 (0부터 오름차순)",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="해시태그 생성 시각",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="해시태그 수정 시각",
    )

    class Meta:
        db_table = "wardrobe_hashtag"
        db_table_comment = "개인 옷장 아이템에 사용자가 붙이는 정리용 해시태그"
        ordering = ["position", "created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "normalized_name"],
                name="uq_wd_hashtag_user_normalized",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "position"],
                name="idx_wd_hashtag_user_pos",
            ),
        ]

    @staticmethod
    def normalize_name(value: str) -> tuple[str, str]:
        display_name = " ".join(value.strip().split())
        if display_name.startswith("#"):
            display_name = display_name[1:].lstrip()
        return display_name, display_name.casefold()

    def clean(self) -> None:
        super().clean()
        self.name, self.normalized_name = self.normalize_name(self.name)
        if not self.name:
            raise ValidationError({"name": "해시태그 이름을 입력해 주세요."})

    def save(self, *args, **kwargs) -> None:
        # API 서비스 밖에서 생성해도 정규화 이름이 비어 저장되지 않게 한다.
        self.clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name} ({self.user_id})"


class WardrobeViewPreference(models.Model):
    """기기와 무관하게 복원하는 개인 옷장 묶기·정렬 설정."""

    class GroupMode(models.TextChoices):
        SYSTEM_CATEGORY = "SYSTEM_CATEGORY", "기본 카테고리별"
        HASHTAG = "HASHTAG", "해시태그별"

    class ItemSort(models.TextChoices):
        ADDED_DESC = "ADDED_DESC", "최근 추가순"
        COLOR_NAME_ASC = "COLOR_NAME_ASC", "색상·이름순"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="wardrobe_view_preference",
        db_comment="옷장 보기 설정 소유 사용자 FK (users.id, 사용자당 1건)",
    )
    group_mode = models.CharField(
        max_length=20,
        choices=GroupMode.choices,
        default=GroupMode.SYSTEM_CATEGORY,
        db_comment="옷장 섹션 묶기 방식 (SYSTEM_CATEGORY/HASHTAG)",
    )
    item_sort = models.CharField(
        max_length=20,
        choices=ItemSort.choices,
        default=ItemSort.ADDED_DESC,
        db_comment="섹션 내부 아이템 정렬 방식 (ADDED_DESC/COLOR_NAME_ASC)",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="옷장 보기 설정 수정 시각",
    )

    class Meta:
        db_table = "wardrobe_view_preference"
        db_table_comment = "사용자별 개인 옷장 묶기 및 아이템 정렬 설정"


class WardrobeItem(models.Model):
    """분리·태깅된 옷장 아이템 1벌. 태그 스키마는 taxonomy.py를 따른다.

    벡터는 DB에 저장하지 않고 Qdrant(wardrobe_items 컬렉션)에만 둔다.
    confirmed=False는 사용자 확인 대기 상태 — 추천 검색 대상에서 제외한다.

    added_to_closet_at 은 confirmed 와 다른 것을 가리킨다.
      confirmed          = 자동 태깅 결과를 사용자가 검토했는가
      added_to_closet_at = 이 옷을 옷장에 두기로 했는가
    룩 사진에서 뽑은 옷은 행은 만들되 옷장에는 넣지 않는다(NULL) — 사용자가 고르지도 않은
    옷이 옷장에 쌓이기 때문이다. 룩 상세에서 '옷장에 추가'를 누를 때 시각이 찍힌다.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="아이템 UUID (외부 노출 식별자)",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wardrobe_items",
        db_comment="소유 사용자 FK (users.id)",
    )
    job = models.ForeignKey(
        WardrobeUploadJob,
        on_delete=models.SET_NULL,
        null=True,
        related_name="items",
        db_comment="생성 출처 업로드 job FK (wardrobe_upload_job.id, job 삭제 시 NULL)",
    )
    s3_key = models.CharField(
        "크롭 이미지 S3 키", max_length=512, db_comment="배경 제거·크롭된 아이템 이미지 S3 키"
    )

    # ── 캡셔닝(태깅) 필드 — Confluence 태그 체계 ──
    item_name = models.CharField(
        max_length=120, blank=True, default="", db_comment="아이템 표시 이름 (태깅 생성 또는 사용자 수정)"
    )
    category_large = models.CharField(
        max_length=20, db_comment="대분류 (상의/하의/아우터/신발/가방 등)"
    )
    category_small = models.CharField(
        max_length=30, blank=True, default="", db_comment="소분류 (티셔츠/청바지 등)"
    )
    season = ArrayField(
        models.CharField(max_length=10), default=list, blank=True, db_comment="계절 태그 배열"
    )
    style = ArrayField(
        models.CharField(max_length=10), default=list, blank=True, db_comment="스타일 태그 배열"
    )
    color = models.CharField(max_length=10, blank=True, default="", db_comment="색상 태그")
    pattern = models.CharField(max_length=10, blank=True, default="", db_comment="패턴 태그")
    fit = models.CharField(max_length=10, blank=True, default="", db_comment="핏 태그")
    material = models.CharField(max_length=10, blank=True, default="", db_comment="소재 태그")
    sleeve = models.CharField(max_length=10, blank=True, default="", db_comment="소매 길이 태그")
    length = models.CharField(max_length=10, blank=True, default="", db_comment="기장 태그")
    usage = ArrayField(
        models.CharField(max_length=20), default=list, blank=True, db_comment="용도(TPO) 태그 배열"
    )
    layer_role = models.CharField(
        max_length=10, blank=True, default="", db_comment="레이어링 역할 태그"
    )
    layer_order = models.PositiveSmallIntegerField(
        null=True, blank=True, db_comment="레이어링 착용 순서 (안쪽부터 1)"
    )

    # ── 메타 ──
    seg_meta = models.JSONField(
        "세그멘테이션 메타(raw_label·score·bbox 등)",
        default=dict,
        blank=True,
        db_comment="세그멘테이션 메타 JSON (raw_label/score/bbox 등)",
    )
    is_favorite = models.BooleanField(
        "즐겨찾기",
        default=False,
        db_comment="사용자가 별로 표시한 옷 (자주 입는 옷 모아보기용)",
    )
    confirmed = models.BooleanField(
        "사용자 확정 여부",
        default=False,
        db_comment="사용자 확정 여부 (false: 확인 대기 — 추천 검색 제외)",
    )
    added_to_closet_at = models.DateTimeField(
        "옷장 편입 시각",
        null=True,
        blank=True,
        db_comment=(
            "사용자가 이 옷을 옷장에 두기로 한 시각 "
            "(NULL: 룩 사진에서 뽑혔지만 아직 옷장에 넣지 않음 — 옷장 목록에서 제외)"
        ),
    )
    # ── 공유 예약 ──
    # "확정되면 이 방에 공유한다". 갓 만들어진 옷은 confirmed=False 라 서버가 공유를
    # 거부하므로(shared_wardrobe.register_item_to_shared_room), 확정 전까지 여기 대기시킨다.
    # 확정(PATCH confirmed=true) 시점에 서버가 소진하고 NULL 로 되돌린다.
    # ⚠️ 이 컬럼은 '예약'이지 '공유 상태'가 아니다. 실제 공유 관계는 shared_wardrobe_item
    #    (room, wardrobe_item) 행이며, 한 옷이 여러 방에 걸릴 수 있어 1:N 을 여기 담을 수 없다.
    pending_share_room = models.ForeignKey(
        "SharedWardrobeRoom",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pending_items",
        db_comment="확정 시 공유할 예약 방 FK (NULL: 예약 없음, 방 삭제 시 NULL)",
    )
    # UI/API 에서 제거됨 — 공유는 항상 available 로 동작한다. 레거시 값 보존용 컬럼.
    pending_share_status = models.CharField(
        "공유 예약 상태",
        max_length=15,
        blank=True,
        default="",
        db_comment="예약 소진 시 적용할 공유 상태 (available/borrowed/private, 빈 문자열: 기본값 사용)",
    )
    embedding_version = models.CharField(
        max_length=40, blank=True, default="", db_comment="Qdrant 임베딩 버전 (재임베딩 판단 기준)"
    )
    wardrobe_hashtags = models.ManyToManyField(
        WardrobeHashtag,
        through="WardrobeItemHashtag",
        related_name="wardrobe_items",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_comment="행 생성 시각")
    updated_at = models.DateTimeField(auto_now=True, db_comment="행 수정 시각")

    class Meta:
        db_table = "wardrobe_item"
        db_table_comment = "사용자 옷장 아이템 (업로드 사진에서 분리·태깅된 옷 1벌, 벡터는 Qdrant에 별도 저장)"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "category_large"]),
            models.Index(fields=["user", "confirmed"]),
            # 옷장 목록의 기본 조건 — 사용자별로 '옷장에 든 것'만 훑는다.
            models.Index(fields=["user", "added_to_closet_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.item_name or self.category_large} ({self.user_id})"


class WardrobeItemHashtag(models.Model):
    """개인 옷장 아이템과 사용자 해시태그의 다대다 연결."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="개인 옷장 아이템 해시태그 연결 UUID",
    )
    wardrobe_item = models.ForeignKey(
        WardrobeItem,
        on_delete=models.CASCADE,
        related_name="wardrobe_hashtag_links",
        db_comment="해시태그를 지정한 개인 옷장 아이템 FK",
    )
    hashtag = models.ForeignKey(
        WardrobeHashtag,
        on_delete=models.CASCADE,
        related_name="item_links",
        db_comment="아이템에 지정한 사용자 해시태그 FK",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="아이템 해시태그 연결 시각",
    )

    class Meta:
        db_table = "wardrobe_item_hashtag"
        db_table_comment = "개인 옷장 아이템과 사용자 해시태그 연결"
        constraints = [
            models.UniqueConstraint(
                fields=["wardrobe_item", "hashtag"],
                name="uq_wd_item_hashtag_pair",
            ),
        ]
        indexes = [
            models.Index(
                fields=["hashtag", "wardrobe_item"],
                name="idx_wd_item_hashtag_lookup",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if not self.wardrobe_item_id or not self.hashtag_id:
            return
        if self.wardrobe_item.user_id != self.hashtag.user_id:
            raise ValidationError(
                "옷장 아이템과 사용자 해시태그의 소유자가 같아야 합니다."
            )

    def save(self, *args, **kwargs) -> None:
        # DB 제약은 FK가 가리키는 두 행의 user_id를 비교할 수 없어 모델에서도 방어한다.
        self.clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.wardrobe_item_id} - {self.hashtag_id}"


class SharedWardrobeRoom(models.Model):
    """공유 옷장 방 정보. 초대코드 및 만료 관리를 담당합니다."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="공유방 UUID (외부 노출 식별자)",
    )
    title = models.CharField(
        "방 이름", max_length=100, db_comment="공유방 이름 (예: 가족 옷장)"
    )
    invite_code = models.CharField(
        "초대코드", max_length=6, unique=True, null=True, blank=True, db_comment="6자리 초대용 핀코드"
    )
    code_expires_at = models.DateTimeField(
        "초대코드 만료일시", null=True, blank=True, db_comment="초대코드 유효 만료 시각"
    )
    created_at = models.DateTimeField(
        "생성일시", auto_now_add=True, db_comment="방 개설 시각"
    )

    class Meta:
        db_table = "shared_wardrobe_room"
        db_table_comment = "공유 옷장 그룹 방"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title


class SharedWardrobeMember(models.Model):
    """공유 옷장 참여 멤버십 정보. 사용자 권한(방장/멤버)을 관리합니다."""

    class Role(models.TextChoices):
        OWNER = "owner", "방장"
        MEMBER = "member", "멤버"

    room = models.ForeignKey(
        SharedWardrobeRoom,
        on_delete=models.CASCADE,
        related_name="members",
        db_comment="소속 공유방 FK"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shared_rooms",
        db_comment="참여 사용자 FK"
    )
    role = models.CharField(
        "역할", max_length=10, choices=Role.choices, default=Role.MEMBER, db_comment="방 권한 (owner/member)"
    )
    joined_at = models.DateTimeField(
        "참여일시", auto_now_add=True, db_comment="방 참가 시각"
    )

    class Meta:
        db_table = "shared_wardrobe_member"
        db_table_comment = "공유 옷장 그룹 참여자"
        unique_together = (("room", "user"),)
        ordering = ["joined_at"]

    def __str__(self) -> str:
        return f"{self.user} in {self.room} ({self.role})"


class SharedWardrobeItem(models.Model):
    """공유 옷장에 등록된 의류 아이템 정보. 사용자 탈퇴 시 아이템 유지/삭제 분기가 가능합니다."""

    class Status(models.TextChoices):
        AVAILABLE = "available", "공유가능"
        BORROWED = "borrowed", "대여중"
        PRIVATE = "private", "나만보기"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="공유 아이템 UUID"
    )
    room = models.ForeignKey(
        SharedWardrobeRoom,
        on_delete=models.CASCADE,
        related_name="items",
        db_comment="소속 공유방 FK"
    )
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shared_items",
        db_comment="등록 사용자 FK (사용자 탈퇴 시에도 옷 유지를 위해 SET_NULL 지원)"
    )
    # 기존 개인 옷장 아이템에서 정보(사진 및 메타)를 참조하여 연결
    wardrobe_item = models.ForeignKey(
        WardrobeItem,
        on_delete=models.CASCADE,
        related_name="shared_instances",
        db_comment="원본 옷장 아이템 FK"
    )
    # UI/API 에서 제거됨 — 항상 available 로 동작한다. 과거 borrowed/private 행 보존용 컬럼.
    status = models.CharField(
        "공유상태", max_length=15, choices=Status.choices, default=Status.AVAILABLE, db_comment="공유 대여 가능 상태"
    )
    created_at = models.DateTimeField(
        "등록일시", auto_now_add=True, db_comment="공유방 등록 시각"
    )
    categories = models.ManyToManyField(
        "SharedWardrobeCategory",
        through="SharedWardrobeItemCategory",
        related_name="shared_items",
    )

    class Meta:
        db_table = "shared_wardrobe_item"
        db_table_comment = "공유 옷장 내 등록된 의류 아이템"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["room", "wardrobe_item"],
                name="uq_shared_wardrobe_item_room_item",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.wardrobe_item.item_name or '옷'} in {self.room}"


class SharedWardrobeCategory(models.Model):
    """공유방 구성원이 함께 사용하는 사용자 정의 필터 카테고리."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="공유 옷장 사용자 정의 카테고리 UUID",
    )
    room = models.ForeignKey(
        SharedWardrobeRoom,
        on_delete=models.CASCADE,
        related_name="categories",
        db_comment="카테고리가 속한 공유방 FK",
    )
    name = models.CharField(
        max_length=30,
        db_comment="사용자 정의 카테고리명 (공유방 안에서 중복 불가)",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_shared_wardrobe_categories",
        db_comment="카테고리 생성 사용자 FK (탈퇴 시 NULL)",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="카테고리 생성 시각",
    )

    class Meta:
        db_table = "shared_wardrobe_category"
        db_table_comment = "공유 옷장의 사용자 정의 필터 카테고리"
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["room", "name"],
                name="uq_shared_wardrobe_category_room_name",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} in {self.room}"


class SharedWardrobeItemCategory(models.Model):
    """공유 아이템과 사용자 정의 카테고리의 다대다 연결."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="공유 아이템 카테고리 연결 UUID",
    )
    shared_item = models.ForeignKey(
        SharedWardrobeItem,
        on_delete=models.CASCADE,
        related_name="category_links",
        db_comment="분류할 공유 아이템 FK",
    )
    category = models.ForeignKey(
        SharedWardrobeCategory,
        on_delete=models.CASCADE,
        related_name="item_links",
        db_comment="공유 아이템에 지정한 사용자 정의 카테고리 FK",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="아이템 카테고리 연결 시각",
    )

    class Meta:
        db_table = "shared_wardrobe_item_category"
        db_table_comment = "공유 옷장 아이템과 사용자 정의 카테고리 연결"
        constraints = [
            models.UniqueConstraint(
                fields=["shared_item", "category"],
                name="uq_shared_item_category_pair",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.shared_item_id} - {self.category_id}"
