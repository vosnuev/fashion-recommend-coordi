import { Platform, Linking } from 'react-native';
import { openBrowserAsync, WebBrowserPresentationStyle } from 'expo-web-browser';

/**
 * 외부 쇼핑몰 이동.
 *
 * 우리는 상품을 팔지 않는다. 추천한 상품을 실제로 사려면 판매처로 나가야 하고,
 * 그 마지막 한 칸을 여기서 담당한다.
 *
 * ⚠️ 지금은 백엔드가 상품 링크를 안 내려준다(catalog 응답에 link 없음).
 *    그래서 상품에 `link` 가 있으면 그걸 쓰고, 없으면 **브랜드+상품명 검색 주소**로 보낸다.
 *    검색 결과 페이지라 정확한 상품이 아닐 수 있지만, 막다른 길로 끝나는 것보다 낫다.
 *    백엔드가 link 를 채워주면 이 파일은 그대로 두고 데이터만 바뀌면 된다.
 */

export type MallKey = 'naver' | 'musinsa' | '29cm';

const MALLS: Record<MallKey, { label: string; search: (q: string) => string; host: RegExp }> = {
  naver: {
    label: '네이버쇼핑',
    search: (q) => `https://search.shopping.naver.com/search/all?query=${encodeURIComponent(q)}`,
    host: /(^|\.)naver\.com/i,
  },
  musinsa: {
    label: '무신사',
    search: (q) => `https://www.musinsa.com/search/goods?keyword=${encodeURIComponent(q)}`,
    host: /(^|\.)musinsa\.com/i,
  },
  '29cm': {
    label: '29CM',
    search: (q) => `https://www.29cm.co.kr/search?keyword=${encodeURIComponent(q)}`,
    host: /(^|\.)29cm\.co\.kr/i,
  },
};

/** 기본 판매처. 취급 품목이 가장 넓어 검색이 비는 일이 적다. */
export const DEFAULT_MALL: MallKey = 'naver';

/** 상품 하나가 나갈 주소 — 직접 링크가 있으면 그대로, 없으면 검색 주소 */
export function productUrl(
  p: { name: string; brand: string; link?: string },
  mall: MallKey = DEFAULT_MALL,
): string {
  if (p.link) return p.link;
  /* 브랜드는 비어 있을 수 있다(추천 API 가 안 내려준다) — 그대로 이으면 검색어가 공백으로 시작한다. */
  return MALLS[mall].search([p.brand, p.name].filter(Boolean).join(' '));
}

/** 주소를 보고 어느 몰인지 — 버튼에 "네이버쇼핑에서 보기"처럼 쓴다 */
export function mallLabel(url: string): string {
  const found = (Object.keys(MALLS) as MallKey[]).find((k) => MALLS[k].host.test(url));
  return found ? MALLS[found].label : '판매처';
}

/**
 * 외부 주소 열기.
 * 네이티브는 인앱 브라우저(앱을 벗어나지 않아 돌아오기 쉽다),
 * 웹은 새 탭 — Linking.openURL 은 웹에서 같은 탭을 덮어써 우리 앱이 사라진다.
 */
export async function openExternal(url: string): Promise<void> {
  if (Platform.OS === 'web') {
    globalThis.open?.(url, '_blank', 'noopener,noreferrer');
    return;
  }
  try {
    await openBrowserAsync(url, { presentationStyle: WebBrowserPresentationStyle.AUTOMATIC });
  } catch {
    // 인앱 브라우저를 못 띄우는 기기가 있다 — 기본 브라우저로 넘긴다.
    await Linking.openURL(url);
  }
}
