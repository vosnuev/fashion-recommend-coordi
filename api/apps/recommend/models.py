"""코디 사진 AI 평가 기록 모델.

`POST /api/v1/outfits/analyze/` 요청 1건 = `OutfitAnalysis` 1행.

설계 원칙
- **스냅샷 저장**: 날씨·체형·추구미는 FK로 참조하지 않고 요청 시점 값을 복사해 둔다.
  프로필과 날씨는 계속 바뀌므로, 참조만 두면 "왜 이 평가가 나왔는지"를 나중에
  재현할 수 없다.
- **질의와 응답을 함께 보관**: LLM에 보낸 요청 본문과 원본 응답을 그대로 남겨
  프롬프트·모델 교체 전후의 평가 품질을 비교할 수 있게 한다.
- **이미지는 S3**: 원본 사진은 DB에 넣지 않고 S3 키만 저장한다 (wardrobe와 동일).
- **옷장 등록은 곁가지**: 로그인 사용자가 원하면 같은 사진을 옷장 아이템 등록
  파이프라인에도 넘긴다. 실패해도 코디 평가는 그대로 진행된다 (services/wardrobe_link.py).
- **익명 접수는 나중에 소유권을 넘길 수 있다**: 로그인 후 claim하면 user가 채워진다.
  이때 평가 자체는 다시 하지 않으므로, 개인화 없이 나온 결과라는 사실을
  `accepted_anonymously`로 남긴다 (services/claim.py).
- **익명 요청도 기록**: 이 API는 AllowAny라 user가 NULL인 행이 정상적으로 존재한다.
  익명 행은 소유자를 특정할 수 없어 UUID를 아는 사람만 조회할 수 있고(뷰에서 제어),
  일정 시간이 지나면 조회를 막는다.

테이블·컬럼 comment는 db_table_comment/db_comment로 모델이 소유한다
(새 필드 추가 시 반드시 db_comment 지정 — CLAUDE.md 5장).
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class OutfitAnalysis(models.Model):
    """코디 사진 1장에 대한 LLM 평가 요청·결과 1건."""

    class Status(models.TextChoices):
        QUEUED = "QUEUED", "대기중"
        PROCESSING = "PROCESSING", "평가 진행중"
        SUCCEEDED = "SUCCEEDED", "평가 완료"
        FAILED = "FAILED", "평가 실패"

    #: 아직 결과가 나오지 않아 클라이언트가 폴링을 계속해야 하는 상태
    PENDING_STATUSES = (Status.QUEUED, Status.PROCESSING)

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="평가 UUID (외부 노출 식별자)",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="outfit_analyses",
        # FK 기본 인덱스는 아래 (user, -created_at) 복합 인덱스와 선두 컬럼이 겹쳐 불필요하다
        db_index=False,
        db_comment="요청 사용자 FK (users.id, 비로그인 요청이면 NULL)",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
        db_comment="평가 상태 (QUEUED/PROCESSING/SUCCEEDED/FAILED)",
    )

    # ── 입력(사진·위치) ──
    image_s3_key = models.CharField(
        "원본 사진 S3 키",
        max_length=512,
        blank=True,
        default="",
        db_comment="평가 대상 코디 사진 S3 키 (업로드 미설정 또는 실패 시 빈 문자열)",
    )
    image_content_type = models.CharField(
        max_length=50,
        blank=True,
        default="",
        db_comment="업로드 이미지 MIME 타입 (image/jpeg, image/png, image/webp)",
    )
    image_bytes = models.PositiveIntegerField(
        null=True, blank=True, db_comment="업로드 이미지 크기 (bytes)"
    )
    requested_lat = models.FloatField(
        null=True, blank=True, db_comment="요청 위도 (클라이언트가 보낸 값, 미전달 시 NULL)"
    )
    requested_lon = models.FloatField(
        null=True, blank=True, db_comment="요청 경도 (클라이언트가 보낸 값, 미전달 시 NULL)"
    )
    resolved_lat = models.FloatField(
        null=True,
        blank=True,
        db_comment="날씨 조회에 실제 사용한 위도 (미전달·국내 범위 밖이면 서울 좌표로 대체)",
    )
    resolved_lon = models.FloatField(
        null=True,
        blank=True,
        db_comment="날씨 조회에 실제 사용한 경도 (미전달·국내 범위 밖이면 서울 좌표로 대체)",
    )

    # ── LLM 질의 구성 정보 (요청 시점 스냅샷) ──
    weather = models.JSONField(
        "날씨 스냅샷",
        default=dict,
        blank=True,
        db_comment="질의에 사용한 날씨 JSON (region/temperature/sky_state/is_stale/observed_at)",
    )
    body = models.JSONField(
        "신체치수 스냅샷",
        null=True,
        blank=True,
        db_comment="질의에 사용한 신체치수·성별 JSON (비로그인 또는 미등록이면 NULL)",
    )
    pursuit = models.JSONField(
        "추구미 스냅샷",
        null=True,
        blank=True,
        db_comment="질의에 사용한 추구미 JSON (preferred/avoided, 비로그인이면 NULL)",
    )
    personalized = models.BooleanField(
        default=False,
        db_comment="개인화 정보 반영 여부 (로그인 요청이면 true)",
    )

    # ── 익명 접수 · 소유권 이전 ──
    accepted_anonymously = models.BooleanField(
        default=False,
        db_comment=(
            "접수 시점에 비로그인이었는지 여부 "
            "(소유권 이전 후에도 유지 — 개인화 없이 평가된 기록임을 구분한다)"
        ),
    )
    claimed_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="익명 접수 건의 소유권이 사용자에게 이전된 시각 (감사용)",
    )
    # ── 옷장 등록 연계 (선택) ──
    save_to_wardrobe = models.BooleanField(
        default=False,
        db_comment="이 사진을 옷장 아이템 등록에도 넘길지 여부 (비로그인 요청이면 항상 false)",
    )
    wardrobe_job = models.ForeignKey(
        "wardrobe.WardrobeUploadJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="outfit_analyses",
        db_comment="연계 생성한 옷장 등록 job FK (wardrobe_upload_job.id, 미요청·적재 실패 시 NULL)",
    )

    # ── LLM 호출·응답 ──
    llm_model = models.CharField(
        max_length=80,
        blank=True,
        default="",
        db_comment="평가에 사용한 LLM 모델명 (예: gemini-3.5-flash)",
    )
    request_payload = models.JSONField(
        "LLM 요청 본문",
        default=dict,
        blank=True,
        db_comment="LLM에 보낸 요청 본문 JSON 전체 (사진 base64는 자리표시자로 대체)",
    )
    response_payload = models.JSONField(
        "LLM 원본 응답",
        default=dict,
        blank=True,
        db_comment="LLM 원본 응답 JSON 전체 (candidates/usageMetadata 등, 실패 시 오류 본문)",
    )
    evaluation = models.JSONField(
        "평가 결과",
        null=True,
        blank=True,
        db_comment="파싱된 평가 결과 JSON (API 응답의 evaluation 필드와 동일, 실패 시 NULL)",
    )
    llm_image_bytes = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_comment="LLM에 실제 전송한 축소본 크기 (bytes, image_bytes는 원본 크기)",
    )
    latency_ms = models.PositiveIntegerField(
        null=True, blank=True, db_comment="LLM 호출 소요 시간 (밀리초)"
    )
    attempts = models.PositiveSmallIntegerField(
        default=0, db_comment="워커 처리 시도 횟수 (재시도 포함)"
    )
    error_message = models.TextField(
        blank=True, default="", db_comment="실패 시 오류 메시지"
    )

    created_at = models.DateTimeField(
        auto_now_add=True, db_comment="요청 접수 시각"
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="워커가 처리를 시작한 시각 (큐 대기시간 측정·좀비 정리 기준)",
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="평가 종료 시각 (SUCCEEDED/FAILED 전환 시)",
    )

    class Meta:
        # 프로젝트 규칙: db_table 명시 (기본값이면 recommend_outfitanalysis)
        db_table = "outfit_analysis"
        db_table_comment = (
            "코디 사진 AI 평가 기록 (질의에 쓴 날씨·체형·추구미 스냅샷과 LLM 요청·응답 원본 보관)"
        )
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="ix_outfit_analysis_user"),
            models.Index(fields=["status", "-created_at"], name="ix_outfit_analysis_stat"),
        ]

    def __str__(self) -> str:
        return f"outfit-analysis {self.id} ({self.status})"

    @property
    def overall_score(self) -> int | None:
        """목록 응답에서 쓰는 요약 점수. 평가 실패 행이면 None."""
        return (self.evaluation or {}).get("overall_score")

    @property
    def is_pending(self) -> bool:
        return self.status in self.PENDING_STATUSES

    @property
    def is_claimable(self) -> bool:
        """아직 주인이 없는 익명 접수 건인지. TTL 검사는 claim 서비스가 따로 한다."""
        return self.user_id is None

    def llm_context(self) -> dict:
        """LLM 질의에 넣을 컨텍스트를 접수 시점 스냅샷에서 복원한다.

        워커는 이 값을 쓰고 컨텍스트를 다시 만들지 않는다. 큐에서 대기하는 사이
        날씨가 바뀌거나 사용자가 추구미를 수정해도, 사용자가 사진을 올린 그 순간의
        조건으로 평가해야 결과와 기록이 일치한다.
        """
        return {
            "weather": self.weather or {},
            "pursuit": self.pursuit,
            "body": self.body,
            "personalized": self.personalized,
        }


class RecommendationResult(models.Model):
    """기본 응답 하나 또는 스타일리스트 한 명의 확정 추천 결과."""

    class Mode(models.TextChoices):
        WARDROBE_BASED = "WARDROBE_BASED", "옷장 기반 추천"
        NEW_ITEM = "NEW_ITEM", "신규 상품 포함 추천"

    class ResponseMode(models.TextChoices):
        DEFAULT = "DEFAULT", "기본 통합 응답"
        STYLIST = "STYLIST", "스타일리스트별 응답"

    class ResultType(models.TextChoices):
        INITIAL = "INITIAL", "최초 추천"
        ALTERNATIVE = "ALTERNATIVE", "다른 추천"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="추천 결과 UUID (외부 노출 식별자)",
    )
    identity = models.ForeignKey(
        "chat.ChatIdentity",
        on_delete=models.CASCADE,
        related_name="recommendation_results",
        db_column="identity_id",
        db_comment="추천 결과를 소유한 회원 또는 게스트 채팅 identity FK (chat_identity.id)",
    )
    session = models.ForeignKey(
        "chat.ChatSession",
        on_delete=models.CASCADE,
        related_name="recommendation_results",
        db_column="session_id",
        db_comment="추천이 생성된 채팅 세션 FK (chat_session.id)",
    )
    run = models.ForeignKey(
        "chat.ChatRun",
        on_delete=models.CASCADE,
        related_name="recommendation_results",
        db_column="run_id",
        db_comment="추천을 생성한 채팅 실행 FK (chat_run.id)",
    )
    persona_execution = models.ForeignKey(
        "chat.ChatRunPersona",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="recommendation_results",
        db_column="persona_execution_id",
        db_comment=(
            "스타일리스트별 실행 FK (chat_run_persona.id, 기본 응답이면 NULL, 재추천 이력 허용)"
        ),
    )
    response_mode = models.CharField(
        max_length=12,
        choices=ResponseMode.choices,
        default=ResponseMode.DEFAULT,
        db_comment="추천 결과 응답 모드 (DEFAULT/STYLIST)",
    )
    persona_id = models.CharField(
        max_length=32,
        blank=True,
        default="",
        db_comment=(
            "결과를 생성한 스타일리스트 고정 ID "
            "(minimal/experimental/practical, 기본 응답이면 빈 문자열)"
        ),
    )
    persona_version = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_comment="결과 생성 당시 스타일리스트 설정 버전 (기본 응답이면 NULL)",
    )
    persona_explanation = models.CharField(
        max_length=500,
        blank=True,
        default="",
        db_comment="확정된 코디를 설명하는 스타일리스트 핵심 문장",
    )
    validated_reason_codes = models.JSONField(
        default=list,
        blank=True,
        db_comment="Validator를 통과한 추천 근거 코드 문자열 JSON 배열",
    )
    strategy_snapshot = models.JSONField(
        default=dict,
        blank=True,
        db_comment="결과 선택에 사용한 스타일리스트 추천 전략 JSON 스냅샷",
    )
    result_type = models.CharField(
        max_length=16,
        choices=ResultType.choices,
        default=ResultType.INITIAL,
        db_comment="추천 결과 생성 목적 (INITIAL/ALTERNATIVE)",
    )
    generation = models.PositiveSmallIntegerField(
        default=1,
        db_comment="동일 run·스타일리스트 안의 추천 결과 세대 (최초 1, 다른 추천마다 증가)",
    )
    is_current = models.BooleanField(
        default=True,
        db_comment="동일 run·스타일리스트에서 현재 노출할 최신 결과 여부",
    )
    replaces = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replaced_by_results",
        db_comment="다른 추천이 교체한 직전 추천 결과 FK (최초 추천이면 NULL)",
    )
    mode = models.CharField(
        max_length=24,
        choices=Mode.choices,
        db_comment="추천 모드 (WARDROBE_BASED/NEW_ITEM)",
    )
    dataset_version = models.CharField(
        max_length=128,
        db_comment="추천에 사용한 골든셋 데이터 버전",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="추천 결과 생성 시각",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="추천 결과 마지막 수정 시각",
    )

    class Meta:
        db_table = "recommendation_result"
        db_table_comment = (
            "채팅과 독립적으로 조회하는 기본 또는 스타일리스트별 추천 결과 "
            "(소유권·실행·전략·근거·골든셋 버전 보관)"
        )
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["identity", "-created_at"],
                name="ix_reco_result_identity",
            ),
            models.Index(
                fields=["session", "-created_at"],
                name="ix_reco_result_session",
            ),
            models.Index(
                fields=["run", "response_mode"],
                name="ix_reco_result_run_mode",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(response_mode__in=["DEFAULT", "STYLIST"]),
                name="ck_reco_result_response_mode",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        response_mode="DEFAULT",
                        persona_id="",
                        persona_version__isnull=True,
                        persona_execution__isnull=True,
                    )
                    | Q(
                        response_mode="STYLIST",
                        persona_id__in=["minimal", "experimental", "practical"],
                        persona_version__gte=1,
                        persona_execution__isnull=False,
                    )
                ),
                name="ck_reco_result_persona_fields",
            ),
            models.UniqueConstraint(
                fields=["run"],
                condition=Q(response_mode="DEFAULT"),
                name="uq_reco_result_default_run",
            ),
            models.UniqueConstraint(
                fields=["run", "persona_id", "generation"],
                condition=Q(response_mode="STYLIST"),
                name="uq_reco_result_run_persona_gen",
            ),
            models.UniqueConstraint(
                fields=["run", "persona_id"],
                condition=Q(response_mode="STYLIST", is_current=True),
                name="uq_reco_result_current_persona",
            ),
            models.CheckConstraint(
                condition=Q(generation__gte=1),
                name="ck_reco_result_generation",
            ),
            models.CheckConstraint(
                condition=Q(result_type__in=["INITIAL", "ALTERNATIVE"]),
                name="ck_reco_result_type",
            ),
        ]

    def __str__(self) -> str:
        scope = self.persona_id or self.response_mode
        return f"recommendation-result {self.id} ({self.mode}/{scope})"

    def clean(self) -> None:
        """소유권과 실행 스냅샷이 서로 다른 결과가 저장되는 것을 막는다."""

        super().clean()
        errors: dict[str, str] = {}
        if self.run_id:
            if self.session_id and self.run.session_id != self.session_id:
                errors["session"] = "추천 결과 세션이 상위 ChatRun 세션과 다릅니다."
            if self.identity_id and self.run.session.identity_id != self.identity_id:
                errors["identity"] = "추천 결과 소유자가 상위 ChatRun 소유자와 다릅니다."
            if self.response_mode != self.run.response_mode:
                errors["response_mode"] = (
                    "추천 결과 응답 모드가 상위 ChatRun 스냅샷과 다릅니다."
                )

        if not isinstance(self.validated_reason_codes, list) or any(
            not isinstance(code, str) or not code.strip()
            for code in self.validated_reason_codes
        ):
            errors["validated_reason_codes"] = (
                "검증 근거 코드는 비어 있지 않은 문자열 배열이어야 합니다."
            )
        elif len(self.validated_reason_codes) != len(
            set(self.validated_reason_codes)
        ):
            errors["validated_reason_codes"] = (
                "검증 근거 코드는 중복될 수 없습니다."
            )
        if not isinstance(self.strategy_snapshot, dict):
            errors["strategy_snapshot"] = "전략 스냅샷은 JSON 객체여야 합니다."

        if self.result_type == self.ResultType.INITIAL:
            if self.generation != 1 or self.replaces_id is not None:
                errors["result_type"] = (
                    "최초 추천은 generation 1이며 교체 전 결과가 없어야 합니다."
                )
        elif self.response_mode != self.ResponseMode.STYLIST:
            errors["result_type"] = "다른 추천 결과는 STYLIST 응답만 지원합니다."
        elif self.generation < 2 or self.replaces_id is None:
            errors["result_type"] = (
                "다른 추천은 generation 2 이상이며 직전 결과를 연결해야 합니다."
            )
        elif (
            self.replaces.run_id != self.run_id
            or self.replaces.persona_id != self.persona_id
            or self.replaces.generation != self.generation - 1
        ):
            errors["replaces"] = (
                "교체 전 결과는 같은 run·스타일리스트의 바로 이전 세대여야 합니다."
            )

        if self.response_mode == self.ResponseMode.STYLIST:
            if not self.persona_execution_id:
                errors["persona_execution"] = (
                    "스타일리스트 결과에는 개별 실행 연결이 필요합니다."
                )
            else:
                execution = self.persona_execution
                if self.run_id and execution.run_id != self.run_id:
                    errors["persona_execution"] = (
                        "개별 스타일리스트 실행의 상위 ChatRun이 다릅니다."
                    )
                if execution.persona_id != self.persona_id:
                    errors["persona_id"] = (
                        "스타일리스트 ID가 개별 실행 스냅샷과 다릅니다."
                    )
                if execution.persona_version != self.persona_version:
                    errors["persona_version"] = (
                        "스타일리스트 버전이 개별 실행 스냅샷과 다릅니다."
                    )
        elif any(
            (
                self.persona_id,
                self.persona_version is not None,
                self.persona_execution_id is not None,
            )
        ):
            errors["response_mode"] = (
                "기본 응답에는 스타일리스트 실행 정보를 저장할 수 없습니다."
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        self.clean()
        super().save(*args, **kwargs)


class GoldenTemplateSnapshot(models.Model):
    """추천 당시 선택한 골든 코디와 검색 근거의 불변 스냅샷."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="골든 템플릿 스냅샷 UUID",
    )
    result = models.OneToOneField(
        RecommendationResult,
        on_delete=models.CASCADE,
        related_name="golden_template",
        db_comment="추천 결과 FK (recommendation_result.id, 결과당 선택 템플릿 1개)",
    )
    golden_id = models.CharField(
        max_length=128,
        db_comment="골든셋 원본 코디 식별자",
    )
    point_id = models.CharField(
        max_length=128,
        db_comment="outfit_goldenset Qdrant point 식별자",
    )
    retrieval_score = models.FloatField(
        db_comment="최종 선택 시 골든 코디 검색·재정렬 점수",
    )
    payload_snapshot = models.JSONField(
        default=dict,
        db_comment="추천 당시 골든 코디 Qdrant payload JSON 스냅샷",
    )
    reasons = models.JSONField(
        default=list,
        db_comment="골든 코디 선택 점수와 근거 JSON 배열",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="골든 템플릿 스냅샷 생성 시각",
    )

    class Meta:
        db_table = "golden_template_snapshot"
        db_table_comment = (
            "추천 결과가 선택한 골든 코디 템플릿과 검색 점수·근거·payload 스냅샷"
        )

    def __str__(self) -> str:
        return f"golden-template {self.golden_id} for {self.result_id}"


