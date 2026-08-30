import logging
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile, UploadedFile
from django.urls import reverse
from drf_spectacular.utils import extend_schema_field
from PIL import Image, ImageOps
from rest_framework import serializers

from apps.lookbook.serializers import LookbookPostSerializer

from .models import (
    DailyLook,
    OutfitAnalysis,
    OutfitComposition,
    OutfitCompositionItem,
    OutfitRenderJob,
    ProductClickEvent,
    RecommendationFeedback,
    RecommendationResult,
    SavedOutfit,
    WishlistItem,
)
from .services import item_images, storage, wardrobe_link

MAX_OUTFIT_IMAGE_SIZE_MB = 15
ALLOWED_OUTFIT_IMAGE_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    logging.getLogger(__name__).warning(
        "pillow-heif 미설치: iPhone HEIC 착장 사진을 처리할 수 없습니다."
    )


def _normalize_outfit_heic(image: UploadedFile) -> UploadedFile:
    """인물사진·Live Photo의 정지 HEIC를 옷장 파이프라인 공용 JPEG로 만든다."""
    position = image.tell()
    image.seek(0)
    try:
        with Image.open(image) as opened:
            if (opened.format or "").upper() not in {"HEIC", "HEIF"}:
                image.seek(position)
                return image
            normalized = ImageOps.exif_transpose(opened)
            if normalized.mode != "RGB":
                normalized = normalized.convert("RGB")
            buffer = BytesIO()
            normalized.save(buffer, format="JPEG", quality=90, optimize=True)
    finally:
        image.seek(position)

    stem = Path(image.name or "outfit").stem
    return SimpleUploadedFile(
        f"{stem}.jpg",
        buffer.getvalue(),
        content_type="image/jpeg",
    )


class OutfitAnalysisRequestSerializer(serializers.Serializer):
    """코디 평가 사진과 선택적인 위치를 검증한다."""

    image = serializers.ImageField(
        help_text="평가할 코디 사진 (JPEG, PNG, WebP, HEIC, 최대 15MB)",
    )
    lat = serializers.FloatField(
        required=False,
        min_value=33.0,
        max_value=39.5,
        help_text="현재 위치 위도. 생략하면 서울 기준 날씨를 사용합니다.",
    )
    lon = serializers.FloatField(
        required=False,
        min_value=124.0,
        max_value=132.0,
        help_text="현재 위치 경도. 생략하면 서울 기준 날씨를 사용합니다.",
    )
    save_to_wardrobe = serializers.BooleanField(
        required=False,
        default=False,
        help_text=(
            "이 사진을 옷장 아이템 등록에도 넘길지 여부. "
            "**로그인 요청에만 적용**되며, 비로그인 요청에서는 무시됩니다(옷장은 사용자 소유 데이터). "
            "true면 응답의 wardrobe_job_id로 GET /api/v1/wardrobe/uploads/{job_id}/ 에서 "
            "등록 진행 상황을 따로 조회할 수 있습니다."
        ),
    )

    def validate_image(self, image: UploadedFile) -> UploadedFile:
        if image.size > MAX_OUTFIT_IMAGE_SIZE_MB * 1024 * 1024:
            raise serializers.ValidationError(
                f"이미지는 {MAX_OUTFIT_IMAGE_SIZE_MB}MB 이하여야 합니다."
            )
        if image.content_type not in ALLOWED_OUTFIT_IMAGE_CONTENT_TYPES:
            raise serializers.ValidationError(
                "지원하지 않는 이미지 형식입니다. JPEG, PNG, WebP, HEIC만 사용할 수 있습니다."
            )
        normalized = _normalize_outfit_heic(image)
        if normalized.size > MAX_OUTFIT_IMAGE_SIZE_MB * 1024 * 1024:
            raise serializers.ValidationError(
                f"변환된 이미지는 {MAX_OUTFIT_IMAGE_SIZE_MB}MB 이하여야 합니다."
            )
        return normalized

    def validate(self, attrs: dict) -> dict:
        if ("lat" in attrs) != ("lon" in attrs):
            raise serializers.ValidationError(
                "lat과 lon은 함께 입력하거나 모두 생략해야 합니다."
            )
        return attrs


class OutfitEvaluationSerializer(serializers.Serializer):
    overall_score = serializers.IntegerField(min_value=0, max_value=100)
    summary = serializers.CharField()
    strengths = serializers.ListField(child=serializers.CharField())
    weather_comment = serializers.CharField()
    personalization_comment = serializers.CharField()
    styling_tips = serializers.ListField(child=serializers.CharField())


class AnalysisContextSerializer(serializers.Serializer):
    weather = serializers.JSONField()
    personalized = serializers.BooleanField()
    used_pursuit = serializers.BooleanField()
    used_body = serializers.BooleanField()


