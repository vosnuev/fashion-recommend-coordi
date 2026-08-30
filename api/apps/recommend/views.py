import hashlib
import logging
import re
from datetime import timedelta
from pathlib import Path

import redis
from django.conf import settings
from django.db import close_old_connections
from django.http import StreamingHttpResponse
from django.urls import reverse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    PolymorphicProxySerializer,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.exceptions import NotAuthenticated, NotFound
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chat.openapi import (
    CHAT_IDENTITY_GUIDE,
    CHAT_SSE_GUIDE,
    CHAT_TAG,
    CHAT_UUID_GUIDE,
    path_uuid_parameter,
)
from apps.chat.renderers import ServerSentEventRenderer
from apps.chat.services import identity as identity_service
from apps.lookbook.serializers import LookbookPostSerializer

from .models import DailyLook, OutfitAnalysis, OutfitRenderJob
from .serializers import (
    DailyLookSaveRequestSerializer,
    DailyLookSaveResponseSerializer,
    DailyLookSerializer,
    OutfitAnalysisAcceptedSerializer,
    OutfitAnalysisClaimRequestSerializer,
    OutfitAnalysisClaimResponseSerializer,
    OutfitAnalysisDetailSerializer,
    OutfitAnalysisListItemSerializer,
    OutfitAnalysisListResponseSerializer,
    OutfitAnalysisPublicSerializer,
    OutfitAnalysisRequestSerializer,
    OutfitRenderJobSerializer,
    ProductClickEngagementRequestSerializer,
    ProductClickEventSerializer,
    WishlistItemSerializer,
    RecommendationCardSerializer,
    RecommendationFeedbackRequestSerializer,
    RecommendationFeedbackSerializer,
    RecommendationHistoryItemSerializer,
    RecommendationHistoryQuerySerializer,
    RecommendationHistoryResponseSerializer,
    RecommendationResultDetailSerializer,
    DailyLookVirtualTryOnRequestSerializer,
    VirtualTryOnJobSerializer,
    VirtualTryOnRequestSerializer,
    VirtualTryOnResultSerializer,
    SavedOutfitSerializer,
)
from .services import analysis as analysis_service
from .services import claim as claim_service
from .services import daily_look as daily_look_service
from .services import daily_look_save
from .services import recommendation_results as recommendation_service
from .services import render_jobs, render_queue, storage
from .services import virtual_try_on_jobs
from .services.mixed_outfit_render import OutfitRenderError
from .services.render_events import (
    RenderEvent,
    RenderEventStore,
    encode_sse,
    heartbeat,
)
from .services.virtual_try_on import (
    DIRECT_PROMPT_VERSION,
    MANNEQUIN_PROMPT_VERSION,
    VirtualTryOnService,
)

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_LIMIT = 20
MAX_HISTORY_LIMIT = 100
_REDIS_STREAM_ID = re.compile(r"^(?:0|[1-9]\d*)-(?:0|[1-9]\d*)$")

_RESULT_ID_PARAMETER = path_uuid_parameter(
    name="result_id",
    source="GET /api/v1/recommendations/ 또는 AI 답변 metadata의 result_id를 입력합니다.",
    example="44444444-4444-4444-8444-444444444444",
)
_CARD_ID_PARAMETER = path_uuid_parameter(
    name="card_id",
    source="GET /api/v1/recommendations/{result_id}/ 응답의 cards[].card_id를 입력합니다.",
    example="55555555-5555-4555-8555-555555555555",
)
_ITEM_ID_PARAMETER = path_uuid_parameter(
    name="item_id",
    source=(
        "GET /api/v1/recommendations/{result_id}/ 응답의 "
        "cards[].items[].item_id를 입력합니다."
    ),
    example="77777777-7777-4777-8777-777777777777",
)
_WISH_ID_PARAMETER = path_uuid_parameter(
    name="wish_id",
    source="GET /api/v1/wishlist/ 응답의 wish_id를 입력합니다.",
    example="99999999-9999-4999-8999-999999999999",
)
_PRODUCT_CLICK_ID_PARAMETER = path_uuid_parameter(
    name="product_click_id",
    source="POST .../items/{item_id}/click/ 응답의 product_click_id를 입력합니다.",
    example="88888888-8888-4888-8888-888888888888",
)
_JOB_ID_PARAMETER = path_uuid_parameter(
    name="job_id",
    source="POST .../render/ 응답의 job_id를 입력합니다.",
    example="66666666-6666-4666-8666-666666666666",
)

_RECOMMENDATION_MODE_PARAMETER = OpenApiParameter(
    name="mode",
    type=OpenApiTypes.STR,
    enum=["WARDROBE_BASED", "NEW_ITEM"],
    location=OpenApiParameter.QUERY,
    required=False,
    description="추천 모드 필터. 비우면 두 모드를 모두 조회합니다.",
    examples=[OpenApiExample(name="새 상품 포함 추천", value="NEW_ITEM")],
)
_RECOMMENDATION_LIMIT_PARAMETER = OpenApiParameter(
    name="limit",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.QUERY,
    required=False,
    description="한 번에 조회할 추천 결과 수 (1~100, 기본값 20)",
    default=20,
    examples=[OpenApiExample(name="20개 조회", value=20)],
)
_RECOMMENDATION_OFFSET_PARAMETER = OpenApiParameter(
    name="offset",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.QUERY,
    required=False,
    description="건너뛸 추천 결과 수 (0 이상, 첫 페이지 기본값 0)",
    default=0,
    examples=[OpenApiExample(name="첫 페이지", value=0)],
)


def _recommendation_identity(request: Request):
    """회원 JWT 또는 게스트 채팅 쿠키를 같은 추천 소유자로 해석한다."""
    guest_token = request.COOKIES.get(settings.CHAT_GUEST_COOKIE_NAME, "")
    try:
        identity = identity_service.resolve_identity(
            user=request.user,
            guest_token=guest_token,
        )
    except identity_service.ChatIdentityError as exc:
        raise NotAuthenticated(
            {"code": exc.code, "detail": "유효한 채팅 identity가 필요합니다."}
        ) from exc

    if identity.identity_type == identity.IdentityType.GUEST:
        django_request = getattr(request, "_request", request)
        django_request.chat_guest_cookie_refresh_token = guest_token
    return identity


def _positive_int(raw: str | None, *, default: int) -> int:
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


# ──────────────────────────────────────────────────────────────
# 조회 응답 예시 (Swagger)
#
# 이 엔드포인트는 인증 여부에 따라 응답 모양이 둘로 갈린다. 스키마에 한쪽만
# 적어두면 나머지 한쪽의 필드(사진 URL·체형 스냅샷·옷장 연계)가 문서에 아예
# 드러나지 않으므로 oneOf로 둘 다 노출하고, 예시로 구분한다.
# ──────────────────────────────────────────────────────────────

_EVALUATION_EXAMPLE = {
    "overall_score": 88,
    "summary": "색상 조화가 안정적이고 계절감에 맞는 코디입니다.",
    "strengths": ["상하의 명도 대비가 좋습니다.", "실루엣이 깔끔합니다."],
    "weather_comment": "현재 기온 24도에 적당한 두께입니다.",
    "personalization_comment": "선호하시는 미니멀 무드와 잘 맞습니다.",
    "styling_tips": ["밝은 색 가방을 더하면 포인트가 생깁니다."],
}
_WEATHER_EXAMPLE = {
    "region": "서울특별시 종로구",
    "temperature": 24.0,
    "sky_state": "맑음",
    "is_stale": False,
    "observed_at": "2026-08-06T14:00:00+09:00",
}

ANONYMOUS_RESULT_EXAMPLE = OpenApiExample(
    name="비로그인 조회 (축소 응답)",
    description=(
        "UUID만 알면 볼 수 있는 응답이라 사진 URL·체형 스냅샷·LLM 원본은 빠진다. "
        "옷장은 사용자 소유 데이터라 `wardrobe` 키 자체가 없다."
    ),
    value={
        "analysis_id": "9f1c2d3e-4a5b-4c6d-8e7f-0a1b2c3d4e5f",
        "status": "SUCCEEDED",
        "evaluation": _EVALUATION_EXAMPLE,
        "context": {
            "weather": _WEATHER_EXAMPLE,
            "personalized": False,
            "used_pursuit": False,
            "used_body": False,
        },
        "poll_after_ms": None,
        "detail": None,
        "created_at": "2026-08-06T14:58:02+09:00",
        "finished_at": "2026-08-06T14:58:29+09:00",
    },
    response_only=True,
)

