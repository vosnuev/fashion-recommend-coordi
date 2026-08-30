from drf_spectacular.extensions import (
    OpenApiAuthenticationExtension,
    OpenApiViewExtension,
)
from rest_framework import serializers
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import serializers

from apps.api_docs.serializers import (
    BodyPhotoResponseSerializer,
    DetailResponseSerializer,
    HomeResponseSerializer,
    PreferenceOptionsResponseSerializer,
    SocialLoginResponseSerializer,
)
from apps.lookbook.serializers import (
    LookbookMetadataUpdateSerializer,
    LookbookPhotoCreateSerializer,
    LookbookPostSerializer,
    LookbookProcessingStatusSerializer,
    LookbookWardrobeCreateSerializer,
)
from apps.style_calendar.serializers import (
    CalendarEntrySerializer,
    CalendarMetadataUpdateSerializer,
    CalendarPhotoCreateSerializer,
    CalendarProcessingStatusSerializer,
    CalendarWardrobeCreateSerializer,
)
from apps.users.serializers import (
    BodyBasicInputSerializer,
    BodyDetailInputSerializer,
    BodyEstimateInputSerializer,
    BodyEstimationResultSerializer,
    BodyMeasurementSerializer,
    BodyPhotoTransactionSerializer,
    BodyPhotoUploadSerializer,
    EmailLoginSerializer,
    EmailSignupSerializer,
    EmailVerificationResendSerializer,
    EmailVerificationSerializer,
    BudgetSerializer,
    PursuitPayloadInputSerializer,
    PursuitPayloadResponseSerializer,
    SocialLoginSerializer,
    UserSerializer,
)
from apps.wardrobe.serializers import (
    CallbackSerializer,
    WardrobeBatchCreateSerializer,
    WardrobeItemSerializer,
    WardrobeItemUpdateSerializer,
    WardrobeJobSerializer,
    WardrobeUploadSerializer,
)


class JWTAuthenticationExtension(OpenApiAuthenticationExtension):
    """
    simplejwt 기본 확장을 대체(priority)해 헤더 인증 구조 설명을 추가한다.

    보호된 엔드포인트는 소셜 로그인으로 발급받은 access 토큰을
    Authorization 헤더에 담아 호출해야 한다.
    """

    target_class = "rest_framework_simplejwt.authentication.JWTAuthentication"
    name = "jwtAuth"
    priority = 1  # drf-spectacular 내장 simplejwt 확장(priority 0)보다 우선

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": (
                "이메일 또는 소셜 로그인 API가 발급한 "
                "**access 토큰**을 `Authorization: Bearer <access>` 헤더로 전달합니다.\n\n"
                "- access 토큰 만료 시 401이 반환되며, "
                "`POST /api/v1/auth/token/refresh/`로 재발급합니다.\n"
                "- refresh 토큰은 회전(rotate)되므로 갱신 응답의 새 refresh 토큰으로 "
                "교체 저장해야 합니다 (이전 refresh 토큰은 블랙리스트 처리)."
            ),
        }


class TokenRefreshViewExtension(OpenApiViewExtension):
    """simplejwt 기본 영문 설명을 서비스 맥락에 맞는 한국어 문서로 교체한다."""

    target_class = "rest_framework_simplejwt.views.TokenRefreshView"

    def view_replacement(self):
        @extend_schema_view(
            post=extend_schema(
                operation_id="token_refresh",
                tags=["Authentication"],
                summary="JWT 토큰 갱신",
                description=(
                    "refresh 토큰으로 새 access 토큰을 발급합니다.\n\n"
                    "- refresh 토큰이 회전되므로 응답에 **새 refresh 토큰**도 함께 "
                    "반환됩니다. 클라이언트는 두 토큰 모두 교체 저장해야 합니다.\n"
                    "- 이전 refresh 토큰은 블랙리스트 처리되어 재사용 시 401이 "
                    "반환됩니다."
                ),
            )
        )
        class DocumentedTokenRefreshView(self.target_class):
            pass

        return DocumentedTokenRefreshView


class EmailSignupViewExtension(OpenApiViewExtension):
    target_class = "apps.users.views.EmailSignupView"

    def view_replacement(self):
        @extend_schema_view(
            post=extend_schema(
                operation_id="email_signup",
                tags=["Authentication"],
                summary="이메일 회원가입",
                description="비활성 이메일 계정을 생성하고 6자리 소유 확인 코드를 발송합니다.",
                request=EmailSignupSerializer,
                responses={
                    202: inline_serializer(
                        name="EmailSignupPendingResponse",
                        fields={
                            "email": serializers.EmailField(),
                            "verification_required": serializers.BooleanField(),
                            "retry_after": serializers.IntegerField(),
                        },
                    ),
                    400: OpenApiResponse(description="이메일 중복 또는 비밀번호 정책 오류"),
                },
            )
        )
        class DocumentedEmailSignupView(self.target_class):
            pass

        return DocumentedEmailSignupView


class EmailVerificationViewExtension(OpenApiViewExtension):
    target_class = "apps.users.views.EmailVerificationView"

    def view_replacement(self):
        @extend_schema_view(
            post=extend_schema(
                operation_id="email_verify",
                tags=["Authentication"],
                summary="이메일 인증 코드 확인",
                description=(
                    "이메일 소유를 확인하고 계정을 활성화합니다. "
                    "**토큰은 발급하지 않으므로** 인증 후 로그인 API를 호출해야 합니다."
                ),
                request=EmailVerificationSerializer,
                responses={
                    200: inline_serializer(
                        name="EmailVerifiedResponse",
                        fields={
                            "email": serializers.EmailField(),
                            "verified": serializers.BooleanField(),
                        },
                    ),
                    400: OpenApiResponse(description="코드 오류·만료·이미 인증된 이메일"),
                },
            )
        )
        class DocumentedEmailVerificationView(self.target_class):
            pass

        return DocumentedEmailVerificationView


class EmailVerificationResendViewExtension(OpenApiViewExtension):
    target_class = "apps.users.views.EmailVerificationResendView"

    def view_replacement(self):
        @extend_schema_view(
            post=extend_schema(
                operation_id="email_verification_resend",
                tags=["Authentication"],
                summary="이메일 인증 코드 재발송",
                request=EmailVerificationResendSerializer,
                responses={200: OpenApiResponse(description="재발송 완료"), 400: OpenApiResponse(description="재발송 대기 중")},
            )
        )
        class DocumentedEmailVerificationResendView(self.target_class):
            pass

        return DocumentedEmailVerificationResendView


class EmailLoginViewExtension(OpenApiViewExtension):
    target_class = "apps.users.views.EmailLoginView"

    def view_replacement(self):
        @extend_schema_view(
            post=extend_schema(
                operation_id="email_login",
                tags=["Authentication"],
                summary="이메일 로그인",
                description=(
                    "이메일과 비밀번호를 확인하고 서비스 JWT를 발급합니다. "
                    "`is_new_user`는 가입 후 첫 로그인(`last_login`이 NULL)일 때 true이며, "
                    "앱은 이 값으로 온보딩(권한 → 체형 측정 → 추구미) 진입을 분기합니다."
                ),
                request=EmailLoginSerializer,
                responses={
                    200: SocialLoginResponseSerializer,
                    400: OpenApiResponse(description="이메일 또는 비밀번호 불일치"),
                },
            )
        )
        class DocumentedEmailLoginView(self.target_class):
            pass

        return DocumentedEmailLoginView


# apple은 백엔드 코드는 있으나 서비스 구현 보류 상태라 문서에서 제외한다.
PROVIDER_PARAMETER = OpenApiParameter(
    name="provider",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.PATH,
    required=True,
    enum=["naver", "kakao", "google"],
    description="소셜 로그인 제공자",
)

