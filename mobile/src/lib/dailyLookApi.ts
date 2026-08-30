import { DailyLookEndpoint, DailyLookSaveEndpoint } from '@/constants/config';
import { api } from '@/lib/apiClient';
import type { LookbookPostDto } from '@/lib/lookbookApi';

/**
 * 오늘의 룩 API 타입 — 백엔드 DailyLookSerializer(api/apps/recommend/serializers.py)와
 * 필드명을 맞춘다. 생성 전에도 200 으로 내려오며 status 로 분기한다.
 */
export type DailyLookStatus = 'QUEUED' | 'PROCESSING' | 'SUCCEEDED' | 'FAILED' | 'EMPTY';

export type DailyLookItem = {
  item_key: string;
  name?: string;
  category?: string;
  sub_category?: string;
  layer_role?: string;
  color?: string;
  note?: string;
  /** 흰 배경 아이템 이미지 presigned URL — 조회마다 새로 서명되므로 캐시하면 만료된다 */
  image_url: string | null;
};

export type DailyLookResult = {
  headline: string;
  golden_id: string;
  rationale_ko: string;
  /**
   * 카드 태그. **룩북 필터와 같은 어휘**다 (백엔드 apps/lookbook/contracts.py 의
   * LOOKBOOK_TAGS 가 단일 정의이고, state/lookbook.ts 의 ALLOWED_HASHTAGS 와 같은 목록).
   * 골든 코디의 occasion·style 에서, 그것이 비면 사용자 추구미에서 뽑는다.
   * 하나도 못 만들면 빈 배열이며 그때는 태그 줄을 숨긴다 — 아이템 이름을 태그처럼
   * 보여주면(예전 방식) 같은 서비스 안에서 '태그'가 두 가지를 뜻하게 된다.
   */
  tags?: string[];
  styling_tips?: string[];
  /** 문장을 누가 썼는지: 'llm' | 'template' (template 이면 담백한 톤) */
  generated_by?: string;
  items?: DailyLookItem[];
  /** 정면 착용 이미지(대표). 생성 전/실패면 null — 그때는 items[].image_url 카드로 화면을 만든다 */
  render_image_url: string | null;
  /** 원본 코디 사진. 사용권이 열린 코디(exposable)에만 값이 있다 */
  outfit_image_url: string | null;
};

export type DailyLookContext = {
  weather: Record<string, unknown>;
  used_body: boolean;
  used_pursuit: boolean;
  body_profile: string;
  /** 판정하지 못한 치수 — "어깨너비를 입력하면 더 정확해져요" 안내에 쓸 수 있다 */
  missing_measurements: string[];
  candidate_count: number;
};

export type DailyLook = {
  look_id: string;
  look_date: string;
  status: DailyLookStatus;
  /** 생성이 끝나기 전(QUEUED/PROCESSING/EMPTY/FAILED)에는 null */
  result: DailyLookResult | null;
  /**
   * '다른 룩'으로 돌려볼 차순위 후보. `result` 와 **같은 스키마**라 카드 하나를
   * 그리는 코드를 그대로 쓴다. 없으면 빈 배열(구버전 서버면 undefined).
   *
   * 문구는 템플릿이고(generated_by='template') 착용 이미지는 서버가 별도 작업으로
   * 나중에 채운다 — 그전까지 render_image_url 은 null 이고, 그때는 대표 룩과 같은
   * 규칙으로 items[].image_url 이 카드를 채운다.
   */
  alternatives?: DailyLookResult[];
  context: DailyLookContext;
  /** QUEUED/PROCESSING 일 때만 값이 있다 — 이 간격(ms) 뒤에 다시 조회한다 */
  poll_after_ms: number | null;
  /** 상태별 사용자 안내 문구 (SUCCEEDED 면 null) */
  detail: string | null;
  created_at: string;
  updated_at: string;
};

/**
 * 생성 정책 안내 문구 — 화면마다 다르게 쓰면 같은 규칙이 두 말이 된다.
 *
 * 서버는 그날 첫 조회에서 한 번만 만들고(DailyLookTodayView), 같은 날의 이후 조회는
 * 만들어 둔 것을 그대로 돌려준다. '다른 룩'도 그때 함께 뽑아 둔 후보다. 이 사실을
 * 적어 두지 않으면 사용자에게는 "새로고침해도 그대로인" 고장으로만 보인다.
 */
export const DAILY_LOOK_ONCE_A_DAY =
  '오늘의 룩은 하루에 한 번 만들어져요. 새 룩은 내일 도착해요.';