OWNER_WARDROBE_DONE_EXAMPLE = OpenApiExample(
    name="본인 조회 · 옷장 등록까지 완료(DONE)",
    description=(
        "`save_to_wardrobe=true`로 접수한 건을 본인 토큰으로 조회한 경우. "
        "`wardrobe.status`가 DONE이라 `wardrobe.items`에 생성된 아이템 요약이 채워진다."
    ),
    value={
        "id": "9f1c2d3e-4a5b-4c6d-8e7f-0a1b2c3d4e5f",
        "status": "SUCCEEDED",
        "image_url": "https://s3.ap-northeast-2.amazonaws.com/…/original.jpg?X-Amz-Signature=…",
        "image_content_type": "image/jpeg",
        "image_bytes": 2481920,
        "requested_lat": 37.5729,
        "requested_lon": 126.9794,
        "resolved_lat": 37.5729,
        "resolved_lon": 126.9794,
        "weather": _WEATHER_EXAMPLE,
        "body": {"gender": "female", "height": 165, "weight": 52},
        "pursuit": {
            "preferred": {"styles": ["미니멀", "캐주얼"]},
            "avoided": {"styles": ["스포티"]},
        },
        "personalized": True,
        "save_to_wardrobe": True,
        "wardrobe_job": "3f2a7c81-2b44-4a90-9c1e-77d0f5a1b2c3",
        "wardrobe": {
            "job_id": "3f2a7c81-2b44-4a90-9c1e-77d0f5a1b2c3",
            "status": "DONE",
            "error_message": "",
            "created_at": "2026-08-06T14:58:03+09:00",
            "finished_at": "2026-08-06T14:59:41+09:00",
            "items": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "item_name": "화이트 옥스포드 셔츠",
                    "category_large": "상의",
                    "category_small": "셔츠",
                    "color": "화이트",
                    "image_url": "https://s3.ap-northeast-2.amazonaws.com/…/item_01.png?X-Amz-Signature=…",
                    "confirmed": False,
                },
                {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "item_name": "연청 슬림 진",
                    "category_large": "하의",
                    "category_small": "청바지",
                    "color": "블루",
                    "image_url": "https://s3.ap-northeast-2.amazonaws.com/…/item_02.png?X-Amz-Signature=…",
                    "confirmed": False,
                },
            ],
        },
        "llm_model": "gemini-3.5-flash",
        "request_payload": {
            "systemInstruction": {"parts": [{"text": "당신은 패션 스타일리스트입니다…"}]},
            "contents": [
                {
                    "parts": [
                        {"text": "날씨: 서울특별시 종로구 24도 맑음…"},
                        {
                            "inlineData": {
                                "mimeType": "image/jpeg",
                                "data": "<image omitted: 184320 bytes>",
                            }
                        },
                    ]
                }
            ],
        },
        "response_payload": {
            "candidates": [{"content": {"parts": [{"text": "{\"overall_score\": 88, …}"}]}}],
            "usageMetadata": {"totalTokenCount": 1234},
        },
        "evaluation": _EVALUATION_EXAMPLE,
        "llm_image_bytes": 184320,
        "latency_ms": 8452,
        "attempts": 1,
        "error_message": "",
        "created_at": "2026-08-06T14:58:02+09:00",
        "started_at": "2026-08-06T14:58:05+09:00",
        "finished_at": "2026-08-06T14:58:29+09:00",
    },
    response_only=True,
)

OWNER_WARDROBE_PENDING_EXAMPLE = OpenApiExample(
    name="본인 조회 · 평가는 끝났지만 옷장은 진행 중",
    description=(
        "**프론트가 가장 주의해야 할 상태.** 옷장 등록은 GPU 서버 → 콜백이라 "
        "평가가 SUCCEEDED가 된 뒤에도 진행 중일 수 있다. `evaluation`만 보고 폴링을 "
        "멈추면 옷장 아이템을 끝내 받지 못한다 — `wardrobe.status`가 DONE/FAILED가 "
        "될 때까지 이어가야 한다. 지면 관계상 일부 필드는 생략했다."
    ),
    value={
        "id": "9f1c2d3e-4a5b-4c6d-8e7f-0a1b2c3d4e5f",
        "status": "SUCCEEDED",
        "personalized": True,
        "save_to_wardrobe": True,
        "wardrobe_job": "3f2a7c81-2b44-4a90-9c1e-77d0f5a1b2c3",
        "wardrobe": {
            "job_id": "3f2a7c81-2b44-4a90-9c1e-77d0f5a1b2c3",
            "status": "PROCESSING",
            "error_message": "",
            "created_at": "2026-08-06T14:58:03+09:00",
            "finished_at": None,
            "items": [],
        },
        "evaluation": _EVALUATION_EXAMPLE,
        "error_message": "",
        "created_at": "2026-08-06T14:58:02+09:00",
        "finished_at": "2026-08-06T14:58:29+09:00",
    },
    response_only=True,
)

OWNER_NO_WARDROBE_EXAMPLE = OpenApiExample(
    name="본인 조회 · 옷장 미연계",
    description="`save_to_wardrobe`를 요청하지 않으면 `wardrobe`는 null이다. 일부 필드 생략.",
    value={
        "id": "9f1c2d3e-4a5b-4c6d-8e7f-0a1b2c3d4e5f",
        "status": "SUCCEEDED",
        "personalized": True,
        "save_to_wardrobe": False,
        "wardrobe_job": None,
        "wardrobe": None,
        "evaluation": _EVALUATION_EXAMPLE,
        "error_message": "",
        "created_at": "2026-08-06T14:58:02+09:00",
        "finished_at": "2026-08-06T14:58:29+09:00",
    },
    response_only=True,
)