class OutfitComposition(models.Model):
    """추천 결과 안에서 순위가 매겨진 하나의 최종 코디 조합."""

    class Status(models.TextChoices):
        CANDIDATE = "CANDIDATE", "검증 전 후보"
        VALIDATED = "VALIDATED", "검증 통과"
        REJECTED = "REJECTED", "검증 실패"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="코디 조합 UUID",
    )
    result = models.ForeignKey(
        RecommendationResult,
        on_delete=models.CASCADE,
        related_name="compositions",
        db_comment="추천 결과 FK (recommendation_result.id)",
    )
    rank = models.PositiveSmallIntegerField(
        db_comment="추천 결과 안의 코디 노출 순위 (1~3)",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.CANDIDATE,
        db_comment="코디 검증 상태 (CANDIDATE/VALIDATED/REJECTED)",
    )
    composition_fingerprint = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_comment="최종 아이템·순서·이미지 버전 기반 SHA-256 지문 (검증 전이면 빈 문자열)",
    )
    total_product_price = models.PositiveBigIntegerField(
        default=0,
        db_comment="코디에 포함된 신규 상품 가격 합계 (원)",
    )
    validation_reasons = models.JSONField(
        default=list,
        db_comment="Validator의 통과·실패 근거 JSON 배열",
    )
    reference_match = models.JSONField(
        default=dict,
        blank=True,
        db_comment=(
            "공유 옷 참고 매칭 근거 JSON "
            "(match_type/source_type/source_id/score/reasons, 미사용 시 빈 객체)"
        ),
    )
    warnings = models.JSONField(
        default=list,
        db_comment="추천은 가능하지만 사용자에게 안내할 검증 경고 JSON 배열",
    )
    rationale = models.TextField(
        blank=True,
        default="",
        db_comment="사용자에게 보여줄 코디 전체 추천 이유 (없으면 빈 문자열)",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="코디 조합 생성 시각",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="코디 조합 마지막 수정 시각",
    )

    class Meta:
        db_table = "outfit_composition"
        db_table_comment = (
            "추천 결과별 최종 코디 조합 (순위·검증 상태·가격·이미지 캐시 지문 보관)"
        )
        ordering = ["rank", "created_at"]
        indexes = [
            models.Index(
                fields=["composition_fingerprint"],
                name="ix_outfit_comp_fingerprint",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["result", "rank"],
                name="uq_outfit_comp_result_rank",
            ),
            models.CheckConstraint(
                condition=Q(rank__gte=1, rank__lte=3),
                name="ck_outfit_comp_rank",
            ),
        ]

    def __str__(self) -> str:
        return f"outfit-composition {self.result_id}#{self.rank} ({self.status})"

    def clean(self) -> None:
        super().clean()
        if not isinstance(self.reference_match, dict):
            raise ValidationError(
                {"reference_match": "공유 옷 매칭 근거는 JSON 객체여야 합니다."}
            )
        if self.reference_match:
            required = {
                "schema_version",
                "match_type",
                "selection_role",
                "source_type",
                "source_id",
                "source_collection",
                "source_point_id",
                "template_item_point_id",
                "score",
                "reasons",
            }
            if set(self.reference_match) != required:
                raise ValidationError(
                    {
                        "reference_match": (
                            "공유 옷 매칭 근거의 필드 계약이 올바르지 않습니다."
                        )
                    }
                )
            if self.reference_match.get("selection_role") != (
                "PINNED_REFERENCE_ANCHOR"
            ):
                raise ValidationError(
                    {"reference_match": "고정 anchor 매칭 근거만 저장할 수 있습니다."}
                )
            if self.reference_match.get("match_type") not in {
                "VISUAL_SIMILAR",
                "STYLE_SIMILAR",
            }:
                raise ValidationError(
                    {"reference_match": "지원하지 않는 공유 옷 매칭 유형입니다."}
                )
            if not isinstance(self.reference_match.get("reasons"), list):
                raise ValidationError(
                    {"reference_match": "매칭 근거 reasons는 JSON 배열이어야 합니다."}
                )
        if (
            self.result_id
            and self.result.response_mode
            == RecommendationResult.ResponseMode.STYLIST
            and self.rank != 1
        ):
            raise ValidationError(
                {"rank": "스타일리스트별 추천 결과는 순위 1 코디 하나만 저장합니다."}
            )

    def save(self, *args, **kwargs) -> None:
        self.clean()
        super().save(*args, **kwargs)


