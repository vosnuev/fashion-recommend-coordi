import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { TabBarHeight } from '@/constants/theme';

/**
 * 하단 탭바가 실제로 가리는 높이.
 *
 * ⚠️ **화면에서는 쓸 일이 없다.** 본문(TabSlot)이 이미 바 높이만큼 여백을 갖고 있어서
 * 화면은 바 아래로 내려가지 않는다(components/app-tabs.tsx). 화면마다 여백을 챙기는
 * 방식은 언젠가 빠뜨리게 되어 그만뒀다 — 캘린더·채팅·알림이 실제로 빠져 있었다.
 *
 * 남겨 둔 용도는 **탭 바깥(루트)에 떠 있는 오버레이** 하나뿐이다. 토스트는 Stack 밖
 * 루트에 그려져서 탭바 위까지 덮으므로, 스스로 이만큼 띄워야 바에 걸치지 않는다.
 *
 * 바의 내용 높이(TabBarHeight)에 기기의 하단 안전영역을 더한다 — 아이폰 홈 인디케이터(34)나
 * 안드로이드 제스처 바가 기기마다 달라 상수로는 맞출 수 없기 때문이다.
 */
export function useBottomTabInset(): number {
  const insets = useSafeAreaInsets();
  return TabBarHeight + Math.max(insets.bottom, 8);
}
