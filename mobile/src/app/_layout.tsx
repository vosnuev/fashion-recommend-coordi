import { DarkTheme, DefaultTheme, Stack, ThemeProvider } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { useEffect } from 'react';
import { useColorScheme } from 'react-native';

import { AnimatedSplashOverlay } from '@/components/animated-icon';
import { DevReset } from '@/components/dev-reset';
import { ConfirmProvider, ErrorBoundary, ToastProvider } from '@/components/ui';
import { clearLegacyPendingShare } from '@/lib/secureStore';
import { initSocialSDKs } from '@/lib/socialLogin';
import { authStore } from '@/state/auth';
import { likesStore } from '@/state/likes';
import { lookVoteStore } from '@/state/look-votes';
import { outfitAnalysisStore } from '@/state/outfit-analysis';
import { outfitClaimStore } from '@/state/outfit-claim';
import { prefsStore } from '@/state/prefs';

SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const colorScheme = useColorScheme();

  // 앱 시작 시: 소셜 SDK 초기화(카카오/네이버/구글) + 저장된 토큰으로 세션 복원
  useEffect(() => {
    initSocialSDKs();
    /* 예산은 세션이 정해진 뒤에 받아 온다 — 룩 상세가 '예산 내' 배지에 쓰는 값이라
       그 화면에 들어가기 전에 채워져 있어야 한다. */
    void authStore.bootstrap().then(() => prefsStore.loadBudget());
    outfitAnalysisStore.bootstrap();
    /* 룩북 피드에서 하트로 담아 둔 룩(위시)을 되살린다 — 서버에 자리가 없어 기기 보관이다. */
    void likesStore.bootstrap();
    /* 룩에 남긴 좋아요/별로예요 — 룩북 정렬이 이 값을 쓰므로 목록보다 먼저 준비돼야 한다. */
    void lookVoteStore.bootstrap();
    /* 두 스토어를 구독하므로 뒤에 둔다 — 비로그인 분석의 claim 토큰을 모았다가 로그인 때 넘긴다. */
    outfitClaimStore.bootstrap();
    /* 공유 예약이 서버로 옮겨가기 전(secureStore) 남은 값을 치운다. 아무도 읽지 않지만
       남겨 두면 나중에 "예약이 어디 있지"를 두 군데서 찾게 된다. */
    void clearLegacyPendingShare();
  }, []);
  return (
    <ThemeProvider value={colorScheme === 'dark' ? DarkTheme : DefaultTheme}>
      {/* 전역 피드백 레이어: 어디서든 useToast()/useConfirm() 호출 가능 */}
      <ConfirmProvider>
        <ToastProvider>
          <AnimatedSplashOverlay />
          {/* 화면 하나가 터져도 앱 전체가 백지가 되지 않게 감싼다 (ErrorBoundary 주석 참고).
              토스트·확인창 바깥에 두지 않는 이유 — 오류 화면에서도 그 둘은 살아 있어야 한다. */}
          <ErrorBoundary>
          {/* 헤더는 전 화면 숨김. 진입 흐름(스플래시/온보딩/인증)은 파일명 그대로 자동 등록됨 */}
          <Stack screenOptions={{ headerShown: false }}>
            {/* 메인 앱 = 홈 · 옷장 · 질문(+) · 룩북 · 마이 */}
            <Stack.Screen name="(tabs)" />
            {/* 위에서 올라오는 모달 화면들 */}
            <Stack.Screen name="look-add" options={{ presentation: 'modal' }} />
            <Stack.Screen name="item-add" options={{ presentation: 'modal' }} />
            <Stack.Screen name="item-add-library" options={{ presentation: 'modal' }} />
            <Stack.Screen name="import" options={{ presentation: 'modal' }} />
            <Stack.Screen name="calendar-entry" options={{ presentation: 'modal' }} />
            <Stack.Screen name="outfit-review" options={{ presentation: 'modal' }} />
            <Stack.Screen name="edit-profile" options={{ presentation: 'modal' }} />
          </Stack>
          </ErrorBoundary>
          {/* 개발 전용: 어디서든 스플래시로 돌아가는 단축 버튼 (배포 빌드엔 안 뜸) */}
          <DevReset />
        </ToastProvider>
      </ConfirmProvider>
    </ThemeProvider>
  );
}
