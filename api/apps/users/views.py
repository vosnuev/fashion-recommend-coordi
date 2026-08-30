import logging

from django.conf import settings
from django.contrib.auth.models import update_last_login
from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.chat.services import identity as chat_identity
from apps.users.models import BodyMeasurement, BodyPhotoTransaction, SocialAccount
from apps.users.services import profile_image as profile_image_service
from apps.users.serializers import (
    BodyBasicInputSerializer,
    BodyDetailInputSerializer,
    BodyEstimateInputSerializer,
    BodyEstimationResultSerializer,
    BodyMeasurementSerializer,
    BodyPhotoUploadSerializer,
    BudgetSerializer,
    EmailLoginSerializer,
    EmailSignupSerializer,
    EmailVerificationResendSerializer,
    EmailVerificationSerializer,
    PreferenceCategorySerializer,
    PursuitPayloadInputSerializer,
    PursuitPayloadResponseSerializer,
    SocialLoginSerializer,
    UserSerializer,
)
from apps.users.services import (
    accounts,
    body_inference,
    email_verification,
    oauth,
    pursuit,
    withdrawal,
)

logger = logging.getLogger(__name__)


def _token_response(
    user,
    *,
    created: bool,
    request=None,
    is_new_user: bool | None = None,
) -> Response:
    """JWT 발급 공통 응답.

    is_new_user를 따로 넘기면 HTTP 상태(created)와 분리해서 내려보낸다. 이메일
    로그인은 계정 생성 시점이 회원가입이라 항상 200이지만, '가입 후 첫 로그인'이면
    앱이 온보딩으로 분기해야 해서 두 값이 갈린다.
    """
    refresh = RefreshToken.for_user(user)
    update_last_login(None, user)
    guest_claim = None
    guest_token = (
        request.COOKIES.get(settings.CHAT_GUEST_COOKIE_NAME, "")
        if request is not None
        else ""
    )
    if guest_token:
        try:
            guest_claim = chat_identity.claim_guest_identity(user, guest_token)
        except chat_identity.ChatIdentityError as exc:
            # 게스트 토큰 문제로 정상적인 회원 로그인을 막지 않는다.
            logger.info("게스트 채팅 이전 생략: code=%s", exc.code)

    response = Response(
        {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": UserSerializer(user).data,
            "is_new_user": created if is_new_user is None else is_new_user,
            "guest_chat_claim": guest_claim.__dict__ if guest_claim else None,
        },
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )
    if guest_claim is not None:
        response.delete_cookie(
            settings.CHAT_GUEST_COOKIE_NAME,
            path="/api/v1/",
            samesite=settings.CHAT_GUEST_COOKIE_SAMESITE,
        )
    return response


class EmailSignupView(APIView):
    """POST /api/v1/auth/signup/ — 이메일·비밀번호 계정 생성 및 JWT 발급."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request):
        serializer = EmailSignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            user = serializer.save()
            retry_after = email_verification.issue_code(user)
        return Response(
            {
                "email": user.email,
                "verification_required": True,
                "retry_after": retry_after,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class EmailVerificationView(APIView):
    """POST /api/v1/auth/email/verify/ — 인증 코드 확인 후 계정 활성화.

    **토큰은 발급하지 않는다.** 코드 검증만으로 세션을 열어 주면 인증 상태를
    되짚는 실수 하나가 곧 비밀번호 없는 로그인이 된다. 소유 확인은 계정 활성화까지만
    하고, 세션은 비밀번호를 아는 사람만 로그인 API로 열게 한다.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request):
        serializer = EmailVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = email_verification.verify_code(**serializer.validated_data)
        return Response(
            {"email": user.email, "verified": True}, status=status.HTTP_200_OK
        )


class EmailVerificationResendView(APIView):
    """POST /api/v1/auth/email/resend/ — 만료 또는 미수신 인증 코드 재발송."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request):
        serializer = EmailVerificationResendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            retry_after = email_verification.resend_code(serializer.validated_data["email"])
        return Response({"retry_after": retry_after}, status=status.HTTP_200_OK)


class EmailLoginView(APIView):
    """POST /api/v1/auth/login/ — 이메일·비밀번호 확인 및 JWT 발급.

    응답의 is_new_user는 가입 후 첫 로그인일 때 true다 (앱의 온보딩 진입 분기용).
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request):
        serializer = EmailLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        # last_login은 _token_response의 update_last_login이 채우므로 그 전에 읽는다.
        # NULL이면 가입 후 첫 로그인 → 앱이 온보딩(권한→체형 측정→추구미)으로 보낸다.
        is_first_login = user.last_login is None
        return _token_response(
            user,
            created=False,
            request=request,
            is_new_user=is_first_login,
        )


