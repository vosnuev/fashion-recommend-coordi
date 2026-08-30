/**
 * Below are the colors that are used in the app. The colors are defined in the light and dark mode.
 * There are many other ways to style your app. For example, [Nativewind](https://www.nativewind.dev/), [Tamagui](https://tamagui.dev/), [unistyles](https://reactnativeunistyles.vercel.app), etc.
 */

// @ts-ignore
import '@/global.css';

import { Platform } from 'react-native';

export const Colors = {
  light: {
    text: '#000000',
    background: '#ffffff',
    backgroundElement: '#F0F0F3',
    backgroundSelected: '#E0E1E6',
    textSecondary: '#60646C',
  },
  dark: {
    text: '#ffffff',
    background: '#000000',
    backgroundElement: '#212225',
    backgroundSelected: '#2E3135',
    textSecondary: '#B0B4BA',
  },
} as const;

export type ThemeColor = keyof typeof Colors.light & keyof typeof Colors.dark;

export const Fonts = Platform.select({
  ios: {
    /** iOS `UIFontDescriptorSystemDesignDefault` */
    sans: 'system-ui',
    /** 한국어 제목이 궁서체처럼 보이지 않도록 시스템 산세리프로 통일한다. */
    serif: 'system-ui',
    /** iOS `UIFontDescriptorSystemDesignRounded` */
    rounded: 'ui-rounded',
    /** iOS `UIFontDescriptorSystemDesignMonospaced` */
    mono: 'ui-monospace',
  },
  default: {
    sans: 'normal',
    serif: 'normal',
    rounded: 'normal',
    mono: 'monospace',
  },
  web: {
    sans: 'var(--font-display)',
    serif: 'var(--font-display)',
    rounded: 'var(--font-rounded)',
    mono: 'var(--font-mono)',
  },
});

export const Spacing = {
  half: 2,
  one: 4,
  two: 8,
  three: 16,
  four: 24,
  five: 32,
  six: 64,
} as const;

/**
 * 에디토리얼 '본(bone)' 팔레트 — 실제 화면들이 각자 로컬로 복붙해 쓰던 값들을 한 곳으로 통합.
 * 새 화면·공용 컴포넌트는 이걸 import 해서 쓰고, 기존 화면은 손볼 때 점진적으로 이걸로 교체.
 * 앱은 현재 라이트 고정(다크모드는 팀 결정 대기)이라 단일 팔레트.
 */
/** ink 위 불투명도 램프: 보조 텍스트·보더·백드롭에 사용 (rgba(28,25,23,a)) */
export const ink = (a: number) => `rgba(28,25,23,${a})`;

/**
 * 어두운 내비게이션 면(Editorial.nav) 위에 얹는 흰색 램프.
 *
 * 밝은 면 위의 ink() 와 짝을 이룬다 — 면이 어두운 자리에서 ink() 를 쓰면 글자가 묻힌다.
 * 토프브라운(#6B564A) 기준 실측: 1.0 = 6.9:1, 0.78 = 4.9:1, **0.75 = 4.7:1 이 AA 하한**.
 * ⚠️ 하한은 면 색에 딸린 값이다 — 코코아(#4A3A30)였을 땐 0.55 였다.
 *    nav 를 바꾸면 여기 수치도 반드시 다시 잴 것.
 */
export const onNav = (a: number) => `rgba(255,255,255,${a})`;


/**
 * 앱의 유일한 면 색 — 오트(따뜻한 미색).
 *
 * 배경이든 카드든 컨트롤이든 태그든 전부 이 값 하나를 쓴다. 면끼리는 색으로 구분하지 않고
 * **테두리 명암(lineSoft < line < lineStrong)으로만** 위계를 만든다.
 * 면에 색을 얹기 시작하면 층이 늘어나 화면이 탁해지므로, 색은 CTA 한 자리에만 남긴다.
 *
 * 2026-08-20 순백에서 교체. 후보 8종을 홈·룩북 실화면에 입혀 팀이 고른 값이다
 * (색상 38°·채도 44%·밝기 96% — 순백에서 한 걸음만 뗀 미색).
 *
 * ⚠️ 이 값을 바꾸면 아래 글자색·테두리 램프의 대비가 전부 달라진다. 같이 다시 잴 것.
 *    아래 textSoft·textCaption 의 알파는 이 값 기준으로 다시 잰 결과다(순백일 땐 0.72/0.60).
 *    웹 페이지 배경(global.css 의 #root/body)은 syncWebPageBackground() 가 맞춰 준다.
 */
const PAPER = '#FAF7F2';

/**
 * 면 색을 알파와 함께 — 사진 위에 뜨는 가격칩처럼 반투명한 면이 필요한 자리에.
 * 하드코딩 rgba(255,255,255,…) 를 쓰면 면 색이 바뀔 때 그 자리만 흰색으로 남는다.
 */
export const paper = (a: number) => {
  const h = PAPER.replace('#', '');
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  return `rgba(${r},${g},${b},${a})`;
};