SOCIAL_LOGIN_DESCRIPTION = """소셜 로그인 제공자의 인증 정보를 서비스 JWT로 교환합니다.

## 제공자별 필수값

### kakao — 두 가지 방식 지원
| 방식 | 필수값 | 사용처 |
|------|--------|--------|
| code 방식 | `code`, `redirect_uri` | 웹 프론트엔드 (인가 코드 플로우) |
| token 방식 | `access_token` | 네이티브 앱 (Android/iOS SDK) |

token 방식은 백엔드가 토큰의 `app_id`를 검증하므로 다른 앱에서 발급된 토큰은 거부됩니다.

### google — 두 가지 방식 지원
| 방식 | 필수값 | 사용처 |
|------|--------|--------|
| code 방식 | `code`, `redirect_uri` | 웹 프론트엔드 (인가 코드 플로우) |
| token 방식 | `access_token` | 네이티브 앱 (Android/iOS SDK) |

token 방식은 백엔드가 토큰의 `aud`(발급 대상 client_id)를 검증하므로 다른 앱에서 발급된 토큰은 거부됩니다.

### naver — 두 가지 방식 지원
| 방식 | 필수값 | 사용처 |
|------|--------|--------|
| code 방식 | `code`, `state` | 웹 프론트엔드 (redirect_uri 대신 CSRF 방지용 state 검증) |
| token 방식 | `access_token` | 네이티브 앱 (Android/iOS SDK) |

⚠️ naver token 방식은 토큰 유효성 확인과 사용자 식별(`/v1/nid/me`)만 수행합니다.
naver는 발급 앱을 확인할 API를 제공하지 않아 **다른 naver 앱에서 발급된 토큰을
구분할 수 없습니다** (kakao/google과 달리 발급 앱 검증 없음). 이 한계를 수용한
구현이므로, 가능하면 code 방식을 우선 사용하세요.

### 공통 규칙
- code 방식의 `redirect_uri`는 인가 요청 시 사용한 값과 동일해야 합니다.
- `code`와 `access_token`을 함께 보내면 code 방식으로 처리됩니다.

## 응답
성공 시 서비스 자체 JWT(`access`/`refresh`)와 사용자 정보를 반환합니다.
신규 가입이면 201, 기존 사용자 로그인이면 200입니다.
"""

SOCIAL_LOGIN_EXAMPLES = [
    OpenApiExample(
        name="kakao (code 방식, 웹)",
        value={
            "code": "인가_코드",
            "redirect_uri": "https://service.example.com/oauth/kakao/callback",
        },
        request_only=True,
    ),
    OpenApiExample(
        name="kakao (token 방식, 네이티브 앱)",
        value={"access_token": "카카오_SDK가_발급한_액세스_토큰"},
        request_only=True,
    ),
    OpenApiExample(
        name="google (code 방식)",
        value={
            "code": "인가_코드",
            "redirect_uri": "https://service.example.com/oauth/google/callback",
        },
        request_only=True,
    ),
    OpenApiExample(
        name="google (token 방식, 네이티브 앱)",
        value={"access_token": "구글_SDK가_발급한_액세스_토큰"},
        request_only=True,
    ),
    OpenApiExample(
        name="naver (code 방식)",
        value={"code": "인가_코드", "state": "인가_요청_시_보낸_state"},
        request_only=True,
    ),
    OpenApiExample(
        name="naver (token 방식, 네이티브 앱)",
        value={"access_token": "네이버_SDK가_발급한_액세스_토큰"},
        request_only=True,
    ),
]


class SocialLoginViewExtension(OpenApiViewExtension):
    target_class = "apps.users.views.SocialLoginView"

    def view_replacement(self):
        @extend_schema_view(
            post=extend_schema(
                operation_id="social_login",
                tags=["Authentication"],
                summary="소셜 로그인",
                description=SOCIAL_LOGIN_DESCRIPTION,
                parameters=[PROVIDER_PARAMETER],
                request=SocialLoginSerializer,
                examples=SOCIAL_LOGIN_EXAMPLES,
                responses={
                    200: SocialLoginResponseSerializer,
                    201: SocialLoginResponseSerializer,
                    400: OpenApiResponse(
                        response=DetailResponseSerializer,
                        description="요청값 또는 provider 오류",
                    ),
                    401: OpenApiResponse(
                        response=DetailResponseSerializer,
                        description="소셜 로그인 실패",
                    ),
                },
            )
        )
        class DocumentedSocialLoginView(self.target_class):
            pass

        return DocumentedSocialLoginView


HOME_DESCRIPTION = """홈 화면에 필요한 데이터를 한 번에 반환합니다 (로그인 필요).

- `lat`/`lon`을 보내면 가장 가까운 예보구역의 현재 날씨를 반환합니다.
- 좌표가 없거나 국내 범위(위도 33~39, 경도 124~132)를 벗어나면 서울시청 좌표로 대체합니다.
- `quick_recommends`, `closet_count`, `saved_look_count`는 실제 추천·옷장 기능 연동 전까지 mock 값입니다.
- 부수 효과: 그날 첫 호출이면 **오늘의 룩 생성을 미리 걸어둡니다** (`GET /api/v1/looks/today/`가 곧 완성된 결과를 받도록). 전달한 `lat`/`lon`이 그날 추천의 날씨 기준이 됩니다.
"""

HOME_COORDINATE_PARAMETERS = [
    OpenApiParameter(
        name="lat",
        type=OpenApiTypes.NUMBER,
        location=OpenApiParameter.QUERY,
        required=False,
        description="위도 (예: 37.5665). 생략 시 서울시청 좌표 사용.",
    ),
    OpenApiParameter(
        name="lon",
        type=OpenApiTypes.NUMBER,
        location=OpenApiParameter.QUERY,
        required=False,
        description="경도 (예: 126.9780). 생략 시 서울시청 좌표 사용.",
    ),
]

HOME_RESPONSE_EXAMPLE = OpenApiExample(
    name="맑은 날 예시",
    value={
        "nickname": "건우",
        "weather": {
            "region": "서울",
            "temperature": 26,
            "sky_state": "맑음",
            "is_stale": False,
            "observed_at": "2026-07-15T14:00:00+09:00",
        },
        "today_look": {
            "comment": "26도예요. 반팔이면 딱 좋은 날씨예요.",
            "tags": ["반팔 티셔츠", "얇은 셔츠", "면바지"],
        },
        "quick_recommends": ["출근룩", "데이트룩", "면접룩", "주말룩"],
        "closet_count": 42,
        "saved_look_count": 8,
    },
    response_only=True,
)


class HomeViewExtension(OpenApiViewExtension):
    target_class = "apps.home.views.HomeView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="get_home",
                tags=["Home"],
                summary="홈 화면 통합 조회",
                description=HOME_DESCRIPTION,
                parameters=HOME_COORDINATE_PARAMETERS,
                examples=[HOME_RESPONSE_EXAMPLE],
                responses={
                    200: HomeResponseSerializer,
                    401: OpenApiResponse(
                        response=DetailResponseSerializer,
                        description="인증 실패 (JWT 필요)",
                    ),
                },
            )
        )
        class DocumentedHomeView(self.target_class):
            pass

        return DocumentedHomeView


