import { useSyncExternalStore } from 'react';

import {
  listStylists,
  type ApiStylist,
  type ApiStylistCatalog,
  type StylistId,
} from '@/lib/stylistApi';

/**
 * 고를 수 있는 스타일리스트 목록.
 *
 * 서버(stylist_personas.json)가 원본이라 앱이 이름·설명을 들고 있지 않는다 — 페르소나가
 * 늘거나 문구가 바뀌어도 앱을 고치지 않아도 되게. 대화별 모드·선택값은 세션에 속하므로
 * 여기가 아니라 state/chat.ts 가 들고 있다.
 */

let catalog: ApiStylistCatalog | null = null;
let loading = false;
let error: string | null = null;
const listeners = new Set<() => void>();
const FALLBACK_DISPLAY_ORDER: Record<StylistId, number> = {
  minimal: 1,
  experimental: 2,
  practical: 3,
};

function notify() {
  listeners.forEach((l) => l());
}

/* useSyncExternalStore 는 getSnapshot 이 매번 같은 참조를 주길 요구한다 (state/chat.ts 와 같은 이유). */
let snapshot: {
  stylists: ApiStylist[];
  minSelect: number;
  maxSelect: number;
  defaultIds: StylistId[];
  lastSelectedIds: StylistId[];
  loading: boolean;
  error: string | null;
} = {
  stylists: [],
  minSelect: 1,
  maxSelect: 3,
  defaultIds: [],
  lastSelectedIds: [],
  loading: false,
  error: null,
};

function setSnapshot() {
  snapshot = {
    // display_order 로 고정한다 — 서버가 순서를 보장해도 화면이 그것에 기대지 않게.
    stylists: [...(catalog?.stylists ?? [])].sort((a, b) => a.display_order - b.display_order),
    minSelect: catalog?.min_select ?? 1,
    maxSelect: catalog?.max_select ?? 3,
    defaultIds: catalog?.default_persona_ids ?? [],
    lastSelectedIds: catalog?.last_selected_persona_ids ?? [],
    loading,
    error,
  };
}

export const stylistStore = {
  getSnapshot: () => snapshot,

  /** 목록 받아오기. 이미 받아 뒀으면 다시 부르지 않는다 — 대화 중에 바뀌는 값이 아니다. */
  async load(options: { force?: boolean } = {}): Promise<void> {
    if (!options.force && catalog) return;
    if (loading) return;
    loading = true;
    error = null;
    setSnapshot();
    notify();
    try {
      catalog = await listStylists();
    } catch (e) {
      error = e instanceof Error && e.message ? e.message : '스타일리스트를 불러오지 못했어요';
    } finally {
      loading = false;
      setSnapshot();
      notify();
    }
  },

  /** 이름 찾기 — 구분선·카드 머리가 id 대신 사람이 읽는 이름을 써야 한다. */
  displayName(id: StylistId): string {
    return catalog?.stylists.find((s) => s.id === id)?.display_name ?? id;
  },

  displayNames(ids: StylistId[]): string[] {
    return ids.map((id) => stylistStore.displayName(id));
  },

  /** catalog 응답 전에도 서버 저장 계약과 같은 고정 순서를 사용한다. */
  displayOrder(id: StylistId): number {
    return (
      catalog?.stylists.find((s) => s.id === id)?.display_order ?? FALLBACK_DISPLAY_ORDER[id] ?? 99
    );
  },

  /** 카드 순서 고정용. 고른 순서가 아니라 정해진 순서로 늘어놓는다. */
  sortIds(ids: StylistId[]): StylistId[] {
    return [...ids].sort((a, b) => stylistStore.displayOrder(a) - stylistStore.displayOrder(b));
  },

  /**
   * 팝업을 처음 열 때 체크해 둘 값.
   * 서버가 쓰는 복원 순서를 그대로 따른다: 이 대화의 이전 선택 → 회원 마지막 선택 → minimal.
   */
  restoreSelection(sessionIds: StylistId[]): StylistId[] {
    if (sessionIds.length > 0) return sessionIds;
    if (snapshot.lastSelectedIds.length > 0) return snapshot.lastSelectedIds;
    return snapshot.defaultIds;
  },

  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};