class SocialLoginView(APIView):
    """
    POST /api/v1/auth/{provider}/login/

    body (code 방식): {"code": "...", "redirect_uri": "...", "state": "..."}
    body (token 방식, 카카오 네이티브 앱 SDK 전용): {"access_token": "..."}
    응답: {"access": "...", "refresh": "...", "user": {...}, "is_new_user": bool}
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []  # JWT 미보유 상태에서 호출

    def post(self, request, provider: str):
        if provider not in SocialAccount.Provider.values:
            return Response(
                {"detail": f"지원하지 않는 provider입니다: {provider}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = SocialLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # token 방식(카카오 네이티브 앱 SDK): 앱이 SDK로 받은 access_token을 전달.
        # code 방식: 웹 프론트가 받은 인가 코드를 전달 (기존 흐름).
        use_token_login = bool(data.get("access_token")) and not data.get("code")

        if not use_token_login:
            # 제공사별 필수 파라미터: 카카오/구글은 인가 요청과 동일한 redirect_uri를
            # 토큰 교환에 다시 보내야 하고, 네이버는 state가 필수다.
            if provider in ("kakao", "google", "apple") and not data.get("redirect_uri"):
                return Response(
                    {"detail": "redirect_uri가 필요합니다."}, status=status.HTTP_400_BAD_REQUEST
                )
            if provider == "naver" and not data.get("state"):
                return Response(
                    {"detail": "state가 필요합니다."}, status=status.HTTP_400_BAD_REQUEST
                )

        try:
            if use_token_login:
                profile = oauth.authenticate_with_token(
                    provider=provider,
                    access_token=data["access_token"],
                )
            else:
                profile = oauth.authenticate(
                    provider=provider,
                    code=data["code"],
                    redirect_uri=data.get("redirect_uri") or None,
                    state=data.get("state") or None,
                    apple_user_name=data.get("user_name") or None,
                )
        except oauth.OAuthError as exc:
            # 제공사 원본 응답에 내부 정보가 포함될 수 있어 로그에만 남긴다.
            logger.warning("소셜 로그인 실패 (%s): %s", provider, exc)
            return Response(
                {"detail": "소셜 로그인에 실패했습니다. 인가 코드 또는 토큰을 확인해주세요."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        user, created = accounts.get_or_create_user(profile)
        # 토큰 발급은 _token_response로 일원화됐다(이메일 로그인과 같은 응답 모양).
        # 오늘의 룩 선반영은 여기가 아니라 홈 API(GET /api/v1/home/)가 건다 —
        # 홈 요청에는 위경도가 실려 와 사용자 위치의 날씨로 만들 수 있다
        # (apps/home/views.py의 _daily_look_payload).
        # request는 같은 브라우저의 게스트 채팅을 로그인 회원에게 이전하는 데 쓴다.
        return _token_response(user, created=created, request=request)


class MeView(APIView):
    """GET/PATCH/DELETE /api/v1/users/me/ — 내 정보 조회·수정, 회원 탈퇴."""

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request):
        """회원 탈퇴. 계정과 딸린 데이터를 지운다 — 되돌릴 수 없다.

        본문 없이 204 로 답한다. 앱은 이 응답을 받은 뒤 토큰을 지우고 로그아웃 상태로 돌아간다.
        (지운 계정의 토큰은 사용자 조회에서 걸려 어차피 401 이 된다.)
        """
        withdrawal.withdraw(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _save_body_measurement(request, serializer_class, *, partial: bool) -> Response:
    """신체치수 upsert 공통 처리. 저장 후 전체 치수를 응답한다."""
    measurement, _ = BodyMeasurement.objects.get_or_create(user=request.user)
    serializer = serializer_class(measurement, data=request.data, partial=partial)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(BodyMeasurementSerializer(measurement).data)


class ProfileImageView(APIView):
    """POST/DELETE /api/v1/users/me/profile-image/ — 프로필 사진 올리기·지우기.

    소셜 사진(profile_image URL)은 건드리지 않는다 — 올린 사진을 지우면 그리로 되돌아간다.
    """

    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        upload = request.FILES.get("image")
        if upload is None:
            return Response(
                {"image": ["사진 파일이 필요합니다."]}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            key = profile_image_service.store(request.user.id, upload.read())
        except profile_image_service.ProfileImageInvalidError as error:
            return Response({"image": [str(error)]}, status=status.HTTP_400_BAD_REQUEST)
        except profile_image_service.ProfileImageConfigurationError:
            logger.exception("프로필 사진 저장소가 설정되지 않았습니다")
            return Response(
                {"detail": "지금은 사진을 올릴 수 없어요. 잠시 뒤 다시 시도해 주세요."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        previous = request.user.profile_image_key
        request.user.profile_image_key = key
        request.user.save(update_fields=["profile_image_key"])
        # 새 key 로 바꾼 뒤에 지운다 — 먼저 지우면 실패 시 사진이 없는 순간이 생긴다.
        profile_image_service.delete(previous)
        return Response(UserSerializer(request.user).data)

    def delete(self, request):
        previous = request.user.profile_image_key
        if previous:
            request.user.profile_image_key = ""
            request.user.save(update_fields=["profile_image_key"])
            profile_image_service.delete(previous)
        return Response(UserSerializer(request.user).data)


class BodyMeasurementView(APIView):
    """GET /api/v1/users/me/body/ — 내 신체치수 조회 (미입력 필드는 null)."""

    def get(self, request):
        measurement = BodyMeasurement.objects.filter(user=request.user).first()
        # 아직 입력 전이면 모든 필드가 null인 빈 치수를 반환한다 (404 대신).
        return Response(BodyMeasurementSerializer(measurement or BodyMeasurement()).data)


class BodyBasicView(APIView):
    """PUT /api/v1/users/me/body/basic/ — 성별·키·몸무게 입력 (셋 다 필수)."""

    def put(self, request):
        return _save_body_measurement(request, BodyBasicInputSerializer, partial=False)


class BodyDetailView(APIView):
    """PATCH /api/v1/users/me/body/detail/ — 상세 치수·체형 지표 입력 (전부 선택)."""

    def patch(self, request):
        return _save_body_measurement(request, BodyDetailInputSerializer, partial=True)


class BodyEstimateView(APIView):
    """POST /api/v1/users/me/body/estimate/ — 사진 없이 상세 신체치수 추정.

    성별·키·몸무게만으로 새 11개 항목을 추정해 저장하고 결과를 반환한다. 세 값을 본문에 담지 않으면
    이미 저장된 기본 신체치수를 사용한다.

    추론이 수십 ms로 끝나므로 동기 처리한다(사진 경로는 VLM 호출이 수 초 걸려
    비동기). 응답의 결과 형식은 사진 경로 조회와 동일하다.
    """

    def post(self, request):
        serializer = BodyEstimateInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            measurement = body_inference.estimate_from_basic_info(
                request.user, **serializer.validated_data
            )
        except body_inference.BodyEstimationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        result = body_inference.build_result(
            status=BodyPhotoTransaction.Status.SUCCEEDED,
            source=body_inference.SOURCE_BASIC,
            measurement=measurement,
        )
        return Response(BodyEstimationResultSerializer(result).data)


IN_PROGRESS_DETAIL = "이미 진행 중인 신체 측정이 있습니다. 완료 후 다시 시도해주세요."


def _read_upload(image) -> bytes:
    """업로드 파일 전체를 바이트로 읽는다 (읽기 위치를 처음으로 되돌린 뒤)."""
    if hasattr(image, "seek"):
        image.seek(0)
    return image.read()


class BodyPhotoView(APIView):
    """POST /api/v1/users/me/body/photos/ — 정면/측면 사진 접수 → 측정 트랜잭션 시작.

    사진은 디스크·DB에 저장하지 않고 추론에만 쓴다. 접수 시 측정 트랜잭션을
    '진행중'으로 만들고 202와 함께 transaction_id를 반환하며, 백그라운드에서
    KNN·VLM 추론이 끝나면 상세 수치를 갱신하고 '성공'으로 마친다.
    진행중 트랜잭션이 이미 있으면 400.

    결과는 GET /users/me/body/photos/{transaction_id}/ 로 폴링해서 받는다.
    """

    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        serializer = BodyPhotoUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # 추론에 필요한 기본 정보를 접수 시점에 확정한다. 사진만 받고 나중에
        # 백그라운드에서 알아채면 사용자는 실패 사유를 폴링으로만 알게 된다.
        measurement, _ = BodyMeasurement.objects.get_or_create(user=request.user)
        try:
            gender, height, weight = body_inference.resolve_basic_info(
                measurement,
                gender=data.get("gender"),
                height=data.get("height"),
                weight=data.get("weight"),
            )
        except body_inference.BodyEstimationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        body_inference.expire_stale_transactions(request.user)
        if BodyPhotoTransaction.objects.filter(
            user=request.user, status=BodyPhotoTransaction.Status.IN_PROGRESS
        ).exists():
            return Response(
                {"detail": IN_PROGRESS_DETAIL}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            tx = BodyPhotoTransaction.objects.create(user=request.user)
        except IntegrityError:
            # 동시 요청이 부분 유니크 제약(사용자당 진행중 1건)에 걸린 경우
            return Response(
                {"detail": IN_PROGRESS_DETAIL}, status=status.HTTP_400_BAD_REQUEST
            )

        # 여기서 예외가 나면 트랜잭션이 '진행중'으로 남아 사용자당 1건 제약 때문에
        # 그 사용자는 다시는 사진을 못 올린다. 반드시 실패로 닫고 넘긴다.
        try:
            # 업로드 파일은 응답 후 사라지므로 지금 바이트로 읽어 스레드에 넘긴다.
            # ImageField 검증이 파일을 이미 읽었으므로 처음으로 되감고 읽는다.
            front_bytes = _read_upload(data["front_image"])
            side_bytes = _read_upload(data["side_image"])

            body_inference.start_measurement(
                tx.pk,
                gender=gender,
                height=height,
                weight=weight,
                front_image=front_bytes,
                side_image=side_bytes,
            )
        except Exception as exc:
            logger.exception("사진 측정 시작 실패 (tx=%s)", tx.pk)
            error_message = str(exc).strip() or "사진 측정을 시작하지 못했습니다."
            BodyPhotoTransaction.objects.filter(pk=tx.pk).update(
                status=BodyPhotoTransaction.Status.FAILED,
                error_message=f"측정을 시작하지 못했습니다: {error_message}"[:500],
            )
            return Response(
                {"detail": "사진을 처리하지 못했습니다. 다시 시도해주세요."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "detail": "사진이 접수되었습니다. 신체 측정이 진행 중입니다.",
                "transaction_id": str(tx.pk),
                "status": tx.status,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class BodyPhotoTransactionView(APIView):
    """GET /api/v1/users/me/body/photos/{transaction_id}/ — 측정 트랜잭션 조회.

    프론트가 폴링으로 진행중 → 성공/실패 전환을 확인한다. 성공이면 추정된
    신체치수까지 함께 내려주므로 별도 조회 없이 화면을 그릴 수 있고, 응답의
    결과 형식은 무사진 추정 API와 동일하다.
    """

    def get(self, request, transaction_id):
        # 프로세스 재시작으로 스레드가 사라진 트랜잭션은 여기서 실패로 닫는다.
        # 그러지 않으면 프론트가 '진행중'만 무한히 폴링한다.
        body_inference.expire_stale_transactions(request.user)

        tx = BodyPhotoTransaction.objects.filter(
            pk=transaction_id, user=request.user
        ).first()
        if tx is None:
            return Response(
                {"detail": "해당 측정 트랜잭션을 찾을 수 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # 아직 진행중이거나 실패했으면 치수는 이전 값(또는 빈 값)이 내려간다.
        measurement = BodyMeasurement.objects.filter(user=request.user).first()
        result = body_inference.build_result(
            status=tx.status,
            source=body_inference.SOURCE_PHOTO,
            measurement=measurement,
            transaction_id=tx.pk,
            error_message=tx.error_message,
            error_code=tx.error_code,
        )
        return Response(BodyEstimationResultSerializer(result).data)


# =============================================================================
# 추구미 (Pursuit) — 옵션 마스터 + 사용자 선택
# =============================================================================


class PreferenceOptionsView(APIView):
    """GET /api/v1/preference-options/ — 11개 카테고리 + 옵션 마스터.

    인증 필요. 프론트가 화면 진입 시 옵션 목록을 받아 칩을 렌더링하는 용도.
    """

    def get(self, request):
        grouped = pursuit.get_options_grouped_by_category()
        # OrderedDict을 list[dict]로 변환 (Serializer와 호환)
        categories = [grouped[k] for k in grouped]
        return Response(
            {"categories": PreferenceCategorySerializer(categories, many=True).data}
        )


class PursuitView(APIView):
    """GET /api/v1/users/me/pursuit/ — 내 추구미 조회 (저장 없으면 빈 payload).
    PUT /api/v1/users/me/pursuit/ — 내 추구미 저장 (upsert, 전체 교체).
    """

    def get(self, request):
        payload = pursuit.get_pursuit(request.user)
        return Response(PursuitPayloadResponseSerializer(payload).data)

    def put(self, request):
        serializer = PursuitPayloadInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        obj = pursuit.upsert_pursuit(
            request.user,
            preferred=serializer.validated_data["preferred"],
            avoided=serializer.validated_data["avoided"],
        )
        # 저장된 결과 응답 (재조회와 동일 형식)
        return Response(
            PursuitPayloadResponseSerializer(obj.payload).data,
            status=status.HTTP_200_OK,
        )


class BudgetView(APIView):
    """GET/PUT /api/v1/users/me/budget/ — 카테고리별 상품 예산 조회/설정.

    Swagger 문서(operation_id·request/response 스키마·예시)는 다른 users 뷰와
    마찬가지로 api_docs/extensions.py의 BudgetViewExtension이 담당한다.
    """

    def get(self, request):
        return Response(BudgetSerializer(request.user).data)

    def put(self, request):
        serializer = BudgetSerializer(request.user, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
