import { redirectKakaoInvitePath } from '@/lib/kakaoInviteLink';

/**
 * Expo Router가 네이티브 딥링크를 화면 경로로 해석하기 전에 호출하는 진입점.
 * 앱이 꺼진 상태와 이미 열린 상태 모두 여기서 카카오 초대 스킴을 정규화한다.
 */
export function redirectSystemPath({ path }: { path: string; initial: boolean }): string {
  try {
    return redirectKakaoInvitePath(path);
  } catch {
    // 제3자 URL 하나가 잘못됐다고 앱 시작 자체를 막으면 안 된다.
    return path;
  }
}