class OutfitAnalysisAcceptedSerializer(serializers.Serializer):
    """202 접수 응답. 분석 결과는 poll_url로 따로 조회한다."""

    analysis_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=OutfitAnalysis.Status.choices)
    poll_url = serializers.CharField(
        help_text="결과 조회 경로. 이 URL을 poll_after_ms 간격으로 호출한다."
    )
    poll_after_ms = serializers.IntegerField(
        help_text="다음 조회까지 기다릴 시간(ms). 프론트가 간격을 하드코딩하지 않게 서버가 준다."
    )
    estimated_seconds = serializers.IntegerField(
        help_text="예상 소요 시간(초). 안내 문구용."
    )
    claim_token = serializers.CharField(
        allow_null=True,
        help_text=(
            "비로그인 접수 건의 소유권 이전용 1회성 토큰. 로그인 접수면 null. "
            "**이 응답에서만 받을 수 있으니 앱이 로컬에 보관**했다가, 로그인 직후 "
            "POST /api/v1/outfits/analyses/claim/ 으로 보내세요. 유효 시간이 짧습니다."
        ),
    )
    wardrobe_job_id = serializers.UUIDField(
        allow_null=True,
        help_text=(
            "옷장 등록 job ID. save_to_wardrobe를 요청하지 않았거나 비로그인이면 null. "
            "GET /api/v1/wardrobe/uploads/{job_id}/ 로 진행 상황을 조회합니다."
        ),
    )


class OutfitAnalysisPublicSerializer(serializers.ModelSerializer):
    """익명 조회용 — UUID만 아는 사람에게 내려주는 축소 응답.

    UUID는 URL·로그·Referer로 새어나갈 수 있다. 평가 문구가 노출되는 것과 본인 사진·
    체형이 노출되는 것은 무게가 다르므로, 개인 스냅샷과 LLM 원본은 여기서 뺀다.
    """

    analysis_id = serializers.UUIDField(source="id", read_only=True)
    evaluation = serializers.SerializerMethodField()
    context = serializers.SerializerMethodField()
    poll_after_ms = serializers.SerializerMethodField()
    detail = serializers.SerializerMethodField()

    class Meta:
        model = OutfitAnalysis
        fields = [
            "analysis_id",
            "status",
            "evaluation",
            "context",
            "poll_after_ms",
            "detail",
            "created_at",
            "finished_at",
        ]

    @extend_schema_field(OutfitEvaluationSerializer(allow_null=True))
    def get_evaluation(self, obj: OutfitAnalysis) -> dict | None:
        """완료 전에는 null. 프론트는 status로 판단하고 이 값은 참고만 한다."""
        return obj.evaluation

    @extend_schema_field(AnalysisContextSerializer)
    def get_context(self, obj: OutfitAnalysis) -> dict:
        # 개인화에 무엇이 쓰였는지는 알려주되(로그인 유도 안내에 쓴다) 값 자체는 주지 않는다
        return {
            "weather": obj.weather,
            "personalized": obj.personalized,
            "used_pursuit": obj.pursuit is not None,
            "used_body": obj.body is not None,
        }

    def get_poll_after_ms(self, obj: OutfitAnalysis) -> int | None:
        return settings.OUTFIT_POLL_AFTER_MS if obj.is_pending else None

    def get_detail(self, obj: OutfitAnalysis) -> str | None:
        """실패 사유(error_message)는 내부용이라 사용자 문구로 갈음한다."""
        if obj.status != OutfitAnalysis.Status.FAILED:
            return None
        return "코디 평가를 완료하지 못했습니다. 다시 시도해주세요."


class OutfitAnalysisListItemSerializer(serializers.ModelSerializer):
    """이력 목록용 요약. LLM 요청·응답 원본은 상세에서만 내려준다."""

    overall_score = serializers.IntegerField(allow_null=True, read_only=True)
    summary = serializers.SerializerMethodField()

    class Meta:
        model = OutfitAnalysis
        fields = [
            "id",
            "status",
            "overall_score",
            "summary",
            "weather",
            "personalized",
            "created_at",
        ]

    def get_summary(self, obj: OutfitAnalysis) -> str:
        return (obj.evaluation or {}).get("summary", "")


class WardrobeLinkedItemSerializer(serializers.Serializer):
    """옷장 등록이 끝난 뒤 생성된 아이템 1건의 요약.

    전체 태그(season/style/pattern/fit/material/sleeve/length/usage/layer_*/seg_meta)는
    옷장 API에서 본다 — GET /api/v1/wardrobe/items/ 또는
    GET /api/v1/wardrobe/uploads/{job_id}/.
    """

    id = serializers.UUIDField(help_text="옷장 아이템 UUID")
    item_name = serializers.CharField(allow_blank=True, help_text="아이템 표시 이름")
    category_large = serializers.CharField(help_text="대분류 (상의/하의/아우터 등)")
    category_small = serializers.CharField(allow_blank=True, help_text="소분류")
    color = serializers.CharField(allow_blank=True, help_text="색상 태그")
    image_url = serializers.CharField(
        allow_null=True,
        help_text="배경 제거·크롭된 아이템 이미지 presigned URL (발급 실패 시 null)",
    )
    confirmed = serializers.BooleanField(
        help_text="사용자 확정 여부. false면 태깅 확인 대기 상태다(추천 검색 제외)."
    )


