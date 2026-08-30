"""
소셜 로그인 OAuth 서비스 (naver / kakao / google 직접 구현).

흐름 (Authorization Code Grant):
  1. 프론트가 제공사 로그인 페이지에서 authorization code를 받는다.
  2. 프론트가 백엔드로 code(+ redirect_uri, state)를 전달한다.
  3. 백엔드가 code를 제공사 토큰 엔드포인트에서 access_token으로 교환한다.
  4. access_token으로 제공사 프로필 API를 호출해 사용자 정보를 얻는다.
  5. SocialAccount를 조회/생성하고 서비스 자체 JWT를 발급한다 (views에서 처리).

엔드포인트/파라미터 출처:
- 네이버: https://developers.naver.com/docs/login/api/
- 카카오: https://developers.kakao.com/docs/latest/ko/kakaologin/rest-api
- 구글:   https://developers.google.com/identity/protocols/oauth2/web-server
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import jwt
import requests
from django.conf import settings


class OAuthError(Exception):
    """제공사 통신/응답 오류. 뷰에서 401/502로 변환한다."""


@dataclass(frozen=True)
class SocialProfile:
    provider: str
    provider_user_id: str
    email: str
    nickname: str
    profile_image: str
    raw: Dict[str, Any]


# client_secret이 필수인 제공사 (카카오는 콘솔에서 활성화한 경우만 선택 사용)
_SECRET_REQUIRED = {"naver", "google"}


def _generate_apple_client_secret(config: Dict[str, Any]) -> str:
    """
    Apple Sign In용 client_secret JWT를 동적으로 생성한다.

    Apple은 정적 client_secret 대신 개발자 개인키(ES256)로 서명한 JWT를 요구한다.
    유효시간은 Apple 권장에 따라 5분으로 제한한다.
    """
    if not config.get("team_id"):
        raise OAuthError("APPLE_TEAM_ID가 설정되지 않았습니다 (.env 확인).")
    if not config.get("key_id"):
        raise OAuthError("APPLE_KEY_ID가 설정되지 않았습니다 (.env 확인).")
    if not config.get("private_key"):
        raise OAuthError("APPLE_PRIVATE_KEY가 설정되지 않았습니다 (.env 확인).")

    now = int(time.time())
    payload = {
        "iss": config["team_id"],
        "iat": now,
        "exp": now + 300,  # 5분 (Apple 권장 최대값)
        "aud": "https://appleid.apple.com",
        "sub": config["client_id"],
    }
    return jwt.encode(
        payload,
        config["private_key"],
        algorithm="ES256",
        headers={"kid": config["key_id"]},
    )


def _provider_config(provider: str) -> Dict[str, Any]:
    config = settings.OAUTH_PROVIDERS.get(provider)
    if not config:
        raise OAuthError(f"지원하지 않는 provider: {provider}")
    if not config.get("client_id"):
        raise OAuthError(f"{provider} OAuth client_id가 설정되지 않았습니다 (.env 확인).")
    if provider in _SECRET_REQUIRED and not config.get("client_secret"):
        raise OAuthError(f"{provider} OAuth client_secret이 설정되지 않았습니다 (.env 확인).")
    return config


def _post_token(url: str, data: Dict[str, str]) -> Dict[str, Any]:
    try:
        response = requests.post(
            url,
            data=data,
            headers={"Accept": "application/json"},
            timeout=settings.OAUTH_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise OAuthError(f"토큰 엔드포인트 요청 실패: {exc}") from exc
    payload = _json_or_error(response)
    if "error" in payload or "access_token" not in payload:
        raise OAuthError(f"토큰 교환 실패: {payload.get('error_description') or payload}")
    return payload


def _get_profile(url: str, access_token: str) -> Dict[str, Any]:
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=settings.OAUTH_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise OAuthError(f"프로필 요청 실패: {exc}") from exc
    return _json_or_error(response)


def _json_or_error(response: requests.Response) -> Dict[str, Any]:
    if response.status_code >= 400:
        raise OAuthError(f"제공사 응답 오류: status={response.status_code}, body={response.text[:300]}")
    try:
        return response.json()
    except ValueError as exc:
        raise OAuthError(f"JSON 파싱 실패: {response.text[:300]}") from exc


# ------------------------------------------------------------
# 토큰 교환
# ------------------------------------------------------------


def exchange_code(
    provider: str,
    code: str,
    redirect_uri: Optional[str] = None,
    state: Optional[str] = None,
) -> str:
    """authorization code → access_token."""
    config = _provider_config(provider)
    data = {
        "grant_type": "authorization_code",
        "client_id": config["client_id"],
        "code": code,
    }
    if provider == "naver":
        # 네이버는 redirect_uri 대신 state 검증을 사용한다.
        data["client_secret"] = config["client_secret"]
        if state:
            data["state"] = state
    elif provider == "kakao":
        # 카카오는 client_secret이 선택(콘솔에서 활성화한 경우만).
        if redirect_uri:
            data["redirect_uri"] = redirect_uri
        if config.get("client_secret"):
            data["client_secret"] = config["client_secret"]
    elif provider == "google":
        data["client_secret"] = config["client_secret"]
        if redirect_uri:
            data["redirect_uri"] = redirect_uri
    elif provider == "apple":
        # client_secret은 개인키로 서명한 JWT를 동적 생성한다.
        data["client_secret"] = _generate_apple_client_secret(config)
        if redirect_uri:
            data["redirect_uri"] = redirect_uri

    payload = _post_token(config["token_url"], data)

    # Apple은 access_token으로 프로필 API를 제공하지 않는다.
    # 대신 토큰 교환 응답에 포함된 id_token(JWT)으로 사용자 정보를 얻는다.
    if provider == "apple":
        if "id_token" not in payload:
            raise OAuthError(f"Apple 토큰 교환 실패: id_token 없음 {payload}")
        return payload["id_token"]

    return payload["access_token"]


# ------------------------------------------------------------
# 프로필 조회 → 공통 스키마 정규화
# ------------------------------------------------------------


def fetch_profile(
    provider: str,
    access_token: str,
    apple_user_name: Optional[str] = None,
) -> SocialProfile:
    config = _provider_config(provider)

    # Apple은 profile_url이 없고 id_token(JWT)에서 사용자 정보를 추출한다.
    if provider == "apple":
        return _fetch_apple_profile(config, id_token=access_token, user_name=apple_user_name)

    raw = _get_profile(config["profile_url"], access_token)

    if provider == "naver":
        # {"resultcode": "00", "response": {"id", "email", "nickname", "profile_image", ...}}
        body = raw.get("response") or {}
        if raw.get("resultcode") != "00" or not body.get("id"):
            raise OAuthError(f"네이버 프로필 응답 오류: {raw}")
        return SocialProfile(
            provider=provider,
            provider_user_id=str(body["id"]),
            email=body.get("email") or "",
            nickname=body.get("nickname") or "",
            profile_image=body.get("profile_image") or "",
            raw=raw,
        )

    if provider == "kakao":
        # {"id": 123, "kakao_account": {"email", "profile": {"nickname", "profile_image_url"}}}
        if not raw.get("id"):
            raise OAuthError(f"카카오 프로필 응답 오류: {raw}")
        account = raw.get("kakao_account") or {}
        profile = account.get("profile") or {}
        return SocialProfile(
            provider=provider,
            provider_user_id=str(raw["id"]),
            email=account.get("email") or "",
            nickname=profile.get("nickname") or "",
            profile_image=profile.get("profile_image_url") or "",
            raw=raw,
        )

    if provider == "google":
        # OIDC userinfo: {"sub", "email", "name", "picture"}
        if not raw.get("sub"):
            raise OAuthError(f"구글 프로필 응답 오류: {raw}")
        return SocialProfile(
            provider=provider,
            provider_user_id=str(raw["sub"]),
            email=raw.get("email") or "",
            nickname=raw.get("name") or "",
            profile_image=raw.get("picture") or "",
            raw=raw,
        )

    raise OAuthError(f"지원하지 않는 provider: {provider}")


def _fetch_apple_profile(
    config: Dict[str, Any],
    id_token: str,
    user_name: Optional[str] = None,
) -> SocialProfile:
    """
    Apple id_token(JWT)을 Apple 공개키로 검증하고 SocialProfile로 변환한다.

    Apple은 최초 로그인 시에만 프론트엔드에 사용자 이름을 전달한다.
    이후 로그인에서는 이름이 없으므로 user_name이 없으면 빈 문자열로 저장한다
    (accounts.py의 _refresh_profile이 빈 값을 덮어쓰지 않으므로 기존 닉네임 유지됨).
    """
    try:
        jwks_response = requests.get(
            "https://appleid.apple.com/auth/keys",
            timeout=settings.OAUTH_REQUEST_TIMEOUT,
        )
        jwks = _json_or_error(jwks_response)
    except requests.RequestException as exc:
        raise OAuthError(f"Apple 공개키 조회 실패: {exc}") from exc

    try:
        header = jwt.get_unverified_header(id_token)
        matched_key = next(
            (k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid")),
            None,
        )
        if not matched_key:
            raise OAuthError("Apple id_token의 kid와 일치하는 공개키가 없습니다.")

        from jwt.algorithms import RSAAlgorithm  # noqa: PLC0415

        public_key = RSAAlgorithm.from_jwk(matched_key)
        claims = jwt.decode(
            id_token,
            public_key,
            algorithms=["RS256"],
            audience=config["client_id"],
            issuer="https://appleid.apple.com",
        )
    except jwt.PyJWTError as exc:
        raise OAuthError(f"Apple id_token 검증 실패: {exc}") from exc

    sub = claims.get("sub")
    if not sub:
        raise OAuthError("Apple id_token에 sub 클레임이 없습니다.")

    return SocialProfile(
        provider="apple",
        provider_user_id=str(sub),
        email=claims.get("email") or "",
        nickname=user_name or "",
        profile_image="",  # Apple은 프로필 이미지를 제공하지 않는다.
        raw=claims,
    )


def authenticate(
    provider: str,
    code: str,
    redirect_uri: Optional[str] = None,
    state: Optional[str] = None,
    apple_user_name: Optional[str] = None,
) -> SocialProfile:
    """code 교환 + 프로필 조회를 한 번에."""
    access_token = exchange_code(provider, code, redirect_uri, state)
    return fetch_profile(provider, access_token, apple_user_name=apple_user_name)


# ------------------------------------------------------------
# token 방식 로그인 (네이티브 앱 SDK 전용: kakao / google / naver)
# ------------------------------------------------------------
#
# 네이티브 앱 SDK는 인가 코드를 앱에 노출하지 않고 access_token을 직접
# 반환하므로 code 방식이 불가능하다. token 방식의 핵심 보안 요건은
# "토큰이 우리 앱으로 발급되었는지" 검증하는 것이다:
#   - kakao:  /v1/user/access_token_info의 app_id 대조
#   - google: /oauth2/v3/tokeninfo의 aud(client_id) 대조
#   - naver:  ⚠️ 발급 앱을 확인할 공식 API가 없다 (introspection·id_token 미제공).
#             토큰 유효성 + /v1/nid/me 사용자 식별만 가능하며, 다른 네이버
#             앱에서 발급된 토큰을 구분할 수 없다. 이 한계를 팀이 인지하고
#             수용한 상태로 지원한다 (2026-07 결정). 네이버 id가 앱별로
#             다른 값인지 실증되면 기존 계정 탈취 위험은 해소된다.


def _verify_kakao_token(config: Dict[str, Any], access_token: str) -> None:
    """
    access_token이 '우리 앱'에서 발급된 것인지 검증한다.

    프론트가 보낸 토큰을 무검증으로 신뢰하면, 공격자가 다른 카카오 앱에서
    피해자에게 발급된 토큰으로 우리 서비스에 로그인할 수 있다.
    /v1/user/access_token_info의 app_id를 반드시 대조한다.
    """
    expected_app_id = config.get("app_id")
    if not expected_app_id:
        raise OAuthError("KAKAO_APP_ID가 설정되지 않았습니다 (.env 확인).")

    info = _get_profile(config["token_info_url"], access_token)
    if str(info.get("app_id")) != str(expected_app_id):
        raise OAuthError(
            f"access_token의 app_id가 서비스 앱과 일치하지 않습니다: {info.get('app_id')}"
        )


def _verify_google_token(config: Dict[str, Any], access_token: str) -> None:
    """
    access_token의 aud(발급 대상 client_id)가 우리 앱인지 검증한다.

    tokeninfo는 Bearer 헤더가 아닌 access_token 쿼리 파라미터를 받는다.
    유효하지 않은 토큰은 400을 반환하므로 _json_or_error에서 걸러진다.
    """
    try:
        response = requests.get(
            config["token_info_url"],
            params={"access_token": access_token},
            timeout=settings.OAUTH_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise OAuthError(f"구글 tokeninfo 요청 실패: {exc}") from exc

    info = _json_or_error(response)
    allowed = config.get("allowed_client_ids") or [config["client_id"]]
    if info.get("aud") not in allowed:
        raise OAuthError(
            f"access_token의 aud가 서비스 앱과 일치하지 않습니다: {info.get('aud')}"
        )


def _verify_naver_token(config: Dict[str, Any], access_token: str) -> None:
    """
    네이버는 발급 앱 검증이 불가능하다 — 의도적으로 아무것도 하지 않는다.

    네이버는 introspection API와 OIDC id_token을 제공하지 않아 토큰이
    우리 앱으로 발급됐는지 확인할 수 없다. 토큰 유효성 검증은 이후
    fetch_profile의 /v1/nid/me 호출이 겸한다(무효 토큰이면 401 → OAuthError).
    다른 네이버 앱에서 발급된 토큰을 구분하지 못하는 한계를 수용한 결정임.
    """


_TOKEN_VERIFIERS = {
    "kakao": _verify_kakao_token,
    "google": _verify_google_token,
    "naver": _verify_naver_token,
}


def authenticate_with_token(provider: str, access_token: str) -> SocialProfile:
    """
    프론트(네이티브 앱 SDK)가 전달한 제공사 access_token으로 인증한다.

    앱 소유권 검증(발급 대상 앱 대조) 후 프로필을 조회한다.
    """
    verifier = _TOKEN_VERIFIERS.get(provider)
    if verifier is None:
        raise OAuthError(f"{provider}는 access_token 방식 로그인을 지원하지 않습니다.")

    config = _provider_config(provider)
    verifier(config, access_token)
    return fetch_profile(provider, access_token)