class OutfitAnalysisView(APIView):
    """코디 사진을 접수하고 분석은 워커에 넘긴다 (Gemini를 여기서 호출하지 않는다)."""

    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        operation_id="outfit_analysis_create",
        tags=["Outfit Analysis"],
        summary="AI 코디 사진 평가 접수 (비동기)",
        description=(
            "코디 사진과 선택적인 위치를 multipart/form-data로 받아 **접수만 하고 202를 반환**합니다. "
            "분석은 백그라운드 워커가 처리하므로, 응답의 `poll_url`을 `poll_after_ms` 간격으로 "
            "조회해 결과를 받아가세요 (보통 30초 내외).\n\n"
            "인증 없이 호출할 수 있으며, 유효한 JWT를 보내면 저장된 추구미·체형·성별을 평가에 반영합니다. "
            "평가에 사용한 날씨·체형·추구미는 **접수 시점 값으로 고정**되어, 대기 중 날씨가 바뀌어도 "
            "사진을 올린 순간의 조건으로 평가합니다.\n\n"
            "`save_to_wardrobe=true`(로그인 전용)로 보내면 같은 사진을 옷장 아이템 등록 "
            "파이프라인에도 넘기고, 응답의 `wardrobe_job_id`로 등록 진행 상황을 따로 조회할 수 있습니다. "
            "옷장 등록이 실패해도 코디 평가 접수는 그대로 진행됩니다.\n\n"
            "비로그인으로 접수하면 응답에 `claim_token`이 함께 옵니다. **이 응답에서만 받을 수 있으니** "
            "앱이 보관했다가 로그인 직후 `POST /api/v1/outfits/analyses/claim/` 으로 보내면 "
            "그 평가 기록의 소유권을 계정으로 가져올 수 있습니다 (유효 시간이 짧습니다)."
        ),
        request=OutfitAnalysisRequestSerializer,
        responses={
            202: OutfitAnalysisAcceptedSerializer,
            400: OpenApiResponse(description="파일 또는 좌표가 유효하지 않음"),
            415: OpenApiResponse(description="multipart/form-data가 아닌 요청"),
            503: OpenApiResponse(description="사진 저장소 또는 처리 대기열 장애"),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = OutfitAnalysisRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            analysis = analysis_service.accept_analysis(
                request.user,
                data["image"],
                lat=data.get("lat"),
                lon=data.get("lon"),
                save_to_wardrobe=data.get("save_to_wardrobe", False),
            )
        except analysis_service.AnalysisAcceptError as exc:
            return Response(
                {"detail": exc.detail}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        response_serializer = OutfitAnalysisAcceptedSerializer(
            data={
                "analysis_id": str(analysis.pk),
                "status": analysis.status,
                "poll_url": reverse(
                    "recommend:outfit-analysis-detail", args=[analysis.pk]
                ),
                "poll_after_ms": settings.OUTFIT_POLL_AFTER_MS,
                "estimated_seconds": settings.OUTFIT_ESTIMATED_SECONDS,
                "claim_token": claim_service.issue_token(analysis),
                "wardrobe_job_id": (
                    str(analysis.wardrobe_job_id) if analysis.wardrobe_job_id else None
                ),
            }
        )
        response_serializer.is_valid(raise_exception=True)
        return Response(
            response_serializer.validated_data, status=status.HTTP_202_ACCEPTED
        )


class OutfitAnalysisHistoryView(APIView):
    """GET /api/v1/outfits/analyses/ — 내 코디 평가 이력 목록.

    익명 요청 기록(user=NULL)은 소유자를 특정할 수 없어 조회 대상이 아니다.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="outfit_analysis_list",
        tags=["Outfit Analysis"],
        summary="내 코디 평가 이력 목록",
        parameters=[
            OpenApiParameter(
                "status",
                description="상태 필터 (QUEUED/PROCESSING/SUCCEEDED/FAILED)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                "limit",
                description=(
                    f"페이지 크기 (기본 {DEFAULT_HISTORY_LIMIT}, 최대 {MAX_HISTORY_LIMIT})"
                ),
                required=False,
                type=int,
            ),
            OpenApiParameter(
                "offset", description="건너뛸 개수", required=False, type=int
            ),
        ],
        responses={200: OutfitAnalysisListResponseSerializer},
    )
    def get(self, request: Request) -> Response:
        queryset = OutfitAnalysis.objects.filter(user=request.user)
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())

        limit = min(
            _positive_int(request.query_params.get("limit"), default=DEFAULT_HISTORY_LIMIT),
            MAX_HISTORY_LIMIT,
        )
        offset = _positive_int(request.query_params.get("offset"), default=0)

        total = queryset.count()
        page = queryset[offset : offset + limit]
        return Response(
            {
                "count": total,
                "limit": limit,
                "offset": offset,
                "results": OutfitAnalysisListItemSerializer(page, many=True).data,
            }
        )


class OutfitAnalysisDetailView(APIView):
    """GET /api/v1/outfits/analyses/{analysis_id}/ — 진행 상태 겸 결과 조회.

    프론트가 폴링하는 엔드포인트이자 최종 결과를 받는 엔드포인트다. 미완료 응답이
    수십 바이트라 별도 status 엔드포인트를 두지 않았다.

    권한:
    - 익명 접수 기록(user=NULL) → UUID를 아는 사람이면 조회 가능. UUID4는 122비트
      랜덤이라 사실상 추측할 수 없다. 다만 URL은 로그·Referer로 샐 수 있으므로
      응답에서 사진 URL·체형·LLM 원본을 빼고, 접수 후 일정 시간이 지나면 닫는다.
    - 로그인 사용자 기록 → 본인 토큰이 있어야 한다.

    없는 기록과 권한 없는 기록을 모두 404로 처리한다 (403은 "그 UUID는 존재한다"를
    알려주는 셈이라 익명 기록의 존재 여부를 캐볼 수 있게 된다).
    """

    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="outfit_analysis_retrieve",
        tags=["Outfit Analysis"],
        summary="코디 평가 상태·결과 조회 (폴링용)",
        description=(
            "접수 응답의 `analysis_id`로 진행 상태와 결과를 조회합니다. "
            "`status`가 QUEUED/PROCESSING이면 `poll_after_ms` 뒤에 다시 호출하세요.\n\n"
            "비로그인 접수 건은 토큰 없이 조회할 수 있으며 접수 후 일정 시간이 지나면 닫힙니다"
            " (OUTFIT_ANON_TTL_HOURS, 기본 24시간). "
            "로그인 상태로 접수한 건은 본인 토큰이 있어야 하고, 이 경우 질의에 쓴 "
            "체형·추구미 스냅샷과 LLM 요청·응답 원본까지 함께 내려갑니다.\n\n"
            "`save_to_wardrobe=true`로 접수한 건은 본인 조회 응답에 `wardrobe` 객체가 함께 옵니다 "
            "(연계하지 않았으면 null). 옷장 등록은 별도 파이프라인이라 평가가 SUCCEEDED가 된 뒤에도 "
            "`wardrobe.status`는 아직 PENDING/PROCESSING일 수 있으며, **DONE이 되면** "
            "`wardrobe.items`에 생성된 아이템 요약(이름·분류·색상·이미지 URL·확정 여부)이 채워집니다. "
            "전체 태그가 필요하면 GET /api/v1/wardrobe/uploads/{job_id}/ 를 쓰세요.\n\n"
            "아래 **Example** 드롭다운에서 비로그인·본인 응답과 옷장 상태별 샘플을 골라 볼 수 있습니다."
        ),
        responses={
            # 인증 여부에 따라 모양이 달라지므로 둘 다 싣는다. Public만 적어두면
            # 사진 URL·체형 스냅샷·wardrobe 같은 소유자 전용 필드가 문서에 안 나온다.
            200: PolymorphicProxySerializer(
                component_name="OutfitAnalysisResult",
                serializers=[
                    OutfitAnalysisPublicSerializer,
                    OutfitAnalysisDetailSerializer,
                ],
                resource_type_field_name=None,
            ),
            404: OpenApiResponse(description="존재하지 않거나, 본인 기록이 아니거나, 조회 기간이 지남"),
        },
        examples=[
            OWNER_WARDROBE_DONE_EXAMPLE,
            OWNER_WARDROBE_PENDING_EXAMPLE,
            OWNER_NO_WARDROBE_EXAMPLE,
            ANONYMOUS_RESULT_EXAMPLE,
        ],
    )
    def get(self, request: Request, analysis_id) -> Response:
        # 상세 응답은 옷장 연계 job과 그 아이템까지 싣는다 — 미리 당기지 않으면 직렬화에서 쿼리가 더 난다.
        # 익명 응답에는 옷장이 없지만, 익명은 애초에 wardrobe_job이 NULL이라 빈 join 1번이 전부다.
        analysis = (
            OutfitAnalysis.objects.select_related("wardrobe_job")
            .prefetch_related("wardrobe_job__items")
            .filter(pk=analysis_id)
            .first()
        )
        if analysis is None:
            raise NotFound("평가 기록을 찾을 수 없습니다.")

        if analysis.user_id is None:
            deadline = timezone.now() - timedelta(
                hours=settings.OUTFIT_ANON_TTL_HOURS
            )
            if analysis.created_at < deadline:
                raise NotFound("조회 기간이 지난 평가 기록입니다.")
            return Response(OutfitAnalysisPublicSerializer(analysis).data)

        if not request.user.is_authenticated or analysis.user_id != request.user.pk:
            raise NotFound("평가 기록을 찾을 수 없습니다.")
        return Response(OutfitAnalysisDetailSerializer(analysis).data)


class OutfitAnalysisClaimView(APIView):
    """POST /api/v1/outfits/analyses/claim/ — 익명 접수 건의 소유권을 계정으로 가져온다.

    비로그인으로 평가하고 로그인한 사용자가, 앱에 보관해 둔 `claim_token`들을 한 번에
    넘긴다. 평가는 **다시 하지 않고** 주인만 바꾼다.

    조회와 달리 UUID만으로는 허용하지 않는다 — claim은 쓰기이고, 성공하면 소유자
    응답으로 바뀌어 사진 URL과 체형 스냅샷까지 열리는 권한 상승 경로다.
    자세한 근거는 services/claim.py 참고.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="outfit_analysis_claim",
        tags=["Outfit Analysis"],
        summary="비로그인 코디 평가 소유권 이전",
        description=(
            "로그인 직후, 비로그인 상태에서 접수했던 평가 기록을 계정으로 가져옵니다. "
            "접수 응답에서 받은 `claim_token`을 그대로 보내세요(토큰 안에 대상 식별자가 있습니다).\n\n"
            "평가 결과는 다시 계산하지 않습니다. 비로그인 접수 건은 추구미·체형이 반영되지 않은 "
            "상태로 평가가 끝나 있으므로, 이력에서도 개인화되지 않은 결과로 남습니다.\n\n"
            "**주의**: 이전이 끝나면 그 기록은 더 이상 익명 조회가 되지 않습니다. "
            "분석이 진행 중인 건을 넘겨받았다면 이후 폴링에는 반드시 Authorization 헤더를 실어야 "
            "하며, 그렇지 않으면 404가 납니다."
        ),
        request=OutfitAnalysisClaimRequestSerializer,
        responses={
            200: OutfitAnalysisClaimResponseSerializer,
            400: OpenApiResponse(description="토큰 목록이 비었거나 상한을 초과함"),
            401: OpenApiResponse(description="로그인 필요"),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = OutfitAnalysisClaimRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = claim_service.claim_analyses(
            request.user, serializer.validated_data["claim_tokens"]
        )
        response_serializer = OutfitAnalysisClaimResponseSerializer(
            data={"claimed": result.claimed, "skipped": result.skipped}
        )
        response_serializer.is_valid(raise_exception=True)
        return Response(response_serializer.validated_data)


class RecommendationHistoryView(APIView):
    """회원과 게스트가 자기 채팅 추천 이력을 조회한다."""

    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="recommendation_history_list",
        tags=[CHAT_TAG],
        summary="내 추천 이력 목록",
        description=(
            "채팅에서 확정·저장된 추천 결과를 최신순으로 조회합니다. "
            "`mode`에는 `WARDROBE_BASED` 또는 `NEW_ITEM`을 넣어 필터링할 수 "
            "있습니다. 응답의 `result_id`와 `top_card.card_id`를 상세·피드백·이미지 "
            "생성 API에 사용합니다.\n\n"
            f"{CHAT_IDENTITY_GUIDE}"
        ),
        parameters=[
            _RECOMMENDATION_MODE_PARAMETER,
            _RECOMMENDATION_LIMIT_PARAMETER,
            _RECOMMENDATION_OFFSET_PARAMETER,
        ],
        responses={
            200: RecommendationHistoryResponseSerializer,
            401: OpenApiResponse(
                description="회원 JWT 또는 유효한 게스트 채팅 쿠키 필요"
            ),
        },
    )
    def get(self, request: Request) -> Response:
        identity = _recommendation_identity(request)
        query = RecommendationHistoryQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data

        queryset = recommendation_service.owned_results(identity)
        if mode := data.get("mode"):
            queryset = queryset.filter(mode=mode)

        total = queryset.count()
        offset = data["offset"]
        limit = data["limit"]
        page = queryset[offset : offset + limit]
        return Response(
            {
                "count": total,
                "limit": limit,
                "offset": offset,
                "results": RecommendationHistoryItemSerializer(page, many=True).data,
            }
        )


class RecommendationResultDetailView(APIView):
    """한 번의 추천 실행에서 확정된 카드들을 조회한다."""

    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="recommendation_result_retrieve",
        tags=[CHAT_TAG],
        summary="추천 결과와 카드 목록 조회",
        description=(
            "한 번의 채팅 추천 실행으로 확정된 코디 카드를 순위·구성 아이템과 함께 "
            "조회합니다. 실제 result_id는 추천 이력 응답 또는 AI 메시지 metadata에서 "
            f"가져옵니다.\n\n{CHAT_UUID_GUIDE}"
        ),
        parameters=[_RESULT_ID_PARAMETER],
        responses={
            200: RecommendationResultDetailSerializer,
            404: OpenApiResponse(
                description="결과가 없거나 요청 identity의 소유가 아님"
            ),
        },
    )
    def get(self, request: Request, result_id) -> Response:
        identity = _recommendation_identity(request)
        result = recommendation_service.owned_result(
            identity=identity,
            result_id=result_id,
        )
        if result is None:
            raise NotFound("추천 결과를 찾을 수 없습니다.")
        return Response(RecommendationResultDetailSerializer(result).data)


class RecommendationCardDetailView(APIView):
    """추천 결과 안의 검증 통과 카드 한 장을 조회한다."""

    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="recommendation_card_retrieve",
        tags=[CHAT_TAG],
        summary="추천 카드 상세 조회",
        description=(
            "코디 카드 한 장의 아이템 출처(옷장·골든셋·상품), 가격, 구매 URL, "
            "검증 사유와 현재 피드백을 조회합니다."
        ),
        parameters=[_RESULT_ID_PARAMETER, _CARD_ID_PARAMETER],
        responses={
            200: RecommendationCardSerializer,
            404: OpenApiResponse(
                description="카드가 없거나 요청 identity의 소유가 아님"
            ),
        },
    )
    def get(self, request: Request, result_id, card_id) -> Response:
        identity = _recommendation_identity(request)
        card = recommendation_service.owned_card(
            identity=identity,
            result_id=result_id,
            card_id=card_id,
        )
        if card is None:
            raise NotFound("추천 카드를 찾을 수 없습니다.")
        return Response(RecommendationCardSerializer(card).data)


