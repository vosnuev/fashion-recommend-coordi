# ruff: noqa: RUF012  # Django Meta의 list 설정은 프레임워크가 요구하는 클래스 속성이다.
"""골든셋 지식 구축 도메인 모델.

원본 이미지와 사람이 승인한 판단 데이터는 PostgreSQL이 소유하고, Qdrant는
검색을 위한 파생 저장소로만 사용한다. 이미지 바이너리와 임베딩 벡터는 DB에
넣지 않고 S3(또는 파일럿 run artifact)에 보관한다.
"""

from __future__ import annotations

import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models

#: 골든셋 전용 PostgreSQL 스키마. 다른 앱 테이블(public)과 섞이지 않게 격리한다.
#: 스키마 생성은 goldenset/migrations/0001_initial.py의 첫 RunSQL이 담당한다.
#: app label도 'goldenset'이라 스키마 이름과 일치한다 (apps.py 참고).
GOLDENSET_SCHEMA = "goldenset"


def _table(name: str) -> str:
    """스키마 한정 테이블명을 만든다.

    Django의 quote_name은 이미 따옴표로 시작하지 않는 이름 전체를 한 번만
    감싸므로, 가운데에 `"."`를 넣어두면 최종 SQL이 "goldenset"."golden_image"가
    된다. Django에 스키마 지정 API가 없어 쓰는 표준 우회다.
    """
    return f'{GOLDENSET_SCHEMA}"."{name}'


class GoldenDataset(models.Model):
    """서로 섞이면 안 되는 골든셋 버전 단위."""

    class Status(models.TextChoices):
        PILOT = "PILOT", "파일럿"
        DRAFT = "DRAFT", "초안"
        ACTIVE = "ACTIVE", "운영"
        ARCHIVED = "ARCHIVED", "보관"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="골든셋 버전 UUID",
    )
    name = models.CharField(
        max_length=120,
        db_comment="골든셋 표시 이름",
    )
    version = models.CharField(
        max_length=40,
        db_comment="골든셋 버전 식별자",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PILOT,
        db_comment="골든셋 상태 (PILOT/DRAFT/ACTIVE/ARCHIVED)",
    )
    run_id = models.CharField(
        max_length=80,
        blank=True,
        default="",
        db_comment="오프라인 파이프라인 실행 식별자",
    )
    source_metadata = models.JSONField(
        default=dict,
        blank=True,
        db_comment="출처·분할·분포 등 데이터셋 메타데이터 JSON",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="골든셋 버전 생성 시각",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="골든셋 버전 수정 시각",
    )

    class Meta:
        db_table = _table("golden_dataset")
        db_table_comment = "패션 판단 지식 구축에 사용하는 골든셋 버전"
        constraints = [
            models.UniqueConstraint(
                fields=["name", "version"],
                name="uq_golden_dataset_name_version",
            )
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name}:{self.version}"


