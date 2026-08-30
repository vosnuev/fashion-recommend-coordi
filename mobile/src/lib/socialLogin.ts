import { initializeKakaoSDK } from '@react-native-kakao/core';
import { login as kakaoNativeLogin } from '@react-native-kakao/user';
import NaverLogin from '@react-native-seoul/naver-login';
import * as AppleAuthentication from 'expo-apple-authentication';
import { Platform } from 'react-native';

import {
  AuthEndpoints,
  GOOGLE_IOS_CLIENT_ID,
  GOOGLE_WEB_CLIENT_ID,
  isGoogleConfigured,
  isNaverConfigured,
  KAKAO_NATIVE_APP_KEY,
  NAVER_CONSUMER_KEY,
  NAVER_CONSUMER_SECRET,
  NAVER_URL_SCHEME,
  type SocialProvider,
} from '@/constants/config';
import { api } from '@/lib/apiClient';
import { redirectUri, startWebLogin } from '@/lib/webOAuth';
import { authStore, type AuthUser } from '@/state/auth';

/**
 * 소셜 로그인.
 * 프론트는 인가정보(access_token)만 받아 백엔드로 넘기고, JWT 발급은 백엔드가 한다.
 *
 * 세 제공자 모두 네이티브 SDK 로 access_token 을 받아 백엔드로 보낸다
 * (카카오·네이버·구글 다 커스텀 스킴 redirect 가 막혀 브라우저 code 방식을 쓰지 않는다).
 * - 카카오: @react-native-kakao
 * - 네이버: @react-native-seoul/naver-login (import 안전 — 네이티브 모듈을 지연 참조)
 * - 구글:   @react-native-google-signin/google-signin
 *           ⚠️ import 시점에 네이티브 모듈을 강제 로드(getEnforcing)하므로, 미설정/미빌드 상태의
 *           앱이 죽지 않도록 "동적 import" 로 실제 호출 시점까지 로딩을 미룬다.
 * - 애플: iOS 네이티브(expo-apple-authentication)로 identity_token → 백엔드
 *
 * 백엔드 계약: POST /api/v1/auth/{provider}/login/  (api/apps/users/views.py)
 * ⚠️ 네이버/구글 access_token 경로는 백엔드 _TOKEN_LOGIN_PROVIDERS 확장이 선행돼야 동작한다(현재 kakao 전용).
 */

/** 백엔드 SocialLoginView 응답 형식 */
type SocialLoginResponse = {
  access: string;
  refresh: string;
  user: AuthUser;
  is_new_user: boolean;
};

/** 로그인 결과: 사용자가 취소하면 null, 성공하면 신규 유저 여부 */
export type SocialLoginResult = { isNewUser: boolean } | null;

/** 인가정보를 백엔드에 넘겨 우리 JWT 를 받고 세션을 시작한다. */
async function finishLogin(
  path: string,
  body: Record<string, string>,
): Promise<SocialLoginResult> {
  // 아직 우리 JWT 가 없으므로 auth: false (Authorization 헤더 생략)
  const data = await api.post<SocialLoginResponse>(path, body, { auth: false });
  await authStore.signIn(
    { access: data.access, refresh: data.refresh },
    data.user,
  );
  return { isNewUser: data.is_new_user };
}

function isCancel(e: unknown): boolean {
  const err = e as { code?: string; message?: string };
  return `${err?.code ?? ''} ${err?.message ?? ''}`.toLowerCase().includes('cancel');
}

/**
 * 웹이면 제공사 인가 페이지로 보내고 여기서 흐름을 끊는다.
 *
 * 이동하면 페이지가 통째로 바뀌므로 이 함수는 **돌아오지 않는다** — 이어지는 네이티브 SDK
 * 호출은 실행되지 않는다. 나머지 절반(코드 → JWT)은 /auth/callback 화면이 맡는다.
 * 키가 없어 URL 을 못 만들면 false 를 돌려 호출부가 안내 문구를 띄우게 한다.
 */
function handOffToWeb(provider: SocialProvider): boolean {
  if (Platform.OS !== 'web') return false;
  if (startWebLogin(provider)) return true;
  throw new Error('웹 로그인 설정이 아직 없습니다. (클라이언트 ID 대기 중)');
}

/**
 * 웹 콜백에서 받은 인가 코드로 로그인을 마친다.
 * `redirect_uri` 는 인가 요청 때와 **같은 값**이어야 해서 webOAuth 가 만든 것을 그대로 쓴다.
 */
export async function completeWebLogin(
  provider: SocialProvider,
  code: string,
  state: string,
): Promise<SocialLoginResult> {
  return finishLogin(AuthEndpoints.socialLogin(provider), {
    code,
    redirect_uri: redirectUri(),
    state,
  });
}

/**
 * 앱 시작 시 소셜 SDK 초기화 (_layout 에서 1회 호출).
 * 카카오는 항상 초기화하고, 네이버/구글은 키가 채워졌을 때만 초기화한다
 * — 미설정 상태에선 네이티브 모듈을 건드리지 않아 재빌드 전에도 앱이 안전하게 뜬다.
 */