class WardrobeLinkSerializer(serializers.Serializer):
    """save_to_wardrobe로 연계된 옷장 등록 job의 진행 상황과 결과."""

    job_id = serializers.UUIDField(help_text="옷장 등록 job UUID")
    status = serializers.CharField(
        help_text="등록 상태 (PENDING/PROCESSING/DONE/FAILED)"
    )
    error_message = serializers.CharField(
        allow_blank=True, help_text="등록 실패 사유 (FAILED가 아니면 빈 문자열)"
    )
    created_at = serializers.DateTimeField(help_text="job 생성 시각")
    finished_at = serializers.DateTimeField(
        allow_null=True, help_text="등록 종료 시각 (진행 중이면 null)"
    )
    items = WardrobeLinkedItemSerializer(
        many=True,
        help_text="생성된 옷장 아이템 요약. **status가 DONE일 때만** 채워진다.",
    )


class OutfitAnalysisDetailSerializer(serializers.ModelSerializer):
    """이력 상세. 질의에 쓴 스냅샷과 LLM 요청·응답 원본을 그대로 노출한다."""

    image_url = serializers.SerializerMethodField()
    wardrobe = serializers.SerializerMethodField()

    class Meta:
        model = OutfitAnalysis
        fields = [
            "id",
            "status",
            "image_url",
            "image_content_type",
            "image_bytes",
            "requested_lat",
            "requested_lon",
            "resolved_lat",
            "resolved_lon",
            "weather",
            "body",
            "pursuit",
            "personalized",
            "save_to_wardrobe",
            "wardrobe_job",
            "wardrobe",
            "llm_model",
            "request_payload",
            "response_payload",
            "evaluation",
            "llm_image_bytes",
            "latency_ms",
            "attempts",
            "error_message",
            "created_at",
            "started_at",
            "finished_at",
        ]

    def get_image_url(self, obj: OutfitAnalysis) -> str | None:
        """비공개 버킷이므로 presigned GET으로만 노출한다. 발급 실패 시 null."""
        if not obj.image_s3_key or not storage.is_configured():
            return None
        try:
            return storage.presigned_get(obj.image_s3_key)
        except Exception:  # noqa: BLE001 — URL 발급 실패가 조회를 막지 않는다
            return None

    @extend_schema_field(WardrobeLinkSerializer(allow_null=True))
    def get_wardrobe(self, obj: OutfitAnalysis) -> dict | None:
        """옷장 연계 job의 상태와(완료시) 생성된 아이템 요약.

        옷장 모델 접근은 services/wardrobe_link.py가 전담한다 (두 도메인을 섞지 않기 위해).
        """
        return wardrobe_link.job_summary(obj)


class OutfitAnalysisListResponseSerializer(serializers.Serializer):
    """페이지네이션 응답 (DRF 전역 페이지네이션 미설정이라 뷰에서 직접 구성)."""

    count = serializers.IntegerField()
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    results = OutfitAnalysisListItemSerializer(many=True)


class OutfitAnalysisClaimRequestSerializer(serializers.Serializer):
    """로그인 직후 넘겨받을 익명 접수 건들."""

    claim_tokens = serializers.ListField(
        child=serializers.CharField(),
        min_length=1,
        max_length=settings.OUTFIT_CLAIM_MAX_ITEMS,
        help_text=(
            "접수 응답에서 받은 claim_token 목록. 토큰 안에 대상 식별자가 들어 있어 "
            "analysis_id를 따로 보낼 필요가 없습니다."
        ),
    )


class OutfitAnalysisClaimSkippedSerializer(serializers.Serializer):
    analysis_id = serializers.UUIDField(allow_null=True)
    reason = serializers.ChoiceField(
        choices=["invalid_token", "expired", "not_found", "already_owned"]
    )


class OutfitAnalysisClaimResponseSerializer(serializers.Serializer):
    claimed = serializers.ListField(
        child=serializers.UUIDField(),
        help_text="소유권이 넘어온 평가 ID. 이미 본인 것이던 건도 포함합니다(멱등).",
    )
    skipped = OutfitAnalysisClaimSkippedSerializer(many=True)


logger = logging.getLogger(__name__)


