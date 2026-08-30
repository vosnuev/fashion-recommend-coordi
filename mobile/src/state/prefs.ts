import { useSyncExternalStore } from 'react';

import { BudgetEndpoint } from '@/constants/config';
import { api } from '@/lib/apiClient';
import { authStore } from '@/state/auth';

/**
 * 개인화 설정(예산) 경량 스토어.
 * draft-item.ts / auth.ts 와 동일한 "모듈 스토어 + useSyncExternalStore" 패턴.
 *
 * **예산은 서버에 남는다**(`/api/v1/users/me/budget/`). 별명은 아직 서버에 자리가 없어
 * 메모리 보관이라 앱을 껐다 켜면 사라진다.
 */
export type Prefs = {
  nickname: string | null; // 프로필 편집에서 정한 표시 이름 (미설정이면 계정 별명으로 폴백)
  categoryBudgets: CategoryBudgets;
  effectiveCategoryBudgets: CategoryBudgets;
};

export const BUDGET_CATEGORIES = [
  '상의',
  '하의',
  '아우터',
  '원피스/세트',
  '신발',
  '가방',
  '액세서리',
] as const;
export type BudgetCategory = (typeof BUDGET_CATEGORIES)[number];
export type CategoryBudgets = Partial<Record<BudgetCategory, number>>;
export const DEFAULT_CATEGORY_BUDGETS: CategoryBudgets = {
  상의: 50_000,
  하의: 50_000,
  아우터: 150_000,
  '원피스/세트': 50_000,
  신발: 100_000,
  가방: 200_000,
  액세서리: 50_000,
};

/**
 * ⚠️ 두 필드 모두 **없을 수 있다.** `effective_category_budgets` 는 2026-08-13 에 백엔드에
 * 들어왔는데, 그 전 버전이 떠 있는 서버는 이 필드 없이 응답한다. 그대로 스토어에 넣으면
 * `effectiveCategoryBudgets` 가 undefined 가 되고, 그 값을 읽는 화면(룩북·룩 상세·위시·마이)이
 * 통째로 흰 화면이 된다 — 실제로 그렇게 터졌다. 받는 쪽에서 반드시 메워 넣을 것(normalize).
 */
type BudgetResponse = {
  category_budgets?: CategoryBudgets | null;
  effective_category_budgets?: CategoryBudgets | null;
};

/**
 * 서버 응답을 화면이 믿고 쓸 수 있는 모양으로 맞춘다.
 *
 * effective = 기본값 위에 내가 정한 값을 덮은 것이다. 서버가 안 주면 여기서 같은 규칙으로
 * 만든다 — 비로그인 저장 경로(saveBudget)가 쓰는 계산과 일부러 같게 뒀다.
 */
function fromBudgetResponse(
  response: BudgetResponse,
): Pick<Prefs, 'categoryBudgets' | 'effectiveCategoryBudgets'> {
  const own = response.category_budgets ?? {};
  return {
    categoryBudgets: own,
    effectiveCategoryBudgets:
      response.effective_category_budgets ?? { ...DEFAULT_CATEGORY_BUDGETS, ...own },
  };
}

let state: Prefs = {
  nickname: null,
  categoryBudgets: {},
  effectiveCategoryBudgets: DEFAULT_CATEGORY_BUDGETS,
};
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export const prefsStore = {
  get: () => state,
  setNickname(name: string | null) {
    state = { ...state, nickname: name && name.trim() ? name.trim() : null };
    emit();
  },
  /** 서버에 저장해 둔 예산을 읽어 온다. 비로그인·데모는 서버를 부르지 않는다. */
  async loadBudget(): Promise<void> {
    if (!isAuthed()) return;
    try {
      const response = await api.get<BudgetResponse>(BudgetEndpoint);
      state = { ...state, ...fromBudgetResponse(response) };
      emit();
    } catch {
      /* 예산은 없어도 화면이 도는 값이다 — 못 받아 왔다고 에러를 띄우지 않는다.
         '예산을 설정하면…' 안내가 그대로 보이고, 저장은 여전히 된다. */
    }
  },

  /**
   * 카테고리별 예산 저장(전체 교체). 빈 객체는 모든 예산을 기본값으로 되돌린다.
   */
  async saveBudget(values: CategoryBudgets): Promise<void> {
    const normalized = Object.fromEntries(
      Object.entries(values).map(([category, amount]) => [category, normalizeBudget(amount)]),
    ) as CategoryBudgets;
    const response = isAuthed()
      ? await api.put<BudgetResponse>(BudgetEndpoint, { category_budgets: normalized })
      : {
          category_budgets: normalized,
          effective_category_budgets: { ...DEFAULT_CATEGORY_BUDGETS, ...normalized },
        };
    state = { ...state, ...fromBudgetResponse(response) };
    emit();
  },
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};

/* 앱 시작 시점(_layout)에는 아직 로그인 전일 수 있다. 로그인 직후에도 예산이 채워지도록
   세션 변화를 지켜본다 — 로그아웃하면 다시 받을 수 있게 표시를 되돌린다. */
let budgetLoaded = false;
authStore.subscribe(() => {
  if (isAuthed()) {
    if (!budgetLoaded) {
      budgetLoaded = true;
      void prefsStore.loadBudget();
    }
    return;
  }
  budgetLoaded = false;
});

function isAuthed(): boolean {
  const { status, isDemo } = authStore.getState();
  // 데모 세션은 서버 토큰이 없다 — 부르면 401 이라 화면 안에서만 유지한다.
  return status === 'authed' && !isDemo;
}

/** 서버가 받는 범위로 맞춘다 — 1만원 단위, 1만원 이상. */
export const MIN_BUDGET = 10_000;
function normalizeBudget(n: number): number {
  return Math.max(MIN_BUDGET, Math.round(n / MIN_BUDGET) * MIN_BUDGET);
}

/**
 * 슬롯/대분류에 해당하는 상품 1개 예산. 레거시 '잡화' 슬롯은 가방으로 본다.
 *
 * values 가 비어 있어도 터지지 않게 받는다 — 예산은 **없어도 화면이 도는 값**이라
 * (loadBudget 의 catch 주석 참고) 여기서 예외가 나면 잃는 게 예산 표시 하나가 아니라
 * 그 화면 전체다. 스토어에서 이미 메워 넣지만, 읽는 쪽도 한 번 더 막아 둔다.
 */
export function categoryBudget(
  values: CategoryBudgets | null | undefined,
  rawCategory: string,
): number | null {
  if (!values) return null;
  const category = rawCategory === '잡화'
    ? '가방'
    : BUDGET_CATEGORIES.find((candidate) => rawCategory.startsWith(candidate));
  return category ? values[category] ?? null : null;
}

/** 개인화 설정 구독 (예산) */
export function usePrefs() {
  return useSyncExternalStore(prefsStore.subscribe, prefsStore.get, prefsStore.get);
}

/** 예산을 "10만원" 형태로 표시. 미설정이면 null */
export function formatBudget(n: number | null): string | null {
  if (n == null) return null;
  const man = n / 10000;
  return `${Number.isInteger(man) ? man : man.toFixed(0)}만원`;
}

/** "89,000" 같은 가격 문자열을 숫자로 (콤마 제거) */
export function parsePrice(price: string): number {
  return Number(price.replace(/[^0-9]/g, '')) || 0;
}
