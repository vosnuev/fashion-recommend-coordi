import { useSyncExternalStore } from 'react';
import { Platform } from 'react-native';

import { AuthEndpoints } from '@/constants/config';
import { DEMO_USER } from '@/constants/demo';
import { api, onUnauthorized } from '@/lib/apiClient';
import {
  clearDemoFlag,
  clearTokens,
  getAccessToken,
  hasDemoFlag,
  saveDemoFlag,
  saveTokens,
} from '@/lib/secureStore';
import { chatStore } from '@/state/chat';
import { outfitAnalysisStore } from '@/state/outfit-analysis';

/**
 * 전역 인증 상태.
 * draft-item.ts 와 동일한 "경량 모듈 스토어 + useSyncExternalStore" 패턴.
 * 스토어를 모듈로 두면 React 밖(apiClient)에서도 세션을 조작할 수 있다.
 *
 * 일반 로그인/소셜 로그인 모두 성공하면 signIn(tokens, user) 하나로 수렴한다.
 */

export type SocialAccountInfo = {
  provider: string;
  email: string | null;
  connected_at: string;
};

/** 백엔드 UserSerializer 응답 형식 (api/apps/users/serializers.py) */
export type AuthUser = {
  id: number;
  username: string;
  email: string;
  nickname: string | null;
  profile_image: string | null;
  /** 사용자가 직접 올린 사진인지 — 소셜 사진과 구분해 '기본으로 되돌리기' 노출을 정한다. */
  profile_image_uploaded?: boolean;
  social_accounts: SocialAccountInfo[];
};

type Status = 'loading' | 'authed' | 'guest';

type AuthState = {
  status: Status;
  user: AuthUser | null;
  /** 과거 버전에서 저장된 토큰 없는 데모 세션의 호환 상태. */
  isDemo: boolean;
};

let state: AuthState = { status: 'loading', user: null, isDemo: false };
const listeners = new Set<() => void>();

function setState(next: Partial<AuthState>): void {
  state = { ...state, ...next };
  listeners.forEach((l) => l());
}

