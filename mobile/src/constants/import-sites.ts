/**
 * 옷장 "가져오기(Import)" — 쇼핑몰별 어댑터.
 *
 * 사이트마다 로그인/주문내역 URL과 HTML 구조가 다르므로, 화면 코드는 건드리지 않고
 * 여기 설정만 갈아끼워 사이트를 늘린다. (문서/md파일/WebView.md 4.2 "사이트 어댑터")
 *
 * ⚠️ selector 는 사이트가 HTML 을 바꾸면 언제든 깨진다. 지금은 앱에 하드코딩돼 있지만,
 *    장기적으로는 백엔드가 JSON 으로 내려주고 앱은 캐시만 하는 구조로 옮겨야 한다
 *    (WebView.md 7.3-5). 그래서 이 파일의 형태를 그대로 JSON 으로 직렬화할 수 있게 유지한다.
 */

export type ImportSiteKey = 'naver' | '29cm' | 'musinsa';

/**
 * 각 항목은 **후보 배열**이다. 앞에서부터 시도해 첫 번째로 맞는 selector 를 쓴다.
 * 요즘 사이트는 `css-1a2b3c` 같은 해시 클래스라 정확한 selector 하나로 고정하면 금방 깨진다.
 * → 부분일치(`[class*="..." i]`)를 넉넉히 깔아두고, 전부 실패하면 화면 코드가 휴리스틱으로 넘어간다.
 */
export type ScrapeRules = {
  /** 상품 카드 한 칸 */
  itemSelector: string[];
  nameSelector: string[];
  imageSelector: string[];
  priceSelector: string[];
  dateSelector: string[];
  linkSelector: string[];
};

export type ImportSite = {
  key: ImportSiteKey;
  label: string;
  /**
   * WebView 시작 주소 = **구매목록 페이지**.
   * 미로그인이면 사이트가 알아서 로그인 페이지로 보내고, 로그인이 끝나면 이 주소로 되돌려준다
   * (네이버는 `nid.naver.com/nidlogin.login?url=<원래주소>` 형태로 복귀 주소를 들고 간다).
   * 즉 "로그인하면 구매목록으로 넘어간다"는 요구사항의 1차 구현이 이 한 줄이다.
   */
  orderUrl: string;
  /** 구매목록 페이지에 도달했는지 판별 → "불러오기" 버튼 활성화 */
  orderUrlPattern: RegExp;
  /** 로그인 화면인지 판별 → 안내 문구 전환 + 로그인 완료 감지의 기준점 */
  loginUrlPattern: RegExp;
  /** 이 사이트 관련 도메인인지 (벗어나면 "구매목록으로" 안내) */
  hostPattern: RegExp;
  /**
   * WebView 순정 UA 대신 일반 모바일 브라우저 UA 를 내걸지 여부. **기본 꺼짐.**
   *
   * 순정 UA 에는 인앱브라우저 표시가 들어간다(Android 는 `; wv)`, iOS 는 진짜 사파리에 있는
   * `Version/... Safari/...` 토큰이 빠져 있다). 쇼핑몰이 이걸 보고 로그인을 거부할 때만 켠다.
   *
   * 켜기 전에 **반드시 순정 UA 로 먼저 시도할 것.** 처음부터 켜두면 원래 됐을 것을 괜히
   * 건드린 건지 구분할 수 없어 문제를 잘못 짚게 된다.
   *
   * ⚠️ 켜면 MOBILE_USER_AGENT 의 OS·브라우저 버전이 하드코딩이라 낡는다. 오래되면 사이트가
   *    "지원하지 않는 브라우저"로 취급할 수 있으니 켜서 쓰는 동안은 주기적으로 갱신할 것.
   * ※ UA 를 바꿔도 `window.ReactNativeWebView` 가 페이지에 그대로 노출되므로 인앱브라우저라는
   *    사실 자체를 숨기지는 못한다. 단순 UA 분기만 피하는 수단으로만 기대할 것.
   */
  spoofUserAgent?: boolean;
  /**
   * 긁어온 썸네일 주소를 **더 큰 이미지**로 바꾼다. 규칙을 모르면 그대로 돌려준다.
   *
   * 구매목록 썸네일은 화면에 보이는 크기 그대로다(네이버는 200px·7KB). 이 사진으로
   * 서버 모델이 소재·패턴·디테일까지 읽어야 하는데, 그 해상도로는 "패딩"까지가 한계다.
   * 주소 규칙은 사이트마다 달라서 어댑터가 안다.
   */
  upscaleImage?: (url: string) => string;
  scrape: ScrapeRules;
};