class RecommendationFeedbackView(APIView):
    """추천 카드의 최신 피드백을 멱등 생성·교체·삭제한다."""

    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="recommendation_feedback_put",
        tags=[CHAT_TAG],
        summary="추천 카드 피드백 생성 또는 교체",
        description=(
            "추천 카드에 대한 최신 반응을 저장합니다. 같은 카드에 다시 PUT하면 기존 "
            "피드백 전체를 교체합니다. reason_codes는 최대 5개의 대문자 코드이며, "
            "예시는 `COLOR`, `FIT`, `PRICE`, `STYLE`, `ALREADY_OWNED`입니다."
        ),
        parameters=[_RESULT_ID_PARAMETER, _CARD_ID_PARAMETER],
        request={"application/json": RecommendationFeedbackRequestSerializer},
        examples=[
            OpenApiExample(
                name="추천이 마음에 듦",
                value={
                    "reaction": "LIKE",
                    "reason_codes": ["STYLE", "COLOR"],
                    "comment": "색 조합이 마음에 들어요",
                },
                request_only=True,
            ),
            OpenApiExample(
                name="추천이 마음에 들지 않음",
                value={
                    "reaction": "DISLIKE",
                    "reason_codes": ["PRICE", "FIT"],
                    "comment": "예산보다 비싸고 핏이 취향과 달라요",
                },
                request_only=True,
            ),
        ],
        responses={
            200: RecommendationFeedbackSerializer,
            201: RecommendationFeedbackSerializer,
            404: OpenApiResponse(
                description="카드가 없거나 요청 identity의 소유가 아님"
            ),
        },
    )
    def put(self, request: Request, result_id, card_id) -> Response:
        identity = _recommendation_identity(request)
        serializer = RecommendationFeedbackRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        feedback, created = recommendation_service.put_feedback(
            identity=identity,
            result_id=result_id,
            card_id=card_id,
            **serializer.validated_data,
        )
        if feedback is None:
            raise NotFound("추천 카드를 찾을 수 없습니다.")
        return Response(
            RecommendationFeedbackSerializer(feedback).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="recommendation_feedback_delete",
        tags=[CHAT_TAG],
        summary="추천 카드 피드백 삭제",
        description="해당 카드에 저장된 최신 피드백을 삭제합니다. 추천 카드 자체는 유지됩니다.",
        parameters=[_RESULT_ID_PARAMETER, _CARD_ID_PARAMETER],
        responses={
            204: None,
            404: OpenApiResponse(
                description="카드가 없거나 요청 identity의 소유가 아님"
            ),
        },
    )
    def delete(self, request: Request, result_id, card_id) -> Response:
        identity = _recommendation_identity(request)
        card = recommendation_service.owned_card(
            identity=identity,
            result_id=result_id,
            card_id=card_id,
        )
        if card is None:
            raise NotFound("추천 카드를 찾을 수 없습니다.")
        recommendation_service.delete_feedback(
            identity=identity,
            result_id=result_id,
            card_id=card_id,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class SavedOutfitView(APIView):
    """회원이 소유한 추천 카드의 저장 상태를 멱등 변경한다."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="recommendation_saved_outfit_put",
        tags=[CHAT_TAG],
        summary="추천 코디 저장",
        description=(
            "로그인 회원이 소유한 검증 완료 추천 코디를 저장합니다. 같은 카드를 "
            "다시 요청하면 기존 저장 행과 최초 저장 시각을 그대로 반환합니다."
        ),
        parameters=[_RESULT_ID_PARAMETER, _CARD_ID_PARAMETER],
        request=None,
        responses={
            200: SavedOutfitSerializer,
            201: SavedOutfitSerializer,
            401: OpenApiResponse(description="로그인 회원 필요"),
            404: OpenApiResponse(
                description="카드가 없거나 요청 회원의 소유가 아니거나 검증 미통과"
            ),
        },
    )
    def put(self, request: Request, result_id, card_id) -> Response:
        identity = _recommendation_identity(request)
        saved_outfit, created = recommendation_service.save_outfit(
            identity=identity,
            result_id=result_id,
            card_id=card_id,
        )
        if saved_outfit is None:
            raise NotFound("추천 카드를 찾을 수 없습니다.")
        return Response(
            SavedOutfitSerializer(saved_outfit).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="recommendation_saved_outfit_delete",
        tags=[CHAT_TAG],
        summary="추천 코디 저장 해제",
        description=(
            "로그인 회원이 소유한 추천 코디의 저장 상태를 해제합니다. 이미 저장이 "
            "해제된 카드에 다시 요청해도 204를 반환합니다."
        ),
        parameters=[_RESULT_ID_PARAMETER, _CARD_ID_PARAMETER],
        responses={
            204: None,
            401: OpenApiResponse(description="로그인 회원 필요"),
            404: OpenApiResponse(
                description="카드가 없거나 요청 회원의 소유가 아니거나 검증 미통과"
            ),
        },
    )
    def delete(self, request: Request, result_id, card_id) -> Response:
        identity = _recommendation_identity(request)
        card_exists = recommendation_service.delete_saved_outfit(
            identity=identity,
            result_id=result_id,
            card_id=card_id,
        )
        if not card_exists:
            raise NotFound("추천 카드를 찾을 수 없습니다.")
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProductClickEventView(APIView):
    """회원이 실제로 누른 추천 판매 상품을 참고 행동으로 수집한다."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="recommendation_product_click_create",
        tags=[CHAT_TAG],
        summary="추천 상품 클릭 수집",
        description=(
            "로그인 회원이 소유한 검증 완료 추천 카드의 판매 상품 클릭을 참고 "
            "신호로 저장합니다. 같은 상품을 5분 안에 다시 호출하면 새 행을 만들지 "
            "않고 기존 이벤트를 반환하며 `deduplicated=true`로 표시합니다. 클릭 "
            "수집 실패가 판매처 이동을 막지 않도록 클라이언트는 이 요청과 링크 "
            "열기를 독립적으로 처리해야 합니다."
        ),
        parameters=[
            _RESULT_ID_PARAMETER,
            _CARD_ID_PARAMETER,
            _ITEM_ID_PARAMETER,
        ],
        request=None,
        responses={
            200: ProductClickEventSerializer,
            201: ProductClickEventSerializer,
            401: OpenApiResponse(description="로그인 회원 필요"),
            404: OpenApiResponse(
                description=(
                    "상품이 없거나 요청 회원의 소유가 아니거나 "
                    "검증 카드의 판매 상품이 아님"
                )
            ),
        },
    )
    def post(self, request: Request, result_id, card_id, item_id) -> Response:
        identity = _recommendation_identity(request)
        event, created = recommendation_service.record_product_click(
            identity=identity,
            result_id=result_id,
            card_id=card_id,
            item_id=item_id,
        )
        if event is None:
            raise NotFound("추천 상품을 찾을 수 없습니다.")
        event.deduplicated = not created
        return Response(
            ProductClickEventSerializer(event).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class WishlistView(APIView):
    """GET /api/v1/wishlist/ — 담아 둔 상품 목록."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="wishlist_list",
        tags=[CHAT_TAG],
        summary="찜한 상품 목록",
        description=(
            "회원이 담아 둔 판매 상품을 최근 담은 순서로 반환합니다. 값은 담은 "
            "시점의 스냅샷이며, 상품은 카탈로그 원본 식별자"
            "(source_collection·source_id)로 구분합니다."
        ),
        responses={
            200: WishlistItemSerializer(many=True),
            401: OpenApiResponse(description="로그인 회원 필요"),
        },
    )
    def get(self, request: Request) -> Response:
        identity = _recommendation_identity(request)
        items = recommendation_service.wishlist_items(identity)
        return Response(WishlistItemSerializer(items, many=True).data)


class WishlistAddView(APIView):
    """POST .../items/{item_id}/wish/ — 추천 카드의 판매 상품을 찜에 담는다."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="wishlist_add",
        tags=[CHAT_TAG],
        summary="추천 상품 찜하기",
        description=(
            "로그인 회원이 소유한 검증 완료 추천 카드의 판매 상품을 찜에 담습니다. "
            "같은 상품을 다시 담으면 새 행을 만들지 않고 기존 찜을 200으로 "
            "반환합니다. 브랜드·판매처 주소는 담는 순간 상품 카탈로그에서 채웁니다."
        ),
        parameters=[
            _RESULT_ID_PARAMETER,
            _CARD_ID_PARAMETER,
            _ITEM_ID_PARAMETER,
        ],
        request=None,
        responses={
            200: WishlistItemSerializer,
            201: WishlistItemSerializer,
            401: OpenApiResponse(description="로그인 회원 필요"),
            404: OpenApiResponse(
                description=(
                    "상품이 없거나 요청 회원의 소유가 아니거나 "
                    "검증 카드의 판매 상품이 아님"
                )
            ),
        },
    )
    def post(self, request: Request, result_id, card_id, item_id) -> Response:
        identity = _recommendation_identity(request)
        wish, created = recommendation_service.add_wishlist_item(
            identity=identity,
            result_id=result_id,
            card_id=card_id,
            item_id=item_id,
        )
        if wish is None:
            raise NotFound("추천 상품을 찾을 수 없습니다.")
        return Response(
            WishlistItemSerializer(wish).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class WishlistItemView(APIView):
    """DELETE /api/v1/wishlist/{wish_id}/ — 찜에서 뺀다."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="wishlist_delete",
        tags=[CHAT_TAG],
        summary="찜 빼기",
        description="회원이 담아 둔 상품 하나를 뺍니다. 남의 찜은 404입니다.",
        parameters=[_WISH_ID_PARAMETER],
        request=None,
        responses={
            204: OpenApiResponse(description="삭제됨"),
            401: OpenApiResponse(description="로그인 회원 필요"),
            404: OpenApiResponse(description="찜을 찾을 수 없음"),
        },
    )
    def delete(self, request: Request, wish_id) -> Response:
        identity = _recommendation_identity(request)
        if not recommendation_service.remove_wishlist_item(
            identity=identity,
            wish_id=wish_id,
        ):
            raise NotFound("찜을 찾을 수 없습니다.")
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProductClickEngagementView(APIView):
    """외부 판매처에서 앱으로 돌아온 시점의 근사 체류 시간을 기록한다."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="recommendation_product_click_engagement_update",
        tags=[CHAT_TAG],
        summary="추천 상품 클릭 체류 시간 기록",
        description=(
            "상품 클릭 수집 응답의 product_click_id를 사용해 외부 판매처 이동 후 "
            "앱 복귀까지 측정한 근사 시간을 기록합니다. 재시도 시 더 큰 값만 "
            "보존합니다. 체류 시간만으로 선호로 판정하지 않으며 클릭은 중립 참고 "
            "신호로 유지됩니다."
        ),
        parameters=[_PRODUCT_CLICK_ID_PARAMETER],
        request=ProductClickEngagementRequestSerializer,
        examples=[
            OpenApiExample(
                name="42초 체류 기록",
                value={"duration_ms": 42_000},
                request_only=True,
            ),
            OpenApiExample(
                name="체류 기록 응답",
                value={
                    "product_click_id": "88888888-8888-4888-8888-888888888888",
                    "result_id": "44444444-4444-4444-8444-444444444444",
                    "card_id": "55555555-5555-4555-8555-555555555555",
                    "item_id": "77777777-7777-4777-8777-777777777777",
                    "persona_id": "minimal",
                    "source_collection": "naver_products",
                    "source_id": "naver-101",
                    "deduplicated": False,
                    "clicked_at": "2026-08-16T10:00:00+09:00",
                    "engagement_duration_ms": 42_000,
                    "engagement_recorded_at": "2026-08-16T10:00:42+09:00",
                },
                response_only=True,
                status_codes=["200"],
            ),
        ],
        responses={
            200: ProductClickEventSerializer,
            400: OpenApiResponse(description="duration_ms 범위 오류 (0~86400000)"),
            401: OpenApiResponse(description="로그인 회원 필요"),
            404: OpenApiResponse(description="클릭 이벤트가 없거나 요청 회원의 소유가 아님"),
        },
    )
    def patch(self, request: Request, product_click_id) -> Response:
        serializer = ProductClickEngagementRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        identity = _recommendation_identity(request)
        event = recommendation_service.update_product_click_engagement(
            identity=identity,
            product_click_id=product_click_id,
            duration_ms=serializer.validated_data["duration_ms"],
        )
        if event is None:
            raise NotFound("상품 클릭 이벤트를 찾을 수 없습니다.")
        event.deduplicated = False
        return Response(ProductClickEventSerializer(event).data)