class OutfitCompositionItem(models.Model):
    """코디 슬롯에 최종 선택된 옷장 또는 실제 상품 아이템."""

    class SourceType(models.TextChoices):
        WARDROBE = "WARDROBE", "옷장 아이템"
        PRODUCT = "PRODUCT", "판매 상품"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="코디 구성 아이템 UUID",
    )
    composition = models.ForeignKey(
        OutfitComposition,
        on_delete=models.CASCADE,
        related_name="items",
        db_comment="코디 조합 FK (outfit_composition.id)",
    )
    position = models.PositiveSmallIntegerField(
        db_comment="코디 내부 아이템 순서 (1부터 시작, 이미지 지문 계산에 사용)",
    )
    slot = models.CharField(
        max_length=64,
        db_comment="골든 템플릿에서 정한 코디 슬롯 식별자",
    )
    source_type = models.CharField(
        max_length=16,
        choices=SourceType.choices,
        db_comment="최종 아이템 출처 (WARDROBE/PRODUCT)",
    )
    source_id = models.CharField(
        max_length=128,
        db_comment="옷장 또는 상품 원본 레코드 식별자",
    )
    source_collection = models.CharField(
        max_length=128,
        db_comment="후보를 조회한 Qdrant 컬렉션명",
    )
    source_point_id = models.CharField(
        max_length=128,
        db_comment="후보 아이템의 Qdrant point 식별자",
    )
    template_item_point_id = models.CharField(
        max_length=128,
        db_comment="이 슬롯의 교체 기준이 된 goldenset_items Qdrant point 식별자",
    )
    replacement_score = models.FloatField(
        null=True,
        blank=True,
        db_comment="골든 기준 아이템과 최종 아이템의 교체 적합 점수 (미산정 시 NULL)",
    )
    image_ref = models.CharField(
        max_length=1024,
        db_comment="추천·렌더에 사용한 아이템 이미지 S3 키 또는 검증된 URL",
    )
    price_snapshot = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        db_comment="추천 당시 상품 가격 (원, 옷장 아이템이면 NULL)",
    )
    reasons = models.JSONField(
        default=list,
        db_comment="아이템 선택·교체 근거 JSON 배열",
    )
    note = models.TextField(
        blank=True,
        default="",
        db_comment="사용자에게 보여줄 개별 아이템 선택 이유 (없으면 빈 문자열)",
    )
    item_snapshot = models.JSONField(
        default=dict,
        db_comment="추천 당시 아이템 표시 정보·태그·구매 링크 JSON 스냅샷",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="코디 구성 아이템 생성 시각",
    )

    class Meta:
        db_table = "outfit_composition_item"
        db_table_comment = (
            "최종 코디의 슬롯별 옷장·상품 아이템과 교체 근거·표시 정보 스냅샷"
        )
        ordering = ["position", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["composition", "slot"],
                name="uq_outfit_comp_item_slot",
            ),
            models.UniqueConstraint(
                fields=["composition", "position"],
                name="uq_outfit_comp_item_pos",
            ),
            models.UniqueConstraint(
                fields=[
                    "composition",
                    "source_type",
                    "source_collection",
                    "source_id",
                ],
                name="uq_outfit_comp_item_source",
            ),
            models.CheckConstraint(
                condition=Q(position__gte=1),
                name="ck_outfit_comp_item_pos",
            ),
            models.CheckConstraint(
                condition=Q(source_type__in=["WARDROBE", "PRODUCT"]),
                name="ck_outfit_comp_item_source",
            ),
        ]

    def __str__(self) -> str:
        return f"outfit-item {self.composition_id}:{self.slot} ({self.source_type})"