class GoldenImage(models.Model):
    """골든 이미지 1장과 사람 점수 앵커 메타데이터."""

    class Split(models.TextChoices):
        KNOWLEDGE = "KNOWLEDGE", "지식 발견"
        VALIDATION = "VALIDATION", "검증"
        TEST = "TEST", "비공개 테스트"

    class UsageScope(models.TextChoices):
        INTERNAL = "INTERNAL", "내부 분석"
        EVALUATION = "EVALUATION", "평가 전용"
        UNKNOWN = "UNKNOWN", "미확인"

    class ScoreBand(models.TextChoices):
        HIGH = "high", "상"
        MID = "mid", "중"
        LOW = "low", "하"
        UNSET = "", "미정"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="골든 이미지 UUID",
    )
    dataset = models.ForeignKey(
        GoldenDataset,
        on_delete=models.CASCADE,
        related_name="images",
        db_comment="소속 골든셋 버전 FK (golden_dataset.id)",
    )
    golden_id = models.CharField(
        max_length=80,
        db_comment="manifest에서 사용하는 안정적인 이미지 식별자",
    )
    source_uri = models.CharField(
        max_length=1024,
        db_comment="원본 이미지 S3 URI 또는 파일럿 입력 경로",
    )
    source_name = models.CharField(
        max_length=160,
        blank=True,
        default="",
        db_comment="이미지 출처 이름",
    )
    usage_scope = models.CharField(
        max_length=16,
        choices=UsageScope.choices,
        default=UsageScope.UNKNOWN,
        db_comment="이미지 활용 범위 (INTERNAL/EVALUATION/UNKNOWN)",
    )
    original_exposable = models.BooleanField(
        default=False,
        db_comment="원본 이미지를 사용자 응답에 노출할 수 있는지 여부",
    )
    image_sha256 = models.CharField(
        max_length=64,
        db_comment="원본 이미지 SHA-256 해시",
    )
    perceptual_hash = models.CharField(
        max_length=32,
        blank=True,
        default="",
        db_comment="근접 중복 탐지용 지각 해시",
    )
    split = models.CharField(
        max_length=16,
        choices=Split.choices,
        default=Split.KNOWLEDGE,
        db_comment="데이터 분할 (KNOWLEDGE/VALIDATION/TEST)",
    )
    presentation_group = models.CharField(
        max_length=40,
        blank=True,
        default="",
        db_comment="수집·공정성 평가용 성별 표현 그룹",
    )
    cluster_id = models.CharField(
        max_length=40,
        blank=True,
        default="",
        db_comment="임베딩 클러스터 식별자",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        db_comment="스타일·계절·TPO·선정 이유 등 이미지 메타데이터 JSON",
    )
    human_score = models.FloatField(
        null=True,
        blank=True,
        db_comment="쌍대 비교에서 환산한 상대 점수 (0~100)",
    )
    score_band = models.CharField(
        max_length=4,
        choices=ScoreBand.choices,
        blank=True,
        default=ScoreBand.UNSET,
        db_comment="보조 점수 앵커 구간 (high/mid/low, 미정은 빈 문자열)",
    )
    score_confidence = models.FloatField(
        null=True,
        blank=True,
        db_comment="사람 점수 신뢰도 (0~1)",
    )
    embedding_version = models.CharField(
        max_length=80,
        blank=True,
        default="",
        db_comment="이미지 임베딩 모델·전처리 버전",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="골든 이미지 등록 시각",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="골든 이미지 수정 시각",
    )

    class Meta:
        db_table = _table("golden_image")
        db_table_comment = "골든셋 원본 이미지와 보조 점수 앵커 메타데이터"
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "golden_id"],
                name="uq_golden_image_dataset_golden_id",
            )
        ]
        indexes = [
            models.Index(fields=["dataset", "split"]),
            models.Index(fields=["dataset", "score_band"]),
            models.Index(fields=["image_sha256"]),
        ]
        ordering = ["golden_id"]

    def __str__(self) -> str:
        return self.golden_id


