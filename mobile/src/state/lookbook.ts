import { useSyncExternalStore } from 'react';

import { getDiscoveryLooks, type LookGender, type LookGenderFilter } from '@/lib/discoveryLookApi';
import { listPublicLookbooks, type LookbookPostDto } from '@/lib/lookbookApi';

/**
 * 룩북 필터 태그 어휘.
 *
 * ⚠️ **단일 정의는 백엔드에 있다** — api/apps/lookbook/contracts.py 의 LOOKBOOK_TAGS.
 * 오늘의 룩이 같은 어휘로 태그를 만들려면 서버 쪽에 기준이 있어야 해서 옮겼다.
 * 여기는 필터 칩을 그리기 위한 사본이므로, 어휘를 바꿀 때는 **양쪽을 같이** 고친다
 * (순서도 맞춘다 — 두 화면의 나열이 달라지면 같은 어휘인데 다른 목록처럼 보인다).
 */
export const ALLOWED_HASHTAGS = [
  '출근', '데이트', '나들이', '여행', '미니멀', '캐주얼', '빈티지', '스트릿', '하객룩',
] as const;
export type AllowedHashtag = (typeof ALLOWED_HASHTAGS)[number];

export type LookPost = {
  id: string;
  image: string;
  tags: AllowedHashtag[];
  price?: string;
  variantId?: string;
  gender?: LookGender;
  createdAt: number;
};

let curatedLooks: LookPost[] = [];
let publicLooks: LookPost[] = [];
let looks: LookPost[] = [];
const listeners = new Set<() => void>();
let loadSequence = 0;
type LoadState = { loading: boolean; error: string | null; loaded: boolean; progress: number };
let loadState: LoadState = { loading: false, error: null, loaded: false, progress: 0 };

function notify() {
  looks = [...curatedLooks, ...publicLooks];
  listeners.forEach((listener) => listener());
}

export function isAllowedHashtag(value: string): value is AllowedHashtag {
  return (ALLOWED_HASHTAGS as readonly string[]).includes(value);
}

function toPublicLook(dto: LookbookPostDto): LookPost {
  return {
    id: dto.id,
    image: dto.image_url,
    tags: (dto.hashtags ?? []).filter(isAllowedHashtag),
    createdAt: Date.parse(dto.created_at) || 0,
  };
}

export const lookbookStore = {
  getLooks: () => looks,
  getLoadState: () => loadState,

  async load(gender: LookGenderFilter = 'ALL', selectedTags: string[] = []): Promise<void> {
    const sequence = ++loadSequence;
    loadState = { ...loadState, loading: true, error: null, progress: 8 };
    notify();

    const loadCurated = async () => {
      if (selectedTags.length > 0) {
        let completed = 0;
        const pages = await Promise.all(
          selectedTags.map(async (tag) => {
            const page = await getDiscoveryLooks('', tag, gender, 50);
            completed += 1;
            loadState = {
              ...loadState,
              progress: Math.min(92, 8 + Math.round((completed / selectedTags.length) * 84)),
            };
            notify();
            return page;
          }),
        );
        const unique = new Map(pages.flatMap((page) => page.results).map((look) => [look.id, look]));
        return [...unique.values()];
      }

      const accumulated = new Map<string, Awaited<ReturnType<typeof getDiscoveryLooks>>['results'][number]>();
      let offset = 0;
      while (true) {
        const page = await getDiscoveryLooks('', '', gender, 20, offset);
        if (sequence !== loadSequence) return [...accumulated.values()];
        page.results.forEach((look) => accumulated.set(look.id, look));
        loadState = {
          ...loadState,
          progress: Math.min(
            92,
            Math.max(8, Math.round((accumulated.size / Math.max(page.count, 1)) * 92)),
          ),
        };
        curatedLooks = [...accumulated.values()].map((look) => ({
          id: look.id,
          variantId: look.id,
          image: look.image,
          tags: look.tags.filter(isAllowedHashtag),
          price: `₩${look.total_price.toLocaleString('ko-KR')}`,
          gender: look.gender,
          createdAt: 0,
        }));
        notify();
        if (page.next_offset == null) return [...accumulated.values()];
        offset = page.next_offset;
      }
    };

    const [curatedResult, publicResult] = await Promise.allSettled([
      loadCurated(),
      listPublicLookbooks({ limit: 60 }),
    ]);
    if (sequence !== loadSequence) return;

    if (curatedResult.status === 'fulfilled') {
      curatedLooks = curatedResult.value.map((look) => ({
        id: look.id,
        variantId: look.id,
        image: look.image,
        tags: look.tags.filter(isAllowedHashtag),
        price: `₩${look.total_price.toLocaleString('ko-KR')}`,
        gender: look.gender,
        createdAt: 0,
      }));
    }
    if (publicResult.status === 'fulfilled') {
      publicLooks = gender === 'ALL' ? publicResult.value.results.map(toPublicLook) : [];
    }
    const failureCount = [curatedResult, publicResult].filter((result) => result.status === 'rejected').length;
    loadState = {
      loading: false,
      error: failureCount === 2 ? '둘러보기를 불러오지 못했어요.' : failureCount === 1 ? '일부 룩을 불러오지 못했어요.' : null,
      loaded: curatedResult.status === 'fulfilled' || publicResult.status === 'fulfilled',
      progress: 100,
    };
    notify();
  },

  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};

export function useLookbook() {
  return useSyncExternalStore(lookbookStore.subscribe, lookbookStore.getLooks, lookbookStore.getLooks);
}

export function useLookbookLoadState() {
  return useSyncExternalStore(
    lookbookStore.subscribe,
    lookbookStore.getLoadState,
    lookbookStore.getLoadState,
  );
}

export const LOOKBOOK_FILTER_OPTIONS = ['전체', ...ALLOWED_HASHTAGS];
