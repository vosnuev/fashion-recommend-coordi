import { Platform } from 'react-native';
import { Href, router } from 'expo-router';

/**
 * 뒤로가기.
 *
 * 웹에서는 상세 화면들이 (tabs) 탭으로 등록돼 있어 expo-router 의 router.back() 이나
 * 브라우저 히스토리가 직전 화면이 아니라 엉뚱한 탭(예: 마이)으로 가는 경우가 있다.
 * 그래서 웹에서는 히스토리에 기대지 않고 **항상 지정된 목적지(fallback)로 확정 이동**한다.
 * (각 화면은 자신이 돌아가야 할 자리를 fallback 으로 넘긴다.)
 *
 * ⚠️ 웹은 router.replace 가 아니라 **router.navigate** 를 쓴다. replace 로 하면 특정 탭
 * (확인된 예: '/(tabs)/closet')로의 이동이 조용히 무시돼(no-op) 뒤로가기가 먹통이 된다
 * — home/my/lookbook 는 되는데 closet 만 안 되는 재현을 CDP 클릭으로 확인. navigate 는
 * 탭 트리거 방식과 동일하게 동작해 모든 탭 목적지로 확정 이동한다. replace 로 되돌리지 말 것.
 *
 * 네이티브에서는 정상적인 스택이라 이력이 있으면 뒤로, 없으면 fallback 으로 보낸다.
 */
export function goBack(fallback: Href = '/(tabs)/home') {
  if (Platform.OS === 'web') {
    router.navigate(fallback);
    return;
  }
  if (router.canGoBack()) router.back();
  else router.replace(fallback);
}

/**
 * 히스토리를 쓰지 않고 **지정한 자리로 확정 이동**한다.
 *
 * 삭제처럼 지금 화면이 사라져야 하는 동작에 쓴다. `goBack` 은 네이티브에서 이력이 있으면
 * `router.back()` 으로 돌아가는데, 상세 화면들이 (tabs) 안에 등록돼 있어 그 '직전'이
 * 들어온 자리가 아닐 수 있다(옷 삭제 후 마이로 튀던 이유). 목적지가 정해진 동작은
 * 이력을 묻지 않는다.
 *
 * 웹에서 `router.replace` 를 쓰지 않는 이유는 goBack 주석과 같다 — 특정 탭으로의 replace 가
 * 조용히 무시된다.
 */
export function goTo(dest: Href) {
  if (Platform.OS === 'web') router.navigate(dest);
  else router.replace(dest);
}

/**
 * 여러 화면에서 들어오는 상세로 갈 때, 돌아올 자리를 `from` 으로 함께 넘긴다.
 *
 * 웹의 goBack 은 히스토리를 안 쓰고 fallback 으로 확정 이동하는데, 상세 화면 하나에
 * fallback 을 하나만 박아 두면 어느 길로 들어왔든 같은 곳으로 튕긴다
 * (룩 상세는 홈·룩북·채팅에서, 저장 룩은 룩북·캘린더에서 들어온다).
 * 부르는 쪽이 자기 자리를 알려 주고, 상세는 `from ?? 기본값` 으로 돌아간다.
 */
export function withReturn(href: string, from: string): Href {
  return `${href}${href.includes('?') ? '&' : '?'}from=${encodeURIComponent(from)}` as Href;
}

/** 상세 화면에서 쓰는 짝 — `from` 이 없으면 그 화면의 기본 자리로 돌아간다. */
export function backTo(from: string | undefined, fallback: Href): Href {
  return (from as Href) ?? fallback;
}