class GoldenOutfitItem(models.Model):
    """골든 코디 사진에서 분리한 의상 아이템 1개.

    태그 축은 apps.wardrobe.WardrobeItem과 의도적으로 같게 맞춘다. 골든 코디의
    상의를 옷장 아이템이나 네이버 상품으로 교체하려면 세 저장소가 같은 필터
    언어(category_large / layer_role / season / style ...)를 써야 하기 때문이다.

    분리된 아이템 이미지는 S3에 두고, 벡터는 Qdrant(goldenset_items)에만 둔다.
    """

    class Status(models.TextChoices):
        SUCCEEDED = "SUCCEEDED", "성공"
        FAILED = "FAILED", "실패"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="골든 코디 아이템 UUID",
    )
    image = models.ForeignKey(
        GoldenImage,
        on_delete=models.CASCADE,
        related_name="items",
        db_comment="아이템이 속한 골든 코디 이미지 FK (golden_image.id)",
    )
    item_index = models.PositiveSmallIntegerField(
        db_comment="코디 안에서의 아이템 순번 (0부터, 파이프라인 산출 순서)",
    )
    item_key = models.CharField(
        max_length=120,
        db_comment="Qdrant point ID 재현용 안정 식별자 (golden_id#000 형식)",
    )

    # ── 분리 이미지 위치 ──
    s3_bucket = models.CharField(
        max_length=200,
        blank=True,
        default="",
        db_comment="분리된 아이템 이미지 S3 버킷",
    )
    s3_key = models.CharField(
        max_length=512,
        blank=True,
        default="",
        db_comment="분리된 아이템 이미지 S3 키",
    )

    # ── 태그 (apps.wardrobe.taxonomy 라벨 체계와 동일) ──
    item_name = models.CharField(
        max_length=120,
        blank=True,
        default="",
        db_comment="아이템 표시 이름",
    )
    category_large = models.CharField(
        max_length=20,
        blank=True,
        default="",
        db_comment="대분류 (상의/하의/아우터/신발/가방 등)",
    )
    category_small = models.CharField(
        max_length=30,
        blank=True,
        default="",
        db_comment="소분류 (티셔츠/데님 팬츠 등)",
    )
    season = ArrayField(
        models.CharField(max_length=10),
        default=list,
        blank=True,
        db_comment="계절 태그 배열",
    )
    style = ArrayField(
        models.CharField(max_length=10),
        default=list,
        blank=True,
        db_comment="스타일 태그 배열",
    )
    usage = ArrayField(
        models.CharField(max_length=20),
        default=list,
        blank=True,
        db_comment="용도(TPO) 태그 배열",
    )
    color = models.CharField(
        max_length=10, blank=True, default="", db_comment="색상 태그"
    )
    pattern = models.CharField(
        max_length=10, blank=True, default="", db_comment="패턴 태그"
    )
    fit = models.CharField(
        max_length=10, blank=True, default="", db_comment="핏 태그"
    )
    material = models.CharField(
        max_length=10, blank=True, default="", db_comment="소재 태그"
    )
    sleeve = models.CharField(
        max_length=10, blank=True, default="", db_comment="소매 길이 태그"
    )
    length = models.CharField(
        max_length=10, blank=True, default="", db_comment="기장 태그"
    )
    layer_role = models.CharField(
        max_length=10,
        blank=True,
        default="",
        db_comment="레이어링 역할 태그 (아이템 교체 질의의 핵심 축)",
    )
    layer_order = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        db_comment="레이어링 착용 순서 (안쪽부터 1)",
    )

    # ── 열거 단계 메타 ──
    label_ko = models.CharField(
        max_length=120,
        blank=True,
        default="",
        db_comment="열거 단계가 붙인 짧은 한국어 라벨",
    )
    descriptor_en = models.TextField(
        blank=True,
        default="",
        db_comment="아이템을 특정하는 영어 서술 (분리 프롬프트 입력)",
    )
    view_angle = models.CharField(
        max_length=16,
        blank=True,
        default="",
        db_comment="원본에서 관측된 각도 (front/side/back/three-quarter)",
    )
    occluded_by = models.JSONField(
        default=list,
        blank=True,
        db_comment="이 아이템을 가리는 요소 목록",
    )
    bbox = models.JSONField(
        null=True,
        blank=True,
        db_comment="원본 좌표 [ymin, xmin, ymax, xmax] 0~1000 정규화",
    )
    missing_required = models.JSONField(
        default=list,
        blank=True,
        db_comment="taxonomy 필수 태그 중 채워지지 않은 필드 목록",
    )

    # ── 파이프라인·임베딩 버전 ──
    pipeline_key = models.CharField(
        max_length=40,
        blank=True,
        default="",
        db_comment="아이템을 만든 파이프라인 구현 키 (gemini-image-edit/sam3-crop 등)",
    )
    image_embedding_version = models.CharField(
        max_length=80,
        blank=True,
        default="",
        db_comment="아이템 이미지 임베딩 모델 버전",
    )
    text_embedding_version = models.CharField(
        max_length=80,
        blank=True,
        default="",
        db_comment="아이템 캡션 임베딩 모델 버전",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.SUCCEEDED,
        db_comment="아이템 처리 상태 (SUCCEEDED/FAILED)",
    )
    error_message = models.TextField(
        blank=True,
        default="",
        db_comment="아이템 처리 실패 사유",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="아이템 생성 시각",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="아이템 수정 시각",
    )

    class Meta:
        db_table = _table("golden_outfit_item")
        db_table_comment = "골든 코디 사진에서 분리한 의상 아이템과 태그"
        constraints = [
            models.UniqueConstraint(
                fields=["image", "item_index"],
                name="uq_golden_outfit_item_index",
            ),
            models.UniqueConstraint(
                fields=["image", "item_key"],
                name="uq_golden_outfit_item_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["category_large", "layer_role"],
                name="idx_golden_item_cat_layer",
            ),
            models.Index(fields=["item_key"], name="idx_golden_item_key"),
            models.Index(fields=["status"], name="idx_golden_item_status"),
        ]
        ordering = ["image", "item_index"]

    def __str__(self) -> str:
        return f"{self.item_key} {self.label_ko}".strip()