class OutfitRenderJob(models.Model):
    """검증된 추천 카드 한 장의 비동기 착용 이미지 생성 상태."""

    class Status(models.TextChoices):
        QUEUED = "QUEUED", "생성 대기"
        PROCESSING = "PROCESSING", "생성 중"
        SUCCEEDED = "SUCCEEDED", "생성 완료"
        FAILED = "FAILED", "생성 실패"

    TERMINAL_STATUSES = (Status.SUCCEEDED, Status.FAILED)

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="코디 이미지 생성 작업 UUID (큐·SSE 공통 추적 ID)",
    )
    composition = models.OneToOneField(
        OutfitComposition,
        on_delete=models.CASCADE,
        related_name="render_job",
        db_comment="이미지를 생성할 검증 완료 코디 조합 FK (outfit_composition.id)",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
        db_comment="이미지 생성 상태 (QUEUED/PROCESSING/SUCCEEDED/FAILED)",
    )
    composition_fingerprint = models.CharField(
        max_length=64,
        db_comment="작업 접수 당시 코디 조합 SHA-256 지문",
    )
    render_fingerprint = models.CharField(
        max_length=64,
        db_comment="코디 지문·모델·프롬프트·출력 설정을 합친 렌더 캐시 SHA-256 지문",
    )
    output_s3_bucket = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_comment="생성 결과를 보관한 비공개 S3 버킷 (미완료이면 빈 문자열)",
    )
    output_s3_key = models.CharField(
        max_length=512,
        blank=True,
        default="",
        db_comment="생성 결과 S3 객체 키 (외부 API에는 직접 노출하지 않음)",
    )
    output_media_type = models.CharField(
        max_length=50,
        blank=True,
        default="",
        db_comment="생성 결과 MIME 타입 (image/jpeg/image/png/image/webp, 미완료이면 빈 문자열)",
    )
    output_bytes = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_comment="생성 결과 이미지 크기 (bytes, 미완료이면 NULL)",
    )
    provider = models.CharField(
        max_length=32,
        blank=True,
        default="",
        db_comment="이미지 생성 제공자 (예: openrouter, 캐시 결과도 원 생성 제공자 기록)",
    )
    model = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_comment="이미지 생성 모델명 (예: qwen/qwen-image-3-pro)",
    )
    prompt_version = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_comment="이미지 생성 프롬프트 버전",
    )
    reference_count = models.PositiveSmallIntegerField(
        default=0,
        db_comment="최종 이미지 생성에 사용한 참조 아이템 이미지 수",
    )
    usage = models.JSONField(
        default=dict,
        blank=True,
        db_comment="이미지 제공자가 반환한 사용량·비용 JSON",
    )
    cache_hit = models.BooleanField(
        default=False,
        db_comment="동일 렌더 지문의 기존 생성 결과를 재사용했는지 여부",
    )
    attempts = models.PositiveSmallIntegerField(
        default=0,
        db_comment="이미지 워커 처리 시도 횟수 (재시도 포함)",
    )
    error_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_comment="실패 오류 코드 (성공 또는 대기 상태이면 빈 문자열)",
    )
    error_message = models.CharField(
        max_length=500,
        blank=True,
        default="",
        db_comment="사용자에게 노출 가능한 실패 메시지 (성공 또는 대기 상태이면 빈 문자열)",
    )
    enqueued_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="Redis pending 큐 적재 확인 시각 (적재 확인 전이면 NULL)",
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="이미지 워커가 마지막 처리를 시작한 시각",
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="이미지 생성 성공 또는 최종 실패 시각",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="이미지 생성 작업 최초 접수 시각",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="이미지 생성 작업 마지막 수정 시각",
    )

    class Meta:
        db_table = "outfit_render_job"
        db_table_comment = (
            "추천 카드별 비동기 착용 이미지 생성 상태·비공개 S3 결과·캐시 근거"
        )
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["status", "created_at"],
                name="ix_outfit_render_status",
            ),
            models.Index(
                fields=["render_fingerprint"],
                name="ix_outfit_render_fprint",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=["QUEUED", "PROCESSING", "SUCCEEDED", "FAILED"]),
                name="ck_outfit_render_status",
            ),
        ]

    def __str__(self) -> str:
        return f"outfit-render {self.id} ({self.status})"