export function initSocialSDKs(): void {
  if (Platform.OS === 'web') return;

  initializeKakaoSDK(KAKAO_NATIVE_APP_KEY);

  if (isNaverConfigured()) {
    NaverLogin.initialize({
      appName: 'cozy',
      consumerKey: NAVER_CONSUMER_KEY,
      consumerSecret: NAVER_CONSUMER_SECRET,
      serviceUrlSchemeIOS: NAVER_URL_SCHEME,
    });
  }

  if (isGoogleConfigured()) {
    // 동적 import: 여기서 처음 네이티브 모듈을 로드한다 (위 주석 참고).
    void import('@react-native-google-signin/google-signin')
      .then(({ GoogleSignin }) =>
        GoogleSignin.configure({
          webClientId: GOOGLE_WEB_CLIENT_ID || undefined,
          iosClientId: GOOGLE_IOS_CLIENT_ID || undefined,
        }),
      )
      .catch(() => {
        // 설정 실패는 조용히 무시 — 실제 안내는 로그인 시도 시점에 한다.
      });
  }
}

/**
 * 카카오 로그인 — 네이티브 SDK.
 * SDK 초기화(initializeKakaoSDK)는 앱 시작 시 _layout 에서 1회 수행된다.
 * ⚠️ 백엔드 필드명은 팀 백엔드와 맞춘다 (현재 access_token 으로 가정).
 */
export async function loginWithKakao(): Promise<SocialLoginResult> {
  if (handOffToWeb('kakao')) return null;
  try {
    const token = await kakaoNativeLogin();
    return finishLogin(AuthEndpoints.socialLogin('kakao'), {
      access_token: token.accessToken,
    });
  } catch (e) {
    if (isCancel(e)) return null; // 사용자가 카카오 로그인 취소
    throw e;
  }
}

/**
 * 네이버 로그인 — 네이티브 SDK.
 * SDK 초기화(NaverLogin.initialize)는 앱 시작 시 initSocialSDKs 에서 수행된다.
 */
export async function loginWithNaver(): Promise<SocialLoginResult> {
  if (handOffToWeb('naver')) return null;
  if (!isNaverConfigured()) {
    throw new Error('네이버 로그인 설정이 아직 없습니다. (키 대기 중)');
  }

  const res = await NaverLogin.login();
  if (!res.isSuccess || !res.successResponse) {
    if (res.failureResponse?.isCancel) return null; // 사용자가 취소
    throw new Error(res.failureResponse?.message ?? '네이버 로그인에 실패했습니다.');
  }

  return finishLogin(AuthEndpoints.socialLogin('naver'), {
    access_token: res.successResponse.accessToken,
  });
}

/**
 * 구글 로그인 — 네이티브 SDK(동적 import).
 * configure 는 앱 시작 시 initSocialSDKs 에서 수행된다.
 * getTokens() 의 accessToken 을 백엔드로 보낸다 (백엔드가 userinfo 로 프로필 조회).
 */
export async function loginWithGoogle(): Promise<SocialLoginResult> {
  if (handOffToWeb('google')) return null;
  if (!isGoogleConfigured()) {
    throw new Error('구글 로그인 설정이 아직 없습니다. (키 대기 중)');
  }

  const { GoogleSignin } = await import('@react-native-google-signin/google-signin');
  await GoogleSignin.hasPlayServices(); // iOS 에선 no-op, Android Play 서비스 확인
  const result = await GoogleSignin.signIn();
  if (result.type !== 'success') return null; // 사용자가 취소

  const { accessToken } = await GoogleSignin.getTokens();
  return finishLogin(AuthEndpoints.socialLogin('google'), {
    access_token: accessToken,
  });
}

/**
 * 애플 로그인 — iOS 네이티브(expo-apple-authentication).
 * code 대신 identity_token 을 백엔드로 넘긴다. 이름/이메일은 "최초 1회"만 오므로 그때 함께 전달.
 * ⚠️ 백엔드에 애플 엔드포인트(/api/v1/auth/apple/login/)가 아직 없음 → 추가되면 동작한다.
 */
export async function loginWithApple(): Promise<SocialLoginResult> {
  if (Platform.OS !== 'ios') {
    throw new Error('애플 로그인은 iOS에서만 지원됩니다.');
  }

  let credential: AppleAuthentication.AppleAuthenticationCredential;
  try {
    credential = await AppleAuthentication.signInAsync({
      requestedScopes: [
        AppleAuthentication.AppleAuthenticationScope.FULL_NAME,
        AppleAuthentication.AppleAuthenticationScope.EMAIL,
      ],
    });
  } catch (e) {
    if ((e as { code?: string })?.code === 'ERR_REQUEST_CANCELED') return null;
    throw e;
  }

  if (!credential.identityToken) {
    throw new Error('애플 인증 토큰을 받지 못했습니다.');
  }

  const fullName = credential.fullName
    ? [credential.fullName.familyName, credential.fullName.givenName]
        .filter(Boolean)
        .join(' ')
    : '';

  /* 백엔드 계약(SocialLoginSerializer)에 맞춘 이름으로 보낸다.
     예전엔 identity_token/authorization_code/full_name 을 보내
     "code 또는 access_token 중 하나가 필요합니다" 로 400 이 났다.

     ⚠️ 그래도 애플은 아직 성공하지 못한다 — 백엔드가 애플에 **code 방식만** 열어 두고
        (`authenticate_with_token` 은 애플 미지원), code 방식엔 `redirect_uri` 를 필수로 받는데
        네이티브 Apple Sign In 에는 리디렉트 주소라는 게 없다.
        백엔드가 네이티브(bundle id 클라이언트, redirect_uri 없음)를 받아줘야 열린다. */
  const body: Record<string, string> = {
    ...(credential.authorizationCode ? { code: credential.authorizationCode } : {}),
    ...(fullName ? { user_name: fullName } : {}),
  };

  return finishLogin('/api/v1/auth/apple/login/', body);
}