BODY_DETAIL_DESCRIPTION = """상세 치수와 체형 지표를 저장합니다. **모든 필드가 선택 입력**입니다.

- 보낸 필드만 갱신됩니다 (partial update).
- 필드에 `null`을 보내면 저장된 값을 지웁니다.
- 이 값들은 옷 추천·핏 판단에 쓰는 **패션용 체형 지표**입니다. 정밀 의료 실측값이 아닙니다.
- 둘레·길이감 단위는 cm, 소수점 1자리까지 허용합니다 (1 ~ 999.9).
- `thigh_length`는 사진상 샅선/인심 라인에서 무릎뼈/무릎 중심까지의 허벅지 길이감입니다.
- `calf_length`는 사진상 무릎뼈/무릎 중심에서 복사뼈/발목 라인까지의 종아리 길이감입니다.
- `torso_length`는 사진상 어깨선에서 골반점까지의 상체 길이감입니다.
- `leg_length`는 사진상 샅선/인심 라인에서 복사뼈/발목 라인까지의 하체 길이감입니다.
- `neck_length`는 정면 기준 턱밑/턱끝 라인에서 목앞/쇄골 라인까지 보이는 목 길이감입니다.
- `thigh_calf_ratio`는 허벅지 길이감 / 종아리 길이감입니다 (정확 3D 랜드마크 SizeKorea 평균 `0.823`, p01~p99 약 `0.652~0.970`).
- `torso_leg_ratio`는 상체 길이감 / 하체 길이감입니다 (정확 3D 랜드마크 SizeKorea 평균 `0.546`, p01~p99 약 `0.466~0.637`).
"""

BODY_PHOTOS_DESCRIPTION = """정면/측면 전신 사진을 접수하고 **신체 측정을 비동기로 시작**합니다 (multipart/form-data).

- 사진은 **서버에 저장하지 않습니다.** 추론에만 쓰고 요청 처리 후 즉시 버립니다.
- 접수 시 측정 트랜잭션이 `in_progress`로 생성되고, 202와 함께 `transaction_id`가 반환됩니다.
- 결과는 **결과 조회 API**(`GET /users/me/body/photos/{transaction_id}/`)를 폴링해서 받습니다.
- 성공하면 11개 패션용 체형 지표(어깨·가슴·허리·엉덩이·허벅지 길이감·종아리 길이감·상체 길이감·하체 길이감·목 길이감·두 비율)가 **전부** 갱신됩니다.
- `gender`/`height`/`weight`는 생략 가능합니다. 생략하면 저장된 기본 신체치수를 사용하며,
  저장된 값도 없고 요청에도 없으면 **400**입니다.
- 이미 진행 중인 측정이 있으면 **400**입니다. 단 5분이 지나도 끝나지 않은 측정은
  자동으로 실패 처리되어 다시 올릴 수 있습니다.
- 파일당 10MB 이하의 이미지 파일이어야 합니다.
"""

BODY_PHOTO_TX_DESCRIPTION = """사진 접수 시 발급된 `transaction_id`로 측정 상태와 결과를 조회합니다.

- `status`: `in_progress`(진행중) | `succeeded`(성공) | `failed`(실패)
- 응답 형식은 사진 없이 추정하는 API(`POST /users/me/body/estimate/`)와 **동일**합니다.
  사진 등록 여부와 무관하게 같은 파서로 처리할 수 있습니다.
- ⚠️ **`status`를 반드시 확인하고 화면을 그리세요.** `in_progress`일 때도 `measurement`에는
  값이 들어 있는데, 이건 **이전 추정 결과**입니다. `status`를 보지 않으면 옛 수치를
  새 결과처럼 표시하게 됩니다.
- `failed`면 `error_message`에 실패 사유가 들어갑니다.
- 다른 사용자의 트랜잭션이거나 없는 ID면 404입니다.
"""


class BodyMeasurementViewExtension(OpenApiViewExtension):
    target_class = "apps.users.views.BodyMeasurementView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="get_body_measurement",
                tags=["Body"],
                summary="신체치수 조회",
                description=(
                    "저장된 신체치수를 반환합니다. 아직 입력하지 않은 필드는 `null`입니다.\n\n"
                    "- `gender`: `male` | `female` (기본 신체치수 입력 전이면 `null`)"
                ),
                responses={
                    200: BodyMeasurementSerializer,
                    401: DetailResponseSerializer,
                },
            )
        )
        class DocumentedBodyMeasurementView(self.target_class):
            pass

        return DocumentedBodyMeasurementView


class BodyBasicViewExtension(OpenApiViewExtension):
    target_class = "apps.users.views.BodyBasicView"

    def view_replacement(self):
        @extend_schema_view(
            put=extend_schema(
                operation_id="update_body_basic",
                tags=["Body"],
                summary="기본 신체치수 입력 (성별·키·몸무게)",
                description=(
                    "성별, 키(cm), 몸무게(kg)를 저장합니다. **세 값 모두 필수**입니다.\n\n"
                    "- `gender`: `male` | `female`\n"
                    "- `height`/`weight`: 소수점 1자리까지 허용 (1 ~ 999.9)\n"
                    "- 상세 수치는 건드리지 않습니다."
                ),
                request=BodyBasicInputSerializer,
                responses={
                    200: BodyMeasurementSerializer,
                    400: DetailResponseSerializer,
                    401: DetailResponseSerializer,
                },
            )
        )
        class DocumentedBodyBasicView(self.target_class):
            pass

        return DocumentedBodyBasicView


class BodyDetailViewExtension(OpenApiViewExtension):
    target_class = "apps.users.views.BodyDetailView"

    def view_replacement(self):
        @extend_schema_view(
            patch=extend_schema(
                operation_id="update_body_detail",
                tags=["Body"],
                summary="상세 신체치수 입력 (전부 선택)",
                description=BODY_DETAIL_DESCRIPTION,
                request=BodyDetailInputSerializer,
                responses={
                    200: BodyMeasurementSerializer,
                    400: DetailResponseSerializer,
                    401: DetailResponseSerializer,
                },
            )
        )
        class DocumentedBodyDetailView(self.target_class):
            pass

        return DocumentedBodyDetailView


class BodyEstimateViewExtension(OpenApiViewExtension):
    target_class = "apps.users.views.BodyEstimateView"

    def view_replacement(self):
        @extend_schema_view(
            post=extend_schema(
                operation_id="estimate_body_measurement",
                tags=["Body"],
                summary="사진 없이 상세 신체치수 추정 (동기)",
                description=(
                    "성별·키·몸무게만으로 새 11개 항목을 추정해 저장하고 결과를 반환합니다.\n\n"
                    "- 세 값을 본문에 담지 않으면 이미 저장된 기본 신체치수를 사용합니다.\n"
                    "- 저장된 값도 없고 요청에도 없으면 400입니다.\n"
                    "- 추정값은 기존 상세 수치를 덮어씁니다. 이후 "
                    "`PATCH /users/me/body/detail/`로 사용자가 직접 고칠 수 있습니다.\n"
                    "- 성별·키·몸무게는 추정 모델이 만들어내지 않습니다. "
                    "저장되는 값은 항상 사용자가 입력한 값입니다.\n"
                    "- 응답 형식은 사진 측정 결과 조회 API와 동일합니다."
                ),
                request=BodyEstimateInputSerializer,
                responses={
                    200: BodyEstimationResultSerializer,
                    400: OpenApiResponse(
                        response=DetailResponseSerializer,
                        description="요청값 오류 또는 기본 신체치수 미입력",
                    ),
                    401: DetailResponseSerializer,
                },
            )
        )
        class DocumentedBodyEstimateView(self.target_class):
            pass

        return DocumentedBodyEstimateView


class BodyPhotoViewExtension(OpenApiViewExtension):
    target_class = "apps.users.views.BodyPhotoView"

    def view_replacement(self):
        @extend_schema_view(
            post=extend_schema(
                operation_id="upload_body_photos",
                tags=["Body"],
                summary="신체 사진 접수 → 측정 트랜잭션 시작 (비동기)",
                description=BODY_PHOTOS_DESCRIPTION,
                request=BodyPhotoUploadSerializer,
                responses={
                    202: BodyPhotoResponseSerializer,
                    400: OpenApiResponse(
                        response=DetailResponseSerializer,
                        description="요청값 오류 또는 이미 진행 중인 측정 존재",
                    ),
                    401: DetailResponseSerializer,
                },
            )
        )
        class DocumentedBodyPhotoView(self.target_class):
            pass

        return DocumentedBodyPhotoView


