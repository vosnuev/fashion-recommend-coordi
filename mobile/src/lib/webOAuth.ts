import {
  GOOGLE_WEB_CLIENT_ID,
  KAKAO_REST_API_KEY,
  NAVER_OAUTH_CLIENT_ID,
  type SocialProvider,
} from '@/constants/config';

/**
 * 웹 소셜 로그인 — 브라우저 인가 코드(code) 방식.
 *
 * 네이티브는 각 제공사 SDK 로 access_token 을 받아 백엔드에 넘기지만, 웹에는 그 SDK 가 없다.
 * 대신 표준 OAuth 리다이렉트를 쓴다:
 *   ① 제공사 인가 페이지로 이동 → ② 사용자가 승인 → ③ 우리 콜백 주소로 `code` 를 들고 복귀
 *   → ④ 콜백 화면이 `POST /auth/{provider}/login/ {code, redirect_uri, state}` 로 넘김
 *   → ⑤ 백엔드가 토큰 교환·프로필 조회 후 우리 JWT 발급
 *
 * ⚠️ `redirect_uri` 는 ①과 ④에서 **완전히 같아야** 한다(카카오·구글 요구사항). 그래서 한 곳에서만 만든다.
 * ⚠️ 이 주소는 각 제공사 콘솔에 **미리 등록**돼 있어야 한다. 등록 안 된 주소로 보내면
 *    로그인 창 대신 제공사 에러 페이지가 뜬다 (우리 코드가 잡을 수 없는 지점).
 */

/** 제공사가 되돌아올 우리 주소. 콘솔에 등록하는 값과 글자 하나까지 같아야 한다. */
export function redirectUri(): string {
  return `${window.location.origin}/auth/callback`;
}

/**
 * state — CSRF 방지용 난수에 provider 를 실어 보낸다.
 *
 * 콜백 주소를 제공사마다 따로 두면 콘솔에 3개를 등록해야 하고 라우트도 3개가 된다.
 * 주소는 하나로 두고 "누구에게서 돌아왔는지"는 state 로 판별한다 — 세 제공사 모두
 * state 를 그대로 되돌려준다.
 */
const STATE_KEY = 'oauth_state';

export function createState(provider: SocialProvider): string {
  const nonce = Math.random().toString(36).slice(2) + Date.now().toString(36);
  const state = `${provider}.${nonce}`;
  /* 돌아왔을 때 우리가 시작한 요청이 맞는지 대조한다. sessionStorage 라 탭을 닫으면 사라지고,
     다른 탭에서 시작한 로그인과도 섞이지 않는다. */
  try {
    window.sessionStorage.setItem(STATE_KEY, state);
  } catch {
    // 사파리 프라이빗 모드 등에서 막힐 수 있다 — 대조를 못 할 뿐 로그인은 진행한다.
  }
  return state;
}

/** 돌아온 state 를 검증하고 provider 를 꺼낸다. 어긋나면 null. */
export function readState(state: string | null): SocialProvider | null {
  if (!state) return null;
  let saved: string | null = null;
  try {
    saved = window.sessionStorage.getItem(STATE_KEY);
    window.sessionStorage.removeItem(STATE_KEY);
  } catch {
    saved = null;
  }
  // 저장된 값이 있는데 다르면 우리가 시작한 요청이 아니다(CSRF).
  if (saved && saved !== state) return null;
  const provider = state.split('.')[0];
  return provider === 'kakao' || provider === 'naver' || provider === 'google'
    ? provider
    : null;
}

/** 제공사별 인가 URL. 키가 없으면 null — 화면이 "웹에서는 아직" 이라고 알린다. */
export function authorizeUrl(provider: SocialProvider, state: string): string | null {
  const uri = redirectUri();

  if (provider === 'kakao') {
    if (!KAKAO_REST_API_KEY) return null;
    const q = new URLSearchParams({
      client_id: KAKAO_REST_API_KEY,
      redirect_uri: uri,
      response_type: 'code',
      state,
    });
    return `https://kauth.kakao.com/oauth/authorize?${q}`;
  }

  if (provider === 'naver') {
    if (!NAVER_OAUTH_CLIENT_ID) return null;
    const q = new URLSearchParams({
      client_id: NAVER_OAUTH_CLIENT_ID,
      redirect_uri: uri,
      response_type: 'code',
      state,
    });
    return `https://nid.naver.com/oauth2.0/authorize?${q}`;
  }

  if (!GOOGLE_WEB_CLIENT_ID) return null;
  const q = new URLSearchParams({
    client_id: GOOGLE_WEB_CLIENT_ID,
    redirect_uri: uri,
    response_type: 'code',
    scope: 'openid email profile',
    state,
    // 구글은 기본이 online 이라 refresh_token 을 안 준다. 백엔드가 프로필만 읽으므로 그대로 둔다.
    prompt: 'select_account',
  });
  return `https://accounts.google.com/o/oauth2/v2/auth?${q}`;
}

/** 인가 페이지로 이동. 되돌아올 때 앱이 새로 로드되므로 이 뒤 코드는 실행되지 않는다. */
export function startWebLogin(provider: SocialProvider): boolean {
  const url = authorizeUrl(provider, createState(provider));
  if (!url) return false;
  window.location.assign(url);
  return true;
}
