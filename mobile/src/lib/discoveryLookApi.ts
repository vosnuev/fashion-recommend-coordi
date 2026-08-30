import { API_BASE_URL, LookbookEndpoints } from '@/constants/config';
import { api } from '@/lib/apiClient';

export type ShoppingProductDto = {
  id: string;
  /** 서버의 서비스 대분류. 누락된 구버전 응답은 슬롯 검증에서 제외한다. */
  category_large?: string;
  name: string;
  brand: string;
  image: string;
  price: number;
  mall_name: string;
  link: string;
};

export type DiscoveryLookItemDto = ShoppingProductDto & {
  slot: string;
  category_small: string;
  similar_products: ShoppingProductDto[];
};

export type DiscoveryLookDto = {
  id: string;
  gender: LookGender;
  title: string;
  subtitle: string;
  image: string;
  tags: string[];
  total_price: number;
  items: DiscoveryLookItemDto[];
  reasons: string[];
};

export type LookGender = 'WOMAN' | 'MAN';
export type LookGenderFilter = 'ALL' | LookGender;

type DiscoveryLookPage = {
  count: number;
  next_offset: number | null;
  results: DiscoveryLookDto[];
};

export function getDiscoveryLooks(
  query = '',
  tag = '',
  gender: LookGenderFilter = 'ALL',
  limit = 20,
  offset = 0,
): Promise<DiscoveryLookPage> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (gender !== 'ALL') params.set('gender', gender);
  if (query.trim()) params.set('query', query.trim());
  if (tag.trim()) params.set('tag', tag.trim());
  return api.get<DiscoveryLookPage>(`${LookbookEndpoints.discover}?${params}`, { auth: false })
    .then((page) => ({
      ...page,
      /* map 의 콜백은 (값, 인덱스)를 넘긴다 — normalizeLook 을 그대로 건네면 인덱스가
         coverWidth 자리에 들어가 두 번째 룩부터 ?w=1, ?w=2 … 가 붙는다. 감싸서 막는다. */
      results: page.results.map((look) => normalizeLook(look, LIST_COVER_WIDTH)),
    }));
}

export function getDiscoveryLook(id: string): Promise<DiscoveryLookDto> {
  return api.get<DiscoveryLookDto>(LookbookEndpoints.discoverDetail(id), { auth: false })
    .then(normalizeLook);
}

/**
 * 목록 카드에 쓸 커버 폭(px). 카드 한 칸이 195pt 라 2x 기준 390px 이면 충분하다.
 *
 * 서버가 이 파라미터를 받으면 그 폭의 JPEG 축소본을 준다 — 원본은 1080x1350 PNG 로
 * 장당 약 2MB 라, 목록에서 그대로 받으면 한 화면이 수십 MB 가 된다(w=400 이면 약 37KB).
 * 서버 화이트리스트에 있는 값이어야 한다(api/apps/lookbook/services/cover_image.py).
 */
const LIST_COVER_WIDTH = 400;

/**
 * 상대 경로로 오는 커버를 절대 주소로 바꾼다.
 * coverWidth 를 주면 축소본을 요청한다 — 상세는 크게 봐야 하므로 주지 않는다.
 */
function normalizeLook(look: DiscoveryLookDto, coverWidth?: number): DiscoveryLookDto {
  if (!look.image.startsWith('/')) return look;
  const base = `${API_BASE_URL}${look.image}`;
  return { ...look, image: coverWidth ? `${base}?w=${coverWidth}` : base };
}