/**
 * 웹 페이지 자체의 배경(html·body·#root)은 global.css 가 칠하므로 RN 스타일이 닿지 않는다.
 * 여기서 CSS 변수로 넘겨 주지 않으면 앱 면만 색이 들고 프레임 바깥은 흰색으로 남아 어긋난다.
 */
function syncWebPageBackground() {
  if (Platform.OS !== 'web') return;
  try {
    document.documentElement.style.setProperty('--cozy-paper', PAPER);
  } catch {
    // window/document 가 없는 환경 — global.css 의 기본값이 쓰인다
  }
}

syncWebPageBackground();

export const Editorial = {
  ink: '#1c1917', // 웜 블랙 — 본문/버튼/활성 상태
  white: '#ffffff',


  /*
   * ── 면(surface) 토큰 ──
   * 아래는 전부 PAPER 로 같은 값이다. 역할별로 이름을 남겨 둔 건 나중에 한 역할만
   * 따로 조정하기 위해서지, 지금 색이 다르다는 뜻이 아니다.
   * 경계가 필요하면 색이 아니라 테두리를 준다.
   */

  /** 화면 전체 배경. global.css 의 #root / body 배경과 반드시 같아야 한다. */
  page: PAPER,
  /** 카드·패널·말풍선 등 큰 배경 면 (배경 전용, 텍스트색으로 쓰지 않음) */
  surface: PAPER,
  /** 검색바·토글·필터버튼·태그칩·아이콘칩 같은 작은 컨트롤 면 */
  control: PAPER,
  /** 패널·타일 배경, 사이드바 선택 상태 */
  surfaceSoft: PAPER,
  /** '내 옷' 태그 등 */
  surfaceTag: PAPER,
  /** 'new'/경고 배경 강조 — 면은 같고 테두리·글자로 강조한다 */
  accent: PAPER,
  /** 이미지 로딩/깨짐 자리표시자 배경 (영역은 SmartImage 의 테두리가 잡는다) */
  bone: PAPER,
  wine: '#5E2B2F', // 경고 텍스트/아이콘

  /**
   * 내비게이션 면 — 웹의 하단 탭바·좌측 사이드바. 앱에서 유일하게 어두운 면이다.
   *
   * 오트와 같은 갈색 계열(22°)이라 어울리고, 본문과 6.4:1 로 갈려 **테두리 없이도**
   * 경계가 또렷하다. 한때 코코아(#4A3A30, 대비 10.1)였는데 너무 어두워 한 톤 올렸다.
   * 반대로 본문보다 밝게 가는 안(#FCFAF8·#FDF9F2)도 시도했지만 흰 판으로 읽혀 접었다.
   *
   * ⚠️ 이 위에 얹는 글자·아이콘은 ink 가 아니라 onNav() 를 쓴다. 면이 밝아진 만큼
   *    비활성 라벨의 하한도 함께 올라갔다 — 아래 onNav 주석 참고.
   */
  nav: '#6B564A',

  /**
   * 주 행동 버튼(CTA) 배경 — '로그인·저장·시작하기·둘러보기' 처럼 화면당 하나뿐인 버튼.
   * selected 와 같은 값이다: 주 행동과 '지금 선택된 것'은 같은 무게로 읽혀야 한다.
   * 면이 전부 순백이라 화면에서 색을 가진 자리는 여기 하나뿐이고, 그래서 시선이 모인다.
   * 흰 글자 대비 10.8:1.
   */
  cta: '#4A3A30',

  /**
   * 선택 상태 — 칩·체크박스·탭·스텝처럼 '고른 것'을 채우는 색.
   * cta 와 값이 같지만 토큰을 나눠 둔 건 나중에 한 역할만 따로 조정하기 위해서다.
   * ink 보다 밝고 따뜻하다 — 글자(웜블랙)와 선택을 따로 움직이려는 구분.
   * 흰 글자 대비 10.8:1.
   */
  selected: '#4A3A30',
  danger: '#E23B2E', // 폼 에러
  kakao: '#FEE500',

  /*
   * ── 글자색 램프 ──
   * 역할은 넷뿐이다. 새 alpha 를 만들지 말고 여기서 고를 것.
   * (한때 18개 alpha 가 흩어져 있었고 그중 100곳이 대비 미달이었다.)
   * 대비는 PAPER(#FFFFFF) 기준이며, 배경을 바꾸면 여기 값도 다시 재야 한다.
   *
   *   제목·본문·강조 = ink 그대로 (#1C1917, 17.5:1)
   */
  /** 보조 설명 — 6.9:1 (오트 기준. 순백일 땐 0.72 였다) */
  textSoft: ink(0.73),
  /** 캡션·라벨. **읽어야 하는 글자의 최저선** — 4.6:1 (WCAG AA 하한).
   *  순백일 땐 0.60 이었다. 면이 어두워지면 여기가 가장 먼저 미달하므로 함께 올렸다. */
  textCaption: ink(0.61),
  /** 장식·워터마크. 읽을 필요가 없는 것에만 — 2.4:1 */
  textMuted: ink(0.38),

  /*
   * ── 테두리 램프 ──
   * 면이 전부 같은 색이므로 화면의 위계는 오직 이 세 단계가 만든다.
   * 한 화면에서 세 단계를 다 쓰지 말 것 — 보통 line 하나, 강조가 필요할 때만 lineStrong.
   */
  /** 있는 듯 없는 듯한 구분선 — 리스트 행 사이, 카드 내부 분할 */
  lineSoft: ink(0.06),
  /** 기본 경계 — 카드·패널·입력·컨트롤. 새 테두리는 기본적으로 이걸 쓸 것. */
  line: ink(0.12),
  /** 강조 경계 — 선택·포커스처럼 '지금 여기'를 가리키는 자리에만 */
  lineStrong: ink(0.24),
} as const;