class DailyLook(models.Model):
    """사용자 1명의 하루치 '오늘의 룩' 추천 1건.

    코디 평가(OutfitAnalysis)와 결정적으로 다른 점이 하나 있다. 저쪽은 사용자가
    사진을 올려야 시작하지만, 이쪽은 **사용자 입력이 없다.** 그날 처음 홈 화면에
    들어오는 순간(GET /api/v1/home/) 자동으로 만들어지고, 재료는 미리 저장된
    체형·추구미와 그 시점 날씨다.

    그래서 (user, look_date)에 유니크 제약을 건다. 하루에 여러 번 접속해도,
    여러 기기에서 동시에 홈을 열어도 1건만 생긴다 — 경합은 DB가 막고 서비스는
    IntegrityError를 '이미 있음'으로 처리한다.

    스냅샷을 남기는 이유는 OutfitAnalysis와 같다. 날씨와 프로필은 계속 바뀌므로
    참조만 두면 "왜 이 룩이 나왔는지"를 나중에 재현할 수 없다. 여기에 더해
    리트리버가 뽑은 후보(candidates)도 남긴다 — 골든셋과 규칙표가 바뀌면 같은
    입력으로도 다른 후보가 나오기 때문에, LLM 응답만으로는 추천 경로를 되짚을 수 없다.
    """

    class Status(models.TextChoices):
        QUEUED = "QUEUED", "대기중"
        PROCESSING = "PROCESSING", "생성 진행중"
        SUCCEEDED = "SUCCEEDED", "생성 완료"
        FAILED = "FAILED", "생성 실패"
        #: 후보를 하나도 못 찾은 경우. 실패와 구분해야 프론트가 "잠시 후 다시"가
        #: 아니라 "프로필을 채워달라"고 안내할 수 있다.
        EMPTY = "EMPTY", "추천 후보 없음"

    PENDING_STATUSES = (Status.QUEUED, Status.PROCESSING)
    TERMINAL_STATUSES = (Status.SUCCEEDED, Status.FAILED, Status.EMPTY)

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="오늘의 룩 UUID (외부 노출 식별자)",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="daily_looks",
        db_index=False,  # 아래 (user, -look_date) 유니크 제약이 선두 컬럼을 덮는다
        db_comment="추천 대상 사용자 FK (users.id)",
    )
    look_date = models.DateField(
        "추천 날짜",
        db_comment="추천이 속한 날짜 (사용자 로컬 기준, Asia/Seoul)",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
        db_comment="생성 상태 (QUEUED/PROCESSING/SUCCEEDED/FAILED/EMPTY)",
    )
    attempts = models.PositiveSmallIntegerField(
        default=0,
        db_comment="오늘의 룩 추천 워커 처리 시도 횟수 (렌더 보정 작업 제외)",
    )
    enqueued_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="오늘의 룩 Redis 큐 적재 확인 시각 (미적재이면 NULL)",
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="오늘의 룩 추천 워커 마지막 처리 시작 시각",
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="오늘의 룩 추천 성공·후보 없음·최종 실패 시각",
    )

    # ── 생성 시점 스냅샷 ──
    weather = models.JSONField(
        "날씨 스냅샷",
        default=dict,
        blank=True,
        db_comment="추천에 사용한 날씨 JSON (region/temperature/sky_state 등)",
    )
    body = models.JSONField(
        "신체치수 스냅샷",
        null=True,
        blank=True,
        db_comment="추천에 사용한 신체치수 JSON (미등록이면 NULL)",
    )
    body_profile = models.JSONField(
        "체형 판정 스냅샷",
        default=dict,
        blank=True,
        db_comment="치수에서 판정한 실루엣·BMI·비율 JSON (판정 못 한 축은 unknown)",
    )
    pursuit = models.JSONField(
        "추구미 스냅샷",
        null=True,
        blank=True,
        db_comment="추천에 사용한 추구미 JSON (preferred/avoided, 미등록이면 NULL)",
    )

    # ── 리트리버 결과 ──
    candidates = models.JSONField(
        "리트리버 후보",
        default=list,
        blank=True,
        db_comment="리트리버가 뽑은 골든 코디 후보 요약 배열 (point_id/score/reasons)",
    )
    rules_version = models.CharField(
        max_length=40,
        blank=True,
        default="",
        db_comment="추천에 사용한 체형 규칙표 스키마 버전 (body_fit_rules.json)",
    )

    # ── LLM ──
    llm_model = models.CharField(
        max_length=120,
        blank=True,
        default="",
        db_comment="호출한 Gemini 모델 이름 (미호출이면 빈 문자열)",
    )
    llm_request = models.JSONField(
        "LLM 요청 본문",
        default=dict,
        blank=True,
        db_comment="Gemini에 보낸 요청 본문 (프롬프트 교체 전후 비교용)",
    )
    llm_response = models.JSONField(
        "LLM 원본 응답",
        default=dict,
        blank=True,
        db_comment="Gemini 원본 응답 JSON (파싱 실패 시 원인 추적용)",
    )
    llm_latency_ms = models.PositiveIntegerField(
        null=True, blank=True, db_comment="Gemini 호출 소요 시간 (ms)"
    )

    result = models.JSONField(
        "추천 결과",
        default=dict,
        blank=True,
        db_comment="프론트에 내려줄 추천 결과 JSON (headline/outfit/items/rationale)",
    )
    alternatives = models.JSONField(
        "다른 룩 후보",
        default=list,
        blank=True,
        db_comment=(
            "'다른 룩'으로 돌려볼 차순위 후보들. result와 **같은 스키마**의 배열이라 "
            "프론트가 카드 한 벌을 그리는 코드를 그대로 쓴다. 문장은 템플릿이고 "
            "착용 이미지는 별도 큐 작업이 나중에 채운다"
        ),
    )
    error = models.TextField(
        blank=True,
        default="",
        db_comment="실패 사유 (성공 시 빈 문자열)",
    )

    created_at = models.DateTimeField(
        auto_now_add=True, db_comment="행 생성 시각 (그날 첫 로그인 시각과 같다)"
    )
    updated_at = models.DateTimeField(auto_now=True, db_comment="행 수정 시각")

    class Meta:
        db_table = "daily_looks"
        db_table_comment = "사용자별 하루 1건의 오늘의 룩 추천"
        ordering = ["-look_date"]
        constraints = [
            models.UniqueConstraint(
                fields=("user", "look_date"), name="uq_daily_look_user_date"
            ),
        ]
        indexes = [
            models.Index(fields=["status"], name="idx_daily_look_status"),
        ]

    def __str__(self) -> str:
        return f"{self.look_date} / {self.user_id} ({self.status})"

    @property
    def is_pending(self) -> bool:
        return self.status in self.PENDING_STATUSES

    def retrieval_context(self) -> dict:
        """리트리버·LLM에 넘길 컨텍스트를 스냅샷에서 복원한다.

        워커는 이 값을 쓰고 컨텍스트를 다시 만들지 않는다. 큐에서 대기하는 사이
        날씨가 바뀌어도, 사용자가 로그인한 그 순간의 조건으로 추천해야 결과와
        기록이 일치한다 (OutfitAnalysis.llm_context와 같은 원칙).
        """
        return {
            "weather": self.weather or {},
            "body": self.body,
            "body_profile": self.body_profile or {},
            "pursuit": self.pursuit,
        }