def _image_url(row: dict | None) -> str | None:
    """S3 참조를 조회용 URL로 바꾼다.

    **조회 시점에** 서명한다. presigned URL은 만료되므로 DB에 미리 구워 넣으면
    며칠 뒤 죽은 링크가 남는다 (같은 이유로 Qdrant payload에도 넣지 않았다).

    서명 실패가 추천 조회 전체를 막지는 않게 한다 — 이미지가 없는 화면이
    500 화면보다 낫다.
    """
    if not row or not row.get("s3_key") or not row.get("s3_bucket"):
        return None
    try:
        return storage.presigned_get_for(str(row["s3_bucket"]), str(row["s3_key"]))
    except Exception:  # noqa: BLE001
        logger.exception("오늘의 룩 이미지 URL 생성 실패: %s", row.get("s3_key"))
        return None


class DailyLookItemSerializer(serializers.Serializer):
    """착장에 속한 의상 아이템 한 개.

    이미지는 원본 코디 사진이 아니라 파이프라인이 만든 흰 배경 파생물이다.
    그래서 원본이 노출 불가여도 이 이미지는 보여줄 수 있다.
    """

    item_key = serializers.CharField()
    name = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    sub_category = serializers.CharField(required=False, allow_blank=True)
    layer_role = serializers.CharField(required=False, allow_blank=True)
    color = serializers.CharField(required=False, allow_blank=True)
    note = serializers.CharField(required=False, allow_blank=True)
    image_url = serializers.SerializerMethodField()

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_image_url(self, obj: dict) -> str | None:
        return _image_url(obj)


class DailyLookResultSerializer(serializers.Serializer):
    """생성이 끝났을 때만 채워지는 추천 본문."""

    headline = serializers.CharField()
    golden_id = serializers.CharField()
    rationale_ko = serializers.CharField()
    styling_tips = serializers.ListField(child=serializers.CharField(), required=False)
    tags = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text=(
            "룩북 필터와 **같은 어휘**의 태그 (apps/lookbook/contracts.py LOOKBOOK_TAGS). "
            "골든 코디의 occasion·style, 그것이 비면 사용자 추구미에서 뽑는다. "
            "하나도 못 만들면 빈 배열이며, 그때 프론트는 태그 줄을 숨긴다 — "
            "아이템 이름을 태그처럼 보여주면 룩북과 어휘가 갈린다."
        ),
    )
    generated_by = serializers.CharField(
        required=False,
        help_text="문장을 누가 썼는지: llm | template. template이면 담백한 톤이다.",
    )
    items = DailyLookItemSerializer(many=True, required=False)
    render_image_url = serializers.SerializerMethodField()
    outfit_image_url = serializers.SerializerMethodField()

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_render_image_url(self, obj: dict) -> str | None:
        """정면 착용 이미지. 화면의 대표 이미지로 쓰는 값이다.

        골든 원본과 달리 사용권 제약이 없다 — 아이템 이미지를 참조로 새로 만든
        것이라 특정 인물이 담기지 않는다. 생성이 아직/실패면 null이며, 그때는
        items[].image_url 카드로 화면이 성립한다.
        """
        return _image_url(obj.get("render_image"))

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_outfit_image_url(self, obj: dict) -> str | None:
        """원본 코디 사진. 사용권이 열린 코디(exposable)에만 값이 있다."""
        return _image_url(obj.get("outfit_image"))


