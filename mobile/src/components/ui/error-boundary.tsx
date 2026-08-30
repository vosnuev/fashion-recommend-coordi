/**
 * 화면이 렌더 중 터졌을 때 **백지 대신** 사유를 보여주는 마지막 그물.
 *
 * 이게 없으면 어느 화면 하나가 예외를 던지는 순간 앱 전체가 빈 화면이 된다 —
 * 사용자 눈에는 "눌렀는데 아무것도 안 뜬다"로 보이고, 원인을 물어볼 단서도 남지 않는다.
 * (2026-08-20: 서버 응답에 필드 하나가 비면 옷장 화면이 통째로 사라지는 걸 확인했다.)
 *
 * 되살리기는 **다시 그려보기**다 — 원인이 일시적인 응답이었으면 그것으로 복구되고,
 * 아니면 같은 화면이 다시 뜨므로 사용자가 뒤로 갈 수 있다.
 */
import { Component, type ReactNode } from 'react';
import { StyleSheet, View } from 'react-native';

import { ErrorState } from '@/components/ui/state-views';
import { Editorial } from '@/constants/theme';

type Props = { children: ReactNode };
type State = { error: Error | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    // 배포본에서도 남긴다 — 재현이 어려운 화면 오류는 이 한 줄이 유일한 단서다.
    console.error('[화면 오류]', error);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <View style={styles.container}>
        <ErrorState
          title="화면을 보여주지 못했어요"
          description={this.state.error.message || '잠시 후 다시 시도해 주세요.'}
          onRetry={() => this.setState({ error: null })}
          style={styles.fill}
        />
      </View>
    );
  }
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page, paddingHorizontal: 24 },
  fill: { flex: 1 },
});