class RecommendationFeedback(models.Model):
    """사용자가 추천 카드 하나에 남긴 최신 평가.

    소유 identity를 중복 저장하지 않고 코디 조합을 통해 따라간다. 게스트 대화가
    회원 identity로 이전될 때 추천 결과의 소유권만 바뀌어도 피드백이 함께 보존된다.
    """

    class Reaction(models.TextChoices):
        LIKE = "LIKE", "좋아요"
        DISLIKE = "DISLIKE", "싫어요"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="추천 피드백 UUID",
    )
    composition = models.OneToOneField(
        OutfitComposition,
        on_delete=models.CASCADE,
        related_name="feedback",
        db_comment="평가 대상 코디 조합 FK (outfit_composition.id, 조합당 피드백 최대 1개)",
    )
    reaction = models.CharField(
        max_length=8,
        choices=Reaction.choices,
        db_comment="추천 카드 반응 (LIKE/DISLIKE)",
    )
    reason_codes = models.JSONField(
        default=list,
        blank=True,
        db_comment="피드백 사유 코드 문자열 배열 (최대 5개)",
    )
    comment = models.CharField(
        max_length=500,
        blank=True,
        default="",
        db_comment="사용자 자유 의견 (최대 500자, 미입력 시 빈 문자열)",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="피드백 최초 생성 시각",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="피드백 마지막 수정 시각",
    )

    class Meta:
        db_table = "recommendation_feedback"
        db_table_comment = "추천 카드별 사용자 최신 반응과 선택 사유"
        ordering = ["-updated_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(reaction__in=["LIKE", "DISLIKE"]),
                name="ck_reco_feedback_reaction",
            ),
        ]

    def __str__(self) -> str:
        return f"recommendation-feedback {self.composition_id} ({self.reaction})"


