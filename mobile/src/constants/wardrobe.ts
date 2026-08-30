/**
 * 서버에 올릴 수 없는 데모 착장과 앱 카탈로그 화면에만 쓰는 로컬 항목.
 *
 * 내 옷장과 공유 옷장은 실 API를 사용한다. `CLOSET_ITEMS`는 저장 룩 시드의 하위 호환,
 * `LIBRARY_ITEMS`는 아직 서버 상품 선택 계약이 없는 앱 카탈로그용이다.
 */

/** 옷이 어디서 왔는지 — `shared`는 기존 캘린더 기록 복원 호환을 위해 유지한다. */
export type WardrobeSource = 'closet' | 'library' | 'shared';

export type WardrobeItem = {
  id: string;
  name: string;
  category: string;
  tone: number;
  /** 공유 옷장 아이템의 주인 */
  owner?: string;
  /** 앱 카탈로그 아이템의 브랜드 */
  brand?: string;
  image?: string;
};

/** 내 옷장 */
export const CLOSET_ITEMS: WardrobeItem[] = [
  {
    id: '1',
    name: '연두 나시',
    category: '상의',
    tone: 0.05,
    image: 'https://i.pinimg.com/1200x/3e/04/ea/3e04eaa53146fd9bf93736707fffcb4f.jpg',
  },
  {
    id: '2',
    name: '연노랑 반팔 가디건',
    category: '아우터',
    tone: 0.22,
    image: 'https://i.pinimg.com/736x/a5/22/df/a522dfff1a759163fae0616ec0cab583.jpg',
  },
  {
    id: '3',
    name: '버뮤다 팬츠',
    category: '하의',
    tone: 0.16,
    image: 'https://i.pinimg.com/1200x/14/64/d4/1464d4e315aa8e7df53bb6c74fc31e59.jpg',
  },
  {
    id: '4',
    name: '아디다스 스니커즈',
    category: '신발',
    tone: 0.24,
    image: 'https://i.pinimg.com/736x/6e/ce/fa/6ecefa13347d6487fc30c0fda287d4dd.jpg',
  },
  {
    id: '5',
    name: '모자',
    category: '하의',
    tone: 0.08,
    image: 'https://i.pinimg.com/736x/ef/fa/96/effa960f41d4e20b7a0e31253732d75e.jpg',
  },
  {
    id: '6',
    name: '체크 가방',
    category: '가방',
    tone: 0.3,
    image: 'https://i.pinimg.com/1200x/39/32/c4/3932c44dead7e38ad916ef2e8cc2902f.jpg',
  },
];

/** 앱이 가진 옷 (카탈로그) — 아직 안 산 옷도 착장 기록에 올려볼 수 있다 */
export const LIBRARY_ITEMS: WardrobeItem[] = [
  {
    id: 'c1',
    name: '오버사이즈 셔츠',
    category: '상의',
    tone: 0.08,
    brand: '무신사 스탠다드',
    image: 'https://images.unsplash.com/photo-1596755094514-f87e34085b2c?w=400&h=500&fit=crop',
  },
  {
    id: 'c2',
    name: '울 블렌드 코트',
    category: '아우터',
    tone: 0.2,
    brand: 'COS',
    image: 'https://images.unsplash.com/photo-1544966503-7cc5ac882d5f?w=400&h=500&fit=crop',
  },
  {
    id: 'c3',
    name: '데님 팬츠',
    category: '하의',
    tone: 0.16,
    brand: '유니클로',
    image: 'https://images.unsplash.com/photo-1591195853828-11db59a44f6b?w=400&h=500&fit=crop',
  },
  {
    id: 'c4',
    name: '레더 스니커즈',
    category: '신발',
    tone: 0.24,
    brand: '나이키',
    image: 'https://images.unsplash.com/photo-1549298916-b41d501d3772?w=400&h=500&fit=crop',
  },
  {
    id: 'c5',
    name: '리넨 셋업 재킷',
    category: '아우터',
    tone: 0.11,
    brand: '자라',
    image: 'https://images.unsplash.com/photo-1539533018447-63fcce2678e3?w=400&h=500&fit=crop',
  },
  {
    id: 'c6',
    name: '캔버스 토트백',
    category: '가방',
    tone: 0.13,
    brand: '마르디 메크르디',
    image: 'https://images.unsplash.com/photo-1515562141207-7a88fb7ce338?w=400&h=500&fit=crop',
  },
];