class DailyLookSerializer(serializers.ModelSerializer):
    """오늘의 룩 조회 응답.

    생성 전에도 200으로 내려간다. 404를 쓰면 프론트가 "없음"과 "아직"을 구분하지
    못하고, 202는 본문 스키마가 다른 응답을 만들어 클라이언트 분기를 늘린다.
    `status`와 `poll_after_ms` 두 필드로 판단하게 한다.
    """

    look_id = serializers.UUIDField(source="id", read_only=True)
    result = serializers.SerializerMethodField()
    alternatives = serializers.SerializerMethodField()
    context = serializers.SerializerMethodField()
    poll_after_ms = serializers.SerializerMethodField()
    detail = serializers.SerializerMethodField()

    class Meta:
        model = DailyLook
        fields = [
            "look_id",
            "look_date",
            "status",
            "result",
            "alternatives",
            "context",
            "poll_after_ms",
            "detail",
            "created_at",
            "updated_at",
        ]

    @extend_schema_field(DailyLookResultSerializer(allow_null=True))
    def get_result(self, obj: DailyLook) -> dict | None:
        """생성 전(result={})에는 null. 프론트는 status로 분기한다.

        중첩 시리얼라이저를 필드로 직접 붙이면 빈 dict가 들어올 때 필수
        필드(headline)에서 KeyError가 나 조회 전체가 500이 된다 — 생성 전
        행은 result가 {}인 것이 정상 상태라서 여기서 걸러 null로 내린다.
        """
        if not obj.result:
            return None
        return DailyLookResultSerializer(obj.result).data

    @extend_schema_field(DailyLookResultSerializer(many=True))
    def get_alternatives(self, obj: DailyLook) -> list[dict]:
        """'다른 룩'으로 돌려볼 차순위 후보. `result`와 **같은 스키마**다.

        프론트는 대표 룩과 후보를 한 배열로 이어 붙여 카드 하나를 그리는 코드를
        그대로 쓴다. 문장은 템플릿이라 `generated_by`가 `template`이고
        (LLM은 대표 룩에만 붙인다), `render_image_url`은 후보 이미지 생성이
        끝나기 전까지 null이다 — 그때는 `items[].image_url`로 카드가 성립한다.

        저장(POST /looks/today/save/)에 여기 `golden_id`를 그대로 보내면 된다.
        """
        return [
            DailyLookResultSerializer(row).data
            for row in (obj.alternatives or [])
            if isinstance(row, dict) and row.get("golden_id")
        ]

    def get_context(self, obj: DailyLook) -> dict:
        """무엇이 개인화에 쓰였는지만 알려준다 (값 자체는 프로필 API에 있다)."""
        profile = obj.body_profile or {}
        return {
            "weather": obj.weather,
            "used_body": obj.body is not None,
            "used_pursuit": obj.pursuit is not None,
            "body_profile": profile.get("describe", ""),
            # 판정하지 못한 치수를 알려주면 프론트가 "어깨너비를 입력하면 더
            # 정확해져요" 같은 안내를 띄울 수 있다.
            "missing_measurements": profile.get("missing", []),
            "candidate_count": len(obj.candidates or []),
        }

    def get_poll_after_ms(self, obj: DailyLook) -> int | None:
        return settings.OUTFIT_POLL_AFTER_MS if obj.is_pending else None

    def get_detail(self, obj: DailyLook) -> str | None:
        """상태별 사용자 문구. 내부 error는 그대로 노출하지 않는다."""
        if obj.status in DailyLook.PENDING_STATUSES:
            return "오늘의 룩을 만들고 있어요. 잠시만 기다려주세요."
        if obj.status == DailyLook.Status.EMPTY:
            # 재시도해도 같은 결과다. 프론트는 프로필 입력을 유도해야 한다.
            return (
                "조건에 맞는 추천을 찾지 못했어요. "
                "신체치수나 추구미를 입력하면 더 잘 찾을 수 있어요."
            )
        if obj.status == DailyLook.Status.FAILED:
            return "오늘의 룩을 만들지 못했어요. 잠시 후 다시 확인해주세요."
        return None


class DailyLookSaveRequestSerializer(serializers.Serializer):
    """오늘의 룩 저장 입력.

    `golden_id`는 '다른 룩'으로 돌려보던 후보를 담기 위한 값이다. 생략하면 대표
    룩이다.

    이 값을 받는다고 아무 코디나 담을 수 있는 것은 아니다 — **서버가 그 사용자의
    오늘 후보(result + alternatives) 안에 있는지 확인한다.** 클라이언트가 코디를
    지정하되 목록은 서버가 정하는 구조라, "남의 코디를 담을 수 있는 구멍"은
    그대로 막혀 있다.
    """

    golden_id = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=100,
        help_text="담을 룩의 골든 코디 id. 생략하면 대표 룩(result.golden_id).",
    )


class DailyLookSaveResponseSerializer(serializers.Serializer):
    """오늘의 룩 저장 응답.

    `created`가 false면 이미 담아 둔 코디라는 뜻이고 `lookbook`은 그때 만든
    행이다. 프론트는 이 값으로 "담았어요"와 "이미 담겨 있어요"를 가른다 —
    상태코드(201/200)만으로 가르게 하면 재시도·프록시 때문에 흔들린다.

    `lookbook`은 룩북 목록(GET /api/v1/lookbooks/)의 항목과 같은 스키마다.
    저장 직후 룩북 화면으로 이동하는 흐름이라, 프론트가 목록을 다시 부르지 않고
    이 응답만으로 카드를 그릴 수 있어야 한다.
    """

    created = serializers.BooleanField(
        help_text="새로 담았으면 true, 이미 담아 둔 코디면 false"
    )
    lookbook = LookbookPostSerializer(read_only=True)


class RecommendationHistoryQuerySerializer(serializers.Serializer):
    """추천 이력 필터와 offset 페이지네이션 입력."""

    mode = serializers.ChoiceField(
        choices=RecommendationResult.Mode.choices,
        required=False,
    )
    limit = serializers.IntegerField(default=20, min_value=1, max_value=100)
    offset = serializers.IntegerField(default=0, min_value=0)


