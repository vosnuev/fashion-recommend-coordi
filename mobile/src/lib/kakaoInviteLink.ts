/**
 * 카카오톡 공유 카드가 앱을 실행할 때 전달하는 URL을 초대 화면 경로로 바꾼다.
 *
 * 카카오의 네이티브 실행 URL은 Expo Router 경로가 아니다.
 *   kakao{네이티브 앱 키}://kakaolink?code=ABC123
 * 라우터가 이 값을 화면 경로로 해석하기 전에 `/invite?code=...`로 정규화해야 한다.
 */

export function parseKakaoInviteCode(path: string | null): string | null {
  if (!path) return null;

  try {
    // path가 완전한 URL이 아닐 수도 있다는 Expo Router 계약에 맞춰 기준 URL을 둔다.
    const url = new URL(path, 'mobile://app');
    if (url.hostname.toLowerCase() !== 'kakaolink') return null;

    const code = url.searchParams.get('code')?.trim();
    return code ? code.toUpperCase() : null;
  } catch {
    return null;
  }
}

export function redirectKakaoInvitePath(path: string): string {
  const code = parseKakaoInviteCode(path);
  return code ? `/invite?code=${encodeURIComponent(code)}` : path;
}

/** 카카오 JavaScript SDK의 실행 파라미터는 객체가 아니라 query string으로 받는다. */
export function buildKakaoExecutionParams(code: string): string {
  return new URLSearchParams({ code: code.trim().toUpperCase() }).toString();
}