class RecommendationCardRenderView(APIView):
    """소유한 추천 카드의 이미지 생성 접수와 현재 상태 조회."""

    permission_classes = [AllowAny]

    def _card(self, request: Request, result_id, card_id):
        identity = _recommendation_identity(request)
        card = recommendation_service.owned_card(
            identity=identity,
            result_id=result_id,
            card_id=card_id,
        )
        if card is None:
            raise NotFound("추천 카드를 찾을 수 없습니다.")
        return card

    @extend_schema(
        operation_id="recommendation_card_render_retrieve",
        tags=[CHAT_TAG],
        summary="추천 카드 이미지 생성 상태 조회",
        description=(
            "카드의 최종 코디 이미지 생성 작업 상태를 조회합니다. `SUCCEEDED`이면 "
            "만료 시간이 있는 비공개 image_url이 반환됩니다. Swagger에서 비동기 "
            "이미지 완료를 확인할 때 이 API를 반복 호출합니다."
        ),
        parameters=[_RESULT_ID_PARAMETER, _CARD_ID_PARAMETER],
        responses={
            200: OutfitRenderJobSerializer,
            404: OpenApiResponse(
                description="카드·작업이 없거나 요청 identity의 소유가 아님"
            ),
        },
    )
    def get(self, request: Request, result_id, card_id) -> Response:
        card = self._card(request, result_id, card_id)
        job = OutfitRenderJob.objects.filter(composition=card).first()
        if job is None:
            raise NotFound("이미지 생성 작업을 찾을 수 없습니다.")
        return Response(
            OutfitRenderJobSerializer(job, context={"request": request}).data
        )

    @extend_schema(
        operation_id="recommendation_card_render_create",
        tags=[CHAT_TAG],
        summary="추천 카드 이미지 생성 접수",
        description=(
            "추천 카드의 옷장·골든셋·상품 아이템 이미지를 모아 Qwen 이미지 생성 "
            "작업을 Redis 큐에 접수합니다. 이미 완료된 동일 조합은 캐시를 재사용할 "
            "수 있습니다. 응답의 `job_id`로 SSE 또는 상태 조회를 이어갑니다.\n\n"
            "**로컬 테스트 전제:** Redis, 이미지 worker, S3와 이미지 모델 설정이 "
            "필요하며 실제 모델 API 비용이 발생할 수 있습니다."
        ),
        parameters=[_RESULT_ID_PARAMETER, _CARD_ID_PARAMETER],
        request=None,
        responses={
            200: OutfitRenderJobSerializer,
            202: OutfitRenderJobSerializer,
            404: OpenApiResponse(
                description="카드가 없거나 요청 identity의 소유가 아님"
            ),
            503: OpenApiResponse(description="이미지 생성 큐를 사용할 수 없음"),
        },
    )
    def post(self, request: Request, result_id, card_id) -> Response:
        card = self._card(request, result_id, card_id)
        job, should_enqueue = render_jobs.prepare_job(card)
        if should_enqueue:
            try:
                job = render_jobs.enqueue_prepared(job)
            except render_jobs.RenderQueueUnavailable:
                job.refresh_from_db()
                return Response(
                    OutfitRenderJobSerializer(job, context={"request": request}).data,
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
        response_status = (
            status.HTTP_200_OK
            if job.status == OutfitRenderJob.Status.SUCCEEDED
            else status.HTTP_202_ACCEPTED
        )
        return Response(
            OutfitRenderJobSerializer(job, context={"request": request}).data,
            status=response_status,
        )


class RecommendationCardVirtualTryOnView(APIView):
    """완성된 추천 코디를 사용자 사진 또는 체형 마네킹에 적용한다."""

    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        operation_id="recommendation_card_virtual_try_on_create",
        tags=[CHAT_TAG],
        summary="추천 코디 가상 착장",
        description=(
            "완료된 추천 카드 이미지와 전신 사진을 Qwen에 전달합니다. `person`은 "
            "얼굴·체형·포즈를 유지하고 옷만 교체하며, `mannequin`은 같은 체형의 "
            "마네킹에 선택한 추천 룩을 한 번에 입힙니다. 요청 사진 원본은 저장하지 않습니다.\n\n"
            "⚠️ **이 경로는 동기입니다.** 응답이 올 때까지 이미지 생성을 기다리므로 "
            "수십 초~2분이 걸리고, 프록시가 먼저 끊으면 504/524가 납니다. "
            "오늘의 룩(`POST /looks/{look_id}/virtual-try-on/`)은 같은 이유로 "
            "접수(202)와 조회로 나눠 두었으니, 이 API를 화면에 붙일 때 같은 구조로 "
            "옮기는 것을 권합니다."
        ),
        parameters=[_RESULT_ID_PARAMETER, _CARD_ID_PARAMETER],
        request=VirtualTryOnRequestSerializer,
        responses={
            200: VirtualTryOnResultSerializer,
            409: OpenApiResponse(description="추천 코디 이미지가 아직 생성되지 않음"),
            502: OpenApiResponse(description="이미지 모델이 가상 착장 생성에 실패함"),
            503: OpenApiResponse(description="결과 저장소가 설정되지 않음"),
        },
    )
    def post(self, request: Request, result_id, card_id) -> Response:
        identity = _recommendation_identity(request)
        card = recommendation_service.owned_card(
            identity=identity,
            result_id=result_id,
            card_id=card_id,
        )
        if card is None:
            raise NotFound("추천 카드를 찾을 수 없습니다.")

        serializer = VirtualTryOnRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = OutfitRenderJob.objects.filter(
            composition=card,
            status=OutfitRenderJob.Status.SUCCEEDED,
        ).first()
        if job is None or not job.output_s3_bucket or not job.output_s3_key:
            return Response(
                {"detail": "추천 코디 이미지 생성이 완료된 뒤 시도해 주세요."},
                status=status.HTTP_409_CONFLICT,
            )

        upload = serializer.validated_data["person_image"]
        upload.seek(0)
        person = upload.read()
        outfit = storage.download_for(
            job.output_s3_bucket,
            job.output_s3_key,
            max_bytes=settings.OUTFIT_RENDER_MAX_OUTPUT_BYTES,
        )
        mode = serializer.validated_data["mode"]
        service = VirtualTryOnService()
        prefix = settings.VIRTUAL_TRY_ON_RESULT_PREFIX
        person_hash = hashlib.sha256(person).hexdigest()
        outfit_hash = hashlib.sha256(outfit).hexdigest()
        prompt_contract = (
            DIRECT_PROMPT_VERSION
            if mode == "person"
            else MANNEQUIN_PROMPT_VERSION
        )
        contract = hashlib.sha256(
            (
                f"{mode}|{person_hash}|{outfit_hash}|"
                f"{settings.OUTFIT_RENDER_MODEL}|{prompt_contract}"
            ).encode()
        ).hexdigest()
        final_key = f"{prefix}/{contract[:2]}/{contract}/result.png"
        bucket = settings.OUTFIT_RENDER_RESULT_BUCKET
        if not bucket:
            return Response(
                {"detail": "가상 착장 결과 저장소가 설정되지 않았습니다."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        cache_hit = storage.exists_for(bucket, final_key)
        if not cache_hit:
            try:
                if mode == "mannequin":
                    result = service.fit_mannequin(person, outfit)
                else:
                    result = service.fit_person(person, outfit)
            except OutfitRenderError:
                logger.exception("가상 착장 이미지 생성 실패: card=%s", card.pk)
                return Response(
                    {"detail": "가상 착장 이미지를 생성하지 못했습니다."},
                    status=status.HTTP_502_BAD_GATEWAY,
                )
            storage.put_bytes_for(bucket, final_key, result.content, result.media_type)

        ttl = settings.OUTFIT_RENDER_PRESIGNED_GET_TTL_SECONDS
        return Response(
            {
                "mode": mode,
                "image_url": storage.presigned_get_for(bucket, final_key, ttl=ttl),
                "cache_hit": cache_hit,
            }
        )


class DailyLookVirtualTryOnView(APIView):
    """화면에 표시된 오늘의 추천 룩을 사용자 체형 마네킹에 적용한다 (비동기).

    POST 는 접수만 하고, 실제 생성은 워커가 한다. GET 으로 그 룩의 마지막 작업을
    조회하므로 화면을 나갔다 와도 이어서 볼 수 있다.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        operation_id="daily_look_virtual_try_on_create",
        tags=[CHAT_TAG],
        summary="오늘의 추천 룩 가상 착장 요청 (비동기)",
        description=(
            "가상 피팅을 **접수만** 하고 바로 응답한다. 이미지 생성은 워커가 하며 "
            "수십 초~2분이 걸린다 — 요청 스레드에서 기다리면 프록시가 먼저 끊는다"
            "(Cloudflare 터널 100초 → 524).\n\n"
            "- `202`: 접수됨. `poll_after_ms` 간격으로 "
            "`GET /looks/{look_id}/virtual-try-on/` 을 호출해 상태를 본다\n"
            "- `200`: 같은 사진·같은 코디로 이미 만들어 둔 것이 있다 "
            "(`cache_hit=true`, `status=SUCCEEDED`) — 폴링할 필요 없다\n"
            "- `404`: `golden_id` 가 오늘 이 사용자에게 나간 룩이 아니다\n"
            "- `409`: 입힐 추천 룩 이미지(착용 이미지)가 아직 만들어지지 않았다\n\n"
            "전신 사진은 워커가 읽을 때까지 S3 에 잠시 보관되며, 버킷 수명주기 규칙으로 "
            "만료된다.\n\n"
            "`golden_id` 를 주면 '다른 룩'으로 돌려보던 그 후보를 입힌다. 생략하면 대표 룩이다."
        ),
        request=DailyLookVirtualTryOnRequestSerializer,
        responses={
            200: VirtualTryOnJobSerializer,
            202: VirtualTryOnJobSerializer,
            404: OpenApiResponse(description="오늘 나간 룩이 아닌 golden_id"),
            409: OpenApiResponse(description="추천 룩 이미지가 아직 생성되지 않음"),
            503: OpenApiResponse(
                description="결과 저장소 미설정 또는 큐 적재 실패로 접수하지 못함"
            ),
        },
    )
    def post(self, request: Request, look_id) -> Response:
        look = DailyLook.objects.filter(
            pk=look_id,
            user=request.user,
            status=DailyLook.Status.SUCCEEDED,
        ).first()
        if look is None:
            raise NotFound("추천 룩을 찾을 수 없습니다.")

        serializer = DailyLookVirtualTryOnRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # '다른 룩'으로 돌려보던 후보를 입어볼 수 있다. golden_id 없이 대표 룩만
        # 입히면, 화면에서 고른 룩과 마네킹이 입은 룩이 어긋난다 — 저장 버튼에서
        # 같은 문제를 고쳤고 규칙도 같은 함수(pick_result)를 쓴다.
        try:
            chosen = daily_look_service.pick_result(
                look, serializer.validated_data["golden_id"]
            )
        except daily_look_service.GoldenLookNotInTodayError as error:
            return Response(
                {
                    "code": "GOLDEN_LOOK_NOT_IN_TODAY",
                    "golden_id": error.golden_id,
                    "detail": "오늘 추천에 없는 룩입니다. 새로고침 후 다시 시도해주세요.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        image = chosen.get("render_image") or chosen.get("outfit_image")
        if not image or not image.get("s3_bucket") or not image.get("s3_key"):
            # 후보의 착용 이미지는 별도 큐 작업이 채운다. 조회 시점 보정을 한 번
            # 돌려 방금 만들어진 것을 집어 오고, 그래도 없으면 아직인 것이다.
            daily_look_service.refresh_alternatives(look)
            try:
                chosen = daily_look_service.pick_result(
                    look, serializer.validated_data["golden_id"]
                )
            except daily_look_service.GoldenLookNotInTodayError:
                chosen = {}
            image = chosen.get("render_image") or chosen.get("outfit_image")
        if not image or not image.get("s3_bucket") or not image.get("s3_key"):
            return Response(
                {"detail": "추천 룩 이미지 생성이 완료된 뒤 시도해 주세요."},
                status=status.HTTP_409_CONFLICT,
            )

        upload = serializer.validated_data["person_image"]
        upload.seek(0)
        person = upload.read()
        outfit = storage.download_for(
            str(image["s3_bucket"]),
            str(image["s3_key"]),
            max_bytes=settings.OUTFIT_RENDER_MAX_OUTPUT_BYTES,
        )

        # 여기서 이미지를 만들지 않는다. 생성이 수십 초~2분이라 앞단 프록시가 먼저
        # 끊고(Cloudflare 터널 100초 → 524), 그 연결이 곧 결과의 수명이라 화면을
        # 나가면 만들던 것이 사라진다. 접수만 하고 워커에 넘긴다.
        try:
            job, done = virtual_try_on_jobs.accept(
                user=request.user,
                look=look,
                golden_id=str(chosen.get("golden_id") or ""),
                mode=serializer.validated_data["mode"],
                person=person,
                person_extension=Path(getattr(upload, "name", "") or "person.jpg").suffix
                or ".jpg",
                person_content_type=getattr(upload, "content_type", "") or "image/jpeg",
                outfit=outfit,
            )
        except virtual_try_on_jobs.VirtualTryOnUnavailable:
            return Response(
                {"detail": "가상 착장 결과 저장소가 설정되지 않았습니다."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if done:
            # 같은 사진·같은 코디로 이미 만들어 둔 것이 있다. 폴링할 이유가 없다.
            return Response(virtual_try_on_jobs.payload(job))

        try:
            render_queue.enqueue_virtual_try_on(job)
        except redis.RedisError:
            logger.exception("가상 피팅 큐 적재 실패: job=%s", job.pk)
            virtual_try_on_jobs.mark_failed(
                job.pk,
                error_code="QUEUE_ENQUEUE_FAILED",
                error_message="잠시 후 다시 시도해 주세요.",
            )
            return Response(
                {"detail": "가상 착장 요청을 접수하지 못했습니다."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        virtual_try_on_jobs.mark_enqueued(job)
        return Response(
            virtual_try_on_jobs.payload(job), status=status.HTTP_202_ACCEPTED
        )

    @extend_schema(
        operation_id="daily_look_virtual_try_on_retrieve",
        tags=[CHAT_TAG],
        summary="가상 피팅 결과 조회",
        description=(
            "이 사용자가 그 룩(그 후보)에 대해 **마지막으로 만든** 가상 피팅을 돌려준다.\n\n"
            "화면을 나갔다 다시 들어와도 사진을 다시 고를 필요 없이 이 조회로 복원한다. "
            "아직 한 번도 만든 적이 없으면 `status`가 null이다."
        ),
        parameters=[
            OpenApiParameter(
                name="golden_id", type=str, required=False,
                description="어느 룩의 결과인지. 생략하면 대표 룩.",
            ),
        ],
        responses={200: VirtualTryOnJobSerializer},
    )
    def get(self, request: Request, look_id) -> Response:
        look = DailyLook.objects.filter(pk=look_id, user=request.user).first()
        if look is None:
            raise NotFound("추천 룩을 찾을 수 없습니다.")
        job = virtual_try_on_jobs.latest_job(
            user=request.user,
            look=look,
            golden_id=request.query_params.get("golden_id", ""),
        )
        return Response(virtual_try_on_jobs.payload(job))


def _owned_render_job(request: Request, job_id) -> OutfitRenderJob:
    identity = _recommendation_identity(request)
    job = render_jobs.owned_job(identity=identity, job_id=job_id)
    if job is None:
        raise NotFound("이미지 생성 작업을 찾을 수 없습니다.")
    return job


def _render_terminal_event(
    job: OutfitRenderJob,
    request: Request,
    *,
    event_id: str = "",
) -> RenderEvent | None:
    event_type = {
        OutfitRenderJob.Status.SUCCEEDED: "completed",
        OutfitRenderJob.Status.FAILED: "failed",
    }.get(job.status)
    if event_type is None:
        return None
    return RenderEvent(
        id=event_id,
        event=event_type,
        data=OutfitRenderJobSerializer(job, context={"request": request}).data,
    )


@extend_schema_view(
    get=extend_schema(
        operation_id="recommendation_render_event_stream",
        tags=[CHAT_TAG],
        summary="추천 카드 이미지 생성 진행 이벤트 SSE",
        description=(
            "이미지 생성 작업의 `queued`, `running`, `completed`, `failed` 이벤트를 "
            "`text/event-stream`으로 전달합니다. 재연결할 때 마지막 이벤트 ID를 "
            "`Last-Event-ID` 헤더 또는 `last_event_id` 쿼리에 넣습니다.\n\n"
            f"{CHAT_SSE_GUIDE}"
        ),
        parameters=[
            _JOB_ID_PARAMETER,
            OpenApiParameter(
                name="last_event_id",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="재연결 시 마지막으로 받은 Redis Stream 이벤트 ID",
                examples=[OpenApiExample(name="처음부터 수신", value="0-0")],
            ),
        ],
        responses={
            (200, "text/event-stream"): OpenApiResponse(
                response=OpenApiTypes.STR,
                description="완료 또는 실패 이벤트까지 유지되는 Server-Sent Events 스트림",
            ),
            404: OpenApiResponse(description="작업이 없거나 현재 identity의 소유가 아님"),
        },
    )
)
class OutfitRenderEventStreamView(APIView):
    """소유권을 확인한 이미지 작업의 진행 이벤트를 재생한다."""

    permission_classes = [AllowAny]
    renderer_classes = [ServerSentEventRenderer]

    def get(self, request: Request, job_id):
        job = _owned_render_job(request, job_id)
        requested_cursor = request.headers.get(
            "Last-Event-ID"
        ) or request.query_params.get("last_event_id", "")
        cursor = (
            requested_cursor if _REDIS_STREAM_ID.fullmatch(requested_cursor) else "0-0"
        )
        store = RenderEventStore()

        def stream():
            nonlocal cursor
            yield f"retry: {settings.OUTFIT_RENDER_SSE_RETRY_MILLISECONDS}\n\n"
            try:
                replay = store.read(
                    job.pk,
                    last_event_id=cursor,
                    block_milliseconds=0,
                )
            except redis.RedisError:
                logger.warning(
                    "코디 이미지 SSE 재생 실패: job=%s", job.pk, exc_info=True
                )
                terminal = _render_terminal_event(job, request)
                if terminal is not None:
                    yield encode_sse(terminal)
                else:
                    yield 'event: stream_error\ndata: {"retryable":true}\n\n'
                return

            for event in replay:
                cursor = event.id
                if event.terminal:
                    current = OutfitRenderJob.objects.get(pk=job.pk)
                    terminal = _render_terminal_event(
                        current, request, event_id=event.id
                    )
                    yield encode_sse(terminal or event)
                    return
                yield encode_sse(event)

            terminal = _render_terminal_event(job, request)
            if terminal is not None:
                yield encode_sse(terminal)
                return

            while True:
                try:
                    events = store.read(job.pk, last_event_id=cursor)
                except redis.RedisError:
                    logger.warning(
                        "코디 이미지 SSE 읽기 실패: job=%s", job.pk, exc_info=True
                    )
                    yield 'event: stream_error\ndata: {"retryable":true}\n\n'
                    return
                if events:
                    for event in events:
                        cursor = event.id
                        if event.terminal:
                            current = OutfitRenderJob.objects.get(pk=job.pk)
                            terminal = _render_terminal_event(
                                current, request, event_id=event.id
                            )
                            yield encode_sse(terminal or event)
                            return
                        yield encode_sse(event)
                    continue

                close_old_connections()
                current = OutfitRenderJob.objects.get(pk=job.pk)
                terminal = _render_terminal_event(current, request)
                if terminal is not None:
                    yield encode_sse(terminal)
                    return
                yield heartbeat()

        response = StreamingHttpResponse(stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        return response


DAILY_LOOK_PENDING_EXAMPLE = OpenApiExample(
    "생성 중",
    description=(
        "그날 첫 조회라 방금 생성이 걸렸거나, 워커가 아직 처리 중이다.\n\n"
        "`poll_after_ms` 간격으로 같은 URL을 다시 호출한다. `result`는 아직 null이다."
    ),
    value={
        "look_id": "0c4d1d5a-2f3e-4b7a-9c1e-5a6b7c8d9e01",
        "look_date": "2026-08-09",
        "status": "QUEUED",
        "result": None,
        "context": {
            "weather": {"region": "서울", "temperature": 28.4, "sky_state": "맑음"},
            "used_body": True,
            "used_pursuit": True,
            "body_profile": "역삼각형 · 표준",
            "missing_measurements": ["thigh_length", "calf_length", "torso_length", "leg_length"],
            "candidate_count": 0,
        },
        "poll_after_ms": 1500,
        "detail": "오늘의 룩을 만들고 있어요. 잠시만 기다려주세요.",
    },
    response_only=True,
)

DAILY_LOOK_READY_EXAMPLE = OpenApiExample(
    "생성 완료",
    value={
        "look_id": "0c4d1d5a-2f3e-4b7a-9c1e-5a6b7c8d9e01",
        "look_date": "2026-08-09",
        "status": "SUCCEEDED",
        "result": {
            "headline": "더위엔 가볍게, 어깨는 부드럽게",
            "golden_id": "095",
            # 룩북 필터와 같은 어휘. 골든 코디 라벨(occasion/style)이 비면
            # 사용자 추구미에서 뽑고, 그것도 없으면 빈 배열이다.
            "tags": ["나들이", "미니멀"],
            "rationale_ko": "어깨가 넓은 편이라 상의는 어깨선을 키우지 않는 레귤러핏으로 두고, 하의에 여유를 줘 전체 균형을 맞췄어요. 28도라 겉옷은 생략했습니다.",
            "styling_tips": ["소매를 한 번 접으면 팔 라인이 가벼워 보여요."],
            "generated_by": "llm",
            # 화면의 대표 이미지. 골든 아이템 이미지를 참조로 새로 만든 착용 컷이라
            # 사용권 제약이 없다. 아직 만들어지지 않았으면 null이고, 그때는
            # items[].image_url 카드로 화면을 그린다.
            "render_image_url": "https://skn28-cozy3.s3.ap-northeast-2.amazonaws.com/...render_frontal.png?...",
            # 원본 코디 사진은 사용권이 열린 코디에만 값이 있다 (대개 null).
            "outfit_image_url": None,
            "items": [
                {
                    "item_key": "095#000",
                    "name": "화이트 셔츠",
                    "category": "상의",
                    "sub_category": "셔츠/블라우스",
                    "layer_role": "기본 상의",
                    "color": "화이트",
                    "note": "어깨선을 덮지 않는 기본 기장",
                    # 조회할 때마다 새로 서명한다. 캐시하지 말 것.
                    "image_url": "https://skn28-cozy3.s3.ap-northeast-2.amazonaws.com/...",
                }
            ],
        },
        "context": {
            "weather": {"region": "서울", "temperature": 28.4, "sky_state": "맑음"},
            "used_body": True,
            "used_pursuit": True,
            "body_profile": "역삼각형 · 표준",
            "missing_measurements": [],
            "candidate_count": 5,
        },
        "poll_after_ms": None,
        "detail": None,
    },
    response_only=True,
)

DAILY_LOOK_EMPTY_EXAMPLE = OpenApiExample(
    "추천 후보 없음",
    description=(
        "실패가 아니다. 폴링해도 결과가 바뀌지 않으므로 프론트는 재시도 대신 "
        "프로필 입력을 안내해야 한다."
    ),
    value={
        "look_id": "0c4d1d5a-2f3e-4b7a-9c1e-5a6b7c8d9e01",
        "look_date": "2026-08-09",
        "status": "EMPTY",
        "result": None,
        "context": {
            "weather": {},
            "used_body": False,
            "used_pursuit": False,
            "body_profile": "미판정",
            "missing_measurements": ["height", "weight", "chest", "waist", "hip"],
            "candidate_count": 0,
        },
        "poll_after_ms": None,
        "detail": "조건에 맞는 추천을 찾지 못했어요. 신체치수나 추구미를 입력하면 더 잘 찾을 수 있어요.",
    },
    response_only=True,
)


class DailyLookTodayView(APIView):
    """오늘의 룩 조회 (없으면 생성을 걸고 '생성 중'으로 응답).

    사용자 입력이 없는 기능이라 별도의 생성 엔드포인트를 두지 않았다. 그날 첫
    호출이 곧 생성 트리거다. 홈 API(GET /api/v1/home/)에서 미리 걸어두면
    사용자가 추천 화면에 도착할 때쯤 이미 완성돼 있고, 그 호출이 실패했더라도
    이 조회가 다시 건다 —
    트리거가 한 곳뿐이면 그게 실패했을 때 사용자는 종일 룩을 못 본다.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="daily_look_today",
        summary="오늘의 룩 조회",
        description=(
            "그날의 추천을 돌려준다. 아직 만들어지지 않았으면 생성을 걸고 "
            "`status=QUEUED`로 응답한다.\n\n"
            "**상태별 프론트 동작**\n"
            "- `QUEUED` / `PROCESSING`: `poll_after_ms` 뒤에 다시 호출\n"
            "- `SUCCEEDED`: `result` 표시\n"
            "- `EMPTY`: 폴링하지 말 것. 프로필 입력 안내. 체형·추구미를 저장한 뒤 "
            "다시 호출하면 그 프로필로 재생성이 걸려 `QUEUED`로 바뀐다\n"
            "- `FAILED`: 다음 호출에서 자동 재시도되지 않는다. 사용자에게 알린다\n\n"
            "코디 선택은 검색 단계에서 결정적으로 끝난다. 문장 생성(LLM)이 실패해도 "
            "`SUCCEEDED`이며, 그때는 `result.generated_by`가 `template`이다.\n\n"
            "`image_url`·`render_image_url`은 매 조회마다 새로 서명한다. 클라이언트가 캐시하면 만료된다.\n\n"
            "대표 이미지는 `result.render_image_url`이다. 골든 코디당 한 번만 만들어 "
            "재사용하므로 같은 코디를 받은 사용자끼리 같은 이미지를 본다. 생성 전이거나 "
            "실패하면 null이며, 그때는 `result.items[].image_url` 카드로 화면을 구성한다.\n\n"
            "이 값이 비어 있으면 조회할 때마다 다시 확인한다. 생성이 한 번 실패해도 "
            "다음 시행에서 성공하는 일이 잦아, 그때 이미 만들어져 있으면 이 응답에서 "
            "바로 채워진다. 아직 없으면 재생성을 예약한다(쿨다운 있음). 즉 "
            "`SUCCEEDED`인데 `render_image_url`이 null이면, 잠시 뒤 다시 조회할 때 "
            "값이 생길 수 있다 — 폴링을 계속할 필요는 없고 다음 진입에서 채워진다.\n\n"
            "위경도를 주면 그 위치의 날씨로 추천한다. 생성은 하루 한 번뿐이라 "
            "이미 만들어진 뒤의 좌표는 반영되지 않는다(`EMPTY` 재생성은 예외 — "
            "그때는 그 시점 날씨로 다시 만든다)."
        ),
        parameters=[
            OpenApiParameter(
                name="lat", type=float, required=False,
                description="위도. 미전달 시 서울 좌표로 대체한다.",
            ),
            OpenApiParameter(
                name="lon", type=float, required=False,
                description="경도. 미전달 시 서울 좌표로 대체한다.",
            ),
        ],
        responses={200: DailyLookSerializer},
        examples=[
            DAILY_LOOK_PENDING_EXAMPLE,
            DAILY_LOOK_READY_EXAMPLE,
            DAILY_LOOK_EMPTY_EXAMPLE,
        ],
    )
    def get(self, request: Request) -> Response:
        look, created = daily_look_service.ensure_today_look(
            request.user,
            lat=_float_or_none(request.query_params.get("lat")),
            lon=_float_or_none(request.query_params.get("lon")),
        )
        if created:
            logger.info("오늘의 룩 생성 접수: user=%s look=%s", request.user.pk, look.pk)

        # 착용 이미지는 생성 시점에 실패해도 다음 시행에서 성공하는 일이 잦다.
        # 결과 JSON은 생성이 끝날 때 한 번만 쓰이므로, 그 사이에 이미지가 생겨도
        # 행은 비어 있는 채로 남는다. 조회할 때마다 한 번 더 확인해 붙인다.
        # 생성은 하지 않는다 — 수십 초가 걸려 이 요청을 잡아둘 수 없다.
        daily_look_service.refresh_render(look)
        # '다른 룩' 후보 이미지도 같은 이유로 한 번 더 본다 (생성은 큐에 맡긴다).
        daily_look_service.refresh_alternatives(look)

        return Response(DailyLookSerializer(look).data)


class DailyLookSaveView(APIView):
    """오늘의 룩을 내 룩북에 담는다 (홈 카드의 '저장' 버튼).

    사진 룩북과 달리 **아무것도 업로드하지 않는다.** 담는 대상은 이미 골든셋
    버킷에 있는 코디라, 이미지는 버킷·키로 가리키기만 하고 구성 아이템은
    스냅샷으로만 남는다. 옷장 파이프라인(GPU)도 타지 않는다 — 이미 태깅이 끝난
    옷을 다시 태깅하는 셈이기 때문이다.

    본문은 `golden_id` 하나뿐이고 그마저 선택이다('다른 룩'으로 돌려보던 후보를
    담을 때만 쓴다). 클라이언트가 코디를 **지정**하되 고를 수 있는 목록은 서버가
    정한다 — 그 사용자의 오늘 후보 안에 없으면 404다. 목록까지 클라이언트에게
    맡기면 남의 코디도 담을 수 있는 구멍이 된다.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="daily_look_save",
        summary="오늘의 룩 저장",
        description=(
            "그날의 오늘의 룩을 내 룩북에 담는다. 본문은 없다.\n\n"
            "- `201`: 새로 담았다\n"
            "- `200`: 이미 담아 둔 코디다 (같은 룩북을 돌려준다). "
            "같은 골든 코디는 사용자당 한 번만 담긴다\n"
            "- `409`: 아직 담을 수 없다. `status`가 그 이유이며 "
            "`GET /api/v1/looks/today/`의 상태값과 같다 "
            "(`QUEUED`/`PROCESSING`이면 잠시 뒤 다시, `EMPTY`/`FAILED`/`MISSING`이면 "
            "담을 추천이 없다)\n"
            "- `404`: `golden_id`가 오늘 이 사용자에게 나간 룩이 아니다\n\n"
            "`golden_id`를 주면 '다른 룩'으로 돌려보던 그 후보를 담는다. 생략하면 "
            "대표 룩이다. 값은 조회 응답의 `result.golden_id` 또는 "
            "`alternatives[].golden_id` 여야 하며, **서버가 그 사용자의 오늘 후보 "
            "안에 있는지 확인한다** — 임의의 코디를 담을 수는 없다.\n\n"
            "응답의 `lookbook`은 `GET /api/v1/lookbooks/`의 항목과 같은 스키마다."
        ),
        request=DailyLookSaveRequestSerializer,
        responses={
            200: DailyLookSaveResponseSerializer,
            201: DailyLookSaveResponseSerializer,
            404: OpenApiResponse(description="오늘 나간 룩이 아닌 golden_id"),
            409: OpenApiResponse(description="아직 담을 수 있는 추천이 아니다"),
        },
    )
    def post(self, request: Request) -> Response:
        payload = DailyLookSaveRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            post, created = daily_look_save.save_to_lookbook(
                request.user, golden_id=payload.validated_data["golden_id"]
            )
        except daily_look_save.GoldenLookNotInTodayError as error:
            # 400이 아니라 404다. 값의 형식이 아니라 **그 코디가 여기 없다**는 뜻이고,
            # 어제 룩을 담으려는 오래된 화면에서도 이 응답이 난다.
            return Response(
                {
                    "code": "GOLDEN_LOOK_NOT_IN_TODAY",
                    "golden_id": error.golden_id,
                    "detail": "오늘 추천에 없는 룩입니다. 새로고침 후 다시 시도해주세요.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        except daily_look_save.DailyLookNotSavableError as error:
            return Response(
                {
                    "code": "DAILY_LOOK_NOT_READY",
                    "status": error.status,
                    "detail": "아직 담을 수 있는 오늘의 룩이 없습니다.",
                },
                status=status.HTTP_409_CONFLICT,
            )

        if created:
            logger.info(
                "오늘의 룩 저장: user=%s lookbook=%s golden=%s",
                request.user.pk, post.pk, post.golden_id,
            )
        return Response(
            {"created": created, "lookbook": LookbookPostSerializer(post).data},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


def _float_or_none(raw: str | None) -> float | None:
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        # 좌표가 깨졌다고 추천을 막을 이유는 없다. 서울 좌표로 폴백한다.
        return None