class BodyPhotoTransactionViewExtension(OpenApiViewExtension):
    target_class = "apps.users.views.BodyPhotoTransactionView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="get_body_photo_transaction",
                tags=["Body"],
                summary="신체 측정 트랜잭션 결과 조회",
                description=BODY_PHOTO_TX_DESCRIPTION,
                responses={
                    200: BodyEstimationResultSerializer,
                    401: DetailResponseSerializer,
                    404: OpenApiResponse(
                        response=DetailResponseSerializer,
                        description="트랜잭션 없음 (또는 다른 사용자의 트랜잭션)",
                    ),
                },
            )
        )
        class DocumentedBodyPhotoTransactionView(self.target_class):
            pass

        return DocumentedBodyPhotoTransactionView


class MeViewExtension(OpenApiViewExtension):
    target_class = "apps.users.views.MeView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="get_current_user",
                tags=["Users"],
                summary="내 정보 조회",
                responses={
                    200: UserSerializer,
                    401: DetailResponseSerializer,
                },
            ),
            patch=extend_schema(
                operation_id="update_current_user",
                tags=["Users"],
                summary="내 정보 수정",
                request=UserSerializer,
                responses={
                    200: UserSerializer,
                    400: DetailResponseSerializer,
                    401: DetailResponseSerializer,
                },
            ),
        )
        class DocumentedMeView(self.target_class):
            pass

        return DocumentedMeView


class PreferenceOptionsViewExtension(OpenApiViewExtension):
    target_class = "apps.users.views.PreferenceOptionsView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="get_preference_options",
                tags=["Pursuit"],
                summary="추구미 옵션 마스터 조회",
                description=(
                    "11개 카테고리(계절/스타일/색상/...)의 선택 가능한 옵션 목록을 반환합니다. "
                    "화면 진입 시 옵션 칩을 렌더링하는 용도입니다."
                ),
                responses={
                    200: PreferenceOptionsResponseSerializer,
                    401: DetailResponseSerializer,
                },
            )
        )
        class DocumentedPreferenceOptionsView(self.target_class):
            pass

        return DocumentedPreferenceOptionsView


PURSUIT_DESCRIPTION = """내 추구미(선호/기피 스타일) 정보를 조회·저장합니다.

- `preferred`/`avoided` 두 그룹으로 구성되며, 각 그룹은 11개 카테고리 키를 **모두** 포함해야 합니다.
- 각 카테고리 값은 선택된 옵션 `code` 배열입니다 (빈 배열 허용).
- 저장된 적 없으면 GET은 모든 카테고리가 빈 배열인 payload를 반환합니다 (404 아님).
- PUT은 전체 교체(upsert)입니다 — 부분 갱신을 지원하지 않습니다.
"""


class PursuitViewExtension(OpenApiViewExtension):
    target_class = "apps.users.views.PursuitView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="get_pursuit",
                tags=["Pursuit"],
                summary="내 추구미 조회",
                description=PURSUIT_DESCRIPTION,
                responses={
                    200: PursuitPayloadResponseSerializer,
                    401: DetailResponseSerializer,
                },
            ),
            put=extend_schema(
                operation_id="update_pursuit",
                tags=["Pursuit"],
                summary="내 추구미 저장 (전체 교체)",
                description=PURSUIT_DESCRIPTION,
                request=PursuitPayloadInputSerializer,
                responses={
                    200: PursuitPayloadResponseSerializer,
                    400: DetailResponseSerializer,
                    401: DetailResponseSerializer,
                },
            ),
        )
        class DocumentedPursuitView(self.target_class):
            pass

        return DocumentedPursuitView


BUDGET_DESCRIPTION = """대분류별 상품 1개 최대 가격을 조회·설정합니다.

- 금액은 **1만원 단위**입니다. 10,000으로 나누어떨어지지 않으면 400입니다.
- 범위는 10,000 이상 2,147,480,000 이하입니다.
- 지원 대분류는 상의, 하의, 아우터, 원피스/세트, 신발, 가방, 액세서리입니다.
- `category_budgets`에는 사용자가 바꾼 값만 저장합니다.
- 미설정 카테고리는 시스템 기본값을 적용하며 `effective_category_budgets`에서 확인합니다.
- 빈 객체를 보내면 모든 카테고리를 시스템 기본값으로 되돌립니다.
- PUT은 전체 교체라 `category_budgets` 키가 반드시 있어야 합니다.
"""

BUDGET_REQUEST_EXAMPLES = [
    OpenApiExample(
        name="카테고리별 예산 설정",
        value={"category_budgets": {"상의": 100000, "하의": 150000, "아우터": 300000}},
        request_only=True,
    ),
    OpenApiExample(
        name="모든 예산을 기본값으로 복원",
        description="category_budgets 키는 유지하고 빈 객체를 보낸다.",
        value={"category_budgets": {}},
        request_only=True,
    ),
]

BUDGET_RESPONSE_EXAMPLES = [
    OpenApiExample(
        name="설정됨",
        value={
            "category_budgets": {"상의": 120000},
            "effective_category_budgets": {
                "상의": 120000,
                "하의": 50000,
                "아우터": 150000,
                "원피스/세트": 50000,
                "신발": 100000,
                "가방": 200000,
                "액세서리": 50000,
            },
        },
        response_only=True,
    ),
    OpenApiExample(
        name="미설정",
        value={
            "category_budgets": {},
            "effective_category_budgets": {
                "상의": 50000,
                "하의": 50000,
                "아우터": 150000,
                "원피스/세트": 50000,
                "신발": 100000,
                "가방": 200000,
                "액세서리": 50000,
            },
        },
        response_only=True,
    ),
]


class BudgetViewExtension(OpenApiViewExtension):
    """예산 API는 평범한 APIView라 serializer를 추론할 근거가 없다.

    선언해 주지 않으면 drf-spectacular가 PUT의 request body를 빈 것으로 내보내,
    Swagger UI에서 뭐를 보내야 하는지 알 수 없게 된다.
    """

    target_class = "apps.users.views.BudgetView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="get_budget",
                tags=["Users"],
                summary="내 카테고리별 상품 예산 조회",
                description=BUDGET_DESCRIPTION,
                responses={
                    200: BudgetSerializer,
                    401: DetailResponseSerializer,
                },
                examples=BUDGET_RESPONSE_EXAMPLES,
            ),
            put=extend_schema(
                operation_id="update_budget",
                tags=["Users"],
                summary="내 카테고리별 상품 예산 설정 (전체 교체)",
                description=BUDGET_DESCRIPTION,
                request=BudgetSerializer,
                responses={
                    200: BudgetSerializer,
                    400: DetailResponseSerializer,
                    401: DetailResponseSerializer,
                },
                examples=BUDGET_REQUEST_EXAMPLES + BUDGET_RESPONSE_EXAMPLES,
            ),
        )
        class DocumentedBudgetView(self.target_class):
            pass

        return DocumentedBudgetView


# =============================================================================
# 옷장 (Wardrobe) — 아이템 등록 · 조회 · 확정
# =============================================================================

from rest_framework import serializers as drf_serializers  # noqa: E402

from apps.wardrobe import taxonomy as wardrobe_taxonomy  # noqa: E402

BATCH_STATUSES = ["PENDING", "PROCESSING", "DONE", "PARTIAL", "FAILED"]

class WardrobeBatchCountsSerializer(drf_serializers.Serializer):
    total = drf_serializers.IntegerField()
    pending = drf_serializers.IntegerField()
    done = drf_serializers.IntegerField()
    failed = drf_serializers.IntegerField()


class WardrobeBatchResponseSerializer(drf_serializers.Serializer):
    batch_id = drf_serializers.UUIDField()
    status = drf_serializers.ChoiceField(choices=BATCH_STATUSES)
    source = drf_serializers.CharField()
    counts = WardrobeBatchCountsSerializer()
    progress = drf_serializers.FloatField()
    poll_after_ms = drf_serializers.IntegerField(allow_null=True)
    created_at = drf_serializers.DateTimeField()
    finished_at = drf_serializers.DateTimeField(allow_null=True)
    jobs = WardrobeJobSerializer(many=True)


