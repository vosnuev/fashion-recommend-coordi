"""users 앱 테스트.

OAuth 제공사 호출은 mock 처리한다 (외부 네트워크 의존 금지).
실행: python manage.py test apps.users
"""

from datetime import timedelta
from decimal import Decimal
import re
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.users.constants import category_keys
from apps.users.serializers import UserSerializer
from apps.users.services import profile_image
from apps.wardrobe.models import (
    SharedWardrobeMember,
    SharedWardrobeRoom,
    WardrobeItem,
)
from apps.users.models import (
    BodyMeasurement,
    BodyPhotoTransaction,
    SocialAccount,
    User,
)
from apps.users.services import body_inference
from apps.users.services.oauth import SocialProfile


def make_profile(provider: str = "kakao", uid: str = "12345") -> SocialProfile:
    return SocialProfile(
        provider=provider,
        provider_user_id=uid,
        email="test@example.com",
        nickname="테스터",
        profile_image="https://example.com/p.jpg",
        raw={"id": uid},
    )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailAuthTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.signup_url = reverse("users:email-signup")
        self.login_url = reverse("users:email-login")
        self.verify_url = reverse("users:email-verify")
        self.resend_url = reverse("users:email-resend")
        self.body = {"email": "member@example.com", "password": "Cozy-test-2026!"}

    def _signup_and_code(self):
        response = self.client.post(self.signup_url, self.body, format="json")
        code = re.search(r"\d{6}", mail.outbox[-1].body).group(0)
        return response, code

    def _verify(self):
        _, code = self._signup_and_code()
        return self.client.post(
            self.verify_url,
            {"email": self.body["email"], "code": code},
            format="json",
        )

    def test_signup_creates_inactive_user_and_sends_verification(self):
        response = self.client.post(self.signup_url, self.body, format="json")

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.data["verification_required"])
        self.assertEqual(len(mail.outbox), 1)
        user = User.objects.get(email=self.body["email"])
        self.assertTrue(user.check_password(self.body["password"]))
        self.assertFalse(user.is_active)

    def test_verification_activates_user_without_issuing_token(self):
        response = self._verify()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["verified"])
        self.assertNotIn("access", response.data)
        self.assertTrue(User.objects.get(email=self.body["email"]).is_active)

    def test_verified_email_cannot_be_verified_again(self):
        """인증이 끝난 계정은 임의의 코드로 다시 통과할 수 없어야 한다 (계정 탈취 차단)."""
        self._verify()

        response = self.client.post(
            self.verify_url,
            {"email": self.body["email"], "code": "000000"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access", response.data)

    def test_signup_rejects_duplicate_email(self):
        self.client.post(self.signup_url, self.body, format="json")
        response = self.client.post(self.signup_url, self.body, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.data)

    def test_signup_rejects_weak_password(self):
        response = self.client.post(
            self.signup_url,
            {"email": "weak@example.com", "password": "12345678"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data)

    def test_signup_rejects_password_similar_to_email(self):
        """이메일을 그대로 비밀번호로 쓰면 거절한다.

        앱 가입 화면이 "이메일과 비슷하지 않을 것"이라 안내하는데, 검증에 user를 넘기지
        않던 동안은 그대로 통과했다.
        """
        response = self.client.post(
            self.signup_url,
            {"email": "member@example.com", "password": "member@example.com"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data)
        self.assertFalse(User.objects.filter(email="member@example.com").exists())

    def test_first_login_after_signup_is_flagged_new(self):
        """앱은 is_new_user로 온보딩(권한→체형 측정→추구미) 진입을 분기한다."""
        self._verify()
        response = self.client.post(self.login_url, self.body, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertTrue(response.data["is_new_user"])

    def test_second_login_is_not_flagged_new(self):
        self._verify()
        self.client.post(self.login_url, self.body, format="json")
        response = self.client.post(self.login_url, self.body, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_new_user"])

    def test_login_rejects_invalid_credentials(self):
        self._verify()
        response = self.client.post(
            self.login_url,
            {**self.body, "password": "wrong-password"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_login_token_can_save_pursuit(self):
        self._verify()
        logged_in = self.client.post(self.login_url, self.body, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {logged_in.data['access']}")
        empty_selections = {key: [] for key in category_keys()}

        response = self.client.put(
            reverse("users:pursuit"),
            {"preferred": empty_selections, "avoided": empty_selections},
            format="json",
        )

        self.assertEqual(response.status_code, 200)

    def test_unverified_user_cannot_login(self):
        self.client.post(self.signup_url, self.body, format="json")
        response = self.client.post(self.login_url, self.body, format="json")
        self.assertEqual(response.status_code, 400)

    def test_resend_is_rate_limited(self):
        self.client.post(self.signup_url, self.body, format="json")
        response = self.client.post(self.resend_url, {"email": self.body["email"]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("retry_after", response.data)


class SocialLoginTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    REDIRECT_URI = "http://localhost:3000/oauth/callback"

    @patch("apps.users.views.oauth.authenticate")
    def test_first_login_creates_user_and_returns_jwt(self, mock_auth):
        mock_auth.return_value = make_profile()
        url = reverse("users:social-login", kwargs={"provider": "kakao"})

        response = self.client.post(
            url, {"code": "dummy-code", "redirect_uri": self.REDIRECT_URI}, format="json"
        )

        self.assertEqual(response.status_code, 201)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertTrue(response.data["is_new_user"])
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(SocialAccount.objects.count(), 1)

    @patch("apps.users.views.oauth.authenticate")
    def test_second_login_reuses_user(self, mock_auth):
        mock_auth.return_value = make_profile()
        url = reverse("users:social-login", kwargs={"provider": "kakao"})

        body = {"code": "c1", "redirect_uri": self.REDIRECT_URI}
        self.client.post(url, body, format="json")
        response = self.client.post(url, {**body, "code": "c2"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_new_user"])
        self.assertEqual(User.objects.count(), 1)

    def test_unknown_provider_returns_400(self):
        url = reverse("users:social-login", kwargs={"provider": "github"})
        response = self.client.post(url, {"code": "x"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_kakao_without_redirect_uri_returns_400(self):
        url = reverse("users:social-login", kwargs={"provider": "kakao"})
        response = self.client.post(url, {"code": "x"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_naver_without_state_returns_400(self):
        url = reverse("users:social-login", kwargs={"provider": "naver"})
        response = self.client.post(url, {"code": "x"}, format="json")
        self.assertEqual(response.status_code, 400)


class KakaoTokenLoginTests(TestCase):
    """token 방식 로그인 (카카오 네이티브 앱 SDK 전용)."""

    def setUp(self):
        self.client = APIClient()
        self.url = reverse("users:social-login", kwargs={"provider": "kakao"})

    @patch("apps.users.views.oauth.authenticate_with_token")
    def test_access_token_login_creates_user(self, mock_auth):
        mock_auth.return_value = make_profile()

        response = self.client.post(
            self.url, {"access_token": "kakao-sdk-token"}, format="json"
        )

        self.assertEqual(response.status_code, 201)
        self.assertIn("access", response.data)
        self.assertTrue(response.data["is_new_user"])
        mock_auth.assert_called_once_with(
            provider="kakao", access_token="kakao-sdk-token"
        )

    @patch("apps.users.views.oauth.authenticate_with_token")
    def test_invalid_token_returns_401(self, mock_auth):
        from apps.users.services.oauth import OAuthError  # noqa: PLC0415

        mock_auth.side_effect = OAuthError("app_id 불일치")
        response = self.client.post(self.url, {"access_token": "stolen"}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_missing_code_and_token_returns_400(self):
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 400)

    @patch("apps.users.views.oauth.authenticate_with_token")
    def test_token_login_not_supported_provider_returns_401(self, mock_auth):
        """apple 등 token 방식 미지원 제공사는 서비스 계층에서 OAuthError."""
        from apps.users.services.oauth import OAuthError  # noqa: PLC0415

        mock_auth.side_effect = OAuthError("미지원")
        url = reverse("users:social-login", kwargs={"provider": "apple"})
        response = self.client.post(url, {"access_token": "t"}, format="json")
        self.assertEqual(response.status_code, 401)


class KakaoTokenVerifyTests(TestCase):
    """oauth.authenticate_with_token의 app_id 검증 로직."""

    @patch("apps.users.services.oauth.fetch_profile")
    @patch("apps.users.services.oauth._get_profile")
    def test_app_id_mismatch_raises(self, mock_info, mock_fetch):
        from django.test import override_settings

        from apps.users.services import oauth

        mock_info.return_value = {"app_id": 999, "id": 12345}
        providers = {
            "kakao": {
                "client_id": "k",
                "app_id": "123",
                "token_info_url": "https://kapi.kakao.com/v1/user/access_token_info",
                "profile_url": "https://kapi.kakao.com/v2/user/me",
            }
        }
        with override_settings(OAUTH_PROVIDERS=providers):
            with self.assertRaises(oauth.OAuthError):
                oauth.authenticate_with_token("kakao", "token")
        mock_fetch.assert_not_called()

    @patch("apps.users.services.oauth.fetch_profile")
    @patch("apps.users.services.oauth._get_profile")
    def test_app_id_match_fetches_profile(self, mock_info, mock_fetch):
        from django.test import override_settings

        from apps.users.services import oauth

        mock_info.return_value = {"app_id": 123, "id": 12345}
        mock_fetch.return_value = make_profile()
        providers = {
            "kakao": {
                "client_id": "k",
                "app_id": "123",
                "token_info_url": "https://kapi.kakao.com/v1/user/access_token_info",
                "profile_url": "https://kapi.kakao.com/v2/user/me",
            }
        }
        with override_settings(OAUTH_PROVIDERS=providers):
            profile = oauth.authenticate_with_token("kakao", "token")
        self.assertEqual(profile.provider, "kakao")
        mock_fetch.assert_called_once()

    def test_unsupported_provider_raises(self):
        """apple은 token 방식을 지원하지 않는다."""
        from apps.users.services import oauth

        with self.assertRaises(oauth.OAuthError):
            oauth.authenticate_with_token("apple", "token")


class NaverTokenLoginTests(TestCase):
    """네이버 token 방식: 발급 앱 검증 없이 /v1/nid/me로 사용자 식별만 수행."""

    NAVER_PROVIDERS = {
        "naver": {
            "client_id": "naver-client-id",
            "client_secret": "naver-secret",
            "token_url": "https://nid.naver.com/oauth2.0/token",
            "profile_url": "https://openapi.naver.com/v1/nid/me",
        }
    }

    @patch("apps.users.services.oauth._get_profile")
    def test_valid_token_identifies_user(self, mock_get):
        """유효 토큰이면 앱 검증 없이 프로필 조회로 사용자를 식별한다."""
        from django.test import override_settings

        from apps.users.services import oauth

        mock_get.return_value = {
            "resultcode": "00",
            "message": "success",
            "response": {"id": "naver-uid-1", "email": "u@naver.com", "nickname": "유저"},
        }
        with override_settings(OAUTH_PROVIDERS=self.NAVER_PROVIDERS):
            profile = oauth.authenticate_with_token("naver", "sdk-token")

        self.assertEqual(profile.provider, "naver")
        self.assertEqual(profile.provider_user_id, "naver-uid-1")
        # 검증 단계가 별도 호출을 만들지 않는다 (프로필 조회 1회뿐).
        mock_get.assert_called_once()

    @patch("apps.users.services.oauth._get_profile")
    def test_invalid_token_raises(self, mock_get):
        """무효 토큰은 /v1/nid/me 단계에서 OAuthError."""
        from django.test import override_settings

        from apps.users.services import oauth

        mock_get.side_effect = oauth.OAuthError("제공사 응답 오류: status=401")
        with override_settings(OAUTH_PROVIDERS=self.NAVER_PROVIDERS):
            with self.assertRaises(oauth.OAuthError):
                oauth.authenticate_with_token("naver", "bad-token")


class GoogleTokenVerifyTests(TestCase):
    """oauth.authenticate_with_token의 구글 aud 검증 로직."""

    GOOGLE_PROVIDERS = {
        "google": {
            "client_id": "our-client-id.apps.googleusercontent.com",
            "client_secret": "secret",
            "token_info_url": "https://www.googleapis.com/oauth2/v3/tokeninfo",
            "profile_url": "https://www.googleapis.com/oauth2/v3/userinfo",
        }
    }

    def _mock_tokeninfo(self, mock_get, payload, status_code=200):
        response = mock_get.return_value
        response.status_code = status_code
        response.json.return_value = payload

    @patch("apps.users.services.oauth.fetch_profile")
    @patch("apps.users.services.oauth.requests.get")
    def test_aud_mismatch_raises(self, mock_get, mock_fetch):
        from django.test import override_settings

        from apps.users.services import oauth

        self._mock_tokeninfo(
            mock_get, {"aud": "other-app.apps.googleusercontent.com", "sub": "1"}
        )
        with override_settings(OAUTH_PROVIDERS=self.GOOGLE_PROVIDERS):
            with self.assertRaises(oauth.OAuthError):
                oauth.authenticate_with_token("google", "token")
        mock_fetch.assert_not_called()

    @patch("apps.users.services.oauth.fetch_profile")
    @patch("apps.users.services.oauth.requests.get")
    def test_aud_match_fetches_profile(self, mock_get, mock_fetch):
        from django.test import override_settings

        from apps.users.services import oauth

        self._mock_tokeninfo(
            mock_get,
            {"aud": "our-client-id.apps.googleusercontent.com", "sub": "1"},
        )
        mock_fetch.return_value = make_profile(provider="google", uid="1")
        with override_settings(OAUTH_PROVIDERS=self.GOOGLE_PROVIDERS):
            profile = oauth.authenticate_with_token("google", "token")
        self.assertEqual(profile.provider, "google")
        mock_fetch.assert_called_once()

    @patch("apps.users.services.oauth.fetch_profile")
    @patch("apps.users.services.oauth.requests.get")
    def test_invalid_token_raises(self, mock_get, mock_fetch):
        """만료/무효 토큰은 tokeninfo가 400을 반환한다."""
        from django.test import override_settings

        from apps.users.services import oauth

        self._mock_tokeninfo(mock_get, {"error": "invalid_token"}, status_code=400)
        mock_get.return_value.text = '{"error": "invalid_token"}'
        with override_settings(OAUTH_PROVIDERS=self.GOOGLE_PROVIDERS):
            with self.assertRaises(oauth.OAuthError):
                oauth.authenticate_with_token("google", "token")
        mock_fetch.assert_not_called()


class MeViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create(username="kakao_1", nickname="테스터")

    def test_me_requires_auth(self):
        response = self.client.get(reverse("users:me"))
        self.assertEqual(response.status_code, 401)

    def test_me_returns_profile(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(reverse("users:me"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["nickname"], "테스터")

    def test_me_patch_updates_nickname(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch(
            reverse("users:me"), {"nickname": "새닉네임"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.nickname, "새닉네임")


def make_image_file(name: str = "photo.jpg") -> "SimpleUploadedFile":
    """ImageField 검증을 통과하는 최소 크기 JPEG 파일."""
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (10, 10), "white").save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


class BodyMeasurementTests(TestCase):
    """설정 페이지 — 신체치수 입력 3종 + 조회."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create(username="kakao_1", nickname="테스터")
        self.client.force_authenticate(self.user)

    # ---- 인증 ----

    def test_requires_auth(self):
        client = APIClient()
        for method, url_name in [
            ("get", "users:body"),
            ("put", "users:body-basic"),
            ("patch", "users:body-detail"),
            ("post", "users:body-photos"),
        ]:
            response = getattr(client, method)(reverse(url_name))
            self.assertEqual(response.status_code, 401, url_name)

    # ---- 조회 ----

    def test_get_before_input_returns_nulls(self):
        response = self.client.get(reverse("users:body"))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["height"])
        self.assertIsNone(response.data["chest"])
        # gender도 미입력이면 빈 문자열이 아니라 null로 내려간다 (표현 통일).
        self.assertIsNone(response.data["gender"])

    def test_get_returns_saved_gender(self):
        BodyMeasurement.objects.create(
            user=self.user, gender=BodyMeasurement.Gender.FEMALE, height=160, weight=50
        )
        response = self.client.get(reverse("users:body"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["gender"], "female")

    def test_get_returns_canonical_body_type_label(self):
        BodyMeasurement.objects.create(
            user=self.user,
            gender="female",
            shoulder="38.5",
            chest="84.5",
            waist="68.0",
            hip="92.0",
        )

        response = self.client.get(reverse("users:body"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["body_type"], "inverted_triangle")
        self.assertEqual(response.data["body_type_label"], "역삼각형체형")

    # ---- 기본 수치 (성별·키·몸무게 — 셋 다 필수) ----

    BASIC_PAYLOAD = {"gender": "male", "height": "175.5", "weight": "70.0"}

    def test_basic_put_saves_gender_height_weight(self):
        response = self.client.put(
            reverse("users:body-basic"), self.BASIC_PAYLOAD, format="json"
        )
        self.assertEqual(response.status_code, 200)
        measurement = BodyMeasurement.objects.get(user=self.user)
        self.assertEqual(measurement.gender, "male")
        self.assertEqual(str(measurement.height), "175.5")
        self.assertEqual(str(measurement.weight), "70.0")
        # 저장 응답에도 gender가 포함된다.
        self.assertEqual(response.data["gender"], "male")

    def test_basic_put_requires_all_fields(self):
        for missing in ("gender", "height", "weight"):
            payload = {k: v for k, v in self.BASIC_PAYLOAD.items() if k != missing}
            response = self.client.put(
                reverse("users:body-basic"), payload, format="json"
            )
            self.assertEqual(response.status_code, 400, f"{missing} 누락")
            self.assertIn(missing, response.data)

    def test_basic_put_rejects_invalid_gender(self):
        for bad in ("", "other", "남성", None):
            response = self.client.put(
                reverse("users:body-basic"),
                {**self.BASIC_PAYLOAD, "gender": bad},
                format="json",
            )
            self.assertEqual(response.status_code, 400, f"gender={bad!r}")

    def test_basic_put_updates_gender(self):
        BodyMeasurement.objects.create(
            user=self.user, gender="male", height=175, weight=70
        )
        response = self.client.put(
            reverse("users:body-basic"),
            {"gender": "female", "height": "160.0", "weight": "50.0"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            BodyMeasurement.objects.get(user=self.user).gender, "female"
        )

    def test_basic_put_keeps_detail_values(self):
        BodyMeasurement.objects.create(user=self.user, chest=95)
        self.client.put(
            reverse("users:body-basic"), self.BASIC_PAYLOAD, format="json"
        )
        measurement = BodyMeasurement.objects.get(user=self.user)
        self.assertEqual(str(measurement.chest), "95.0")

    # ---- 상세 수치 ----

    def test_detail_patch_updates_only_sent_fields(self):
        BodyMeasurement.objects.create(user=self.user, chest=95, waist=80)
        response = self.client.patch(
            reverse("users:body-detail"), {"waist": "82.5"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        measurement = BodyMeasurement.objects.get(user=self.user)
        self.assertEqual(str(measurement.chest), "95.0")
        self.assertEqual(str(measurement.waist), "82.5")

    def test_detail_patch_null_clears_value(self):
        BodyMeasurement.objects.create(user=self.user, chest=95)
        response = self.client.patch(
            reverse("users:body-detail"), {"chest": None}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        measurement = BodyMeasurement.objects.get(user=self.user)
        self.assertIsNone(measurement.chest)

    def test_detail_patch_accepts_empty_body(self):
        """전부 선택 입력이므로 빈 바디도 허용된다."""
        response = self.client.patch(reverse("users:body-detail"), {}, format="json")
        self.assertEqual(response.status_code, 200)

    def test_detail_patch_rejects_out_of_range(self):
        response = self.client.patch(
            reverse("users:body-detail"), {"chest": "0.5"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_detail_patch_accepts_outside_reference_ratio_ranges(self):
        """SizeKorea 분포는 참고용이며 사용자가 수정한 양수 비율은 저장한다."""
        response = self.client.patch(
            reverse("users:body-detail"),
            {"thigh_calf_ratio": "1.200", "torso_leg_ratio": "0.250"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

    def test_detail_patch_accepts_redefined_ratio_ranges(self):
        response = self.client.patch(
            reverse("users:body-detail"),
            {"thigh_calf_ratio": "1.112", "torso_leg_ratio": "1.086"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

    # ---- 사진 접수 ----

    def _save_basic(self):
        """사진 추정도 성별·키·몸무게가 있어야 하므로 미리 저장해둔다."""
        return BodyMeasurement.objects.update_or_create(
            user=self.user,
            defaults={"gender": "male", "height": "175.5", "weight": "70.0"},
        )[0]

    def _upload_photos(self, **extra):
        payload = {
            "front_image": make_image_file("front.jpg"),
            "side_image": make_image_file("side.jpg"),
            **extra,
        }
        return self.client.post(
            reverse("users:body-photos"), payload, format="multipart"
        )

    @patch("apps.users.views.body_inference.start_measurement")
    def test_photo_upload_starts_transaction(self, mock_start):
        self._save_basic()

        response = self._upload_photos()

        self.assertEqual(response.status_code, 202)
        # 트랜잭션이 '진행중'으로 생성되고 응답에 id가 포함된다.
        tx = BodyPhotoTransaction.objects.get(user=self.user)
        self.assertEqual(tx.status, BodyPhotoTransaction.Status.IN_PROGRESS)
        self.assertEqual(response.data["transaction_id"], str(tx.pk))
        self.assertEqual(response.data["status"], "in_progress")

        # 업로드 파일은 응답 후 사라지므로 바이트로 읽어 넘겨야 한다.
        kwargs = mock_start.call_args.kwargs
        self.assertEqual(mock_start.call_args.args, (tx.pk,))
        self.assertEqual(kwargs["gender"], "male")
        self.assertIsInstance(kwargs["front_image"], bytes)
        self.assertTrue(kwargs["front_image"])
        self.assertIsInstance(kwargs["side_image"], bytes)

    @patch("apps.users.views.body_inference.start_measurement")
    def test_photo_upload_accepts_basic_info_in_request(self, mock_start):
        """본문으로 보낸 기본 정보가 저장값보다 우선한다."""
        self._save_basic()

        response = self._upload_photos(gender="female", height="160.0", weight="55.0")

        self.assertEqual(response.status_code, 202)
        kwargs = mock_start.call_args.kwargs
        self.assertEqual(kwargs["gender"], "female")
        self.assertEqual(str(kwargs["height"]), "160.0")
        self.assertEqual(str(kwargs["weight"]), "55.0")

    @patch("apps.users.views.body_inference.start_measurement")
    def test_photo_upload_without_basic_info_returns_400(self, mock_start):
        """기본 정보가 없으면 접수 단계에서 막는다 (폴링해야 알게 되면 안 된다)."""
        response = self._upload_photos()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(BodyPhotoTransaction.objects.count(), 0)
        mock_start.assert_not_called()

    @patch("apps.users.views.body_inference.start_measurement")
    def test_photo_upload_rejected_while_in_progress(self, mock_start):
        self._save_basic()
        BodyPhotoTransaction.objects.create(user=self.user)

        response = self._upload_photos()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(BodyPhotoTransaction.objects.count(), 1)
        mock_start.assert_not_called()

    @patch("apps.users.views.body_inference.start_measurement")
    def test_photo_upload_closes_transaction_when_start_fails(self, mock_start):
        """시작에 실패해도 '진행중'으로 남으면 안 된다 (남으면 영영 재업로드 불가)."""
        self._save_basic()
        mock_start.side_effect = RuntimeError("스레드 생성 실패")

        response = self._upload_photos()

        self.assertEqual(response.status_code, 500)
        tx = BodyPhotoTransaction.objects.get(user=self.user)
        self.assertEqual(tx.status, BodyPhotoTransaction.Status.FAILED)
        self.assertIn("시작하지 못했습니다", tx.error_message)

    @patch("apps.users.views.body_inference.start_measurement")
    def test_photo_upload_allowed_after_stale_transaction(self, mock_start):
        """프로세스 재시작으로 방치된 '진행중'은 새 업로드를 막지 않는다."""
        self._save_basic()
        stale = BodyPhotoTransaction.objects.create(user=self.user)
        BodyPhotoTransaction.objects.filter(pk=stale.pk).update(
            created_at=timezone.now()
            - timedelta(minutes=body_inference.STALE_TRANSACTION_TIMEOUT_MINUTES + 1)
        )

        response = self._upload_photos()

        self.assertEqual(response.status_code, 202)
        stale.refresh_from_db()
        self.assertEqual(stale.status, BodyPhotoTransaction.Status.FAILED)
        mock_start.assert_called_once()

    @patch("apps.users.views.body_inference.start_measurement")
    def test_photo_upload_allowed_after_finished_transaction(self, mock_start):
        self._save_basic()
        BodyPhotoTransaction.objects.create(
            user=self.user, status=BodyPhotoTransaction.Status.SUCCEEDED
        )

        response = self._upload_photos()

        self.assertEqual(response.status_code, 202)
        self.assertEqual(BodyPhotoTransaction.objects.count(), 2)

    def test_photo_upload_requires_both_images(self):
        self._save_basic()
        response = self.client.post(
            reverse("users:body-photos"),
            {"front_image": make_image_file("front.jpg")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        # 검증 실패 시 트랜잭션이 생성되지 않는다.
        self.assertEqual(BodyPhotoTransaction.objects.count(), 0)

    def test_photo_upload_rejects_non_image(self):
        self._save_basic()
        fake = SimpleUploadedFile("front.txt", b"not-an-image", content_type="text/plain")
        response = self.client.post(
            reverse("users:body-photos"),
            {"front_image": fake, "side_image": make_image_file("side.jpg")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)


ESTIMATED_MEASUREMENTS = {
    "chest": 98.3,
    "waist": 82.0,
    "hip": 94.9,
    "thigh": 55.2,
    "calf": 37.3,
    "arm": 32.0,
    "thigh_length": 40.1,
    "calf_length": 37.7,
    "torso_length": 45.0,
    "leg_length": 68.2,
    "shoulder": 40.2,
    "neck_length": 9.6,
    "thigh_calf_ratio": 1.064,
    "torso_leg_ratio": 0.660,
}


class BodyEstimateTests(TestCase):
    """POST /users/me/body/estimate — 사진 없이 상세 치수·체형 지표를 추정."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create(username="kakao_1", nickname="테스터")
        self.client.force_authenticate(self.user)
        self.url = reverse("users:body-estimate")

    def test_requires_auth(self):
        self.assertEqual(APIClient().post(self.url).status_code, 401)

    @patch("apps.users.services.body_inference.inference.estimate_from_basic")
    def test_estimate_with_request_body(self, mock_estimate):
        mock_estimate.return_value = dict(ESTIMATED_MEASUREMENTS)

        response = self.client.post(
            self.url, {"gender": "male", "height": "175.5", "weight": "70.0"}
        )

        self.assertEqual(response.status_code, 200)
        mock_estimate.assert_called_once_with("male", 175.5, 70.0)
        self.assertEqual(response.data["status"], "succeeded")
        self.assertEqual(response.data["source"], "basic_info")
        self.assertIsNone(response.data["transaction_id"])
        self.assertIsNone(response.data["error_message"])
        # 상세 항목이 전부 채워져 내려간다.
        measurement = response.data["measurement"]
        for field, value in ESTIMATED_MEASUREMENTS.items():
            self.assertEqual(float(measurement[field]), value, field)

    @patch("apps.users.services.body_inference.inference.estimate_from_basic")
    def test_estimate_falls_back_to_saved_basic_info(self, mock_estimate):
        mock_estimate.return_value = dict(ESTIMATED_MEASUREMENTS)
        BodyMeasurement.objects.create(
            user=self.user, gender="female", height="160.0", weight="55.0"
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        mock_estimate.assert_called_once_with("female", 160.0, 55.0)

    @patch("apps.users.services.body_inference.inference.estimate_from_basic")
    def test_estimate_persists_result(self, mock_estimate):
        mock_estimate.return_value = dict(ESTIMATED_MEASUREMENTS)

        self.client.post(
            self.url, {"gender": "male", "height": "175.5", "weight": "70.0"}
        )

        measurement = BodyMeasurement.objects.get(user=self.user)
        self.assertEqual(str(measurement.chest), "98.3")
        # 요청으로 받은 기본 정보도 함께 저장된다.
        self.assertEqual(measurement.gender, "male")
        self.assertEqual(str(measurement.height), "175.5")

    def test_estimate_without_any_basic_info_returns_400(self):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 400)
        self.assertIn("성별", response.data["detail"])

    def test_estimate_rejects_out_of_range_height(self):
        response = self.client.post(
            self.url, {"gender": "male", "height": "300.0", "weight": "70.0"}
        )
        self.assertEqual(response.status_code, 400)

    @patch("apps.users.services.body_inference.inference.estimate_from_basic")
    def test_estimate_overwrites_user_entered_detail(self, mock_estimate):
        """추정은 사용자가 직접 입력한 상세 값을 덮어쓴다 (합의된 동작)."""
        mock_estimate.return_value = dict(ESTIMATED_MEASUREMENTS)
        BodyMeasurement.objects.create(
            user=self.user, gender="male", height="175.5", weight="70.0", chest="90.0"
        )

        self.client.post(self.url)

        self.assertEqual(str(BodyMeasurement.objects.get(user=self.user).chest), "98.3")

    @patch("apps.users.services.body_inference.inference.estimate_from_basic")
    def test_estimate_returns_error_message_when_result_is_incomplete(self, mock_estimate):
        mock_estimate.return_value = {"chest": 98.3}

        response = self.client.post(
            self.url, {"gender": "male", "height": "175.5", "weight": "70.0"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("추정 결과에 필수 값이 없습니다", response.data["detail"])

    @patch("apps.users.services.body_inference.inference.estimate_from_basic")
    def test_estimate_returns_error_message_for_invalid_result(self, mock_estimate):
        invalid = dict(ESTIMATED_MEASUREMENTS, neck_length=float("nan"))
        mock_estimate.return_value = invalid

        response = self.client.post(
            self.url, {"gender": "male", "height": "175.5", "weight": "70.0"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("neck_length 추정값이 유효하지 않습니다", response.data["detail"])

    @patch("apps.users.services.body_inference.inference.estimate_from_basic")
    def test_estimate_returns_error_message_for_unexpected_inference_error(
        self, mock_estimate
    ):
        mock_estimate.side_effect = RuntimeError("모델 내부 오류")

        response = self.client.post(
            self.url, {"gender": "male", "height": "175.5", "weight": "70.0"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("추정하는 중 오류가 발생했습니다", response.data["detail"])


class BodyPhotoTransactionTests(TestCase):
    """사진 측정 트랜잭션 — 결과 조회 API + 완료/실패 처리."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create(username="kakao_1", nickname="테스터")
        self.client.force_authenticate(self.user)

    def _tx_url(self, tx_id) -> str:
        return reverse("users:body-photo-transaction", kwargs={"transaction_id": tx_id})

    def _complete_kwargs(self):
        return {
            "gender": "male",
            "height": Decimal("175.5"),
            "weight": Decimal("70.0"),
            "front_image": b"front-bytes",
            "side_image": b"side-bytes",
        }

    # ---- 결과 조회 ----

    def test_status_requires_auth(self):
        tx = BodyPhotoTransaction.objects.create(user=self.user)
        response = APIClient().get(self._tx_url(tx.pk))
        self.assertEqual(response.status_code, 401)

    def test_status_returns_transaction(self):
        tx = BodyPhotoTransaction.objects.create(user=self.user)
        response = self.client.get(self._tx_url(tx.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["transaction_id"], str(tx.pk))
        self.assertEqual(response.data["status"], "in_progress")
        self.assertEqual(response.data["source"], "photo")

    def test_status_response_shape_matches_estimate_api(self):
        """두 API의 결과 형식이 같아야 프론트가 한 파서로 처리할 수 있다."""
        BodyMeasurement.objects.create(
            user=self.user, gender="male", height="175.5", weight="70.0"
        )
        tx = BodyPhotoTransaction.objects.create(
            user=self.user, status=BodyPhotoTransaction.Status.SUCCEEDED
        )

        photo = self.client.get(self._tx_url(tx.pk))
        with patch(
            "apps.users.services.body_inference.inference.estimate_from_basic",
            return_value=dict(ESTIMATED_MEASUREMENTS),
        ):
            basic = self.client.post(reverse("users:body-estimate"))

        self.assertEqual(set(photo.data), set(basic.data))
        self.assertEqual(set(photo.data["measurement"]), set(basic.data["measurement"]))

    def test_failed_transaction_returns_error_message(self):
        tx = BodyPhotoTransaction.objects.create(
            user=self.user,
            status=BodyPhotoTransaction.Status.FAILED,
            error_message="VLM 호출 실패 (HTTP 429)",
        )
        response = self.client.get(self._tx_url(tx.pk))
        self.assertEqual(response.data["status"], "failed")
        self.assertEqual(response.data["error_message"], "VLM 호출 실패 (HTTP 429)")
        self.assertIsNone(response.data["error_code"])

    def test_photo_quality_failure_returns_machine_readable_code(self):
        tx = BodyPhotoTransaction.objects.create(
            user=self.user,
            status=BodyPhotoTransaction.Status.FAILED,
            error_message="사진 인식 실패: 양발이 모두 보이게 촬영해 주세요.",
            error_code="photo_quality_failed",
        )

        response = self.client.get(self._tx_url(tx.pk))

        self.assertEqual(response.data["error_code"], "photo_quality_failed")
        self.assertIn("사진 인식 실패", response.data["error_message"])

    def test_failed_transaction_without_error_message_gets_fallback(self):
        tx = BodyPhotoTransaction.objects.create(
            user=self.user,
            status=BodyPhotoTransaction.Status.FAILED,
        )

        response = self.client.get(self._tx_url(tx.pk))

        self.assertEqual(response.data["status"], "failed")
        self.assertEqual(
            response.data["error_message"],
            "신체 측정에 실패했습니다. 다시 시도해주세요.",
        )

    def test_status_unknown_id_returns_404(self):
        import uuid  # noqa: PLC0415

        response = self.client.get(self._tx_url(uuid.uuid4()))
        self.assertEqual(response.status_code, 404)

    def test_status_other_users_transaction_returns_404(self):
        other = User.objects.create(username="kakao_2")
        tx = BodyPhotoTransaction.objects.create(user=other)
        response = self.client.get(self._tx_url(tx.pk))
        self.assertEqual(response.status_code, 404)

    # ---- 완료 처리 (스레드 없이 로직만 직접 검증) ----

    @patch("apps.users.services.body_inference.inference.estimate_from_photos")
    def test_complete_saves_all_measurements(self, mock_estimate):
        mock_estimate.return_value = dict(ESTIMATED_MEASUREMENTS)
        BodyMeasurement.objects.create(
            user=self.user, gender="male", height="175.5", weight="70.0", chest="90.0"
        )
        tx = BodyPhotoTransaction.objects.create(user=self.user)

        body_inference.complete_measurement(tx.pk, **self._complete_kwargs())

        tx.refresh_from_db()
        self.assertEqual(tx.status, BodyPhotoTransaction.Status.SUCCEEDED)
        measurement = BodyMeasurement.objects.get(user=self.user)
        for field, value in ESTIMATED_MEASUREMENTS.items():
            self.assertEqual(float(getattr(measurement, field)), value, field)
        # 사진 바이트가 추론 함수까지 전달된다.
        self.assertEqual(mock_estimate.call_args.args[3], b"front-bytes")

    @patch("apps.users.services.body_inference.inference.estimate_from_photos")
    def test_complete_skips_already_finished_transaction(self, mock_estimate):
        mock_estimate.return_value = dict(ESTIMATED_MEASUREMENTS)
        tx = BodyPhotoTransaction.objects.create(
            user=self.user, status=BodyPhotoTransaction.Status.FAILED
        )

        body_inference.complete_measurement(tx.pk, **self._complete_kwargs())

        tx.refresh_from_db()
        self.assertEqual(tx.status, BodyPhotoTransaction.Status.FAILED)
        self.assertFalse(BodyMeasurement.objects.filter(user=self.user).exists())

    # 커넥션 정리 함수 2종은 테스트 트랜잭션의 커넥션을 닫아버리므로 mock 처리한다.
    @patch("apps.users.services.body_inference.connections")
    @patch("apps.users.services.body_inference.close_old_connections")
    @patch("apps.users.services.body_inference.complete_measurement")
    def test_run_marks_failed_with_reason(
        self, mock_complete, _mock_close_old, _mock_conns
    ):
        """완료 처리 중 예외가 나면 실패 상태와 사유가 함께 남는다."""
        mock_complete.side_effect = RuntimeError("VLM 타임아웃")
        tx = BodyPhotoTransaction.objects.create(user=self.user)

        body_inference._run_measurement(tx.pk, **self._complete_kwargs())

        tx.refresh_from_db()
        self.assertEqual(tx.status, BodyPhotoTransaction.Status.FAILED)
        self.assertEqual(tx.error_message, "VLM 타임아웃")
        self.assertEqual(tx.error_code, "")

    @patch("apps.users.services.body_inference.connections")
    @patch("apps.users.services.body_inference.close_old_connections")
    @patch("apps.users.services.body_inference.complete_measurement")
    def test_run_does_not_overwrite_terminal_transaction_after_error(
        self, mock_complete, _mock_close_old, _mock_conns
    ):
        tx = BodyPhotoTransaction.objects.create(user=self.user)

        def finish_then_raise(*_args, **_kwargs):
            BodyPhotoTransaction.objects.filter(pk=tx.pk).update(
                status=BodyPhotoTransaction.Status.SUCCEEDED,
                error_message="",
                error_code="",
            )
            raise RuntimeError("late worker error")

        mock_complete.side_effect = finish_then_raise

        body_inference._run_measurement(tx.pk, **self._complete_kwargs())

        tx.refresh_from_db()
        self.assertEqual(tx.status, BodyPhotoTransaction.Status.SUCCEEDED)
        self.assertEqual(tx.error_message, "")

    @patch("apps.users.services.body_inference.connections")
    @patch("apps.users.services.body_inference.close_old_connections")
    @patch("apps.users.services.body_inference.inference.estimate_from_photos")
    def test_photo_quality_failure_has_machine_readable_code(
        self, mock_estimate, _mock_close_old, _mock_conns
    ):
        mock_estimate.side_effect = body_inference.inference.PhotoValidationError(
            "feet_not_visible"
        )
        tx = BodyPhotoTransaction.objects.create(user=self.user)

        body_inference._run_measurement(tx.pk, **self._complete_kwargs())

        tx.refresh_from_db()
        self.assertEqual(tx.status, BodyPhotoTransaction.Status.FAILED)
        self.assertEqual(tx.error_code, "photo_quality_failed")
        self.assertIn("사진 인식 실패", tx.error_message)
        self.assertFalse(BodyMeasurement.objects.filter(user=self.user).exists())
class BudgetViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create(username="kakao_budget")
        self.url = reverse("users:budget")

    def test_budget_requires_auth(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)

    def test_budget_returns_empty_object_when_not_set(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["category_budgets"], {})
        self.assertEqual(
            response.data["effective_category_budgets"]["상의"], 50_000
        )
        self.assertEqual(
            response.data["effective_category_budgets"]["아우터"], 150_000
        )

    def test_budget_can_be_set(self):
        self.client.force_authenticate(self.user)
        response = self.client.put(
            self.url,
            {"category_budgets": {"상의": 100_000, "하의": 150_000}},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(
            self.user.category_budgets, {"상의": 100_000, "하의": 150_000}
        )

    def test_custom_budget_overrides_only_that_category(self):
        self.client.force_authenticate(self.user)
        response = self.client.put(
            self.url, {"category_budgets": {"상의": 120_000}}, format="json"
        )

        self.assertEqual(response.data["effective_category_budgets"]["상의"], 120_000)
        self.assertEqual(response.data["effective_category_budgets"]["하의"], 50_000)

    def test_budget_can_be_cleared(self):
        self.user.category_budgets = {"상의": 100_000}
        self.user.save(update_fields=["category_budgets"])
        self.client.force_authenticate(self.user)
        response = self.client.put(
            self.url, {"category_budgets": {}}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.category_budgets, {})
        self.assertEqual(
            response.data["effective_category_budgets"]["액세서리"], 50_000
        )

    def test_budget_rejects_non_ten_thousand_unit(self):
        self.client.force_authenticate(self.user)
        response = self.client.put(
            self.url, {"category_budgets": {"상의": 105_000}}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_budget_rejects_too_small_amount(self):
        self.client.force_authenticate(self.user)
        response = self.client.put(
            self.url, {"category_budgets": {"상의": 0}}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_budget_rejects_missing_field(self):
        self.client.force_authenticate(self.user)
        response = self.client.put(self.url, {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_budget_rejects_unknown_category(self):
        self.client.force_authenticate(self.user)
        response = self.client.put(
            self.url, {"category_budgets": {"잡화": 100_000}}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_budget_is_isolated_per_user(self):
        other_user = User.objects.create(
            username="kakao_other",
            category_budgets={"아우터": 500_000},
        )
        self.client.force_authenticate(self.user)
        self.client.put(
            self.url, {"category_budgets": {"상의": 100_000}}, format="json"
        )
        other_user.refresh_from_db()
        self.assertEqual(other_user.category_budgets, {"아우터": 500_000})


class WithdrawTests(TestCase):
    """회원 탈퇴 — DELETE /api/v1/users/me/.

    S3·Qdrant 는 외부 저장소라 mock 한다(테스트가 네트워크에 기대면 안 된다).
    파일 삭제는 커밋 뒤에 도는 콜백이라 captureOnCommitCallbacks 로 실행시켜 확인한다.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create(username="kakao_leaver")
        self.url = reverse("users:me")

    def test_requires_login(self):
        self.assertEqual(self.client.delete(self.url).status_code, 401)

    @patch("apps.users.services.withdrawal.wardrobe_storage.delete_objects")
    @patch("apps.users.services.withdrawal.vectors.delete_item")
    def test_deletes_account_and_owned_data(self, delete_item, delete_objects):
        item = WardrobeItem.objects.create(
            user=self.user,
            s3_key="wardrobe/leaver/shirt.png",
            item_name="셔츠",
            category_large="상의",
            confirmed=True,
            added_to_closet_at=timezone.now(),
        )
        self.client.force_authenticate(self.user)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(self.url)

        self.assertEqual(response.status_code, 204)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        # 옷은 FK CASCADE 로 함께 사라진다
        self.assertFalse(WardrobeItem.objects.filter(pk=item.pk).exists())
        # 사진과 벡터는 CASCADE 가 못 따라오므로 직접 지워야 한다
        delete_item.assert_called_once_with(item.pk)
        delete_objects.assert_called_once()
        self.assertIn("wardrobe/leaver/shirt.png", delete_objects.call_args.args[0])

    @patch("apps.users.services.withdrawal.wardrobe_storage.delete_objects")
    @patch("apps.users.services.withdrawal.vectors.delete_item")
    def test_owner_withdrawal_hands_room_to_remaining_member(self, _item, _objects):
        """방장이 탈퇴해도 남은 사람의 공유 옷장은 살아남는다."""
        room = SharedWardrobeRoom.objects.create(title="우리집 옷장")
        SharedWardrobeMember.objects.create(
            room=room, user=self.user, role=SharedWardrobeMember.Role.OWNER
        )
        stayer = User.objects.create(username="kakao_stayer")
        SharedWardrobeMember.objects.create(
            room=room, user=stayer, role=SharedWardrobeMember.Role.MEMBER
        )
        self.client.force_authenticate(self.user)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(self.url)

        self.assertEqual(response.status_code, 204)
        self.assertTrue(SharedWardrobeRoom.objects.filter(pk=room.pk).exists())
        remaining = SharedWardrobeMember.objects.get(room=room)
        self.assertEqual(remaining.user_id, stayer.pk)
        self.assertEqual(remaining.role, SharedWardrobeMember.Role.OWNER)

    @patch("apps.users.services.withdrawal.wardrobe_storage.delete_objects")
    @patch("apps.users.services.withdrawal.vectors.delete_item")
    def test_last_member_withdrawal_closes_room(self, _item, _objects):
        """혼자 쓰던 방은 탈퇴와 함께 사라진다 — 아무도 못 들어오는 방을 남기지 않는다."""
        room = SharedWardrobeRoom.objects.create(title="혼자 쓰는 옷장")
        SharedWardrobeMember.objects.create(
            room=room, user=self.user, role=SharedWardrobeMember.Role.OWNER
        )
        self.client.force_authenticate(self.user)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.delete(self.url)

        self.assertFalse(SharedWardrobeRoom.objects.filter(pk=room.pk).exists())


def _png_bytes(width: int = 1200, height: int = 900) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (120, 90, 60)).save(buffer, format="PNG")
    return buffer.getvalue()


class ProfileImageNormalizeTests(TestCase):
    """올라온 파일을 원형 아바타에 맞게 정규화하는 규칙."""

    def test_output_is_square_jpeg_at_max_edge(self):
        with Image.open(BytesIO(profile_image.normalize(_png_bytes(1200, 900)))) as out:
            self.assertEqual(out.format, "JPEG")
            # 가운데를 정사각으로 잘라야 원형 아바타에서 인물이 치우치지 않는다.
            self.assertEqual(out.size, (profile_image.MAX_EDGE, profile_image.MAX_EDGE))

    def test_alpha_input_becomes_rgb(self):
        buffer = BytesIO()
        Image.new("RGBA", (600, 600), (10, 20, 30, 128)).save(buffer, format="PNG")

        with Image.open(BytesIO(profile_image.normalize(buffer.getvalue()))) as out:
            self.assertEqual(out.mode, "RGB")

    def test_output_is_much_smaller_than_source(self):
        source = _png_bytes(3000, 2000)

        self.assertLess(len(profile_image.normalize(source)), len(source) / 5)

    def test_oversized_upload_is_rejected_before_decoding(self):
        with self.assertRaises(profile_image.ProfileImageInvalidError):
            profile_image.normalize(b"x" * (profile_image.MAX_UPLOAD_BYTES + 1))

    def test_non_image_is_rejected(self):
        with self.assertRaises(profile_image.ProfileImageInvalidError):
            profile_image.normalize(b"this is not an image")


class ProfileImagePriorityTests(TestCase):
    """직접 올린 사진이 소셜 사진보다 앞서고, 지우면 소셜로 되돌아간다."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="kakao_1", email="u@example.com", password="pw"
        )
        self.user.profile_image = "https://provider.example/photo.jpg"
        self.user.save(update_fields=["profile_image"])

    def test_social_url_is_used_when_no_upload(self):
        self.assertEqual(
            UserSerializer(self.user).data["profile_image"],
            "https://provider.example/photo.jpg",
        )

    def test_uploaded_key_wins_over_social_url(self):
        self.user.profile_image_key = "users/1/profile/abc.jpg"
        self.user.save(update_fields=["profile_image_key"])

        with patch.object(profile_image, "presigned_get", return_value="https://s3/signed"):
            self.assertEqual(
                UserSerializer(self.user).data["profile_image"], "https://s3/signed"
            )

    def test_uploaded_flag_distinguishes_upload_from_social_photo(self):
        """프론트가 '기본으로 되돌리기' 를 보여줄지 정하는 근거다."""
        self.assertFalse(UserSerializer(self.user).data["profile_image_uploaded"])

        self.user.profile_image_key = "users/1/profile/abc.jpg"
        self.user.save(update_fields=["profile_image_key"])
        with patch.object(profile_image, "presigned_get", return_value="https://s3/x"):
            self.assertTrue(UserSerializer(self.user).data["profile_image_uploaded"])

    def test_falls_back_to_social_url_when_storage_is_unconfigured(self):
        self.user.profile_image_key = "users/1/profile/abc.jpg"
        self.user.save(update_fields=["profile_image_key"])

        with patch.object(
            profile_image,
            "presigned_get",
            side_effect=profile_image.ProfileImageConfigurationError,
        ):
            self.assertEqual(
                UserSerializer(self.user).data["profile_image"],
                "https://provider.example/photo.jpg",
            )


class ProfileImageEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="u", email="u@example.com", password="pw"
        )
        self.client.force_authenticate(self.user)

    def test_upload_stores_key_and_returns_user(self):
        upload = SimpleUploadedFile("me.png", _png_bytes(), content_type="image/png")

        with patch.object(
            profile_image, "store", return_value="users/1/profile/new.jpg"
        ) as store, patch.object(
            profile_image, "presigned_get", return_value="https://s3/new"
        ):
            response = self.client.post(
                "/api/v1/users/me/profile-image/", {"image": upload}, format="multipart"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["profile_image"], "https://s3/new")
        self.user.refresh_from_db()
        self.assertEqual(self.user.profile_image_key, "users/1/profile/new.jpg")
        store.assert_called_once()

    def test_upload_replaces_and_removes_previous_object(self):
        self.user.profile_image_key = "users/1/profile/old.jpg"
        self.user.save(update_fields=["profile_image_key"])
        upload = SimpleUploadedFile("me.png", _png_bytes(), content_type="image/png")

        with patch.object(profile_image, "store", return_value="users/1/profile/new.jpg"), \
             patch.object(profile_image, "presigned_get", return_value="https://s3/new"), \
             patch.object(profile_image, "delete") as remove:
            self.client.post(
                "/api/v1/users/me/profile-image/", {"image": upload}, format="multipart"
            )

        remove.assert_called_once_with("users/1/profile/old.jpg")

    def test_upload_without_file_is_rejected(self):
        response = self.client.post(
            "/api/v1/users/me/profile-image/", {}, format="multipart"
        )

        self.assertEqual(response.status_code, 400)

    def test_delete_clears_key_and_removes_object(self):
        self.user.profile_image_key = "users/1/profile/old.jpg"
        self.user.save(update_fields=["profile_image_key"])

        with patch.object(profile_image, "delete") as remove:
            response = self.client.delete("/api/v1/users/me/profile-image/")

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.profile_image_key, "")
        remove.assert_called_once_with("users/1/profile/old.jpg")

    def test_anonymous_cannot_upload(self):
        response = APIClient().post(
            "/api/v1/users/me/profile-image/", {}, format="multipart"
        )

        self.assertIn(response.status_code, (401, 403))