/**
 * 후보를 못 찾아(EMPTY) 안내를 띄울 때의 문구. **버튼 위 한 줄이 전부다.**
 *
 * 왜 비었는지와 채우면 무슨 일이 생기는지를 한 문장에 담는다. 예전에는 버튼 위(서버 detail)와
 * 버튼 아래(재생성 안내)로 두 줄이 나뉘어, 같은 말을 두 번 읽게 했다.
 *
 * '내일 도착'(DAILY_LOOK_ONCE_A_DAY)을 여기 쓰면 거짓말이 된다 — 서버는 EMPTY 로 끝난
 * 오늘의 룩만은 체형·추구미가 바뀌면 그 자리에서 다시 만든다(ensure_today_look).
 */
export const DAILY_LOOK_EMPTY_RETRY =
  '체형과 추구미를 입력하시면 오늘의 룩을 만들어 드려요.';

/** 아직 결과가 없어 폴링을 계속해야 하는 상태 */
export function isDailyLookPending(look: DailyLook | null): boolean {
  return look?.status === 'QUEUED' || look?.status === 'PROCESSING';
}

/**
 * 화면이 그려야 할 단계. 홈 카드와 룩 상세가 같은 규칙을 쓰도록 여기 한 곳에 둔다.
 *
 * - `pending`: 아직 모르거나 만드는 중 → **스켈레톤**. 목업으로 채우지 않는다.
 *   완성된 추천처럼 보이는 자리채움은 몇 초 뒤 통째로 바뀌어 "가짜를 봤다"는 인상을 준다.
 * - `ready`: 실제 추천이 있다.
 * - `unavailable`: 후보 없음(EMPTY)·실패(FAILED)·폴링 포기(stalled) → 무엇을 하면
 *   되는지 안내한다. EMPTY 와 FAILED 는 안내가 달라야 해서 status 를 함께 본다.
 */
export type DailyLookPhase = 'pending' | 'ready' | 'unavailable';

export function dailyLookPhase(look: DailyLook | null, stalled = false): DailyLookPhase {
  if (look?.status === 'SUCCEEDED') return look.result ? 'ready' : 'unavailable';
  if (look == null || isDailyLookPending(look)) return stalled ? 'unavailable' : 'pending';
  return 'unavailable';
}

/**
 * 오늘의 룩 조회. 그날 첫 호출이면 백엔드가 생성을 걸고 QUEUED 로 응답한다
 * (홈 API 가 진입 시점에 선반영을 걸어 두므로 보통은 이미 만들어져 있다).
 * lat/lon 을 주면 그 위치의 날씨로 만든다 — 단, 생성은 하루 한 번이라
 * 이미 만들어진 뒤에 보낸 좌표는 반영되지 않는다.
 */
export function getTodayLook(coords?: { lat: number; lon: number }): Promise<DailyLook> {
  const qs = coords ? `?lat=${coords.lat}&lon=${coords.lon}` : '';
  return api.get<DailyLook>(`${DailyLookEndpoint}${qs}`);
}

export type DailyLookSaveResponse = {
  /** 새로 담았으면 true, 이미 담아 둔 코디면 false */
  created: boolean;
  /** 룩북 목록(GET /api/v1/lookbooks/)의 항목과 같은 스키마 */
  lookbook: LookbookPostDto;
};

/**
 * 오늘의 룩을 내 룩북에 담는다.
 *
 * 본문을 보내지 않는다 — 담을 대상은 서버가 그날의 추천으로 정한다.
 * 사진 룩북과 달리 업로드도 옷 추출도 없어 응답이 곧 완료다(폴링 없음).
 *
 * 같은 코디를 두 번 담으면 서버가 기존 룩북을 그대로 돌려주고 `created=false` 다.
 * 화면은 이 값으로 '담았어요'와 '이미 담겨 있어요'를 가른다 — 상태코드로만 가르면
 * 재시도·프록시 때문에 흔들린다.
 */
export function saveTodayLook(goldenId?: string): Promise<DailyLookSaveResponse> {
  /* '다른 룩'으로 돌려보던 후보를 담을 때만 golden_id 를 보낸다. 서버는 이 값이
     그 사용자의 오늘 후보(result + alternatives) 안에 있는지 확인하므로, 화면이
     오래돼 어제 룩을 담으려 하면 404 가 온다. */
  return api.post<DailyLookSaveResponse>(
    DailyLookSaveEndpoint,
    goldenId ? { golden_id: goldenId } : undefined,
  );
}
