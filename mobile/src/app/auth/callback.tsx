import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ErrorState, LoadingState } from '@/components/ui';
import { Editorial } from '@/constants/theme';
import { ApiError } from '@/lib/apiClient';
import { completeWebLogin } from '@/lib/socialLogin';
import { readState } from '@/lib/webOAuth';

/**
 * 웹 소셜 로그인 콜백 — 제공사가 인가 코드를 들고 되돌아오는 자리.
 *
 * 주소는 `/auth/callback?code=...&state=...` 이고, 각 제공사 콘솔에 등록된 redirect_uri 와
 * 같은 경로여야 한다. 어느 제공사인지는 state 앞부분으로 판별한다 (webOAuth.createState).
 *
 * 이 화면은 사용자가 직접 열 일이 없다 — 코드 교환이 끝나면 곧바로 홈으로 보낸다.
 * 실패해도 여기 머무르면 주소창에 code 가 남은 채로 갇히므로, 사유만 보여주고 로그인으로 돌린다.
 */
export default function AuthCallback() {
  const { code, state, error, error_description } = useLocalSearchParams<{
    code?: string;
    state?: string;
    error?: string;
    error_description?: string;
  }>();
  const [failed, setFailed] = useState<string | null>(null);
  /* 개발 중 StrictMode 이중 실행이나 리렌더로 코드가 두 번 교환되는 걸 막는다.
     인가 코드는 **일회용**이라 두 번째 요청은 반드시 실패한다. */
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;

    // 사용자가 동의를 거부했거나 제공사가 거절한 경우 — code 없이 error 만 온다.
    if (error) {
      started.current = true;
      setFailed(error_description || '로그인이 취소되었어요.');
      return;
    }
    // 파라미터가 아직 안 붙었을 수 있어(라우터 초기 렌더) 둘 다 있을 때만 시작한다.
    if (!code || !state) return;

    started.current = true;
    const provider = readState(state);
    if (!provider) {
      setFailed('로그인 요청이 확인되지 않았어요. 다시 시도해 주세요.');
      return;
    }

    completeWebLogin(provider, code, state)
      .then(() => router.replace('/(tabs)/home'))
      .catch((e) => {
        setFailed(
          e instanceof ApiError ? e.message : '로그인을 마치지 못했어요. 다시 시도해 주세요.',
        );
      });
  }, [code, state, error, error_description]);

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top', 'bottom']} style={styles.safe}>
        {failed ? (
          <ErrorState
            title="로그인하지 못했어요"
            description={failed}
            onRetry={() => router.replace('/login')}
            retryLabel="로그인으로 돌아가기"
            retryIcon="chevron.left"
            style={styles.fill}
          />
        ) : (
          <LoadingState message="로그인하고 있어요…" style={styles.fill} />
        )}
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  safe: { flex: 1, paddingHorizontal: 24 },
  fill: { flex: 1 },
});