class GoldenAnalysis(models.Model):
    """멀티모달 모델이 생성한 구조화 분석 스냅샷."""

    class Status(models.TextChoices):
        SUCCEEDED = "SUCCEEDED", "성공"
        FAILED = "FAILED", "실패"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="골든 이미지 분석 UUID",
    )
    image = models.ForeignKey(
        GoldenImage,
        on_delete=models.CASCADE,
        related_name="analyses",
        db_comment="분석 대상 골든 이미지 FK (golden_image.id)",
    )
    model_version = models.CharField(
        max_length=120,
        db_comment="분석에 사용한 멀티모달 모델 버전",
    )
    prompt_version = models.CharField(
        max_length=80,
        db_comment="분석 프롬프트 버전",
    )
    schema_version = models.CharField(
        max_length=40,
        db_comment="구조화 분석 스키마 버전",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.SUCCEEDED,
        db_comment="분석 상태 (SUCCEEDED/FAILED)",
    )
    result = models.JSONField(
        default=dict,
        blank=True,
        db_comment="관찰·영역·관계·판단·불확실성 구조화 결과 JSON",
    )
    error_message = models.TextField(
        blank=True,
        default="",
        db_comment="분석 실패 사유",
    )
    latency_seconds = models.FloatField(
        null=True,
        blank=True,
        db_comment="멀티모달 분석 응답 시간(초)",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="분석 실행 시각",
    )

    class Meta:
        db_table = _table("golden_analysis")
        db_table_comment = "모델·프롬프트 버전별 골든 이미지 구조화 분석"
        constraints = [
            models.UniqueConstraint(
                fields=["image", "model_version", "prompt_version", "schema_version"],
                name="uq_golden_analysis_versions",
            )
        ]
        ordering = ["-created_at"]


class GoldenPrinciple(models.Model):
    """사람 검수를 거쳐 지식 RAG에 승격할 조건부 패션 원칙."""

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "초안"
        APPROVED = "APPROVED", "승인"
        REJECTED = "REJECTED", "기각"
        RETIRED = "RETIRED", "폐기"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="조건부 패션 원칙 UUID",
    )
    dataset = models.ForeignKey(
        GoldenDataset,
        on_delete=models.CASCADE,
        related_name="principles",
        db_comment="원칙을 생성한 골든셋 버전 FK (golden_dataset.id)",
    )
    principle_key = models.CharField(
        max_length=120,
        db_comment="실행 내에서 안정적인 원칙 식별자",
    )
    dimension = models.CharField(
        max_length=40,
        db_comment="판단 차원 (color/silhouette/proportion 등)",
    )
    statement = models.TextField(
        db_comment="검수 대상 조건부 패션 원칙 문장",
    )
    applies_when = models.JSONField(
        default=dict,
        blank=True,
        db_comment="스타일·추구미·계절·TPO·의류 조건별 원칙 적용 조건 JSON",
    )
    exceptions = models.JSONField(
        default=list,
        blank=True,
        db_comment="원칙 예외·적용 제외 조건 문자열 배열",
    )
    confidence = models.FloatField(
        default=0.0,
        db_comment="근거 수·검수 합의도를 반영한 원칙 신뢰도 (0~1)",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        db_comment="원칙 상태 (DRAFT/APPROVED/REJECTED/RETIRED)",
    )
    version = models.CharField(
        max_length=40,
        db_comment="원칙 생성 스키마·프롬프트 버전",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        db_comment="클러스터·생성 모델·검수 통계 등 원칙 메타데이터 JSON",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="원칙 생성 시각",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        db_comment="원칙 수정 시각",
    )

    class Meta:
        db_table = _table("golden_principle")
        db_table_comment = "골든 이미지 근거에서 추출한 조건부 패션 판단 원칙"
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "principle_key", "version"],
                name="uq_golden_principle_version",
            )
        ]
        indexes = [
            models.Index(fields=["dataset", "status"]),
            models.Index(fields=["dimension", "status"]),
        ]
        ordering = ["dimension", "principle_key"]

    def __str__(self) -> str:
        return f"[{self.dimension}] {self.statement[:40]}"