/** 쿼리에서 파라미터 하나만 뺀다. 나머지 파라미터와 순서는 건드리지 않는다. */
function withoutQueryParam(url: string, name: string): string {
  const [base, query] = url.split('?');
  if (!query) return url;
  const kept = query.split('&').filter((part) => part.split('=')[0] !== name);
  return kept.length ? `${base}?${kept.join('&')}` : base;
}

/**
 * 네이버 — 1차 지원 대상.
 *
 * URL 은 미로그인 상태로 실제 응답을 확인해 정했다(2026-07 기준):
 *   - `shopping.naver.com/my/order` → 302 `nid.naver.com/nidlogin.login?mode=form&url=...` (복귀 주소 포함)
 *   - 네이버페이 주문내역(`new-m.pay.naver.com/order/status`)도 같은 방식으로 로그인 후 복귀한다.
 * 네이버쇼핑 주문내역을 기본으로 쓰고, 페이 주문내역은 보조 진입점으로 둔다.
 *
 * ⚠️ selector 는 로그인해야 볼 수 있는 페이지라 아직 실물 검증 전이다.
 *    실기기에서 로그인한 뒤 화면 하단 "구조분석"(개발 빌드 전용) 버튼을 눌러 나온 후보로
 *    itemSelector 를 확정할 것. 그전까지는 휴리스틱 추출이 대신 동작한다.
 */
const naver: ImportSite = {
  key: 'naver',
  label: '네이버',
  orderUrl: 'https://shopping.naver.com/my/order',
  orderUrlPattern: /(shopping\.naver\.com\/.*\/?my\/order)|(pay\.naver\.com\/(order|orderstatus))/i,
  loginUrlPattern: /nid\.naver\.com/i,
  hostPattern: /(^|\.)naver\.com/i,
  /**
   * 네이버 이미지 CDN(phinf)은 `?type=f200` 이 붙으면 200px 썸네일을 준다.
   * 이 파라미터만 떼면 원본이 나온다 — 실측(2026-08-11, 구매목록 실제 이미지):
   *   `?type=f200` 200×200·7KB · **쿼리 없음 1000×1000·123KB** · `?type=f640` 640×640·27KB
   *
   * 크기를 키우는 대신 **떼는** 이유: `?type=f800` 은 404 다. 어떤 토큰이 살아 있는지
   * 우리가 알 수 없으니, 추측한 값을 넣었다가 이미지를 통째로 못 받느니
   * 반드시 존재하는 원본을 쓴다. (원본 123KB × 30장 = 4MB, 배치 100MB 제한에 여유가 크다)
   */
  upscaleImage: (url) =>
    /phinf\.pstatic\.net/i.test(url) ? withoutQueryParam(url, 'type') : url,
  scrape: {
    itemSelector: [
      '[class*="orderProduct" i]',
      '[class*="order_product" i]',
      '[class*="productItem" i]',
      'li[class*="order" i]',
      'div[class*="orderList" i] li',
    ],
    // ⚠️ `strong` 은 넣지 말 것 — 네이버는 주문 상태('구매확정완료')를 강조 태그로 넣어서
    //    상품명 자리에 상태 문구가 들어온다(실물에서 확인).
    nameSelector: [
      '[class*="productName" i]',
      '[class*="product_name" i]',
      '[class*="goodsName" i]',
      '[class*="itemName" i]',
      'a[href*="product" i]',
      '[class*="title" i]',
    ],
    imageSelector: ['img[class*="thumb" i]', 'img[class*="image" i]', 'img'],
    priceSelector: ['[class*="price" i]', '[class*="amount" i]'],
    dateSelector: ['[class*="date" i]', 'time'],
    linkSelector: ['a[href*="product" i]', 'a[href]'],
  },
};