class SavedOutfit(models.Model):
    """회원이 나중에 다시 보기 위해 저장한 검증 완료 추천 코디."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="저장 코디 UUID",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_outfits",
        db_comment="코디를 저장한 회원 FK (게스트 저장 불가)",
    )
    composition = models.ForeignKey(
        OutfitComposition,
        on_delete=models.CASCADE,
        related_name="saved_records",
        db_comment="저장 대상 검증 완료 추천 코디 FK (outfit_composition.id)",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="코디를 최초 저장한 시각",
    )

    class Meta:
        db_table = "saved_outfit"
        db_table_comment = "회원이 저장한 추천 코디와 최초 저장 시각"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "composition"],
                name="uq_saved_outfit_user_comp",
            ),
        ]

    def __str__(self) -> str:
        return f"saved-outfit {self.user_id}:{self.composition_id}"

    def clean(self) -> None:
        """검증 카드의 실제 소유 회원만 저장할 수 있게 모델 경계에서도 막는다."""

        super().clean()
        if not self.composition_id:
            return
        errors: dict[str, str] = {}
        if self.composition.status != OutfitComposition.Status.VALIDATED:
            errors["composition"] = "검증을 통과한 추천 코디만 저장할 수 있습니다."
        owner_id = self.composition.result.identity.user_id
        if owner_id is None or owner_id != self.user_id:
            errors["user"] = "추천 코디의 소유 회원만 저장할 수 있습니다."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        self.clean()
        super().save(*args, **kwargs)


class ProductClickEvent(models.Model):
    """회원이 추천 카드의 판매 상품 링크를 누른 참고 행동 이벤트."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="상품 클릭 이벤트 UUID",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recommendation_product_clicks",
        db_comment="상품을 클릭한 회원 FK (게스트 수집 불가)",
    )
    item = models.ForeignKey(
        OutfitCompositionItem,
        on_delete=models.CASCADE,
        related_name="product_click_events",
        db_comment="클릭한 추천 카드의 판매 상품 아이템 FK",
    )
    result_id_snapshot = models.UUIDField(
        db_comment="클릭 당시 추천 결과 UUID 스냅샷",
    )
    composition_id_snapshot = models.UUIDField(
        db_comment="클릭 당시 추천 카드 UUID 스냅샷",
    )
    persona_id = models.CharField(
        max_length=32,
        blank=True,
        default="",
        db_comment="클릭 당시 스타일리스트 ID (기본 추천이면 빈 문자열)",
    )
    source_collection = models.CharField(
        max_length=128,
        db_comment="클릭 당시 상품 원본 컬렉션 스냅샷",
    )
    source_id = models.CharField(
        max_length=128,
        db_comment="클릭 당시 상품 원본 레코드 식별자 스냅샷",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="상품 클릭 이벤트 수집 시각",
    )
    engagement_duration_ms = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        db_comment=(
            "외부 판매처 이동 후 앱 복귀까지의 근사 체류 시간(ms, 미수집 시 NULL)"
        ),
    )
    engagement_recorded_at = models.DateTimeField(
        null=True,
        blank=True,
        db_comment="상품 클릭 근사 체류 시간을 마지막으로 수집한 시각",
    )

    class Meta:
        db_table = "product_click_event"
        db_table_comment = "추천 카드 판매 상품 클릭 참고 행동 이벤트"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user", "item", "-created_at"],
                name="ix_prod_click_user_item",
            ),
        ]

    def __str__(self) -> str:
        return f"product-click {self.user_id}:{self.source_collection}:{self.source_id}"

    def clean(self) -> None:
        """상품·카드·추천·회원 귀속과 저장 스냅샷의 일치를 검증한다."""

        super().clean()
        if not self.item_id:
            return
        errors: dict[str, str] = {}
        composition = self.item.composition
        result = composition.result
        if self.item.source_type != OutfitCompositionItem.SourceType.PRODUCT:
            errors["item"] = "판매 상품 아이템 클릭만 수집할 수 있습니다."
        if composition.status != OutfitComposition.Status.VALIDATED:
            errors["item"] = "검증을 통과한 추천 카드의 상품만 수집할 수 있습니다."
        if result.identity.user_id is None or result.identity.user_id != self.user_id:
            errors["user"] = "추천 상품의 소유 회원만 클릭 이벤트를 저장할 수 있습니다."
        if self.result_id_snapshot != result.id:
            errors["result_id_snapshot"] = "추천 결과 스냅샷이 상품 귀속과 다릅니다."
        if self.composition_id_snapshot != composition.id:
            errors["composition_id_snapshot"] = (
                "추천 카드 스냅샷이 상품 귀속과 다릅니다."
            )
        if self.persona_id != result.persona_id:
            errors["persona_id"] = "스타일리스트 스냅샷이 추천 결과와 다릅니다."
        if self.source_collection != self.item.source_collection:
            errors["source_collection"] = "상품 컬렉션 스냅샷이 원본과 다릅니다."
        if self.source_id != self.item.source_id:
            errors["source_id"] = "상품 식별자 스냅샷이 원본과 다릅니다."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        self.clean()
        super().save(*args, **kwargs)