class WardrobeBatchCreateResponseSerializer(drf_serializers.Serializer):
    batch_id = drf_serializers.UUIDField()
    status = drf_serializers.ChoiceField(choices=BATCH_STATUSES)
    total_count = drf_serializers.IntegerField()
    accepted = drf_serializers.ListField(child=drf_serializers.DictField())
    rejected = drf_serializers.ListField(child=drf_serializers.DictField())
    poll_url = drf_serializers.CharField()
    poll_after_ms = drf_serializers.IntegerField()
    estimated_seconds = drf_serializers.IntegerField()


class WardrobeBatchViewExtension(OpenApiViewExtension):
    target_class = "apps.wardrobe.views.WardrobeBatchView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="wardrobe_batches",
                tags=["Wardrobe"],
                summary="옷장 일괄 등록 목록",
                parameters=[
                    OpenApiParameter(
                        name="status",
                        type=OpenApiTypes.STR,
                        location=OpenApiParameter.QUERY,
                        required=False,
                        enum=BATCH_STATUSES,
                    ),
                    OpenApiParameter(
                        name="limit",
                        type=OpenApiTypes.INT,
                        location=OpenApiParameter.QUERY,
                        required=False,
                        default=20,
                    ),
                    OpenApiParameter(
                        name="offset",
                        type=OpenApiTypes.INT,
                        location=OpenApiParameter.QUERY,
                        required=False,
                        default=0,
                    ),
                ],
                responses={
                    200: WardrobeBatchResponseSerializer(many=True),
                    400: DetailResponseSerializer,
                    401: DetailResponseSerializer,
                },
            ),
            post=extend_schema(
                operation_id="wardrobe_batch_create",
                tags=["Wardrobe"],
                summary="외부 상품 여러 건 옷장 일괄 등록",
                description=(
                    "인앱 브라우저 등에서 수집한 items를 JSON으로 1~30건 전달합니다. "
                    "각 item은 image_link와 알고 있는 옷장 태그를 포함하며, "
                    "서버가 이미지를 S3에 저장하고 Qwen 태깅 큐에 등록합니다."
                ),
                request=WardrobeBatchCreateSerializer,
                responses={
                    202: WardrobeBatchCreateResponseSerializer,
                    400: DetailResponseSerializer,
                    401: DetailResponseSerializer,
                    503: DetailResponseSerializer,
                },
            ),
        )
        class DocumentedWardrobeBatchView(self.target_class):
            pass

        return DocumentedWardrobeBatchView


class WardrobeBatchDetailViewExtension(OpenApiViewExtension):
    target_class = "apps.wardrobe.views.WardrobeBatchDetailView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="wardrobe_batch_detail",
                tags=["Wardrobe"],
                summary="옷장 일괄 등록 상태 조회",
                description=(
                    "각 job의 PENDING/PROCESSING/DONE/FAILED 상태와 error_message를 반환합니다. "
                    "PENDING이 20분을 초과하면 FAILED(processing_timeout)로 종료합니다."
                ),
                responses={
                    200: WardrobeBatchResponseSerializer,
                    401: DetailResponseSerializer,
                    404: DetailResponseSerializer,
                },
            )
        )
        class DocumentedWardrobeBatchDetailView(self.target_class):
            pass

        return DocumentedWardrobeBatchDetailView

WARDROBE_UPLOAD_DESCRIPTION = """사진 1장을 접수해 옷장 아이템 등록을 **비동기로** 시작합니다.

1. 원본이 S3에 저장되고 처리 job이 생성됩니다 (`202 + job_id`).
2. GPU 이미지 프로세서가 아이템 분리(SAM 3)·캡셔닝(Gemini)을 수행합니다.
3. 프론트는 `GET /api/v1/wardrobe/uploads/{job_id}/`를 폴링해 완료를 확인합니다.
4. 완료 후 사용자가 태깅을 확인·수정하고 `PATCH /api/v1/wardrobe/items/{id}/`로
   확정(`confirmed=true`)해야 추천 검색 대상이 됩니다.

제한: jpeg/png/webp/heic, 15MB 이하."""

INTERNAL_TOKEN_PARAMETER = OpenApiParameter(
    name="X-Internal-Token",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description="이미지 프로세서 ↔ 메인 API 공유 시크릿 (WARDROBE_INTERNAL_TOKEN)",
)


class WardrobeUploadViewExtension(OpenApiViewExtension):
    target_class = "apps.wardrobe.views.WardrobeUploadView"

    def view_replacement(self):
        @extend_schema_view(
            post=extend_schema(
                operation_id="wardrobe_upload",
                tags=["Wardrobe"],
                summary="옷장 사진 업로드 (비동기 등록 시작)",
                description=WARDROBE_UPLOAD_DESCRIPTION,
                request=WardrobeUploadSerializer,
                responses={
                    202: inline_serializer(
                        name="WardrobeUploadResponse",
                        fields={
                            "job_id": drf_serializers.UUIDField(),
                            "status": drf_serializers.CharField(),
                        },
                    ),
                    400: DetailResponseSerializer,
                    401: DetailResponseSerializer,
                    503: OpenApiResponse(
                        response=DetailResponseSerializer,
                        description="S3 업로드 또는 처리 큐 적재 실패",
                    ),
                },
            )
        )
        class DocumentedWardrobeUploadView(self.target_class):
            pass

        return DocumentedWardrobeUploadView


class WardrobeUploadJobViewExtension(OpenApiViewExtension):
    target_class = "apps.wardrobe.views.WardrobeUploadJobView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="wardrobe_upload_job",
                tags=["Wardrobe"],
                summary="업로드 job 상태·결과 조회 (폴링)",
                description=(
                    "처리 상태(PENDING/PROCESSING/DONE/FAILED)를 반환합니다.\n\n"
                    "DONE이면 분리된 아이템 목록(presigned 이미지 URL 포함)이 "
                    "`items`에 담깁니다. PENDING이 20분을 초과하면 "
                    "FAILED(processing_timeout)로 종료합니다."
                ),
                responses={
                    200: WardrobeJobSerializer,
                    401: DetailResponseSerializer,
                    404: DetailResponseSerializer,
                },
            )
        )
        class DocumentedWardrobeUploadJobView(self.target_class):
            pass

        return DocumentedWardrobeUploadJobView


class WardrobeCallbackViewExtension(OpenApiViewExtension):
    target_class = "apps.wardrobe.views.WardrobeCallbackView"

    def view_replacement(self):
        @extend_schema_view(
            post=extend_schema(
                operation_id="wardrobe_internal_callback",
                tags=["Wardrobe"],
                summary="[내부] 이미지 프로세서 처리 결과 콜백",
                description=(
                    "이미지 프로세서 전용 내부 엔드포인트입니다 (프론트 사용 금지).\n\n"
                    "- 인증: `X-Internal-Token` 헤더 (JWT 아님)\n"
                    "- `processing`: GPU 워커가 작업을 가져간 상태를 기록합니다.\n"
                    "- 멱등: 이미 DONE/FAILED인 job은 재처리 없이 200을 반환합니다.\n"
                    "- 벡터(`image_vector`/`text_vector`)는 DB가 아닌 Qdrant로 적재됩니다."
                ),
                parameters=[INTERNAL_TOKEN_PARAMETER],
                auth=[],
                request=CallbackSerializer,
                responses={
                    200: OpenApiResponse(description="멱등 응답 (이미 처리된 job)"),
                    201: inline_serializer(
                        name="WardrobeCallbackResponse",
                        fields={
                            "job_id": drf_serializers.UUIDField(),
                            "status": drf_serializers.CharField(),
                            "num_items": drf_serializers.IntegerField(),
                        },
                    ),
                    403: DetailResponseSerializer,
                    404: DetailResponseSerializer,
                },
            )
        )
        class DocumentedWardrobeCallbackView(self.target_class):
            pass

        return DocumentedWardrobeCallbackView