class RecommendationFeedbackRequestSerializer(serializers.Serializer):
    """카드별 최신 피드백 입력. PUT할 때 전체 상태를 교체한다."""

    reaction = serializers.ChoiceField(
        choices=RecommendationFeedback.Reaction.choices,
        help_text="추천 반응: LIKE 또는 DISLIKE",
    )
    reason_codes = serializers.ListField(
        child=serializers.RegexField(r"^[A-Z][A-Z0-9_]{0,49}$"),
        required=False,
        default=list,
        max_length=5,
        help_text=(
            "선택 사유 코드 목록 (최대 5개). 예: STYLE, COLOR, FIT, PRICE, "
            "ALREADY_OWNED"
        ),
    )
    comment = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=500,
        trim_whitespace=True,
        help_text="선택 입력 자유 의견 (최대 500자)",
    )

    def validate_reason_codes(self, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise serializers.ValidationError("피드백 사유 코드는 중복될 수 없습니다.")
        return value


class RecommendationFeedbackSerializer(serializers.ModelSerializer):
    feedback_id = serializers.UUIDField(source="id", read_only=True)

    class Meta:
        model = RecommendationFeedback
        fields = [
            "feedback_id",
            "reaction",
            "reason_codes",
            "comment",
            "created_at",
            "updated_at",
        ]


class SavedOutfitSerializer(serializers.ModelSerializer):
    saved_outfit_id = serializers.UUIDField(source="id", read_only=True)
    card_id = serializers.UUIDField(source="composition_id", read_only=True)
    is_saved = serializers.BooleanField(default=True, read_only=True)
    saved_at = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = SavedOutfit
        fields = [
            "saved_outfit_id",
            "card_id",
            "is_saved",
            "saved_at",
        ]


class ProductClickEventSerializer(serializers.ModelSerializer):
    product_click_id = serializers.UUIDField(source="id", read_only=True)
    result_id = serializers.UUIDField(source="result_id_snapshot", read_only=True)
    card_id = serializers.UUIDField(
        source="composition_id_snapshot",
        read_only=True,
    )
    item_id = serializers.UUIDField(read_only=True)
    persona_id = serializers.SerializerMethodField()
    deduplicated = serializers.BooleanField(default=False, read_only=True)
    clicked_at = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = ProductClickEvent
        fields = [
            "product_click_id",
            "result_id",
            "card_id",
            "item_id",
            "persona_id",
            "source_collection",
            "source_id",
            "deduplicated",
            "clicked_at",
            "engagement_duration_ms",
            "engagement_recorded_at",
        ]

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_persona_id(self, obj: ProductClickEvent) -> str | None:
        return obj.persona_id or None


class ProductClickEngagementRequestSerializer(serializers.Serializer):
    duration_ms = serializers.IntegerField(
        min_value=0,
        max_value=86_400_000,
        help_text="외부 판매처 이동 후 앱 복귀까지 측정한 근사 체류 시간(ms, 최대 24시간)",
    )


def _snapshot_text(snapshot: object, *keys: str) -> str | None:
    if not isinstance(snapshot, dict):
        return None
    for key in keys:
        value = snapshot.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _snapshot_label(snapshot: object, *keys: str) -> str | None:
    """표시용 태그를 문자열 또는 문자열 배열에서 안전하게 꺼낸다."""

    if not isinstance(snapshot, dict):
        return None
    for key in keys:
        value = snapshot.get(key)
        if isinstance(value, str):
            if text := value.strip():
                return text
            continue
        if isinstance(value, (list, tuple)):
            labels = [
                entry.strip()
                for entry in value
                if isinstance(entry, str) and entry.strip()
            ]
            if labels:
                return ", ".join(labels)
    return None


class RecommendationCardItemSerializer(serializers.ModelSerializer):
    item_id = serializers.UUIDField(source="id", read_only=True)
    display_name = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()
    color = serializers.SerializerMethodField()
    purchase_url = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = OutfitCompositionItem
        fields = [
            "item_id",
            "position",
            "slot",
            "source_type",
            "source_id",
            "display_name",
            "category",
            "color",
            "image_ref",
            "image_url",
            "price_snapshot",
            "purchase_url",
            "reasons",
            "note",
        ]

    def get_display_name(self, obj: OutfitCompositionItem) -> str:
        return (
            _snapshot_text(
                obj.item_snapshot,
                "display_name",
                "item_name",
                "product_name",
                "name",
                "title",
            )
            or obj.slot
        )

    def get_category(self, obj: OutfitCompositionItem) -> str | None:
        return _snapshot_label(
            obj.item_snapshot,
            "category_small",
            "category",
            "category_name",
            "category_large",
        )

    def get_color(self, obj: OutfitCompositionItem) -> str | None:
        return _snapshot_label(obj.item_snapshot, "color", "base_color")

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_image_url(self, obj: OutfitCompositionItem):
        """화면에 바로 걸 수 있는 주소. image_ref는 대부분 비공개 S3 키다."""
        return item_images.image_url_for(obj)

    def get_purchase_url(self, obj: OutfitCompositionItem) -> str | None:
        if obj.source_type != OutfitCompositionItem.SourceType.PRODUCT:
            return None
        return _snapshot_text(
            obj.item_snapshot,
            "purchase_url",
            "product_url",
            "link",
            "url",
        )

class WishlistItemSerializer(serializers.ModelSerializer):
    """찜 한 줄. 앱은 이 값만으로 목록을 그리고 판매처로 나간다.

    이미지·가격은 담은 시점 스냅샷이다 — 카탈로그 가격이 바뀌어도 담을 때 본 값이
    목록에 남아야 사용자가 왜 담았는지 알 수 있다.
    """

    wish_id = serializers.UUIDField(source="id", read_only=True)
    item_id = serializers.UUIDField(read_only=True, allow_null=True)
    result_id = serializers.UUIDField(source="result_id_snapshot", read_only=True)
    card_id = serializers.UUIDField(source="composition_id_snapshot", read_only=True)
    added_at = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = WishlistItem
        fields = [
            "wish_id",
            "item_id",
            "result_id",
            "card_id",
            "source_collection",
            "source_id",
            "display_name",
            "brand",
            "price_snapshot",
            "image_ref",
            "purchase_url",
            "slot",
            "added_at",
        ]
        read_only_fields = fields


class RecommendationCardSerializer(serializers.ModelSerializer):
    card_id = serializers.UUIDField(source="id", read_only=True)
    items = RecommendationCardItemSerializer(many=True, read_only=True)
    feedback = serializers.SerializerMethodField()
    is_saved = serializers.SerializerMethodField()

    class Meta:
        model = OutfitComposition
        fields = [
            "card_id",
            "rank",
            "total_product_price",
            "validation_reasons",
            "reference_match",
            "warnings",
            "rationale",
            "items",
            "feedback",
            "is_saved",
        ]

    @extend_schema_field(RecommendationFeedbackSerializer(allow_null=True))
    def get_feedback(self, obj: OutfitComposition) -> dict | None:
        try:
            feedback = obj.feedback
        except RecommendationFeedback.DoesNotExist:
            return None
        return RecommendationFeedbackSerializer(feedback).data

    @extend_schema_field(serializers.BooleanField())
    def get_is_saved(self, obj: OutfitComposition) -> bool:
        return bool(obj.saved_records.all())

class OutfitRenderJobSerializer(serializers.ModelSerializer):
    job_id = serializers.UUIDField(source="id", read_only=True)
    card_id = serializers.UUIDField(source="composition_id", read_only=True)
    image_url = serializers.SerializerMethodField()
    events_url = serializers.SerializerMethodField()
    error = serializers.SerializerMethodField()

    class Meta:
        model = OutfitRenderJob
        fields = [
            "job_id",
            "card_id",
            "status",
            "cache_hit",
            "image_url",
            "output_media_type",
            "output_bytes",
            "provider",
            "model",
            "prompt_version",
            "reference_count",
            "attempts",
            "error",
            "events_url",
            "enqueued_at",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
        ]

    def get_image_url(self, obj: OutfitRenderJob) -> str | None:
        if (
            obj.status != OutfitRenderJob.Status.SUCCEEDED
            or not obj.output_s3_bucket
            or not obj.output_s3_key
        ):
            return None
        return storage.presigned_get_for(
            obj.output_s3_bucket,
            obj.output_s3_key,
            ttl=settings.OUTFIT_RENDER_PRESIGNED_GET_TTL_SECONDS,
        )

    def get_events_url(self, obj: OutfitRenderJob) -> str:
        path = reverse("recommend:outfit-render-events", args=[obj.pk])
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request is not None else path

    def get_error(self, obj: OutfitRenderJob) -> dict | None:
        if obj.status != OutfitRenderJob.Status.FAILED:
            return None
        return {"code": obj.error_code, "message": obj.error_message}


class VirtualTryOnRequestSerializer(serializers.Serializer):
    """가상 착장 공통 입력 (채팅 추천 카드용)."""

    person_image = serializers.ImageField(
        help_text="얼굴·체형·포즈를 유지할 정면 전신 사진 (JPEG, PNG, WebP)",
    )
    mode = serializers.ChoiceField(
        choices=["person", "mannequin"],
        default="person",
        help_text="person은 사용자에게 바로 착장, mannequin은 체형 마네킹에 추천 룩을 바로 착장",
    )

    def validate_person_image(self, image: UploadedFile) -> UploadedFile:
        if image.size > settings.VIRTUAL_TRY_ON_MAX_PERSON_IMAGE_BYTES:
            raise serializers.ValidationError("전신 사진의 허용 크기를 초과했습니다.")
        if image.content_type not in ALLOWED_OUTFIT_IMAGE_CONTENT_TYPES:
            raise serializers.ValidationError("JPEG, PNG, WebP 사진만 사용할 수 있습니다.")
        return image


class DailyLookVirtualTryOnRequestSerializer(VirtualTryOnRequestSerializer):
    """오늘의 룩 가상 피팅 입력.

    `golden_id`는 여기에만 있다 — '다른 룩' 후보라는 개념이 오늘의 룩에만 있기
    때문이다. 공통 입력에 두면 채팅 카드 문서에도 쓰지 않는 필드가 실린다.
    """

    golden_id = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=100,
        help_text=(
            "입힐 룩의 골든 코디 id. 생략하면 대표 룩. "
            "'다른 룩'으로 돌려보던 후보를 입어볼 때 쓴다 — 저장 API와 같은 규칙으로, "
            "서버가 그 사용자의 오늘 후보 안에 있는지 확인한다."
        ),
    )