class WishlistItem(models.Model):
    """회원이 담아 둔 판매 상품(찜).

    ProductClickEvent 와 같은 방식으로 상품을 가리킨다 — 이름이 아니라
    ``source_collection``/``source_id``(카탈로그 원본 식별자)다. 이름으로 묶으면
    같은 상품이 추천마다 다른 이름으로 와서 두 번 담기고, 카탈로그의 브랜드·링크와
    이어 붙일 수도 없다.

    추천 카드가 지워져도 담아 둔 것은 남아야 하므로 ``item`` 은 끊어질 수 있고
    (SET_NULL), 화면에 필요한 값은 담는 순간의 스냅샷으로 이 행에 복사한다.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="찜 UUID",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wishlist_items",
        db_comment="찜한 회원 FK (users.id)",
    )
    item = models.ForeignKey(
        OutfitCompositionItem,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wishlist_entries",
        db_comment="담은 시점의 코디 구성 아이템 FK (추천 삭제 시 NULL)",
    )
    result_id_snapshot = models.UUIDField(
        null=True,
        blank=True,
        db_comment="담은 추천 결과 UUID 스냅샷",
    )
    composition_id_snapshot = models.UUIDField(
        null=True,
        blank=True,
        db_comment="담은 추천 카드 UUID 스냅샷",
    )
    source_collection = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_comment="상품 후보를 조회한 Qdrant 컬렉션명",
    )
    source_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_comment="상품 카탈로그 원본 식별자 (naver_product_id / eleven_product_id)",
    )
    display_name = models.CharField(
        max_length=500,
        db_comment="목록에 보여줄 상품명 (담은 시점 스냅샷)",
    )
    brand = models.CharField(
        max_length=200,
        blank=True,
        default="",
        db_comment="브랜드명 (카탈로그에 있으면 채우고, 없으면 빈 문자열)",
    )
    price_snapshot = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        db_comment="담은 시점 가격 (원, 미상이면 NULL)",
    )
    image_ref = models.CharField(
        max_length=1024,
        blank=True,
        default="",
        db_comment="상품 이미지 S3 키 또는 검증된 URL",
    )
    purchase_url = models.TextField(
        blank=True,
        default="",
        db_comment="판매처 상품 주소 (없으면 앱이 검색 주소를 만든다)",
    )
    slot = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_comment="담을 때의 코디 슬롯 (상의/하의 등, 예산 비교에 쓴다)",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="찜한 시각",
    )

    class Meta:
        db_table = "wishlist_item"
        db_table_comment = "회원이 담아 둔 추천 판매 상품과 담은 시점 스냅샷"
        ordering = ["-created_at"]
        constraints = [
            # 같은 상품을 두 카드에서 담아도 목록에는 하나만 선다.
            # 카탈로그 식별자가 없는 행(과거 목업 등)은 이 규칙에서 뺀다.
            models.UniqueConstraint(
                fields=["user", "source_collection", "source_id"],
                condition=~Q(source_id=""),
                name="uq_wishlist_user_source",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "-created_at"],
                name="ix_wishlist_user_created",
            ),
        ]

    def __str__(self) -> str:
        return f"wishlist {self.user_id}:{self.source_collection}:{self.source_id}"

    def clean(self) -> None:
        """추천에서 담은 행이면 그 상품·회원 귀속과 스냅샷의 일치를 검증한다."""

        super().clean()
        if not self.item_id:
            return
        errors: dict[str, str] = {}
        composition = self.item.composition
        result = composition.result
        if self.item.source_type != OutfitCompositionItem.SourceType.PRODUCT:
            errors["item"] = "판매 상품만 찜할 수 있습니다."
        if result.identity.user_id is None or result.identity.user_id != self.user_id:
            errors["user"] = "추천 상품의 소유 회원만 찜할 수 있습니다."
        if self.result_id_snapshot != result.id:
            errors["result_id_snapshot"] = "추천 결과 스냅샷이 상품 귀속과 다릅니다."
        if self.composition_id_snapshot != composition.id:
            errors["composition_id_snapshot"] = (
                "추천 카드 스냅샷이 상품 귀속과 다릅니다."
            )
        if self.source_collection != self.item.source_collection:
            errors["source_collection"] = "상품 컬렉션 스냅샷이 원본과 다릅니다."
        if self.source_id != self.item.source_id:
            errors["source_id"] = "상품 식별자 스냅샷이 원본과 다릅니다."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        self.clean()
        super().save(*args, **kwargs)


class VirtualTryOnJob(models.Model):
    """가상 피팅 한 건. **비동기로 만들고, 나중에 다시 와서 본다.**

    예전에는 요청 스레드에서 이미지 모델을 그대로 기다렸다. 생성이 수십 초~2분이라
    Cloudflare 터널(100초)이 먼저 끊어 524가 났고, 화면을 나가면 그 결과는 사라졌다.
    이제 접수만 하고(202) 워커가 만든다.

    **다시 보기의 기준은 (user, look, golden_id) 의 최근 작업**이다. job_id를 앱이
    들고 있게 하면 앱을 지우거나 기기를 바꿀 때 결과를 잃는다. 사람이 기억하는 것은
    "어제 그 룩을 입어봤다"이지 작업 번호가 아니다.

    사람 사진은 원본 그대로 S3에 잠시 둔다(워커가 나중에 읽어야 한다). 버킷 수명주기
    규칙으로 지우므로 **보관 기간은 코드가 아니라 버킷 설정이 정한다** — 배포 시
    VIRTUAL_TRY_ON_PERSON_PREFIX 에 TTL 규칙이 걸려 있는지 반드시 확인할 것.
    """

    class Status(models.TextChoices):
        QUEUED = "QUEUED", "대기중"
        PROCESSING = "PROCESSING", "생성 진행중"
        SUCCEEDED = "SUCCEEDED", "생성 완료"
        FAILED = "FAILED", "생성 실패"

    PENDING_STATUSES = (Status.QUEUED, Status.PROCESSING)
    TERMINAL_STATUSES = (Status.SUCCEEDED, Status.FAILED)

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="가상 피팅 작업 UUID",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="virtual_try_on_jobs",
        db_comment="요청 사용자 FK (users.id)",
    )
    look = models.ForeignKey(
        "recommend.DailyLook",
        on_delete=models.CASCADE,
        related_name="virtual_try_on_jobs",
        db_comment="입힐 추천이 담긴 오늘의 룩 FK (daily_look.id)",
    )
    golden_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_comment=(
            "입힌 골든 코디 id. 빈 값이면 대표 룩. "
            "'다른 룩' 후보마다 결과가 다르므로 조회 기준에 포함된다"
        ),
    )
    mode = models.CharField(
        max_length=16,
        choices=[("person", "본인 착장"), ("mannequin", "체형 마네킹")],
        default="mannequin",
        db_comment="가상 착장 방식 (person/mannequin)",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
        db_comment="작업 상태 (QUEUED/PROCESSING/SUCCEEDED/FAILED)",
    )
    contract = models.CharField(
        max_length=64,
        db_comment=(
            "사람 사진·코디 이미지·모델·프롬프트 버전을 합친 해시. "
            "결과 S3 키가 여기서 나오므로 같은 입력은 다시 만들지 않는다"
        ),
    )
    person_s3_bucket = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_comment="사용자 전신 사진을 잠시 둔 버킷",
    )
    person_s3_key = models.CharField(
        max_length=512,
        blank=True,
        default="",
        db_comment=(
            "사용자 전신 사진 S3 키 (수명주기 규칙으로 만료되는 prefix). "
            "워커가 읽고 나면 더 쓰지 않는다"
        ),
    )
    result_s3_bucket = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_comment="생성된 가상 착장 이미지 버킷",
    )
    result_s3_key = models.CharField(
        max_length=512,
        blank=True,
        default="",
        db_comment="생성된 가상 착장 이미지 S3 키 (조회 시점에 서명한다)",
    )
    result_media_type = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_comment="생성 결과 Content-Type",
    )
    cache_hit = models.BooleanField(
        default=False,
        db_comment="같은 입력의 결과가 이미 있어 생성을 건너뛰었는지",
    )
    error_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_comment="실패 코드 (성공 시 빈 문자열)",
    )
    error_message = models.CharField(
        max_length=500,
        blank=True,
        default="",
        db_comment="사용자에게 보여도 되는 실패 사유",
    )
    enqueued_at = models.DateTimeField(
        null=True, blank=True, db_comment="큐 적재 시각"
    )
    started_at = models.DateTimeField(
        null=True, blank=True, db_comment="워커가 집어든 시각"
    )
    finished_at = models.DateTimeField(
        null=True, blank=True, db_comment="성공·실패가 확정된 시각"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_comment="작업 생성 시각")
    updated_at = models.DateTimeField(auto_now=True, db_comment="작업 수정 시각")

    class Meta:
        db_table = "virtual_try_on_job"
        db_table_comment = "오늘의 룩 가상 피팅 생성 작업 (비동기)"
        ordering = ["-created_at"]  # noqa: RUF012 - Django Meta option
        indexes = [  # noqa: RUF012 - Django Meta option
            # 재진입 조회: 이 사용자의 이 룩·이 후보에서 가장 최근 작업 한 건.
            models.Index(
                fields=["user", "look", "golden_id", "-created_at"],
                name="vton_user_look_golden_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"virtual-try-on {self.user_id}:{self.look_id}:{self.status}"

    @property
    def is_pending(self) -> bool:
        return self.status in self.PENDING_STATUSES