class WardrobeItemListViewExtension(OpenApiViewExtension):
    target_class = "apps.wardrobe.views.WardrobeItemListView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="wardrobe_items",
                tags=["Wardrobe"],
                summary="내 옷장 아이템 목록",
                parameters=[
                    OpenApiParameter(
                        name="category_large",
                        type=OpenApiTypes.STR,
                        location=OpenApiParameter.QUERY,
                        required=False,
                        enum=wardrobe_taxonomy.CATEGORY_LARGE,
                        description="대분류 필터",
                    ),
                    OpenApiParameter(
                        name="confirmed",
                        type=OpenApiTypes.BOOL,
                        location=OpenApiParameter.QUERY,
                        required=False,
                        description="확정 여부 필터 (true|false)",
                    ),
                ],
                responses={
                    200: WardrobeItemSerializer(many=True),
                    401: DetailResponseSerializer,
                },
            )
        )
        class DocumentedWardrobeItemListView(self.target_class):
            pass

        return DocumentedWardrobeItemListView


class WardrobeItemDetailViewExtension(OpenApiViewExtension):
    target_class = "apps.wardrobe.views.WardrobeItemDetailView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="wardrobe_item_detail",
                tags=["Wardrobe"],
                summary="아이템 상세 조회",
                responses={
                    200: WardrobeItemSerializer,
                    401: DetailResponseSerializer,
                    404: DetailResponseSerializer,
                },
            ),
            patch=extend_schema(
                operation_id="wardrobe_item_update",
                tags=["Wardrobe"],
                summary="아이템 태깅 수정 + 확정",
                description=(
                    "자동 태깅 결과를 수정하고 `confirmed=true`로 확정합니다.\n"
                    "대분류-소분류 짝이 맞지 않으면 400을 반환합니다."
                ),
                request=WardrobeItemUpdateSerializer,
                responses={
                    200: WardrobeItemSerializer,
                    400: DetailResponseSerializer,
                    401: DetailResponseSerializer,
                    404: DetailResponseSerializer,
                },
            ),
            delete=extend_schema(
                operation_id="wardrobe_item_delete",
                tags=["Wardrobe"],
                summary="아이템 삭제",
                responses={
                    204: OpenApiResponse(description="삭제 완료 (벡터도 함께 제거)"),
                    401: DetailResponseSerializer,
                    404: DetailResponseSerializer,
                },
            ),
        )
        class DocumentedWardrobeItemDetailView(self.target_class):
            pass

        return DocumentedWardrobeItemDetailView


class WardrobeItemAddToClosetViewExtension(OpenApiViewExtension):
    target_class = "apps.wardrobe.views.WardrobeItemAddToClosetView"

    def view_replacement(self):
        @extend_schema_view(
            post=extend_schema(
                operation_id="wardrobe_item_add_to_closet",
                tags=["Wardrobe"],
                summary="아이템을 내 옷장에 추가",
                request=None,
                responses={
                    200: WardrobeItemSerializer,
                    401: DetailResponseSerializer,
                    404: DetailResponseSerializer,
                },
            )
        )
        class DocumentedWardrobeItemAddToClosetView(self.target_class):
            pass

        return DocumentedWardrobeItemAddToClosetView


# =============================================================================
# 캘린더 (다른 도메인과 분리된 Swagger 카테고리)
# =============================================================================

CALENDAR_TAG = "캘린더"

CALENDAR_ID_PARAMETER = OpenApiParameter(
    name="calendar_id",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.PATH,
    required=True,
    description="캘린더 등록 응답에서 받은 UUID",
    examples=[
        OpenApiExample(
            name="캘린더 UUID",
            value="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
    ],
)

CALENDAR_PHOTO_EXAMPLE = OpenApiExample(
    name="사진 업로드 캘린더",
    description=(
        "image에는 로컬 이미지 파일을 선택합니다. 배열 필드는 Swagger UI에서 "
        "항목을 추가해 하나씩 입력합니다."
    ),
    value={
        "image": "(binary)",
        "date": "2026-08-20",
        "wardrobe_item_ids": [],
        "schedule": "성수동 저녁 약속",
        "tpo": ["데이트", "모임"],
        "hashtags": ["여름", "캐주얼"],
    },
    media_type="multipart/form-data",
    request_only=True,
)

CALENDAR_WARDROBE_EXAMPLE = OpenApiExample(
    name="기존 옷장 아이템 직접 선택",
    value={
        "date": "2026-08-21",
        "wardrobe_item_ids": [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        ],
        "schedule": "회사 출근 후 저녁 모임",
        "tpo": ["출근", "모임"],
        "hashtags": ["포멀", "여름"],
    },
    request_only=True,
)

CALENDAR_METADATA_EXAMPLES = [
    OpenApiExample(
        name="전체 메타데이터 수정",
        value={
            "schedule": "회사 회식으로 일정 변경",
            "tpo": ["출근", "회식"],
            "hashtags": ["포멀", "저녁"],
        },
        request_only=True,
    ),
    OpenApiExample(
        name="일정만 부분 수정",
        value={"schedule": "점심 약속"},
        request_only=True,
    ),
]

CALENDAR_START_DATE_PARAMETER = OpenApiParameter(
    name="start_date",
    type=OpenApiTypes.DATE,
    location=OpenApiParameter.QUERY,
    required=True,
    description="조회 시작일(포함)",
    examples=[OpenApiExample(name="2026년 8월 시작일", value="2026-08-01")],
)

CALENDAR_END_DATE_PARAMETER = OpenApiParameter(
    name="end_date",
    type=OpenApiTypes.DATE,
    location=OpenApiParameter.QUERY,
    required=True,
    description="조회 종료일(포함)",
    examples=[OpenApiExample(name="2026년 8월 종료일", value="2026-08-31")],
)

CALENDAR_DATE_PARAMETER = OpenApiParameter(
    name="date",
    type=OpenApiTypes.DATE,
    location=OpenApiParameter.QUERY,
    required=True,
    description="조회할 캘린더 날짜",
    examples=[OpenApiExample(name="조회 날짜", value="2026-08-20")],
)


class CalendarPhotoCreateViewExtension(OpenApiViewExtension):
    target_class = "apps.style_calendar.views.CalendarPhotoCreateView"

    def view_replacement(self):
        @extend_schema_view(
            post=extend_schema(
                operation_id="calendar_photo_create",
                tags=[CALENDAR_TAG],
                summary="사진 업로드 캘린더 등록",
                description=(
                    "사용자 사진을 캘린더와 옷장 S3 경로에 저장하고 기존 "
                    "`WardrobeUploadJob`을 `wardrobe:jobs`에 적재합니다. 응답 직후 "
                    "상태는 REGISTERED이며, 기존 worker와 wardrobe callback이 "
                    "생성한 옷장 아이템을 캘린더에 자동 연결합니다.\n\n"
                    "제한: 사용자별 같은 날짜 한 건, jpeg/png/webp/heic, 15MB 이하."
                ),
                request=CalendarPhotoCreateSerializer,
                examples=[CALENDAR_PHOTO_EXAMPLE],
                responses={
                    202: CalendarEntrySerializer,
                    400: DetailResponseSerializer,
                    401: DetailResponseSerializer,
                    409: OpenApiResponse(description="해당 날짜 캘린더가 이미 존재"),
                    503: DetailResponseSerializer,
                },
            )
        )
        class DocumentedCalendarPhotoCreateView(self.target_class):
            pass

        return DocumentedCalendarPhotoCreateView


class CalendarWardrobeCreateViewExtension(OpenApiViewExtension):
    target_class = "apps.style_calendar.views.CalendarWardrobeCreateView"

    def view_replacement(self):
        @extend_schema_view(
            post=extend_schema(
                operation_id="calendar_wardrobe_create",
                tags=[CALENDAR_TAG],
                summary="기존 옷장 아이템 직접 선택 캘린더 등록",
                description=(
                    "현재 사용자가 소유한 옷장 아이템 UUID를 한 개 이상 선택합니다. "
                    "사진 처리 없이 즉시 COMPLETED로 등록되며 첫 번째 아이템 "
                    "이미지가 대표 이미지가 됩니다."
                ),
                request=CalendarWardrobeCreateSerializer,
                examples=[CALENDAR_WARDROBE_EXAMPLE],
                responses={
                    201: CalendarEntrySerializer,
                    400: DetailResponseSerializer,
                    401: DetailResponseSerializer,
                    409: OpenApiResponse(description="해당 날짜 캘린더가 이미 존재"),
                    503: DetailResponseSerializer,
                },
            )
        )
        class DocumentedCalendarWardrobeCreateView(self.target_class):
            pass

        return DocumentedCalendarWardrobeCreateView


class CalendarEntryListViewExtension(OpenApiViewExtension):
    target_class = "apps.style_calendar.views.CalendarEntryListView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="calendar_list",
                tags=[CALENDAR_TAG],
                summary="기간별 내 캘린더 목록",
                description="start_date와 end_date를 모두 포함해 조회합니다.",
                parameters=[
                    CALENDAR_START_DATE_PARAMETER,
                    CALENDAR_END_DATE_PARAMETER,
                ],
                responses={
                    200: CalendarEntrySerializer(many=True),
                    400: DetailResponseSerializer,
                    401: DetailResponseSerializer,
                },
            )
        )
        class DocumentedCalendarEntryListView(self.target_class):
            pass

        return DocumentedCalendarEntryListView


