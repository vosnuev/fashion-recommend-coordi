from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from apps.users.views import (
    BodyBasicView,
    BodyDetailView,
    BodyEstimateView,
    BodyMeasurementView,
    BodyPhotoTransactionView,
    BodyPhotoView,
    MeView,
    ProfileImageView,
    PreferenceOptionsView,
    PursuitView,
    SocialLoginView,
    EmailLoginView,
    EmailSignupView,
    EmailVerificationResendView,
    EmailVerificationView,
    BudgetView,
)

app_name = "users"

urlpatterns = [
    path("auth/signup/", EmailSignupView.as_view(), name="email-signup"),
    path("auth/login/", EmailLoginView.as_view(), name="email-login"),
    path("auth/email/verify/", EmailVerificationView.as_view(), name="email-verify"),
    path("auth/email/resend/", EmailVerificationResendView.as_view(), name="email-resend"),
    # 소셜 로그인: naver | kakao | google
    path("auth/<str:provider>/login/", SocialLoginView.as_view(), name="social-login"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("users/me/", MeView.as_view(), name="me"),
    path(
        "users/me/profile-image/",
        ProfileImageView.as_view(),
        name="profile-image",
    ),
    # 설정 페이지 — 신체치수
    path("users/me/body/", BodyMeasurementView.as_view(), name="body"),
    path("users/me/body/basic/", BodyBasicView.as_view(), name="body-basic"),
    path("users/me/body/detail/", BodyDetailView.as_view(), name="body-detail"),
    # 사진 없이 상세 치수·체형 지표 추정. 사진 경로와 결과 형식이 같다.
    path("users/me/body/estimate/", BodyEstimateView.as_view(), name="body-estimate"),
    path("users/me/body/photos/", BodyPhotoView.as_view(), name="body-photos"),
    path(
        "users/me/body/photos/<uuid:transaction_id>/",
        BodyPhotoTransactionView.as_view(),
        name="body-photo-transaction",
    ),
    # 추구미: 옵션 마스터 (11개 카테고리, 계절/스타일/색상/...) | 사용자 선택(preferred/avoided 2단 nested payloa)
    path("preference-options/", PreferenceOptionsView.as_view(), name="preference-options"),
    path("users/me/pursuit/", PursuitView.as_view(), name="pursuit"),
    path("users/me/budget/", BudgetView.as_view(), name="budget"),
]