class VirtualTryOnResultSerializer(serializers.Serializer):
    """가상 착장 결과 (채팅 추천 카드 — **동기**).

    이 경로는 요청 스레드에서 이미지를 만들어 바로 돌려준다. 생성이 수십 초~2분이라
    앞단 프록시가 먼저 끊을 수 있다(Cloudflare 터널 100초 → 524). 오늘의 룩은 같은
    이유로 접수·조회를 나눴다(VirtualTryOnJobSerializer) — 이 경로도 화면에 붙일 때
    같은 구조로 옮기는 것이 좋다.
    """

    mode = serializers.ChoiceField(choices=["person", "mannequin"])
    image_url = serializers.URLField()
    cache_hit = serializers.BooleanField()


class VirtualTryOnJobSerializer(serializers.Serializer):
    """가상 피팅 작업 상태 (오늘의 룩 — **비동기**).

    접수(POST)와 조회(GET)가 **같은 본문**을 쓴다. 화면이 두 응답을 다르게 읽으면
    "요청 직후"와 "다시 들어왔을 때"가 갈라져 분기가 늘어난다.

    - `QUEUED`/`PROCESSING`: `poll_after_ms` 뒤에 다시 조회한다
    - `SUCCEEDED`: `image_url` 표시 (조회마다 새로 서명되므로 캐시하면 만료된다)
    - `FAILED`: `detail` 을 보여주고 재시도를 권한다
    - `status=null`: 이 룩으로 아직 한 번도 만든 적이 없다
    """

    job_id = serializers.UUIDField(allow_null=True)
    status = serializers.ChoiceField(
        choices=["QUEUED", "PROCESSING", "SUCCEEDED", "FAILED"], allow_null=True
    )
    mode = serializers.CharField(allow_blank=True)
    golden_id = serializers.CharField(
        allow_blank=True, help_text="어느 룩을 입혔는지 (빈 값이면 대표 룩)"
    )
    image_url = serializers.URLField(allow_null=True)
    cache_hit = serializers.BooleanField()
    poll_after_ms = serializers.IntegerField(
        allow_null=True, help_text="생성 중일 때만 값이 있다 — 이 간격 뒤 재조회"
    )
    detail = serializers.CharField(allow_null=True, help_text="상태별 사용자 안내 문구")


