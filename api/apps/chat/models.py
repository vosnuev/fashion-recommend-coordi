"""대화형 추천의 회원·게스트 identity, 세션, 메시지와 첨부파일 모델."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Upper
from django.utils import timezone

from apps.chat.services.stylist_personas import load_stylist_personas


def validate_selected_persona_ids(value: object) -> None:
    """세션 선택값이 활성 스타일리스트의 고정 순서 부분집합인지 검증한다."""

    if not isinstance(value, list):
        raise ValidationError("스타일리스트 선택값은 JSON 배열이어야 합니다.")

    catalog = load_stylist_personas()
    if len(value) > catalog.max_select:
        raise ValidationError(
            f"스타일리스트는 최대 {catalog.max_select}명까지 선택할 수 있습니다."
        )
    if any(not isinstance(persona_id, str) for persona_id in value):
        raise ValidationError("스타일리스트 ID는 문자열이어야 합니다.")
    if len(value) != len(set(value)):
        raise ValidationError("스타일리스트 ID는 중복될 수 없습니다.")

    supported_ids = catalog.supported_persona_ids
    unsupported_ids = sorted(set(value) - set(supported_ids))
    if unsupported_ids:
        raise ValidationError(
            f"지원하지 않는 스타일리스트 ID입니다: {', '.join(unsupported_ids)}"
        )

    canonical_ids = tuple(
        persona_id for persona_id in supported_ids if persona_id in value
    )
    if tuple(value) != canonical_ids:
        raise ValidationError(
            "스타일리스트 ID는 minimal, experimental, practical 고정 순서로 "
            "저장해야 합니다."
        )


def validate_member_last_selected_persona_ids(value: object) -> None:
    """회원 마지막 선택은 유효한 스타일리스트를 최소 1명 포함해야 한다."""

    validate_selected_persona_ids(value)
    catalog = load_stylist_personas()
    if len(value) < catalog.min_select:
        raise ValidationError(
            f"회원 마지막 선택에는 스타일리스트가 최소 {catalog.min_select}명 필요합니다."
        )


def validate_persona_version_snapshot(value: object) -> None:
    """스타일리스트별 설정 버전 스냅샷의 키와 양의 정수를 검증한다."""

    if not isinstance(value, dict):
        raise ValidationError("스타일리스트 버전 스냅샷은 JSON 객체여야 합니다.")
    supported_ids = set(load_stylist_personas().supported_persona_ids)
    if not set(value).issubset(supported_ids):
        raise ValidationError("버전 스냅샷에 지원하지 않는 스타일리스트 ID가 있습니다.")
    if any(
        isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
        for version in value.values()
    ):
        raise ValidationError("스타일리스트 설정 버전은 1 이상의 정수여야 합니다.")


def validate_persona_prompt_version_snapshot(value: object) -> None:
    """스타일리스트별 프롬프트 버전 스냅샷을 검증한다."""

    if not isinstance(value, dict):
        raise ValidationError("프롬프트 버전 스냅샷은 JSON 객체여야 합니다.")
    supported_ids = set(load_stylist_personas().supported_persona_ids)
    if not set(value).issubset(supported_ids):
        raise ValidationError(
            "프롬프트 버전 스냅샷에 지원하지 않는 스타일리스트 ID가 있습니다."
        )
    if any(
        not isinstance(version, str) or not version.strip()
        for version in value.values()
    ):
        raise ValidationError("프롬프트 버전은 비어 있지 않은 문자열이어야 합니다.")


def validate_personalization_snapshot(value: object) -> None:
    """실행 접수 당시 개인화 기준 스냅샷의 최소 계약을 검증한다."""

    if not isinstance(value, dict):
        raise ValidationError("개인화 데이터 기준 스냅샷은 JSON 객체여야 합니다.")
    if not value:
        return
    for key in ("schema_version", "captured_at", "as_of_date", "identity_type"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValidationError(f"개인화 스냅샷의 {key} 값이 필요합니다.")
    if not isinstance(value.get("personalized"), bool):
        raise ValidationError("개인화 스냅샷의 personalized 값은 boolean이어야 합니다.")
    sources = value.get("sources")
    if not isinstance(sources, dict):
        raise ValidationError("개인화 스냅샷의 sources 값은 JSON 객체여야 합니다.")
    if any(
        not isinstance(sources.get(key), dict)
        for key in ("profile", "wardrobe", "behavior")
    ):
        raise ValidationError(
            "개인화 스냅샷 sources에는 profile, wardrobe, behavior 객체가 필요합니다."
        )


def validate_reference_snapshot(value: object) -> None:
    """실행 접수 당시 개인·공유 옷장 참조의 최소 계약을 검증한다."""

    if not isinstance(value, dict):
        raise ValidationError("옷장 참조 스냅샷은 JSON 객체여야 합니다.")
    if not value:
        return

    required_strings = (
        "schema_version",
        "type",
        "wardrobe_item_id",
        "qdrant_collection",
        "qdrant_point_id",
        "embedding_version",
        "image_s3_key",
        "captured_at",
    )
    for key in required_strings:
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValidationError(f"옷장 참조 스냅샷의 {key} 값이 필요합니다.")

    reference_type = value["type"]
    if reference_type not in {"SHARED_WARDROBE_ITEM", "WARDROBE_ITEM"}:
        raise ValidationError("지원하지 않는 옷장 참조 유형입니다.")
    if reference_type == "SHARED_WARDROBE_ITEM":
        for key in ("shared_item_id", "room_id", "source_status"):
            if not isinstance(value.get(key), str) or not value[key].strip():
                raise ValidationError(
                    f"공유 옷장 참조 스냅샷의 {key} 값이 필요합니다."
                )

    item = value.get("item")
    if not isinstance(item, dict):
        raise ValidationError("옷장 참조 스냅샷의 item 객체가 필요합니다.")
    for key in ("season", "style", "usage"):
        if not isinstance(item.get(key), list) or any(
            not isinstance(tag, str) for tag in item[key]
        ):
            raise ValidationError(f"옷장 참조 item.{key}는 문자열 배열이어야 합니다.")


def validate_wardrobe_scope_snapshot(value: object) -> None:
    if not isinstance(value, dict):
        raise ValidationError("옷장 추천 범위 스냅샷은 JSON 객체여야 합니다.")
    if value and not isinstance(value.get("candidate_item_ids"), list):
        raise ValidationError("옷장 추천 범위 후보 ID는 배열이어야 합니다.")


def validate_stylist_persona_id(value: object) -> None:
    """스타일리스트별 실행 행의 ID가 현재 지원 목록에 있는지 검증한다."""

    if not isinstance(value, str) or not value.strip():
        raise ValidationError("스타일리스트 ID는 비어 있지 않은 문자열이어야 합니다.")
    if value not in load_stylist_personas().supported_persona_ids:
        raise ValidationError(f"지원하지 않는 스타일리스트 ID입니다: {value}")


def validate_persona_error_history(value: object) -> None:
    """재시도 전 오류 이력이 JSON 객체 배열인지 검증한다."""

    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValidationError("스타일리스트 오류 이력은 JSON 객체 배열이어야 합니다.")


class PersonaProfile(models.Model):
    """버전이 고정된 채팅 스타일리스트 페르소나 설정."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="페르소나 프로필 UUID",
    )
    code = models.CharField(
        max_length=64,
        unique=True,
        db_comment="애플리케이션에서 참조하는 페르소나 고유 코드",
    )
    name = models.CharField(
        max_length=100,
        db_comment="사용자에게 표시할 스타일리스트 이름",
    )
    prompt_config = models.JSONField(
        default=dict,
        blank=True,
        db_comment="말투·스타일 철학·설명 길이 등 페르소나 프롬프트 설정 JSON",
    )
    version = models.PositiveIntegerField(
        default=1,
        db_comment="프롬프트 변경 시 증가시키는 페르소나 버전 (1 이상)",
    )
    is_active = models.BooleanField(
        default=False,
        db_comment="현재 기본 페르소나 여부 (전체에서 최대 1개)",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="페르소나 생성 시각",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="페르소나 마지막 수정 시각",
    )

    class Meta:
        db_table = "persona_profile"
        db_table_comment = "채팅 말투와 스타일 철학을 버전 관리하는 페르소나 프로필"
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(
                condition=Q(version__gte=1),
                name="ck_persona_profile_version",
            ),
            models.UniqueConstraint(
                fields=["is_active"],
                condition=Q(is_active=True),
                name="uq_persona_profile_active",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.code}:v{self.version})"