class CalendarEntryByDateViewExtension(OpenApiViewExtension):
    target_class = "apps.style_calendar.views.CalendarEntryByDateView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="calendar_by_date",
                tags=[CALENDAR_TAG],
                summary="특정 날짜의 내 캘린더 조회",
                parameters=[CALENDAR_DATE_PARAMETER],
                responses={
                    200: CalendarEntrySerializer,
                    400: DetailResponseSerializer,
                    401: DetailResponseSerializer,
                    404: DetailResponseSerializer,
                },
            )
        )
        class DocumentedCalendarEntryByDateView(self.target_class):
            pass

        return DocumentedCalendarEntryByDateView


class CalendarEntryDetailViewExtension(OpenApiViewExtension):
    target_class = "apps.style_calendar.views.CalendarEntryDetailView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="calendar_detail",
                tags=[CALENDAR_TAG],
                summary="내 캘린더 상세 조회",
                parameters=[CALENDAR_ID_PARAMETER],
                responses={
                    200: CalendarEntrySerializer,
                    401: DetailResponseSerializer,
                    404: DetailResponseSerializer,
                },
            ),
            patch=extend_schema(
                operation_id="calendar_metadata_update",
                tags=[CALENDAR_TAG],
                summary="캘린더 일정·TPO·해시태그 부분 수정",
                parameters=[CALENDAR_ID_PARAMETER],
                request=CalendarMetadataUpdateSerializer,
                examples=CALENDAR_METADATA_EXAMPLES,
                responses={
                    200: CalendarEntrySerializer,
                    400: DetailResponseSerializer,
                    401: DetailResponseSerializer,
                    404: DetailResponseSerializer,
                },
            ),
            delete=extend_schema(
                operation_id="calendar_delete",
                tags=[CALENDAR_TAG],
                summary="완료·실패 캘린더 삭제",
                description=(
                    "COMPLETED 또는 FAILED 상태만 삭제할 수 있습니다. 캘린더 연결과 "
                    "캘린더 소유 S3 경로는 삭제하지만 실제 WardrobeItem은 유지합니다."
                ),
                parameters=[CALENDAR_ID_PARAMETER],
                responses={
                    204: OpenApiResponse(description="삭제 완료"),
                    401: DetailResponseSerializer,
                    404: DetailResponseSerializer,
                    409: OpenApiResponse(description="이미지 처리가 종료되지 않음"),
                },
            ),
        )
        class DocumentedCalendarEntryDetailView(self.target_class):
            pass

        return DocumentedCalendarEntryDetailView


class CalendarProcessingStatusViewExtension(OpenApiViewExtension):
    target_class = "apps.style_calendar.views.CalendarProcessingStatusView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="calendar_processing_status",
                tags=[CALENDAR_TAG],
                summary="캘린더 사진 처리 상태 조회",
                description=(
                    "사진 캘린더의 REGISTERED/COMPLETED/FAILED 상태와 해당 옷장 "
                    "job으로 생성·연결된 아이템 수를 반환합니다."
                ),
                parameters=[CALENDAR_ID_PARAMETER],
                responses={
                    200: CalendarProcessingStatusSerializer,
                    401: DetailResponseSerializer,
                    404: DetailResponseSerializer,
                },
            )
        )
        class DocumentedCalendarProcessingStatusView(self.target_class):
            pass

        return DocumentedCalendarProcessingStatusView


# ── 룩북 ─────────────────────────────────────────────────
LOOKBOOK_TAG = "룩북"