export const authStore = {
  getState: (): AuthState => state,

  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },

  /** 앱 시작 시 1회: 저장된 토큰으로 세션 복원 */
  async bootstrap(): Promise<void> {
    try {
      const token = await getAccessToken();
      if (token) {
        const user = await api.get<AuthUser>(AuthEndpoints.me);
        setState({ status: 'authed', user, isDemo: false });
        return;
      }
      // 토큰은 없지만 데모로 들어온 세션이면 그대로 복원한다(웹 새로고침·앱 재시작 대비).
      if (await hasDemoFlag()) {
        setState({ status: 'authed', user: DEMO_USER, isDemo: true });
        return;
      }
    } catch {
      // 토큰 없음/검증실패/저장소 접근불가 → 게스트. (401 이면 apiClient 가 토큰 정리)
    }
    setState({ status: 'guest', user: null, isDemo: false });
  },

  /** 로그인 성공(일반/소셜 공통): 토큰 저장 + 상태 갱신 */
  async signIn(
    tokens: { access: string; refresh: string },
    user: AuthUser,
  ): Promise<void> {
    await saveTokens(tokens.access, tokens.refresh);
    await clearDemoFlag();
    setState({ status: 'authed', user, isDemo: false });
  },

  /**
   * 레거시 데모 로그인. 신규 화면에서는 사용하지 않으며 기존 저장 세션 복구만 유지한다.
   */
  async signInDemo(): Promise<void> {
    setState({ status: 'authed', user: DEMO_USER, isDemo: true });
    await saveDemoFlag();
  },

  /** 비회원 둘러보기: 로그인하지 않은 상태를 명시적으로 확정한다(직전 데모 세션도 정리). */
  async continueAsGuest(): Promise<void> {
    setState({ status: 'guest', user: null, isDemo: false });
    await clearDemoFlag();
  },

  /**
   * 표시 이름 저장 — PATCH /users/me/ 로 서버에 남기고 로컬 세션도 갱신한다.
   * 예전엔 로컬에만 두어 앱을 다시 켜면 사라졌다(서버는 진작 받을 준비가 돼 있었다).
   */
  async updateNickname(nickname: string): Promise<AuthUser> {
    const user = await api.patch<AuthUser>(AuthEndpoints.me, { nickname });
    setState({ user });
    return user;
  },

  /**
   * 프로필 사진 올리기 — POST /users/me/profile-image/ (multipart).
   *
   * 서버가 정사각 JPEG 로 줄여 S3 에 넣고, 갱신된 사용자를 돌려준다. 응답의
   * profile_image 는 **만료되는 presigned URL** 이라 오래 들고 있으면 안 된다.
   */
  async uploadProfileImage(uri: string): Promise<AuthUser> {
    const form = new FormData();
    /* RN 의 FormData 는 {uri,name,type} 모양을 파일로 취급한다. 웹에서는 blob 을 만들어
       넣어야 한다 — 같은 코드가 두 플랫폼에서 다르게 동작하는 몇 안 되는 자리다. */
    if (Platform.OS === 'web') {
      const blob = await (await fetch(uri)).blob();
      form.append('image', blob, 'profile.jpg');
    } else {
      form.append('image', { uri, name: 'profile.jpg', type: 'image/jpeg' } as never);
    }
    const user = await api.post<AuthUser>(AuthEndpoints.profileImage, form);
    setState({ user });
    return user;
  },

  /** 올린 사진 지우기 — 소셜 사진이 있으면 그리로 되돌아간다. */
  async removeProfileImage(): Promise<AuthUser> {
    const user = await api.delete<AuthUser>(AuthEndpoints.profileImage);
    setState({ user });
    return user;
  },

  /**
   * 회원 탈퇴 — DELETE /users/me/. 계정과 딸린 데이터가 서버에서 지워진다.
   *
   * 서버가 지운 뒤에 로그아웃과 **같은 뒷정리**를 한다(토큰·기기 데이터). 순서를 바꿔
   * 먼저 로그아웃하면 토큰이 없어져 탈퇴 요청 자체가 401 이 된다.
   */
  async withdraw(): Promise<void> {
    await api.delete(AuthEndpoints.me);
    await authStore.signOut();
  },

  /** 로그아웃: simplejwt(stateless)라 서버 엔드포인트가 없다 → 클라이언트 토큰 폐기로 처리 */
  async signOut(): Promise<void> {
    await Promise.all([clearTokens(), clearDemoFlag()]);
    /* 기기에 남은 착장 분석 결과는 방금 나간 사용자 것이다 — 같이 지운다.
       (서버 데이터가 새는 건 아니지만, 로그아웃 후에도 홈에 그 사람 분석 카드가 남는다)
       claim 토큰은 비로그인으로 접수한 건이라 다음 로그인 때 넘겨야 하므로 남긴다. */
    await outfitAnalysisStore.clear();
    /* 대화 목록·내용은 메모리에만 있다. 안 지우면 로그아웃한 뒤 게스트로 채팅에 들어갔을 때
       방금 나간 사람의 대화 목록이 그대로 보인다. */
    chatStore.reset();
    setState({ status: 'guest', user: null, isDemo: false });
  },
};

// 세션 만료(재발급 실패) → 게스트로 강등. (apiClient 가 토큰은 이미 삭제함)
// 데모 세션은 애초에 토큰이 없어 401 이 정상이므로 강등하지 않는다 — 화면별 에러로만 드러난다.
onUnauthorized(() => {
  if (state.isDemo) return;
  // 세션 만료도 로그아웃과 같다 — 남은 대화를 지우지 않으면 게스트 화면에 그대로 비친다.
  chatStore.reset();
  setState({ status: 'guest', user: null, isDemo: false });
});

/** 화면에서 인증 상태를 구독 */
export function useAuth() {
  const snapshot = useSyncExternalStore(
    authStore.subscribe,
    authStore.getState,
    authStore.getState,
  );
  return {
    status: snapshot.status,
    user: snapshot.user,
    isLoggedIn: snapshot.status === 'authed',
    isLoading: snapshot.status === 'loading',
    isDemo: snapshot.isDemo,
    signIn: authStore.signIn,
    signOut: authStore.signOut,
    withdraw: authStore.withdraw,
  };
}