class ChatIdentity(models.Model):
    """채팅 데이터를 소유하는 회원 또는 만료 가능한 게스트 identity."""

    class IdentityType(models.TextChoices):
        MEMBER = "MEMBER", "회원"
        GUEST = "GUEST", "게스트"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="채팅 identity UUID",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="chat_identity",
        db_comment="회원 사용자 FK (users.id, 게스트이면 NULL)",
    )
    identity_type = models.CharField(
        max_length=12,
        choices=IdentityType.choices,
        db_comment="채팅 identity 유형 (MEMBER/GUEST)",
    )
    guest_token_hash = models.CharField(
        max_length=64,
        unique=True,
        null=True,
        blank=True,
        db_comment="게스트 원문 토큰의 SHA-256 HMAC 해시 (회원이면 NULL)",
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="게스트 identity 만료 시각 (회원이면 NULL, 마지막 활동부터 7일)",
    )
    last_active_at = models.DateTimeField(
        default=timezone.now,
        db_comment="채팅 identity의 마지막 활동 시각",
    )
    claimed_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="게스트 대화가 회원에게 이전된 시각 (미이전이면 NULL)",
    )
    claimed_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="claimed_guest_identities",
        db_comment="게스트 대화를 이전받은 회원 채팅 identity FK (미이전이면 NULL)",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="채팅 identity 생성 시각",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="채팅 identity 마지막 수정 시각",
    )

    class Meta:
        db_table = "chat_identity"
        db_table_comment = "회원과 게스트 채팅 소유권 및 게스트 토큰 만료·이전 기록"
        indexes = [
            models.Index(
                fields=["identity_type", "expires_at"],
                name="ix_chat_identity_expiry",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        identity_type="MEMBER",
                        user__isnull=False,
                        guest_token_hash__isnull=True,
                        expires_at__isnull=True,
                    )
                    | Q(
                        identity_type="GUEST",
                        user__isnull=True,
                        guest_token_hash__isnull=False,
                        expires_at__isnull=False,
                    )
                ),
                name="ck_chat_identity_owner_type",
            ),
        ]

    def __str__(self) -> str:
        owner = self.user_id if self.user_id is not None else "guest"
        return f"chat-identity {self.id} ({self.identity_type}:{owner})"

    @property
    def is_guest_active(self) -> bool:
        return (
            self.identity_type == self.IdentityType.GUEST
            and self.claimed_at is None
            and self.expires_at is not None
            and self.expires_at > timezone.now()
        )