LOOKBOOK_ID_PARAMETER = OpenApiParameter(
    name="lookbook_id",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.PATH,
    required=True,
    description="룩북 UUID",
    examples=[
        OpenApiExample(
            name="룩북 UUID",
            value="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
    ],
)

LOOKBOOK_PHOTO_EXAMPLE = OpenApiExample(
    name="룩 사진 업로드",
    description=(
        "image에는 로컬 이미지 파일을 선택합니다. wardrobe_item_ids에 넣은 옷의 "
        "대분류(상의/하의 등)는 사진에서 다시 등록하지 않습니다. "
        "calendar_date를 넣으면 같은 룩이 그 날짜의 캘린더로도 기록됩니다."
    ),
    value={
        "image": "(binary)",
        "wardrobe_item_ids": ["11111111-1111-1111-1111-111111111111"],
        "schedule": "성수동 저녁 약속",
        "tpo": ["데이트"],
        "hashtags": ["데이트", "캐주얼"],
        "calendar_date": "2026-08-20",
        "overwrite_calendar": False,
    },
    media_type="multipart/form-data",
    request_only=True,
)

LOOKBOOK_WARDROBE_EXAMPLE = OpenApiExample(
    name="옷장 아이템만 골라 올리기",
    value={
        "wardrobe_item_ids": [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        ],
        "schedule": "팀 회의",
        "tpo": ["출근"],
        "hashtags": ["출근", "미니멀"],
        "calendar_date": None,
        "overwrite_calendar": False,
    },
    request_only=True,
)

LOOKBOOK_METADATA_EXAMPLES = [
    OpenApiExample(
        name="전체 메타데이터 수정",
        value={
            "schedule": "회식으로 일정 변경",
            "tpo": ["출근", "회식"],
            "hashtags": ["출근"],
        },
        request_only=True,
    ),
    OpenApiExample(
        name="해시태그만 부분 수정",
        value={"hashtags": ["여행"]},
        request_only=True,
    ),
]

LOOKBOOK_LIST_PARAMETERS = [
    OpenApiParameter(
        name="hashtag",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        description="이 해시태그를 포함한 룩만 조회",
        examples=[OpenApiExample(name="데이트", value="데이트")],
    ),
    OpenApiParameter(
        name="status",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        description="REGISTERED/PROCESSING/COMPLETED/FAILED",
    ),
    OpenApiParameter(
        name="limit",
        type=OpenApiTypes.INT,
        location=OpenApiParameter.QUERY,
        required=False,
        description="한 번에 받을 개수 (기본 20, 최대 100)",
    ),
    OpenApiParameter(
        name="offset",
        type=OpenApiTypes.INT,
        location=OpenApiParameter.QUERY,
        required=False,
        description="건너뛸 개수. 응답의 next_offset을 그대로 넣어 다음 쪽을 받는다.",
    ),
]

LOOKBOOK_LIST_RESPONSE = inline_serializer(
    name="LookbookListResponse",
    fields={
        "count": serializers.IntegerField(),
        "next_offset": serializers.IntegerField(allow_null=True),
        "results": LookbookPostSerializer(many=True),
    },
)


class LookbookPhotoCreateViewExtension(OpenApiViewExtension):
    target_class = "apps.lookbook.views.LookbookPhotoCreateView"

    def view_replacement(self):
        @extend_schema_view(
            post=extend_schema(
                operation_id="lookbook_photo_create",
                tags=[LOOKBOOK_TAG],
                summary="룩 사진 룩북 등록",
                description=(
                    "룩 사진을 룩북·옷장 S3 경로에 저장하고 기존 `WardrobeUploadJob`을 "
                    "`wardrobe:jobs`에 적재합니다. 응답 직후 상태는 REGISTERED이며, "
                    "worker와 wardrobe callback이 만든 옷장 아이템이 룩북에 자동 "
                    "연결됩니다.\n\n"
                    "**겹치는 부위는 건너뜁니다.** wardrobe_item_ids로 지정한 옷의 "
                    "대분류는 큐 페이로드의 exclude_categories로 전달되어 이미지 "
                    "프로세서가 열거 직후 제외합니다 — 같은 부위의 옷이 옷장에 "
                    "두 벌 생기지 않습니다.\n\n"
                    "calendar_date를 넣으면 같은 사진·같은 job을 공유하는 캘린더도 "
                    "함께 만듭니다. 그 날짜에 이미 캘린더가 있으면 409를 반환하며, "
                    "사용자 확인 후 overwrite_calendar=true로 재요청하면 교체합니다.\n\n"
                    "제한: jpeg/png/webp/heic, 15MB 이하."
                ),
                request=LookbookPhotoCreateSerializer,
                examples=[LOOKBOOK_PHOTO_EXAMPLE],
                responses={
                    202: LookbookPostSerializer,
                    400: DetailResponseSerializer,
                    401: DetailResponseSerializer,
                    409: OpenApiResponse(
                        description=(
                            "CALENDAR_DATE_CONFLICT(그날 캘린더가 이미 존재) 또는 "
                            "CALENDAR_BUSY(교체 대상 캘린더가 처리 중)"
                        )
                    ),
                    503: DetailResponseSerializer,
                },
            )
        )
        class DocumentedLookbookPhotoCreateView(self.target_class):
            pass

        return DocumentedLookbookPhotoCreateView


class LookbookWardrobeCreateViewExtension(OpenApiViewExtension):
    target_class = "apps.lookbook.views.LookbookWardrobeCreateView"

    def view_replacement(self):
        @extend_schema_view(
            post=extend_schema(
                operation_id="lookbook_wardrobe_create",
                tags=[LOOKBOOK_TAG],
                summary="옷장 아이템 직접 선택 룩북 등록",
                description=(
                    "사진 없이 옷장 아이템만 골라 올립니다. 이미지 처리가 없어 즉시 "
                    "COMPLETED이며, 첫 번째 아이템 이미지가 표지가 됩니다."
                ),
                request=LookbookWardrobeCreateSerializer,
                examples=[LOOKBOOK_WARDROBE_EXAMPLE],
                responses={
                    201: LookbookPostSerializer,
                    400: DetailResponseSerializer,
                    401: DetailResponseSerializer,
                    409: OpenApiResponse(
                        description=(
                            "CALENDAR_DATE_CONFLICT(그날 캘린더가 이미 존재) 또는 "
                            "CALENDAR_BUSY(교체 대상 캘린더가 처리 중)"
                        )
                    ),
                    503: DetailResponseSerializer,
                },
            )
        )
        class DocumentedLookbookWardrobeCreateView(self.target_class):
            pass

        return DocumentedLookbookWardrobeCreateView


class LookbookListViewExtension(OpenApiViewExtension):
    target_class = "apps.lookbook.views.LookbookListView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="lookbook_list",
                tags=[LOOKBOOK_TAG],
                summary="내 룩북 목록",
                description=(
                    "최신순으로 반환합니다. 피드는 계속 자라므로 항상 limit이 "
                    "적용되며, next_offset이 null이면 마지막 쪽입니다."
                ),
                parameters=LOOKBOOK_LIST_PARAMETERS,
                responses={
                    200: LOOKBOOK_LIST_RESPONSE,
                    400: DetailResponseSerializer,
                    401: DetailResponseSerializer,
                },
            )
        )
        class DocumentedLookbookListView(self.target_class):
            pass

        return DocumentedLookbookListView


class LookbookDetailViewExtension(OpenApiViewExtension):
    target_class = "apps.lookbook.views.LookbookDetailView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="lookbook_detail",
                tags=[LOOKBOOK_TAG],
                summary="내 룩북 상세 조회",
                parameters=[LOOKBOOK_ID_PARAMETER],
                responses={
                    200: LookbookPostSerializer,
                    401: DetailResponseSerializer,
                    404: DetailResponseSerializer,
                },
            ),
            patch=extend_schema(
                operation_id="lookbook_metadata_update",
                tags=[LOOKBOOK_TAG],
                summary="룩북 일정·TPO·해시태그 부분 수정",
                description=(
                    "사진과 아이템 구성은 바꾸지 않습니다. 룩 자체를 바꾸려면 "
                    "삭제 후 다시 등록합니다."
                ),
                parameters=[LOOKBOOK_ID_PARAMETER],
                request=LookbookMetadataUpdateSerializer,
                examples=LOOKBOOK_METADATA_EXAMPLES,
                responses={
                    200: LookbookPostSerializer,
                    400: DetailResponseSerializer,
                    401: DetailResponseSerializer,
                    404: DetailResponseSerializer,
                },
            ),
            delete=extend_schema(
                operation_id="lookbook_delete",
                tags=[LOOKBOOK_TAG],
                summary="완료·실패 룩북 삭제",
                description=(
                    "COMPLETED 또는 FAILED 상태만 삭제할 수 있습니다. 룩북 소유 "
                    "S3 경로는 지우지만 WardrobeItem과 연결된 캘린더는 유지합니다."
                ),
                parameters=[LOOKBOOK_ID_PARAMETER],
                responses={
                    204: OpenApiResponse(description="삭제 완료"),
                    401: DetailResponseSerializer,
                    404: DetailResponseSerializer,
                    409: OpenApiResponse(description="이미지 처리가 종료되지 않음"),
                },
            ),
        )
        class DocumentedLookbookDetailView(self.target_class):
            pass

        return DocumentedLookbookDetailView


class LookbookProcessingStatusViewExtension(OpenApiViewExtension):
    target_class = "apps.lookbook.views.LookbookProcessingStatusView"

    def view_replacement(self):
        @extend_schema_view(
            get=extend_schema(
                operation_id="lookbook_processing_status",
                tags=[LOOKBOOK_TAG],
                summary="룩 사진 처리 상태 조회",
                description=(
                    "룩북의 REGISTERED/COMPLETED/FAILED 상태와 직접 선택(selected)· "
                    "사진 추출(extracted) 아이템 수, 건너뛴 대분류를 반환합니다."
                ),
                parameters=[LOOKBOOK_ID_PARAMETER],
                responses={
                    200: LookbookProcessingStatusSerializer,
                    401: DetailResponseSerializer,
                    404: DetailResponseSerializer,
                },
            )
        )
        class DocumentedLookbookProcessingStatusView(self.target_class):
            pass

        return DocumentedLookbookProcessingStatusView