/**
 * 타이포 스케일 — "글자가 너무 작다"는 피드백 반영해 바닥을 12로 올림.
 * (기존 화면들은 10~13px 하드코딩이 많았음.) 공용 컴포넌트는 이 스케일을 따름.
 */
export const Type = {
  micro: 12, // 배지·아주 작은 라벨 (기존 10~11)
  caption: 13, // 캡션·보조 (기존 11~12)
  footnote: 14, // 리스트 보조 텍스트 (기존 12.5~13)
  body: 15, // 본문 기본
  label: 16, // 버튼·강조 본문
  lead: 18, // 소제목
} as const;

/**
 * 하단 탭바가 콘텐츠 위에 떠 있으므로(position:absolute) 그만큼 아래 여백을 확보해야 한다.
 *
 * ⚠️ 이 값은 **바의 내용 높이만**이다. 실제로 가려지는 높이는 여기에 기기의 하단
 * 안전영역(아이폰 홈 인디케이터 34, 안드로이드 제스처 바 등)이 더해진 값이라,
 * 상수 하나로는 기기마다 맞출 수 없다. 화면에서는 useBottomTabInset() 을 쓸 것.
 */
export const TabBarHeight = Platform.select({ ios: 50, android: 50, web: 68 }) ?? 0;

/** @deprecated 기기별 안전영역이 빠져 콘텐츠가 탭바에 가린다. useBottomTabInset() 을 쓸 것. */
export const BottomTabInset = TabBarHeight;

/**
 * 폰 프레임 폭 — 모바일 레이아웃에서 콘텐츠가 넓어지지 않게 잡는 상한.
 * global.css 의 #root max-width 와 반드시 같아야 한다. 여기가 단일 출처다.
 */
export const PhoneFrameWidth = 440;

/**
 * 데스크톱에서 본문이 과하게 늘어나지 않도록 잡는 최대 폭.
 * 한 줄이 너무 길면 읽기 어렵고, 목록은 라벨과 값이 양 끝으로 벌어져 시선이 끊긴다.
 */
export const ContentMax = {
  /** 세로 사진이 주인공인 카드 — 더 넓히면 사진이 지나치게 커지거나 잘린다 */
  card: 560,
  /** 폼·설정 목록 — 한 줄이 길어지면 안 되는 화면 */
  narrow: 720,
  /** 일반 본문 */
  default: 880,
  /** 그리드처럼 넓게 쓰는 화면 */
  wide: 1280,
} as const;

/**
 * 넓은 화면에서 본문 오른쪽에 상주하는 패널의 폭.
 * 화면마다 내용은 달라도(코지에게 물어보기 / 지난 대화) 자리는 같아야 해서 한 값을 공유한다.
 */
export const ChatPanelWidth = 400;

/**
 * 데스크톱 좌측 사이드바 폭 (웹 전용).
 * 다이얼로그를 본문 열에 맞추려면 사이드바가 얼마를 쓰는지 알아야 해서 여기 둔다.
 */
export const SidebarWidth = 196;

/**
 * 반응형 기준 폭. 창 폭이 이 값 **이상**이면 해당 레이아웃으로 본다.
 * 기기 종류(User-Agent)가 아니라 창 폭으로 판단해야 데스크톱에서 창을 줄였을 때도 맞게 동작한다.
 * 값을 바꾸면 useBreakpoint() 를 쓰는 모든 화면이 함께 따라온다.
 */
export const Breakpoints = {
  /** 2열 그리드가 좁아지기 시작하는 지점 */
  tablet: 768,
  /** 하단 탭바 → 좌측 사이드바로 바뀌는 지점 */
  desktop: 1024,
  /** 우측 채팅 패널까지 함께 띄우는 지점.
      사이드바 232 + 본문 최소 560 + 패널 400 = 1192 가 하한이라 여유를 둔 값이다. */
  wide: 1280,
} as const;

/** 옷장·룩북 2열 그리드 카드 — 이미지 비율·모서리 통일 */
export const GridCard = {
  pad: 20,
  gap: 10,
  maxWidth: PhoneFrameWidth,
  imageRatio: 1,
  radius: 16,
} as const;

export function gridCardWidth(windowWidth: number): number {
  const w = Math.min(windowWidth, GridCard.maxWidth);
  return (w - GridCard.pad * 2 - GridCard.gap) / 2;
}

export function gridCardImageHeight(cardWidth: number): number {
  return cardWidth * GridCard.imageRatio;
}