class GoldenPrincipleEvidence(models.Model):
    """원칙과 이미지 안의 관찰 claim을 잇는 추적 가능한 근거."""

    class Polarity(models.TextChoices):
        SUPPORT = "SUPPORT", "지지"
        EXCEPTION = "EXCEPTION", "예외"
        COUNTEREXAMPLE = "COUNTEREXAMPLE", "반례"

    id = models.BigAutoField(
        primary_key=True,
        db_comment="원칙 근거 행 PK",
    )
    principle = models.ForeignKey(
        GoldenPrinciple,
        on_delete=models.CASCADE,
        related_name="evidence_links",
        db_comment="조건부 패션 원칙 FK (golden_principle.id)",
    )
    image = models.ForeignKey(
        GoldenImage,
        on_delete=models.CASCADE,
        related_name="principle_evidence_links",
        db_comment="근거 골든 이미지 FK (golden_image.id)",
    )
    claim_key = models.CharField(
        max_length=120,
        db_comment="분석 결과 내부 claim 식별자",
    )
    region_ids = models.JSONField(
        default=list,
        blank=True,
        db_comment="claim이 참조하는 이미지 영역 식별자 배열",
    )
    polarity = models.CharField(
        max_length=16,
        choices=Polarity.choices,
        default=Polarity.SUPPORT,
        db_comment="근거 관계 (SUPPORT/EXCEPTION/COUNTEREXAMPLE)",
    )
    confidence = models.FloatField(
        default=0.0,
        db_comment="해당 이미지 근거 연결 신뢰도 (0~1)",
    )

    class Meta:
        db_table = _table("golden_principle_evidence")
        db_table_comment = "조건부 패션 원칙과 이미지 영역 claim의 연결"
        constraints = [
            models.UniqueConstraint(
                fields=["principle", "image", "claim_key", "polarity"],
                name="uq_golden_principle_evidence",
            )
        ]


class GoldenReview(models.Model):
    """이미지 분석 또는 원칙에 대한 사람의 독립 검수."""

    class Verdict(models.TextChoices):
        APPROVE = "APPROVE", "승인"
        EDIT = "EDIT", "수정 필요"
        REJECT = "REJECT", "기각"
        UNSURE = "UNSURE", "판단 보류"

    id = models.BigAutoField(
        primary_key=True,
        db_comment="사람 검수 행 PK",
    )
    dataset = models.ForeignKey(
        GoldenDataset,
        on_delete=models.CASCADE,
        related_name="reviews",
        db_comment="검수 대상 골든셋 버전 FK (golden_dataset.id)",
    )
    review_key = models.CharField(
        max_length=255,
        db_comment="검수 파일 재수입을 위한 안정적인 검수 행 식별자",
    )
    image = models.ForeignKey(
        GoldenImage,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reviews",
        db_comment="이미지 분석 검수 대상 FK (없으면 null)",
    )
    principle = models.ForeignKey(
        GoldenPrinciple,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reviews",
        db_comment="패션 원칙 검수 대상 FK (없으면 null)",
    )
    reviewer_label = models.CharField(
        max_length=80,
        db_comment="개인정보 대신 사용하는 검수자 별칭",
    )
    verdict = models.CharField(
        max_length=16,
        choices=Verdict.choices,
        db_comment="검수 판정 (APPROVE/EDIT/REJECT/UNSURE)",
    )
    scores = models.JSONField(
        default=dict,
        blank=True,
        db_comment="축별 점수·종합점수·확신도 등 사람 평가 JSON",
    )
    rationale = models.TextField(
        blank=True,
        default="",
        db_comment="검수 사유와 수정 제안",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="검수 시각",
    )

    class Meta:
        db_table = _table("golden_review")
        db_table_comment = "골든 이미지 분석·패션 원칙에 대한 사람 검수"
        indexes = [
            models.Index(fields=["dataset", "reviewer_label"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "review_key"],
                name="uq_golden_review_key",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(image__isnull=False, principle__isnull=True)
                    | models.Q(image__isnull=True, principle__isnull=False)
                ),
                name="ck_golden_review_exactly_one_target",
            ),
        ]


