import { type ReactNode } from 'react';
import { Platform, StyleSheet, View, type ViewStyle } from 'react-native';

import { ContentMax, Editorial, ink } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';

/**
 * presentation:'modal' 로 등록된 화면들(look-add·item-add·item-add-library·import)의 본문을 감싸는 래퍼.
 *
 * - 모바일/태블릿: 지금 그대로. children 을 그대로 통과한다(위에서 올라오는 전체 화면).
 * - 데스크톱(≥1024): 뒤 배경을 dim 처리하고 **가운데 고정폭·고정높이 카드**로 띄운다.
 *   넓은 화면에서 폼 하나가 브라우저 창을 통째로 덮지 않게 하는, 웹앱다운 다이얼로그.
 *
 * 각 화면은 자체 헤더·스크롤·하단바를 이미 가지고 있으므로 여기서는 카드의 폭/높이/배경/모서리만 잡는다.
 * 카드에 '명시적 높이'를 주기 때문에 내부의 flex 레이아웃(스크롤 영역·하단 고정바)이
 * 모바일 전체화면과 똑같이 동작한다(높이가 auto 면 flex:1 자식이 0으로 접힌다).
 */
export function ModalShell({
  children,
  maxWidth = ContentMax.narrow,
}: {
  children: ReactNode;
  maxWidth?: number;
}) {
  const { isDesktop, height } = useBreakpoint();
  if (!isDesktop) return <>{children}</>;

  const cardHeight = Math.min(height * 0.9, 820);
  return (
    <View style={styles.backdrop}>
      <View style={[styles.card, cardShadow, { maxWidth, height: cardHeight }]}>{children}</View>
    </View>
  );
}

// RN Web 은 shadow* → boxShadow 로 변환하지만, 웹에선 더 또렷한 그림자를 직접 지정한다.
const cardShadow = Platform.select<ViewStyle>({
  web: { boxShadow: '0 24px 60px rgba(28,25,23,0.22)' } as unknown as ViewStyle,
  default: {
    shadowColor: Editorial.ink,
    shadowOpacity: 0.18,
    shadowRadius: 32,
    shadowOffset: { width: 0, height: 16 },
    elevation: 12,
  },
});

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: ink(0.42),
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  card: {
    width: '100%',
    backgroundColor: Editorial.surface,
    borderWidth: 1, borderColor: Editorial.line,
    borderRadius: 20,
    overflow: 'hidden',
  },
});
