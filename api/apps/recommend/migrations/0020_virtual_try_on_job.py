"""가상 피팅을 비동기로 — 작업 테이블 신설.

요청 스레드에서 이미지 모델을 기다리던 것을 접수(202)와 조회로 나눈다. 결과가
DB·S3에 남으므로 화면을 나갔다 와도 다시 볼 수 있다.

⚠️ 배포 점검: person_s3_key 가 가리키는 prefix(VIRTUAL_TRY_ON_PERSON_PREFIX)에
S3 수명주기 규칙을 걸어야 한다. 삭제는 코드가 하지 않는다.
"""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("recommend", "0019_merge_daily_look_alternatives"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="VirtualTryOnJob",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        db_comment="가상 피팅 작업 UUID",
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "golden_id",
                    models.CharField(
                        blank=True,
                        db_comment="입힌 골든 코디 id. 빈 값이면 대표 룩. '다른 룩' 후보마다 결과가 다르므로 조회 기준에 포함된다",
                        default="",
                        max_length=100,
                    ),
                ),
                (
                    "mode",
                    models.CharField(
                        choices=[("person", "본인 착장"), ("mannequin", "체형 마네킹")],
                        db_comment="가상 착장 방식 (person/mannequin)",
                        default="mannequin",
                        max_length=16,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("QUEUED", "대기중"),
                            ("PROCESSING", "생성 진행중"),
                            ("SUCCEEDED", "생성 완료"),
                            ("FAILED", "생성 실패"),
                        ],
                        db_comment="작업 상태 (QUEUED/PROCESSING/SUCCEEDED/FAILED)",
                        default="QUEUED",
                        max_length=16,
                    ),
                ),
                (
                    "contract",
                    models.CharField(
                        db_comment="사람 사진·코디 이미지·모델·프롬프트 버전을 합친 해시. 결과 S3 키가 여기서 나오므로 같은 입력은 다시 만들지 않는다",
                        max_length=64,
                    ),
                ),
                (
                    "person_s3_bucket",
                    models.CharField(
                        blank=True,
                        db_comment="사용자 전신 사진을 잠시 둔 버킷",
                        default="",
                        max_length=255,
                    ),
                ),
                (
                    "person_s3_key",
                    models.CharField(
                        blank=True,
                        db_comment="사용자 전신 사진 S3 키 (수명주기 규칙으로 만료되는 prefix). 워커가 읽고 나면 더 쓰지 않는다",
                        default="",
                        max_length=512,
                    ),
                ),
                (
                    "result_s3_bucket",
                    models.CharField(
                        blank=True,
                        db_comment="생성된 가상 착장 이미지 버킷",
                        default="",
                        max_length=255,
                    ),
                ),
                (
                    "result_s3_key",
                    models.CharField(
                        blank=True,
                        db_comment="생성된 가상 착장 이미지 S3 키 (조회 시점에 서명한다)",
                        default="",
                        max_length=512,
                    ),
                ),
                (
                    "result_media_type",
                    models.CharField(
                        blank=True,
                        db_comment="생성 결과 Content-Type",
                        default="",
                        max_length=64,
                    ),
                ),
                (
                    "cache_hit",
                    models.BooleanField(
                        db_comment="같은 입력의 결과가 이미 있어 생성을 건너뛰었는지",
                        default=False,
                    ),
                ),
                (
                    "error_code",
                    models.CharField(
                        blank=True,
                        db_comment="실패 코드 (성공 시 빈 문자열)",
                        default="",
                        max_length=64,
                    ),
                ),
                (
                    "error_message",
                    models.CharField(
                        blank=True,
                        db_comment="사용자에게 보여도 되는 실패 사유",
                        default="",
                        max_length=500,
                    ),
                ),
                (
                    "enqueued_at",
                    models.DateTimeField(
                        blank=True, db_comment="큐 적재 시각", null=True
                    ),
                ),
                (
                    "started_at",
                    models.DateTimeField(
                        blank=True, db_comment="워커가 집어든 시각", null=True
                    ),
                ),
                (
                    "finished_at",
                    models.DateTimeField(
                        blank=True, db_comment="성공·실패가 확정된 시각", null=True
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, db_comment="작업 생성 시각"
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, db_comment="작업 수정 시각"),
                ),
                (
                    "look",
                    models.ForeignKey(
                        db_comment="입힐 추천이 담긴 오늘의 룩 FK (daily_look.id)",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="virtual_try_on_jobs",
                        to="recommend.dailylook",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_comment="요청 사용자 FK (users.id)",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="virtual_try_on_jobs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "virtual_try_on_job",
                "db_table_comment": "오늘의 룩 가상 피팅 생성 작업 (비동기)",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["user", "look", "golden_id", "-created_at"],
                        name="vton_user_look_golden_idx",
                    )
                ],
            },
        ),
    ]