class RecommendationHistoryItemSerializer(serializers.ModelSerializer):
    result_id = serializers.UUIDField(source="id", read_only=True)
    replaces_result_id = serializers.UUIDField(source="replaces_id", read_only=True)
    card_count = serializers.SerializerMethodField()
    top_card = serializers.SerializerMethodField()

    class Meta:
        model = RecommendationResult
        fields = [
            "result_id",
            "session_id",
            "mode",
            "response_mode",
            "persona_id",
            "persona_version",
            "persona_explanation",
            "result_type",
            "generation",
            "is_current",
            "replaces_result_id",
            "created_at",
            "card_count",
            "top_card",
        ]

    def get_card_count(self, obj: RecommendationResult) -> int:
        return len(getattr(obj, "public_compositions", ()))

    @extend_schema_field(RecommendationCardSerializer(allow_null=True))
    def get_top_card(self, obj: RecommendationResult) -> dict | None:
        cards = getattr(obj, "public_compositions", ())
        return RecommendationCardSerializer(cards[0]).data if cards else None


class RecommendationHistoryResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    results = RecommendationHistoryItemSerializer(many=True)


class RecommendationResultDetailSerializer(serializers.ModelSerializer):
    result_id = serializers.UUIDField(source="id", read_only=True)
    replaces_result_id = serializers.UUIDField(source="replaces_id", read_only=True)
    cards = serializers.SerializerMethodField()

    class Meta:
        model = RecommendationResult
        fields = [
            "result_id",
            "session_id",
            "run_id",
            "mode",
            "response_mode",
            "persona_id",
            "persona_version",
            "persona_explanation",
            "validated_reason_codes",
            "result_type",
            "generation",
            "is_current",
            "replaces_result_id",
            "dataset_version",
            "created_at",
            "cards",
        ]

    @extend_schema_field(RecommendationCardSerializer(many=True))
    def get_cards(self, obj: RecommendationResult) -> list[dict]:
        cards = getattr(obj, "public_compositions", ())
        return RecommendationCardSerializer(cards, many=True).data
