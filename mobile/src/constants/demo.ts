/**
 * 데모(체험) 계정 데이터.
 *
 * 백엔드에는 이메일/비밀번호 로그인 API 가 없다(소셜 3종 전용). 그래서 로그인 폼의
 * '로그인' 버튼은 서버 세션 대신 이 데모 계정으로 진입시킨다 — 시연에서 '로그인한
 * 사용자의 홈'과 '비회원 둘러보기'를 구분해 보여주기 위한 자리다.
 *
 * JWT 가 없어 홈 API 를 호출할 수 없으므로 홈 데이터도 여기 고정값을 쓴다.
 * 과거 데모 로그인 세션과 목업 홈 화면의 하위 호환을 위해 유지한다.
 */
import type { HomeData } from '@/hooks/use-home';
import type { AuthUser } from '@/state/auth';

export const DEMO_USER: AuthUser = {
  id: 0,
  username: 'demo',
  email: 'demo@cozy.app',
  nickname: '코지',
  profile_image: null,
  social_accounts: [],
};

export const DEMO_HOME: HomeData = {
  nickname: '코지',
  weather: {
    region: '서울',
    temperature: 24,
    sky_state: '맑음',
    is_stale: false,
    observed_at: null,
  },
  today_look: {
    comment: '볕이 좋은 날이라 밝은 톤 상의에 차분한 색 하의를 맞춰 시선을 위로 모았어요.',
    tags: ['#데일리', '#미니멀'],
    image: null,
  },
  /* 데모에는 진짜 추천이 없다. null 은 "상태를 알 수 없음"이고, 데모 홈은 아래
     DEMO_LOOKS 로 카드를 그리므로 이 값을 보지 않는다. */
  daily_look: null,
  quick_recommends: ['출근룩', '주말 나들이', '비 오는 날'],
  closet_count: 42,
  saved_look_count: 8,
};

/**
 * 데모 홈의 '오늘의 룩' 카드용 목업.
 *
 * **여기 있는 것은 전부 가짜다.** 인증된 사용자의 홈은 이 배열을 쳐다보지 않는다 —
 * 예전에는 추천이 아직 안 만들어진 구간에서 이 사진들이 실제 추천 자리를 채워,
 * 몇 초 뒤 통째로 다른 룩으로 바뀌는 화면이 됐다. 목업은 데모 세션 안에만 둔다.
 */
export type DemoLook = {
  image: string;
  comment: string;
  tags: string[];
  /** 눌렀을 때 열 룩 상세 (constants/today-look.ts LOOK_VARIANTS) */
  variantId: string;
};

export const DEMO_LOOKS: DemoLook[] = [
  {
    image: 'https://i.pinimg.com/736x/55/26/0d/55260de328aec1e50740655fd4b5fdc5.jpg',
    comment: '데이트에 어울리게 색을 절제한 부드러운 캐주얼로 골라봤어요.',
    tags: ['#데이트', '#캐주얼'],
    variantId: 'date',
  },
  {
    image: 'https://i.pinimg.com/736x/b4/cd/22/b4cd22015add333e10cd2ba06067406b.jpg',
    comment: '나들이용으로 편하면서도 산뜻한 조합이에요.',
    tags: ['#나들이', '#미니멀'],
    variantId: 'outdoor',
  },
  {
    image: 'https://i.pinimg.com/736x/ec/96/f3/ec96f39eb800d19290736c17f0253ed9.jpg',
    comment: '일교차가 큰 날 가볍게 걸치기 좋은 레이어드 룩이에요.',
    tags: ['#여행', '#캐주얼'],
    variantId: 'outdoor',
  },
];