export function useStylists() {
  return useSyncExternalStore(stylistStore.subscribe, stylistStore.getSnapshot, stylistStore.getSnapshot);
}

/**
 * 근거 코드 → 사람이 읽는 말.
 * 값은 백엔드가 실제로 만드는 것들이다 (api/apps/chat/services/*_stylist_strategy.py).
 * 모르는 코드는 지어내지 않고 코드 그대로 보여준다 — 뜻을 추측해 붙이면 없는 근거가 생긴다.
 *
 * ⚠️ **이 코드들은 "조건을 충족했다"는 뜻이 아니다.** 점수 계산에 쓰인 항목의 이름일 뿐이고,
 *    **감점된 항목도 그대로 들어온다.** 보정값이
 *      delta = (score - 0.5) * 2 * weight * SCALE
 *    이라 점수가 0.5 미만이면 음수인데, reason_codes 는 부호를 가리지 않고 전부 모은다
 *    (stylist_strategy.py 의 `[row.reason_code for row in adjustments]` — 거르는 곳이 없다).
 *
 *    그래서 문구는 **무엇을 따져봤는지**까지만 말한다. "날씨에 맞아요" 처럼 결과를 단정하면
 *    날씨 적합도가 낮아서 감점된 카드에도 그 말이 붙어 거짓말이 된다.
 *    실제 추천 이유는 백엔드가 검증된 사실로만 만든 카드 본문 한 문장(`message`)이 말한다.
 *
 * ⚠️ 코드가 나오는 자리가 **두 군데**다. `_REASON_CODES` 딕셔너리만 보면 빠뜨린다
 *    (실제로 PRACTICAL_PREFERENCE_TPO_FIT·PRACTICAL_RECENT_HISTORY 를 그렇게 놓쳤다).
 *    새 페르소나가 붙으면 `git grep -nE 'reason_code\s*=' -- api/apps/chat/services/` 로 훑을 것.
 */
const REASON_LABELS: Record<string, string> = {
  MINIMAL_COLOR_COHESION: '색 정돈',
  MINIMAL_SILHOUETTE_CONSISTENCY: '실루엣 일관성',
  MINIMAL_VISUAL_SIMPLICITY: '시각적 단순함',
  MINIMAL_WARDROBE_REUSABILITY: '옷장 활용도',
  MINIMAL_TPO_FIT: '자리 적합도',
  MINIMAL_RECENT_HISTORY: '최근 추천 이력',

  EXPERIMENTAL_NOVELTY: '새로움',
  EXPERIMENTAL_HISTORY_DISTANCE: '최근 추천과의 거리',
  EXPERIMENTAL_UNDERUSED_ITEM: '덜 입은 옷 활용',
  EXPERIMENTAL_CROSS_STYLE: '스타일 교차',
  EXPERIMENTAL_HYPOTHESIS_ALIGNMENT: '탐색 방향과의 정합',
  EXPERIMENTAL_RECENT_HISTORY: '최근 착용 이력',

  PRACTICAL_WEATHER_FIT: '날씨 적합도',
  PRACTICAL_ACTIVITY_FIT: '활동량 적합도',
  PRACTICAL_WEARING_CONVENIENCE: '착용 편의성',
  PRACTICAL_MAINTENANCE_EASE: '관리 편의성',
  PRACTICAL_WARDROBE_BUDGET_EFFICIENCY: '옷장·예산 효율',
  // tag_confidence·tpo_fit·preference_fit 의 평균이다 — 취향과 자리를 함께 본다.
  PRACTICAL_PREFERENCE_TPO_FIT: '취향·자리 적합도',
  PRACTICAL_RECENT_HISTORY: '최근 착용 이력',
};

export function reasonLabel(code: string): string {
  return REASON_LABELS[code] ?? code;
}