class MemberStylistSelection(models.Model):
    """회원의 새 채팅방에서 복원할 마지막 스타일리스트 선택."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="stylist_selection",
        db_comment="마지막 스타일리스트 선택을 저장한 회원 FK (users.id, 회원당 1행)",
    )
    last_selected_persona_ids = models.JSONField(
        blank=True,
        validators=[validate_member_last_selected_persona_ids],
        db_comment=(
            "회원이 마지막으로 선택한 스타일리스트 ID JSON 배열 "
            "(minimal/experimental/practical, 고정 순서, 1~3개)"
        ),
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="회원 스타일리스트 선택값 최초 생성 시각",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="회원 스타일리스트 선택값 마지막 변경 시각",
    )

    class Meta:
        db_table = "member_stylist_selection"
        db_table_comment = "회원별 마지막 선택형 스타일리스트 ID와 변경 시각"
        constraints = [
            models.CheckConstraint(
                condition=~Q(last_selected_persona_ids=[]),
                name="ck_member_stylist_ids_not_empty",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        try:
            validate_member_last_selected_persona_ids(
                self.last_selected_persona_ids
            )
        except ValidationError as exc:
            raise ValidationError(
                {"last_selected_persona_ids": exc.messages}
            ) from exc

    def __str__(self) -> str:
        return f"member-stylist-selection {self.user_id}"


class ChatSession(models.Model):
    """추천 모드가 고정된 하나의 대화 세션."""

    class Mode(models.TextChoices):
        WARDROBE_BASED = "WARDROBE_BASED", "옷장 기반 추천"
        NEW_ITEM = "NEW_ITEM", "신규 상품 포함 추천"

    class ResponseMode(models.TextChoices):
        DEFAULT = "DEFAULT", "기본 응답"
        STYLIST = "STYLIST", "선택형 스타일리스트 응답"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="채팅 세션 UUID (외부 노출 식별자)",
    )
    identity = models.ForeignKey(
        ChatIdentity,
        on_delete=models.CASCADE,
        related_name="sessions",
        db_comment="채팅 소유 identity FK (chat_identity.id)",
    )
    mode = models.CharField(
        max_length=24,
        choices=Mode.choices,
        db_comment="세션 생성 후 변경할 수 없는 추천 모드 (WARDROBE_BASED/NEW_ITEM)",
    )
    response_mode = models.CharField(
        max_length=12,
        choices=ResponseMode.choices,
        default=ResponseMode.DEFAULT,
        db_comment="현재 응답 모드 (DEFAULT/STYLIST, 기존 추천 mode와 별도)",
    )
    selected_persona_ids = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_selected_persona_ids],
        db_comment=(
            "현재 선택한 스타일리스트 ID JSON 배열 "
            "(minimal/experimental/practical, 고정 순서, 최대 3개)"
        ),
    )
    persona_selection_updated_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="스타일리스트 선택 배열이 마지막으로 변경된 시각 (미선택이면 NULL)",
    )
    title = models.CharField(
        max_length=120,
        blank=True,
        default="",
        db_comment="사용자 지정 또는 자동 생성 세션 제목",
    )
    persona_profile = models.ForeignKey(
        PersonaProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sessions",
        db_column="persona_profile_id",
        db_comment="세션에 적용할 페르소나 프로필 FK (미지정이면 활성 기본값)",
    )
    parent_session = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="derived_sessions",
        db_comment="모드 변경 시 조건을 이어받은 원본 세션 FK (일반 세션이면 NULL)",
    )
    context_state = models.JSONField(
        default=dict,
        blank=True,
        db_comment="세션의 구조화 추천 조건과 컨텍스트 버전 JSON",
    )
    conversation_summary = models.TextField(
        blank=True,
        default="",
        db_comment="오래된 메시지를 압축한 대화 요약",
    )
    summary_through_sequence = models.PositiveBigIntegerField(
        default=0,
        db_comment="conversation_summary에 반영된 마지막 메시지 sequence (미요약이면 0)",
    )
    last_message_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="마지막 메시지 생성 시각 (메시지가 없으면 NULL)",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="채팅 세션 생성 시각",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="채팅 세션 마지막 수정 시각",
    )
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="사용자가 세션을 삭제한 시각 (소프트 삭제, 활성 세션이면 NULL)",
    )

    class Meta:
        db_table = "chat_session"
        db_table_comment = (
            "추천·응답 모드와 스타일리스트 선택·조건·대화 요약을 보관하는 채팅 세션"
        )
        ordering = ["-updated_at"]
        indexes = [
            models.Index(
                fields=["identity", "deleted_at", "-updated_at"],
                name="ix_chat_session_owner",
            ),
            GinIndex(
                OpClass(Upper("title"), name="gin_trgm_ops"),
                name="ix_chat_session_title_trgm",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(mode__in=["WARDROBE_BASED", "NEW_ITEM"]),
                name="ck_chat_session_mode",
            ),
            models.CheckConstraint(
                condition=Q(response_mode__in=["DEFAULT", "STYLIST"]),
                name="ck_chat_session_response_mode",
            ),
            models.CheckConstraint(
                condition=(
                    Q(response_mode="DEFAULT") | ~Q(selected_persona_ids=[])
                ),
                name="ck_chat_session_stylist_ids",
            ),
        ]

    def __str__(self) -> str:
        return f"chat-session {self.id} ({self.mode})"

    def clean(self) -> None:
        super().clean()
        if (
            self.response_mode == self.ResponseMode.STYLIST
            and not self.selected_persona_ids
        ):
            raise ValidationError(
                {
                    "selected_persona_ids": (
                        "STYLIST 응답 모드에서는 스타일리스트를 1명 이상 "
                        "선택해야 합니다."
                    )
                }
            )

    def save(self, *args, **kwargs) -> None:
        update_fields = kwargs.get("update_fields")
        selection_will_be_saved = (
            update_fields is None or "selected_persona_ids" in update_fields
        )
        if not self._state.adding:
            previous = (
                type(self)
                .objects.filter(pk=self.pk)
                .values("mode", "selected_persona_ids")
                .first()
            )
            if previous is not None and previous["mode"] != self.mode:
                raise ValidationError(
                    {"mode": "추천 모드는 변경할 수 없습니다. 파생 세션을 생성하세요."}
                )
            if (
                previous is not None
                and selection_will_be_saved
                and previous["selected_persona_ids"] != self.selected_persona_ids
            ):
                self.persona_selection_updated_at = timezone.now()
                if update_fields is not None:
                    kwargs["update_fields"] = [
                        *update_fields,
                        "persona_selection_updated_at",
                    ]
        elif self.selected_persona_ids and self.persona_selection_updated_at is None:
            self.persona_selection_updated_at = timezone.now()
        super().save(*args, **kwargs)


class ChatMessage(models.Model):
    """세션 안에서 순서가 보장되는 사용자·AI·시스템 메시지."""

    class Role(models.TextChoices):
        USER = "USER", "사용자"
        ASSISTANT = "ASSISTANT", "AI"
        SYSTEM = "SYSTEM", "시스템"
        TOOL = "TOOL", "도구"

    class Status(models.TextChoices):
        PENDING = "PENDING", "처리 대기"
        PROCESSING = "PROCESSING", "처리 중"
        COMPLETED = "COMPLETED", "처리 완료"
        FAILED = "FAILED", "처리 실패"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="채팅 메시지 UUID",
    )
    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="messages",
        db_comment="채팅 세션 FK (chat_session.id)",
    )
    sequence = models.PositiveBigIntegerField(
        db_comment="세션 내부 메시지 순서 (1부터 시작)",
    )
    role = models.CharField(
        max_length=12,
        choices=Role.choices,
        db_comment="메시지 역할 (USER/ASSISTANT/SYSTEM/TOOL)",
    )
    content = models.TextField(
        blank=True,
        default="",
        db_comment="채팅 메시지 본문 (첨부파일 전용 메시지이면 빈 문자열 가능)",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.COMPLETED,
        db_comment="메시지 처리 상태 (PENDING/PROCESSING/COMPLETED/FAILED)",
    )
    client_message_id = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        db_comment="클라이언트 재전송 중복 방지 ID (서버 메시지이면 NULL)",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        db_comment="추천 결과·실행 ID·오류 등 메시지 부가정보 JSON",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="채팅 메시지 생성 시각",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="채팅 메시지 마지막 수정 시각",
    )

    class Meta:
        db_table = "chat_message"
        db_table_comment = "세션별 순서·역할·처리 상태·중복 방지 ID를 가진 채팅 메시지"
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "sequence"],
                name="uq_chat_message_sequence",
            ),
            models.UniqueConstraint(
                fields=["session", "client_message_id"],
                condition=Q(client_message_id__isnull=False),
                name="uq_chat_message_client_id",
            ),
            models.CheckConstraint(
                condition=Q(sequence__gte=1),
                name="ck_chat_message_sequence",
            ),
        ]
        indexes = [
            GinIndex(
                OpClass(Upper("content"), name="gin_trgm_ops"),
                name="ix_chat_message_content_trgm",
            ),
        ]

    def __str__(self) -> str:
        return f"chat-message {self.session_id}#{self.sequence} ({self.role})"


class ChatAttachment(models.Model):
    """사용자 메시지에 첨부된 비공개 파일 메타데이터."""

    class AnalysisStatus(models.TextChoices):
        NOT_REQUESTED = "NOT_REQUESTED", "분석 안 함"
        QUEUED = "QUEUED", "분석 대기"
        PROCESSING = "PROCESSING", "분석 중"
        SUCCEEDED = "SUCCEEDED", "분석 완료"
        FAILED = "FAILED", "분석 실패"

    class MoodDecision(models.TextChoices):
        UNDECIDED = "UNDECIDED", "미결정"
        APPROVED = "APPROVED", "승인"
        REJECTED = "REJECTED", "거절"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="채팅 첨부파일 UUID",
    )
    message = models.ForeignKey(
        ChatMessage,
        on_delete=models.CASCADE,
        related_name="attachments",
        db_comment="첨부파일이 속한 채팅 메시지 FK (chat_message.id)",
    )
    s3_key = models.CharField(
        max_length=512,
        db_comment="비공개 S3 객체 키",
    )
    mime_type = models.CharField(
        max_length=100,
        db_comment="첨부파일 MIME 타입",
    )
    size = models.PositiveBigIntegerField(
        db_comment="첨부파일 크기 (bytes)",
    )
    sha256 = models.CharField(
        max_length=64,
        db_comment="첨부파일 내용 SHA-256 해시",
    )
    analysis_status = models.CharField(
        max_length=20,
        choices=AnalysisStatus.choices,
        default=AnalysisStatus.NOT_REQUESTED,
        db_comment=(
            "첨부 이미지 분석 상태 (NOT_REQUESTED/QUEUED/PROCESSING/SUCCEEDED/FAILED)"
        ),
    )
    analysis_result = models.JSONField(
        default=dict,
        blank=True,
        db_comment="사진에서 추출한 무드·스타일·색상·핏 분석 결과 JSON",
    )
    mood_decision = models.CharField(
        max_length=12,
        choices=MoodDecision.choices,
        default=MoodDecision.UNDECIDED,
        db_comment="사진 무드 반영 결정 (UNDECIDED/APPROVED/REJECTED)",
    )
    mood_decided_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="사진 무드 승인 또는 거절 확정 시각 (미결정이면 NULL)",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="채팅 첨부파일 메타데이터 생성 시각",
    )

    class Meta:
        db_table = "chat_attachment"
        db_table_comment = "채팅 메시지에 연결된 비공개 S3 첨부파일 메타데이터"
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["message", "sha256"],
                name="uq_chat_attachment_hash",
            ),
        ]

    def __str__(self) -> str:
        return f"chat-attachment {self.id} ({self.mime_type})"


class ChatRun(models.Model):
    """사용자 메시지 하나를 처리하는 오케스트레이터 실행 단위."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "처리 대기"
        RUNNING = "RUNNING", "처리 중"
        NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION", "추가 질문"
        SUCCEEDED = "SUCCEEDED", "성공"
        FAILED = "FAILED", "실패"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="채팅 실행 UUID (큐·추천 결과·SSE의 공통 추적 ID)",
    )
    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="runs",
        db_comment="실행이 속한 채팅 세션 FK (chat_session.id)",
    )
    request_message = models.OneToOneField(
        ChatMessage,
        on_delete=models.CASCADE,
        related_name="run",
        db_comment="실행을 시작한 사용자 메시지 FK (메시지당 실행 최대 1개)",
    )
    response_message = models.ForeignKey(
        ChatMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="responded_runs",
        db_comment="실행이 생성한 최종 AI 메시지 FK (미완료이면 NULL)",
    )
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.PENDING,
        db_comment=("실행 상태 (PENDING/RUNNING/NEEDS_CLARIFICATION/SUCCEEDED/FAILED)"),
    )
    response_mode = models.CharField(
        max_length=12,
        choices=ChatSession.ResponseMode.choices,
        default=ChatSession.ResponseMode.DEFAULT,
        db_comment="실행 접수 당시 응답 모드 스냅샷 (DEFAULT/STYLIST)",
    )
    persona_ids = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_selected_persona_ids],
        db_comment=(
            "실행 접수 당시 선택 스타일리스트 ID JSON 배열 "
            "(고정 순서, 선택하지 않았으면 빈 배열)"
        ),
    )
    persona_versions = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_persona_version_snapshot],
        db_comment="실행 접수 당시 스타일리스트 ID별 설정 버전 JSON 객체",
    )
    persona_prompt_versions = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_persona_prompt_version_snapshot],
        db_comment="실행 접수 당시 스타일리스트 ID별 프롬프트 버전 JSON 객체",
    )
    stylist_config_version = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_comment="실행 접수 당시 스타일리스트 설정 파일 스키마 버전",
    )
    personalization_snapshot = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_personalization_snapshot],
        db_comment=(
            "실행 접수 당시 개인화 원천별 행 수·마지막 변경 시각·설정 지문 "
            "JSON (기존 실행은 빈 객체)"
        ),
    )
    reference_snapshot = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_reference_snapshot],
        db_comment=(
            "실행 접수 당시 공유 옷장 참조 아이템·이미지·태그·벡터 위치 JSON "
            "(참조가 없거나 기존 실행이면 빈 객체, 원본 벡터는 저장하지 않음)"
        ),
    )
    wardrobe_scope_snapshot = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_wardrobe_scope_snapshot],
        db_comment=(
            "실행 접수 당시 개인 옷장 기본 카테고리·해시태그 범위와 후보 아이템 JSON "
            "(범위가 없거나 기존 실행이면 빈 객체)"
        ),
    )
    degradation = models.JSONField(
        default=dict,
        blank=True,
        db_comment=(
            "응답 품질 저하 기록 JSON (설명 LLM 실패로 규칙 문구를 쓴 경우의 여부·사유 등, "
            "운영 관측용이며 사용자에게 노출하지 않음)"
        ),
    )
    enqueued_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="Redis pending 큐 적재 확인 시각 (미적재 또는 적재 확인 전이면 NULL)",
    )
    context_fingerprint = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_comment="요청·프로필·옷장·날씨·인덱스·세션 조건 기반 SHA-256 지문",
    )
    context_cache_hit = models.BooleanField(
        default=False,
        db_comment="Redis 기본 컨텍스트 캐시 적중 여부",
    )
    provider = models.CharField(
        max_length=32,
        default="openai",
        db_comment="텍스트 LLM 제공자 코드 (기본 openai)",
    )
    model = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_comment="실행에 사용한 텍스트 LLM 모델명",
    )
    prompt_version = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_comment="실행에 사용한 오케스트레이터 프롬프트 버전",
    )
    provider_response_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_comment="마지막 OpenAI Responses API 응답 ID",
    )
    input_tokens = models.PositiveIntegerField(
        default=0,
        db_comment="실행 전체 OpenAI 입력 토큰 수",
    )
    cached_input_tokens = models.PositiveIntegerField(
        default=0,
        db_comment="실행 전체 OpenAI 캐시 적중 입력 토큰 수",
    )
    output_tokens = models.PositiveIntegerField(
        default=0,
        db_comment="실행 전체 OpenAI 출력 토큰 수",
    )
    latency_ms = models.PositiveIntegerField(
        default=0,
        db_comment="오케스트레이터 실행 전체 지연시간 (ms)",
    )
    error_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_comment="실패 오류 코드 (성공이면 빈 문자열)",
    )
    error_message = models.CharField(
        max_length=500,
        blank=True,
        default="",
        db_comment="민감정보를 제거한 운영용 실패 요약 (성공이면 빈 문자열)",
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="오케스트레이터 처리를 시작한 시각",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="성공·추가질문·실패로 처리가 종료된 시각",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="채팅 실행 생성 시각",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="채팅 실행 마지막 수정 시각",
    )

    class Meta:
        db_table = "chat_run"
        db_table_comment = (
            "사용자 메시지별 응답 상태 스냅샷과 오케스트레이터·LLM·오류 추적"
        )
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["session", "status", "-created_at"],
                name="ix_chat_run_session_status",
            ),
            models.Index(
                fields=["context_fingerprint"],
                name="ix_chat_run_context_fp",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    status__in=[
                        "PENDING",
                        "RUNNING",
                        "NEEDS_CLARIFICATION",
                        "SUCCEEDED",
                        "FAILED",
                    ]
                ),
                name="ck_chat_run_status",
            ),
            models.CheckConstraint(
                condition=Q(response_mode__in=["DEFAULT", "STYLIST"]),
                name="ck_chat_run_response_mode",
            ),
            models.CheckConstraint(
                condition=(Q(response_mode="DEFAULT") | ~Q(persona_ids=[])),
                name="ck_chat_run_stylist_ids",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        persona_id_set = (
            set(self.persona_ids) if isinstance(self.persona_ids, list) else set()
        )
        if (
            self.response_mode == ChatSession.ResponseMode.STYLIST
            and not self.persona_ids
        ):
            errors["persona_ids"] = (
                "STYLIST 응답 실행에는 스타일리스트가 1명 이상 필요합니다."
            )
        if isinstance(self.persona_versions, dict) and set(
            self.persona_versions
        ) != persona_id_set:
            errors["persona_versions"] = (
                "설정 버전 스냅샷 키는 선택 스타일리스트 ID와 같아야 합니다."
            )
        if isinstance(self.persona_prompt_versions, dict) and set(
            self.persona_prompt_versions
        ) != persona_id_set:
            errors["persona_prompt_versions"] = (
                "프롬프트 버전 스냅샷 키는 선택 스타일리스트 ID와 같아야 합니다."
            )
        if self.persona_ids and not self.stylist_config_version:
            errors["stylist_config_version"] = (
                "스타일리스트 선택이 있으면 설정 스키마 버전이 필요합니다."
            )
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"chat-run {self.id} ({self.status})"


class ChatRunPersona(models.Model):
    """한 ChatRun 안에서 독립적으로 처리되는 스타일리스트 실행 상태."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "처리 대기"
        RUNNING = "RUNNING", "처리 중"
        SUCCEEDED = "SUCCEEDED", "성공"
        FAILED = "FAILED", "실패"

    class AlternativeStatus(models.TextChoices):
        IDLE = "IDLE", "요청 없음"
        PENDING = "PENDING", "다른 추천 대기"
        RUNNING = "RUNNING", "다른 추천 처리 중"
        SUCCEEDED = "SUCCEEDED", "다른 추천 성공"
        FAILED = "FAILED", "다른 추천 실패"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="스타일리스트별 채팅 실행 UUID",
    )
    run = models.ForeignKey(
        ChatRun,
        on_delete=models.CASCADE,
        related_name="persona_executions",
        db_comment="상위 채팅 실행 FK (chat_run.id)",
    )
    persona_id = models.CharField(
        max_length=32,
        validators=[validate_stylist_persona_id],
        db_comment="실행할 스타일리스트 고정 ID (minimal/experimental/practical)",
    )
    persona_version = models.PositiveIntegerField(
        db_comment="ChatRun에 고정된 스타일리스트 설정 버전 (1 이상)",
    )
    prompt_version = models.CharField(
        max_length=64,
        db_comment="ChatRun에 고정된 스타일리스트 프롬프트 버전",
    )
    display_order = models.PositiveSmallIntegerField(
        db_comment="스타일리스트 고정 표시 순서 (minimal=1/experimental=2/practical=3)",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_comment="스타일리스트별 실행 상태 (PENDING/RUNNING/SUCCEEDED/FAILED)",
    )
    latency_ms = models.PositiveIntegerField(
        default=0,
        db_comment="스타일리스트별 추천 실행 지연시간 (ms)",
    )
    error_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_comment="스타일리스트 실행 실패 오류 코드 (실패가 아니면 빈 문자열)",
    )
    error_message = models.CharField(
        max_length=500,
        blank=True,
        default="",
        db_comment="민감정보를 제거한 스타일리스트 실행 실패 요약",
    )
    strategy_snapshot = models.JSONField(
        default=dict,
        blank=True,
        db_comment="실행 접수 당시 스타일리스트 추천 전략 설정 JSON 스냅샷",
    )
    hypothesis_snapshot = models.JSONField(
        default=dict,
        blank=True,
        db_comment="실험형 검색 가설과 fallback 판단 JSON 스냅샷",
    )
    retry_count = models.PositiveSmallIntegerField(
        default=0,
        db_comment="해당 스타일리스트 실행 재시도 횟수 (최초 실행은 0)",
    )
    error_history = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_persona_error_history],
        db_comment="스타일리스트 재시도 전 오류 이력 JSON 배열 (시각·코드·메시지)",
    )
    alternative_status = models.CharField(
        max_length=16,
        choices=AlternativeStatus.choices,
        default=AlternativeStatus.IDLE,
        db_comment="다른 추천 요청 상태 (IDLE/PENDING/RUNNING/SUCCEEDED/FAILED)",
    )
    alternative_count = models.PositiveSmallIntegerField(
        default=0,
        db_comment="해당 스타일리스트의 다른 추천 요청 횟수",
    )
    alternative_error_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_comment="마지막 다른 추천 실패 오류 코드 (성공 또는 미요청이면 빈 문자열)",
    )
    alternative_error_message = models.CharField(
        max_length=500,
        blank=True,
        default="",
        db_comment="마지막 다른 추천 실패 안내 (성공 또는 미요청이면 빈 문자열)",
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="스타일리스트별 추천 처리를 시작한 시각",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="스타일리스트별 추천 처리가 성공 또는 실패로 종료된 시각",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="스타일리스트별 실행 상태 행 생성 시각",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="스타일리스트별 실행 상태 마지막 변경 시각",
    )

    class Meta:
        db_table = "chat_run_persona"
        db_table_comment = "채팅 실행에 속한 스타일리스트별 독립 추천 상태와 오류·전략"
        ordering = ["display_order"]
        indexes = [
            models.Index(
                fields=["run", "status"],
                name="ix_chat_run_persona_status",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "persona_id"],
                name="uq_chat_run_persona_id",
            ),
            models.UniqueConstraint(
                fields=["run", "display_order"],
                name="uq_chat_run_persona_order",
            ),
            models.CheckConstraint(
                condition=Q(
                    status__in=["PENDING", "RUNNING", "SUCCEEDED", "FAILED"]
                ),
                name="ck_chat_run_persona_status",
            ),
            models.CheckConstraint(
                condition=Q(
                    alternative_status__in=[
                        "IDLE",
                        "PENDING",
                        "RUNNING",
                        "SUCCEEDED",
                        "FAILED",
                    ]
                ),
                name="ck_chat_run_persona_alt_status",
            ),
            models.CheckConstraint(
                condition=Q(persona_version__gte=1),
                name="ck_chat_run_persona_version",
            ),
            models.CheckConstraint(
                condition=Q(display_order__gte=1, display_order__lte=3),
                name="ck_chat_run_persona_order",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if not self.run_id or not isinstance(self.persona_id, str):
            return

        catalog = load_stylist_personas()
        if self.persona_id not in catalog.supported_persona_ids:
            return
        persona = catalog.get(self.persona_id)
        errors: dict[str, str] = {}
        if self.persona_id not in self.run.persona_ids:
            errors["persona_id"] = "상위 ChatRun이 선택한 스타일리스트가 아닙니다."
        expected_version = self.run.persona_versions.get(self.persona_id)
        if self.persona_version != expected_version:
            errors["persona_version"] = (
                "스타일리스트 설정 버전이 상위 ChatRun 스냅샷과 다릅니다."
            )
        expected_prompt_version = self.run.persona_prompt_versions.get(
            self.persona_id
        )
        if self.prompt_version != expected_prompt_version:
            errors["prompt_version"] = (
                "프롬프트 버전이 상위 ChatRun 스냅샷과 다릅니다."
            )
        if self.display_order != persona.display_order:
            errors["display_order"] = "스타일리스트 고정 표시 순서와 다릅니다."
        if errors:
            raise ValidationError(errors)

    @property
    def recommendation_result(self):
        """기존 단건 접근 계약을 현재 노출 결과로 유지한다."""

        from apps.recommend.models import RecommendationResult

        prefetched = getattr(self, "current_recommendation_results", None)
        if prefetched is not None:
            result = next(iter(prefetched), None)
        else:
            result = self.recommendation_results.filter(is_current=True).first()
        if result is None:
            raise RecommendationResult.DoesNotExist
        return result

    def __str__(self) -> str:
        return f"chat-run-persona {self.run_id}:{self.persona_id} ({self.status})"
