"""코디 평가 기록(outfit_analysis) 테이블 추가.

LLM 질의에 사용한 날씨·체형·추구미 스냅샷과 요청·응답 원본을 함께 보관한다.
익명 요청도 기록하므로 user는 NULL 허용.
"""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OutfitAnalysis",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        db_comment="평가 UUID (외부 노출 식별자)",
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "평가 진행중"),
                            ("SUCCEEDED", "평가 완료"),
                            ("FAILED", "평가 실패"),
                        ],
                        db_comment="평가 상태 (PENDING/SUCCEEDED/FAILED)",
                        default="PENDING",
                        max_length=16,
                    ),
                ),
                (
                    "image_s3_key",
                    models.CharField(
                        blank=True,
                        db_comment="평가 대상 코디 사진 S3 키 (업로드 미설정 또는 실패 시 빈 문자열)",
                        default="",
                        max_length=512,
                        verbose_name="원본 사진 S3 키",
                    ),
                ),
                (
                    "image_content_type",
                    models.CharField(
                        blank=True,
                        db_comment="업로드 이미지 MIME 타입 (image/jpeg, image/png, image/webp)",
                        default="",
                        max_length=50,
                    ),
                ),
                (
                    "image_bytes",
                    models.PositiveIntegerField(
                        blank=True, db_comment="업로드 이미지 크기 (bytes)", null=True
                    ),
                ),
                (
                    "requested_lat",
                    models.FloatField(
                        blank=True,
                        db_comment="요청 위도 (클라이언트가 보낸 값, 미전달 시 NULL)",
                        null=True,
                    ),
                ),
                (
                    "requested_lon",
                    models.FloatField(
                        blank=True,
                        db_comment="요청 경도 (클라이언트가 보낸 값, 미전달 시 NULL)",
                        null=True,
                    ),
                ),
                (
                    "resolved_lat",
                    models.FloatField(
                        blank=True,
                        db_comment="날씨 조회에 실제 사용한 위도 (미전달·국내 범위 밖이면 서울 좌표로 대체)",
                        null=True,
                    ),
                ),
                (
                    "resolved_lon",
                    models.FloatField(
                        blank=True,
                        db_comment="날씨 조회에 실제 사용한 경도 (미전달·국내 범위 밖이면 서울 좌표로 대체)",
                        null=True,
                    ),
                ),
                (
                    "weather",
                    models.JSONField(
                        blank=True,
                        db_comment="질의에 사용한 날씨 JSON (region/temperature/sky_state/is_stale/observed_at)",
                        default=dict,
                        verbose_name="날씨 스냅샷",
                    ),
                ),
                (
                    "body",
                    models.JSONField(
                        blank=True,
                        db_comment="질의에 사용한 신체치수·성별 JSON (비로그인 또는 미등록이면 NULL)",
                        null=True,
                        verbose_name="신체치수 스냅샷",
                    ),
                ),
                (
                    "pursuit",
                    models.JSONField(
                        blank=True,
                        db_comment="질의에 사용한 추구미 JSON (preferred/avoided, 비로그인이면 NULL)",
                        null=True,
                        verbose_name="추구미 스냅샷",
                    ),
                ),
                (
                    "personalized",
                    models.BooleanField(
                        db_comment="개인화 정보 반영 여부 (로그인 요청이면 true)",
                        default=False,
                    ),
                ),
                (
                    "llm_model",
                    models.CharField(
                        blank=True,
                        db_comment="평가에 사용한 LLM 모델명 (예: gemini-3.5-flash)",
                        default="",
                        max_length=80,
                    ),
                ),
                (
                    "request_payload",
                    models.JSONField(
                        blank=True,
                        db_comment="LLM에 보낸 요청 본문 JSON 전체 (사진 base64는 자리표시자로 대체)",
                        default=dict,
                        verbose_name="LLM 요청 본문",
                    ),
                ),
                (
                    "response_payload",
                    models.JSONField(
                        blank=True,
                        db_comment="LLM 원본 응답 JSON 전체 (candidates/usageMetadata 등, 실패 시 오류 본문)",
                        default=dict,
                        verbose_name="LLM 원본 응답",
                    ),
                ),
                (
                    "evaluation",
                    models.JSONField(
                        blank=True,
                        db_comment="파싱된 평가 결과 JSON (API 응답의 evaluation 필드와 동일, 실패 시 NULL)",
                        null=True,
                        verbose_name="평가 결과",
                    ),
                ),
                (
                    "latency_ms",
                    models.PositiveIntegerField(
                        blank=True, db_comment="LLM 호출 소요 시간 (밀리초)", null=True
                    ),
                ),
                (
                    "error_message",
                    models.TextField(
                        blank=True, db_comment="실패 시 오류 메시지", default=""
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, db_comment="요청 접수 시각"
                    ),
                ),
                (
                    "finished_at",
                    models.DateTimeField(
                        blank=True,
                        db_comment="평가 종료 시각 (SUCCEEDED/FAILED 전환 시)",
                        null=True,
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        db_comment="요청 사용자 FK (users.id, 비로그인 요청이면 NULL)",
                        db_index=False,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="outfit_analyses",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "outfit_analysis",
                "db_table_comment": "코디 사진 AI 평가 기록 (질의에 쓴 날씨·체형·추구미 스냅샷과 LLM 요청·응답 원본 보관)",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["user", "-created_at"], name="ix_outfit_analysis_user"
                    ),
                    models.Index(
                        fields=["status", "-created_at"], name="ix_outfit_analysis_stat"
                    ),
                ],
            },
        ),
    ]