/**
 * 29CM — 2차 지원 대상. 패션 전문몰이라 구매목록에 옷만 들어있다(네이버는 잡화가 섞인다).
 *
 * URL 은 미로그인 상태로 리다이렉트 체인을 따라가 확인했다(2026-07-28):
 *   `/order/my-order/` → 308 `/order/my-order`
 *                      → `auth.29cm.co.kr/login?redirect_uri=https%3A%2F%2F...%2Forder%2Fmy-order`
 * 네이버와 같은 "로그인 후 복귀" 패턴이고, **redirect_uri 가 https** 라
 * 네이버에서 터졌던 ATS(-1022) 문제는 여기선 안 난다.
 * (경로는 마이페이지 SPA 번들의 라우트 정의 `path:"my-order"` 에서 역추적했다)
 *
 * ⚠️ selector 는 로그인해야 보이는 페이지라 미검증. 네이버도 첫 후보가 그대로 맞았으니
 *    일단 같은 방식으로 깔아두고, 안 맞으면 휴리스틱 → `구조분석`(__DEV__) 순으로 확정한다.
 */
const cm29: ImportSite = {
  key: '29cm',
  label: '29CM',
  orderUrl: 'https://www.29cm.co.kr/order/my-order',
  orderUrlPattern: /29cm\.co\.kr\/order\/my-order/i,
  loginUrlPattern: /auth\.29cm\.co\.kr/i,
  hostPattern: /(^|\.)29cm\.co\.kr/i,
  scrape: {
    itemSelector: [
      '[class*="orderItem" i]',
      '[class*="order_item" i]',
      '[class*="orderProduct" i]',
      '[class*="productItem" i]',
      'li[class*="order" i]',
    ],
    // 네이버에서 배운 것 — `strong` 은 주문 상태를 물고 오므로 넣지 않는다
    nameSelector: [
      '[class*="productName" i]',
      '[class*="itemName" i]',
      '[class*="goodsName" i]',
      'a[href*="product" i]',
      '[class*="title" i]',
    ],
    imageSelector: ['img[class*="thumb" i]', 'img[class*="image" i]', 'img'],
    priceSelector: ['[class*="price" i]', '[class*="amount" i]'],
    dateSelector: ['[class*="date" i]', 'time'],
    linkSelector: ['a[href*="product" i]', 'a[href]'],
  },
};

/**
 * 무신사 — 기존 "자동스캔"(방식②)이 쓰던 사이트.
 * ⚠️ orderUrl 은 **미확인**이다. `/mypage/order`·`/my/order` 둘 다 404 를 준다(2026-07-28 확인).
 *    이 사이트를 붙일 때 29CM 처럼 리다이렉트 체인을 따라가 실제 경로부터 찾을 것.
 */
const musinsa: ImportSite = {
  key: 'musinsa',
  label: '무신사',
  orderUrl: 'https://www.musinsa.com/mypage/order',
  orderUrlPattern: /musinsa\.com\/.*(order|mypage)/i,
  loginUrlPattern: /musinsa\.com\/(auth|member)\/login/i,
  hostPattern: /(^|\.)musinsa\.com/i,
  scrape: {
    itemSelector: ['[class*="orderItem" i]', 'li[class*="order" i]'],
    nameSelector: ['[class*="name" i]', 'strong'],
    imageSelector: ['img'],
    priceSelector: ['[class*="price" i]'],
    dateSelector: ['[class*="date" i]'],
    linkSelector: ['a[href]'],
  },
};

export const IMPORT_SITES: Record<ImportSiteKey, ImportSite> = {
  naver,
  '29cm': cm29,
  musinsa,
};

export const DEFAULT_IMPORT_SITE: ImportSiteKey = 'naver';

export function resolveImportSite(key?: string | string[] | null): ImportSite {
  const k = Array.isArray(key) ? key[0] : key;
  return IMPORT_SITES[(k as ImportSiteKey) ?? DEFAULT_IMPORT_SITE] ?? IMPORT_SITES[DEFAULT_IMPORT_SITE];
}

/**
 * `spoofUserAgent: true` 인 사이트에만 쓰이는 예비 UA. 기본 경로에서는 쓰지 않는다.
 *
 * 엔진과 UA 를 반드시 맞춰야 한다 — iOS(WebKit)엔 Safari, Android(Chromium)엔 Chrome.
 * 엇갈리게 주면 사이트가 엉뚱한 브라우저용 우회 코드를 태워서 화면이 오히려 깨진다.
 * 아래 버전은 하드코딩이라 시간이 지나면 낡는다(위 spoofUserAgent 주석 참고).
 */
export const MOBILE_USER_AGENT = {
  ios: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
  android:
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36',
} as const;