class GoldenPairwiseReview(models.Model):
    """두 골든 이미지의 스타일 의도 내 완성도를 비교한 사람 판정."""

    class Outcome(models.TextChoices):
        LEFT = "left", "왼쪽 우세"
        RIGHT = "right", "오른쪽 우세"
        TIE = "tie", "동등"
        CONTEXT_DEPENDENT = "context_dependent", "상황 의존"
        UNASSESSABLE = "unassessable", "판단 불가"

    id = models.BigAutoField(
        primary_key=True,
        db_comment="골든 쌍대 비교 검수 행 PK",
    )
    dataset = models.ForeignKey(
        GoldenDataset,
        on_delete=models.CASCADE,
        related_name="pairwise_reviews",
        db_comment="소속 골든셋 버전 FK (golden_dataset.id)",
    )
    pair_key = models.CharField(
        max_length=120,
        db_comment="실행 내에서 안정적인 비교 쌍 식별자",
    )
    left_image = models.ForeignKey(
        GoldenImage,
        on_delete=models.CASCADE,
        related_name="pairwise_reviews_as_left",
        db_comment="검수 화면 왼쪽 골든 이미지 FK (golden_image.id)",
    )
    right_image = models.ForeignKey(
        GoldenImage,
        on_delete=models.CASCADE,
        related_name="pairwise_reviews_as_right",
        db_comment="검수 화면 오른쪽 골든 이미지 FK (golden_image.id)",
    )
    reviewer_label = models.CharField(
        max_length=80,
        db_comment="개인정보 대신 사용하는 검수자 별칭",
    )
    comparison_scope = models.CharField(
        max_length=40,
        db_comment="비교 쌍 구성 범위 (동일 스타일/근접 이미지/연결 쌍 등)",
    )
    comparison_axis = models.CharField(
        max_length=60,
        default="Q_OVERALL_STYLE_EXECUTION",
        db_comment="쌍대 비교 판단 축",
    )
    context = models.JSONField(
        default=dict,
        blank=True,
        db_comment="비교 가능한 스타일·계절·TPO 및 제시 순서 컨텍스트 JSON",
    )
    outcome = models.CharField(
        max_length=24,
        choices=Outcome.choices,
        db_comment="비교 결과 (left/right/tie/context_dependent/unassessable)",
    )
    confidence = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        db_comment="사람 판단 확신도 (1~3, 미입력은 null)",
    )
    reason_axis = models.CharField(
        max_length=60,
        blank=True,
        default="",
        db_comment="승패 판단에 가장 크게 기여한 패션 축",
    )
    rationale = models.TextField(
        blank=True,
        default="",
        db_comment="쌍대 비교 사유 또는 판단 불가 사유",
    )
    rubric_version = models.CharField(
        max_length=40,
        db_comment="검수 질문·선택지 계약 버전",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_comment="쌍대 비교 검수 시각",
    )

    class Meta:
        db_table = _table("golden_pairwise_review")
        db_table_comment = "골든 이미지 두 장의 사람 상대 비교와 판단 근거"
        indexes = [
            models.Index(fields=["dataset", "comparison_axis"]),
            models.Index(fields=["dataset", "reviewer_label"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "pair_key", "reviewer_label"],
                name="uq_golden_pairwise_reviewer",
            ),
            models.CheckConstraint(
                condition=~models.Q(left_image=models.F("right_image")),
                name="ck_golden_pairwise_distinct_images",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(confidence__isnull=True)
                    | models.Q(confidence__gte=1, confidence__lte=3)
                ),
                name="ck_golden_pairwise_confidence_1_3",
            ),
        ]
