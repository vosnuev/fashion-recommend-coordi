"""사용자 및 소셜 계정 모델.

이메일·비밀번호 및 소셜 로그인(naver/kakao/google)을 지원한다.
- User: 서비스 내부 식별/프로필. username은 로그인 방식별 내부 식별자로 생성된다.
- SocialAccount: 제공사별 계정 연결. 한 User가 여러 제공사를 연결할 수 있다.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.contrib.auth.models import AbstractUser, Permission
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    # 이메일 계정은 password를 사용하고, 소셜 전용 계정은 unusable password를 저장한다.
    nickname = models.CharField(
        "닉네임", max_length=100, blank=True, db_comment="서비스 표시 닉네임 (소셜 프로필에서 초기화)"
    )
    profile_image = models.URLField(
        "프로필 이미지", blank=True, db_comment="프로필 이미지 URL (소셜 프로필에서 초기화)"
    )
    # 사용자가 직접 올린 사진은 우리 S3 에 있고 presigned URL 로만 꺼낼 수 있어,
    # 만료되는 URL 대신 key 를 저장한다. 값이 있으면 위 profile_image(소셜 URL)보다 앞선다.
    profile_image_key = models.CharField(
        "프로필 이미지 S3 키",
        max_length=255,
        blank=True,
        db_comment="사용자가 올린 프로필 사진의 S3 key (있으면 profile_image URL 보다 우선)",
    )

    legacy_monthly_budget = models.PositiveIntegerField(
        "이전 월 의류 구매 예산",
        null=True,
        blank=True,
        editable=False,
        db_comment="이전 단일 월 의류 구매 예산(원), 신규 추천에서는 사용하지 않음",
    )
    category_budgets = models.JSONField(
        "카테고리별 상품 예산",
        default=dict,
        blank=True,
        db_comment="대분류별 상품 1개 최대 가격(원) JSON, 미설정 카테고리는 키 없음",
    )

    # PermissionsMixin의 필드를 재정의해 자동 M2M 테이블명(users_user_permissions)을
    # users_permissions로 단순화한다. db_table 외 옵션은 원본과 동일하게 유지한다.
    user_permissions = models.ManyToManyField(
        Permission,
        verbose_name=_("user permissions"),
        blank=True,
        help_text=_("Specific permissions for this user."),
        related_name="user_set",
        related_query_name="user",
        db_table="users_permissions",
    )

    class Meta:
        db_table = "users"
        db_table_comment = "서비스 사용자 (이메일·비밀번호 또는 소셜 로그인 계정)"
        verbose_name = "사용자"
        verbose_name_plural = "사용자"

    def __str__(self) -> str:
        return self.nickname or self.username


class EmailVerification(models.Model):
    """이메일 계정의 소유 확인 상태와 일회용 인증 코드 메타데이터."""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="email_verification",
        db_comment="인증 대상 사용자 FK (users.id, 사용자당 1건)",
    )
    code_hash = models.CharField(
        "인증 코드 해시",
        max_length=64,
        blank=True,
        db_comment="6자리 이메일 인증 코드의 HMAC-SHA256 해시",
    )
    expires_at = models.DateTimeField(
        "인증 코드 만료 시각",
        null=True,
        blank=True,
        db_comment="현재 인증 코드 만료 시각 (기본 발송 후 10분)",
    )
    resend_available_at = models.DateTimeField(
        "재발송 가능 시각",
        null=True,
        blank=True,
        db_comment="인증 메일 재발송 제한 종료 시각 (기본 발송 후 60초)",
    )
    failed_attempts = models.PositiveSmallIntegerField(
        "인증 실패 횟수",
        default=0,
        db_comment="현재 코드 검증 실패 횟수 (최대 5회)",
    )
    verified_at = models.DateTimeField(
        "이메일 인증 완료 시각",
        null=True,
        blank=True,
        db_comment="이메일 소유 확인 완료 시각 (미인증이면 NULL)",
    )
    created_at = models.DateTimeField(
        "생성 시각", auto_now_add=True, db_comment="인증 레코드 최초 생성 시각"
    )
    updated_at = models.DateTimeField(
        "수정 시각", auto_now=True, db_comment="인증 레코드 마지막 수정 시각"
    )

    class Meta:
        db_table = "email_verifications"
        db_table_comment = "이메일 계정 소유 확인용 일회성 코드와 인증 상태"
        verbose_name = "이메일 인증"
        verbose_name_plural = "이메일 인증"


class SocialAccount(models.Model):
    class Provider(models.TextChoices):
        NAVER = "naver", "네이버"
        KAKAO = "kakao", "카카오"
        GOOGLE = "google", "구글"
        APPLE = "apple", "애플"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="social_accounts",
        db_comment="연결된 서비스 사용자 FK (users.id)",
    )
    provider = models.CharField(
        "제공사", max_length=20, choices=Provider.choices,
        db_comment="소셜 제공사 (naver/kakao/google/apple)",
    )
    provider_user_id = models.CharField(
        "제공사 유저 ID", max_length=255, db_comment="제공사가 발급한 사용자 고유 ID"
    )
    email = models.EmailField("제공사 이메일", blank=True, db_comment="제공사 프로필 이메일")
    # 제공사 원본 프로필 (디버깅/추가 필드 대비)
    extra_data = models.JSONField(
        "원본 프로필", default=dict, blank=True, db_comment="제공사 원본 프로필 JSON (디버깅/추가 필드 대비)"
    )
    connected_at = models.DateTimeField(
        "연결 시각", auto_now_add=True, db_comment="계정 최초 연결 시각"
    )
    last_login_at = models.DateTimeField(
        "마지막 로그인", auto_now=True, db_comment="이 제공사로 마지막 로그인한 시각"
    )

    class Meta:
        db_table = "social_accounts"
        db_table_comment = "소셜 로그인 계정 연결 (사용자 1명이 여러 제공사 연결 가능)"
        verbose_name = "소셜 계정"
        verbose_name_plural = "소셜 계정"
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_user_id"],
                name="uq_social_provider_user",
            )
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.provider_user_id}"


def _measure_field(label: str) -> models.DecimalField:
    """신체 수치 필드 (cm/kg). 소수점 1자리, 1~999.9 범위. label이 컬럼 comment가 된다."""
    return models.DecimalField(
        label,
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("1"))],
        help_text=label,
        db_comment=label,
    )


class BodyMeasurement(models.Model):
    """사용자 신체치수 (설정 페이지 입력값). 사용자당 1행.

    기본 수치(성별/키/몸무게), 상세 치수와 체형 지표를 한 행으로 관리한다.
    상세 수치와 체형 지표는 전부 선택 입력이라 null을 허용하며, 사진 기반
    추론 기능이 같은 컬럼을 추론값으로 갱신하는 것을 전제로 한다.
    """

    class Gender(models.TextChoices):
        MALE = "male", "남성"
        FEMALE = "female", "여성"

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="body_measurement",
        db_comment="대상 사용자 FK (users.id, 사용자당 1행)",
    )
    # 기본 수치 — API(PUT body/basic)에서는 세 값 모두 필수.
    # gender는 기존 행 호환을 위해 DB에서만 빈 문자열을 허용한다 (신규 저장은
    # serializer가 필수로 강제). 미입력 상태 = "".
    gender = models.CharField(
        "성별", max_length=10, choices=Gender.choices, blank=True,
        db_comment="성별 (male/female, 미입력 시 빈 문자열)",
    )
    height = _measure_field("키(cm)")
    weight = _measure_field("몸무게(kg)")
    # 상세 수치 (전부 선택). 추천용 체형 지표이므로 정밀 의료 실측이 아니라
    # 옷 핏과 실루엣 판단에 쓰는 길이/비율로 해석한다.
    # 2026-08-12: 둘레 계약(thigh/calf/arm)은 그대로 두고 길이 4개를 **추가**했다.
    # 화면에는 둘레만 노출하고, 길이는 서버가 함께 추정해 응답에만 실어 준다.
    chest = _measure_field("가슴둘레(cm)")
    waist = _measure_field("허리둘레(cm)")
    hip = _measure_field("엉덩이둘레(cm)")
    thigh = _measure_field("허벅지둘레(cm)")
    calf = _measure_field("종아리둘레(cm)")
    arm = _measure_field("팔뚝둘레(cm)")
    shoulder = _measure_field("어깨너비(cm)")
    thigh_length = _measure_field("패션용 허벅지 길이감(cm, 샅선/인심 라인→무릎뼈)")
    calf_length = _measure_field("패션용 종아리 길이감(cm, 무릎뼈→복사뼈/발목)")
    torso_length = _measure_field("패션용 상체 길이감(cm, 어깨선→골반점)")
    leg_length = _measure_field("패션용 하체 길이감(cm, 골반점/위앞엉덩뼈가시→복사뼈/발목)")

    # 체형 분류에 사용하는 길이·비율 지표
    neck_length = models.DecimalField(
        "패션용 목 길이감(cm)",
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("1"))],
        help_text="패션용 목 길이감(cm, 정면 기준 턱밑/턱끝 라인→목앞/쇄골 라인)",
        db_comment="패션용 목 길이감(cm, 정면 기준 턱밑/턱끝 라인→목앞/쇄골 라인)",
    )
    thigh_calf_ratio = models.DecimalField(
        "허벅지/종아리 길이감 비율",
        max_digits=5,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(Decimal("0.1")),
            MaxValueValidator(Decimal("9.999")),
        ],
        help_text="패션용 허벅지 길이감 / 종아리 길이감 비율 (정확 3D 랜드마크 SizeKorea 평균 0.823, p01~p99 약 0.652~0.970)",
        db_comment="패션용 허벅지 길이감 / 종아리 길이감 비율",
    )
    torso_leg_ratio = models.DecimalField(
        "상하체 길이감 비율",
        max_digits=5,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(Decimal("0.1")),
            MaxValueValidator(Decimal("9.999")),
        ],
        help_text="패션용 상체 길이감 / 하체 길이감 비율 (상체=어깨선→골반점, 하체=골반점→복사뼈; 정확 3D 랜드마크 SizeKorea 평균 0.546, p01~p99 약 0.466~0.637)",
        db_comment="패션용 상체 길이감 / 하체 길이감 비율",
    )

    created_at = models.DateTimeField(
        "생성 시각", auto_now_add=True, db_comment="행 생성 시각"
    )
    updated_at = models.DateTimeField("수정 시각", auto_now=True, db_comment="행 수정 시각")

    class Meta:
        db_table = "body_measurements"
        db_table_comment = "사용자 신체치수 (기본 정보·상세 치수·체형 지표, 사용자당 1행)"
        verbose_name = "신체치수"
        verbose_name_plural = "신체치수"

    def __str__(self) -> str:
        return f"{self.user_id}의 신체치수"


class BodyPhotoTransaction(models.Model):
    """사진 기반 신체치수 측정 트랜잭션.

    사진 등록 API가 접수 시 '진행중'으로 생성하고, 비동기 측정이 끝나면
    성공/실패로 갱신한다. 사용자당 진행중 트랜잭션은 1건만 허용한다
    (부분 유니크 제약 — 동시 요청 경합도 DB에서 차단).
    """

    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "진행중"
        SUCCEEDED = "succeeded", "성공"
        FAILED = "failed", "실패"

    # 외부(프론트)에 노출되는 식별자라 순번 노출이 없는 UUID를 쓴다.
    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False,
        db_comment="트랜잭션 UUID (외부 노출 식별자)",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="body_photo_transactions",
        db_comment="요청 사용자 FK (users.id)",
    )
    status = models.CharField(
        "상태", max_length=20, choices=Status.choices, default=Status.IN_PROGRESS,
        db_comment="측정 상태 (in_progress/succeeded/failed, 사용자당 진행중 1건)",
    )
    # 실패 원인을 남기지 않으면 프론트가 재시도 안내를 못 한다. VLM 호출은 타임아웃·
    # 응답 길이 초과 등으로 실제 실패하므로(검증 39명 중 1건) 사유를 함께 보관한다.
    error_message = models.TextField(
        "실패 사유", blank=True, default="",
        db_comment="측정 실패 사유 (성공/진행중이면 빈 문자열)",
    )
    error_code = models.CharField(
        "실패 코드",
        max_length=50,
        blank=True,
        default="",
        db_comment="클라이언트 분기용 실패 코드 (사진 품질 실패: photo_quality_failed)",
    )
    created_at = models.DateTimeField(
        "생성 시각", auto_now_add=True, db_comment="접수 시각"
    )
    updated_at = models.DateTimeField(
        "수정 시각", auto_now=True, db_comment="상태 변경 시각"
    )

    class Meta:
        db_table = "body_photo_transactions"
        db_table_comment = "사진 기반 신체치수 측정 트랜잭션 (접수 시 진행중 생성 → 비동기 완료 시 성공/실패)"
        verbose_name = "사진 측정 트랜잭션"
        verbose_name_plural = "사진 측정 트랜잭션"
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="in_progress"),
                name="uq_body_photo_tx_in_progress",
            )
        ]

    def __str__(self) -> str:
        return f"{self.id} ({self.status})"
    

class PreferenceOption(models.Model):
    """옵션 마스터. 11개 카테고리 X N개 옵션. 
    
    카테고리별로 (category, code) 조합이 유일. 
    label은 화면에 보일 한글 이름. 
    meta에는 색상 hex, 아이콘 이름 등 부가 정보를 JSON으로 저장.
    """

    # 화면 카테고리 순서 (프론트 정의 순서와 일치)
    CATEGORY_CHOICES = [
        ("seasons", "계절"),
        ("styles", "스타일"),
        ("colors", "색상"),
        ("necklines", "넥라인"),
        ("top_fits", "상의핏"),
        ("top_lengths", "상의기장"),
        ("sleeves", "소매길이"),
        ("pants_fits", "팬츠핏"),
        ("pants_lengths", "팬츠기장"),
        ("skirt_lengths", "스커트기장"),
        ("skirt_types", "스커트타입"),
    ]

    category = models.CharField(
        "카테고리", max_length=50, choices=CATEGORY_CHOICES,
        db_comment="옵션 카테고리 (seasons/styles/colors 등 11종)",
    )
    code = models.CharField(
        "옵션 코드", max_length=50, db_comment="옵션 코드 (카테고리 내 유일, API 페이로드 값)"
    )
    label = models.CharField("라벨", max_length=50, db_comment="화면 표시용 한글 라벨")
    order = models.PositiveIntegerField(
        "정렬 순서", default=0, db_comment="카테고리 내 표시 순서"
    )
    meta = models.JSONField(
        "메타데이터", default=dict, blank=True,
        db_comment='부가 정보 JSON (예: 색상 {"color_hex": "#000000"})',
    )
    # meta(색상) 예: {"color_hex": "#000000"} for colors, {} for others

    created_at = models.DateTimeField(
        "생성 시각", auto_now_add=True, db_comment="행 생성 시각"
    )
    updated_at = models.DateTimeField("수정 시각", auto_now=True, db_comment="행 수정 시각")

    class Meta:
        db_table = "preference_options"
        db_table_comment = "추구미 선호도 옵션 마스터 (11개 카테고리 × N개 옵션)"
        verbose_name = "선호도 옵션"
        verbose_name_plural = "선호도 옵션"
        constraints = [
            models.UniqueConstraint(
                fields=["category", "code"],
                name="uq_preference_option_category_code",
            )
        ]
        ordering = ["category", "order", "id"]

    def __str__(self) -> str:
        return f"[{self.category}] {self.label} ({self.code})"


class Pursuit(models.Model):
    """사용자 스타일 선호/기피. 1행 = 1 user.

    payload 구조 (JSONField 한 컬럼에 통째로):
        {
            "preferred": {
                "seasons": ["spring"],
                "styles": ["minimal", "casual"],
                "colors": ["black", "navy"],
                ...
            },
            "avoided": {
                "seasons": [],
                "styles": [],
                ...
            }
        }

    각 카테고리 키는 PreferenceOption.CATEGORY_CHOICES 와 일치해야 함.
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="pursuit",
        db_comment="대상 사용자 FK (users.id, 사용자당 1행)",
    )

    payload = models.JSONField(
        "선호/기피 데이터", default=dict, blank=True,
        db_comment="선호/기피 선택 JSON ({preferred: {카테고리: [코드...]}, avoided: {...}})",
    )
    # 빈 payload는 {"preferred": {}, "avoided": {}} 형태로 normalize.

    created_at = models.DateTimeField(
        "생성 시각", auto_now_add=True, db_comment="행 생성 시각"
    )
    updated_at = models.DateTimeField("수정 시각", auto_now=True, db_comment="행 수정 시각")

    class Meta:
        db_table = "pursuits"
        db_table_comment = "사용자 추구미 (스타일 선호/기피 선택, 사용자당 1행)"
        verbose_name = "추구미"
        verbose_name_plural = "추구미"

    def __str__(self) -> str:
        return f"{self.user_id}의 추구미"
    
