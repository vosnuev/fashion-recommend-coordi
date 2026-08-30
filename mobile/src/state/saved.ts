import { useSyncExternalStore } from 'react';

import {
  createLookbookFromPhoto,
  createLookbookFromWardrobe,
  deleteLookbook,
  getLookbook,
  getLookbookProcessingStatus,
  listLookbooks,
  patchLookbook,
  type LookbookPostDto,
  type LookbookStatus,
} from '@/lib/lookbookApi';
import { saveTodayLook } from '@/lib/dailyLookApi';
import { addWardrobeItemToCloset } from '@/lib/wardrobeApi';
import { authStore } from '@/state/auth';
import type { EntryItem } from '@/state/calendar';

/**
 * 내 룩북 — '오늘의 추천'에서 저장한 룩과 내가 옷장 옷으로 직접 기록한 룩이 함께 모인다.
 * 룩북 '둘러보기'(남들이 올린 피드, state/lookbook.ts)와는 별개의 컬렉션이다.
 *
 * `GET/POST /api/v1/lookbooks/` 로 서버에 남는다. 서버 스키마에 자리가 없는 것들
 * (comment·memo·reason·origin)은 캘린더 스토어와 같은 방식으로 로컬 오버레이에 둔다 —
 * 앱을 껐다 켜면 사라지는 값이라는 뜻이다.
 *
 * 서버에 올릴 수 없는 룩(번들 목업 이미지만 있는 것)은 예전처럼 로컬에만 담는다.
 * 비로그인은 서버를 부르지 않는다 — 이 기기에 담은 것만 보인다.
 */

/**
 * 룩이 어디서 왔는지. 목록에서 한 그리드에 섞이므로 카드 배지로 이걸 구분한다.
 * - 'ai': 앱이 추천해 준 룩을 저장한 것 (서버에 올릴 수 없는 목업 포함 → 위시 갈래)
 * - 'closet': 내 옷장(·친구 옷장) 옷으로 내가 직접 기록한 것
 * - 'daily': 오늘의 룩 카드에서 담은 골든 코디. 서버에 진짜 룩북 글로 남으므로
 *   위시가 아니라 내 룩북 목록에 선다. 유일하게 **서버가 알려주는** 출처다
 *   (source_type=GOLDEN_LOOK) — 나머지는 로컬 오버레이의 추측이다.
 */
export type LookOrigin = 'ai' | 'closet' | 'daily';

export type SavedLook = {
  id: string;
  /** 원격 사진 URL (SmartImage uri) */
  image?: string;
  /** 번들 목업 사진 (require 결과, SmartImage asset) — image 가 없을 때 */
  asset?: number;
  comment?: string;
  /** 사용자가 직접 남긴 메모 — "회사 발표 있는 날 입기 좋았음" 같은 것 */
  memo?: string;
  /**
   * 추천받을 때 들었던 이유. 추천 룩 API(C4)가 붙으면 저장 시점에 같이 담긴다.
   * 없으면 상세에서 그 칸을 그리지 않는다 — 모든 룩에 같은 이유를 보여주면 그건 거짓말이다.
   */
  reason?: string;
  origin: LookOrigin;
  /**
   * 오늘의 룩에서 담은 골든 코디 id (origin 'daily' 에만 있다).
   *
   * 이 룩의 사진은 presigned URL 이라 **조회마다 달라진다.** 사진으로 같은 룩인지
   * 판정하는 keyOf 는 여기서 무력하다 — 담아 뒀는데도 상세의 북마크가 꺼져 보이고,
   * 뺄 때 그 룩을 못 찾는다. 골든 id 는 그 코디를 가리키는 유일하게 안정된 값이다.
   */
  goldenId?: string;
  /**
   * 둘러보기(큐레이션) 룩에서 담았을 때 그 원본 룩 id (`curated-…`).
   *
   * goldenId 와 같은 이유로 필요하다 — 저장하면 서버가 사진을 **자기 것으로 복사**해
   * image 가 원본 커버 주소와 달라진다. 그래서 사진으로 판정하는 keyOf 는 여기서도
   * 무력했다: 담아 둔 룩을 상세에서 '취소'로 못 찾아 화면만 꺼지고 서버엔 남았고,
   * 한 번 더 누르면 같은 룩이 두 번 담겼다.
   */
  sourceId?: string;
  /** 이 룩을 이룬 옷 — 직접 기록한 룩(origin 'closet')에만 있다 */
  items?: EntryItem[];
  /** 그날의 일정 — '팀 회의', '친구 결혼식' */
  note?: string;
  /** 이어져 있는 착장 기록의 날짜 'YYYY-MM-DD'. 룩북↔캘린더를 잇는 한쪽 끈이다. */
  entryDate?: string;
  tags: string[];
  /**
   * 사진으로 올린 룩은 옷 추출이 끝나야 COMPLETED 다. 로컬에만 있는 룩은 없다.
   * 처리 중에는 카드에 '옷 정리 중'을 띄우고 삭제를 막는 데 쓴다.
   */
  status?: LookbookStatus;
  /** 켜져 있으면 앱 사용자 전체가 둘러보기에서 본다. 룩북에 친구 단위 공유는 없다. */
  isPublic?: boolean;
  savedAt: number;
};

/**
 * 같은 룩을 두 번 저장하지 않도록 사진으로 식별.
 * 사진이 없는 룩(옷·일정만 기록한 것)은 서로 다른 룩이라도 키가 같아지므로 중복 판정에서 뺀다.
 */
function keyOf(look: { image?: string; asset?: number }): string | null {
  if (look.image) return look.image;
  if (look.asset != null) return `asset:${look.asset}`;
  return null;
}

/** 서버에서 받은 룩. 로그인 상태에서만 채워진다. */
let serverLooks: SavedLook[] = [];
/** 서버에 올릴 수 없어 이 기기에만 남는 룩 — 번들 목업 이미지만 있는 추천 룩과 비로그인 저장분. */
let localLooks: SavedLook[] = [];
/** 둘을 합쳐 최신순으로 세운 것. useSyncExternalStore 가 같은 참조를 받아야 해서 캐시한다. */
let savedLooks: SavedLook[] = [...localLooks];

type LoadState = { loading: boolean; error: string | null; loaded: boolean };
let loadState: LoadState = { loading: false, error: null, loaded: false };

/**
 * 서버 스키마에 자리가 없는 것들. 룩 id 로 붙여 둔다.
 *
 * 백엔드 LookbookPost 가 가진 자유 텍스트는 `schedule`(일정) 하나뿐이라
 * 룩 제목(comment)·내 메모(memo)·추천 이유(reason)를 실을 곳이 없다.
 * origin 도 마찬가지다 — source_type(사진/옷장)은 '누가 만든 룩인지'와 다른 축이다.
 */
type Overlay = {
  comment?: string;
  memo?: string;
  reason?: string;
  origin?: LookOrigin;
  /** 캘린더 쪽에서 먼저 만든 기록과 이어 붙인 경우 — 서버는 이 연결을 모른다. */
  entryDate?: string;
  /** 둘러보기 원본 룩 id. 서버 DTO 에 자리가 없어 여기 얹는다(앱을 껐다 켜면 사라진다). */
  sourceId?: string;
};
const overlays: Record<string, Overlay> = {};
const RECOMMENDATION_CARD_LOOKBOOK_PREFIX = 'recommendation-card:';

const listeners = new Set<() => void>();

function rebuild() {
  savedLooks = [...serverLooks, ...localLooks].sort((a, b) => b.savedAt - a.savedAt);
}

function notify() {
  rebuild();
  listeners.forEach((l) => l());
}

/** 서버 응답 + 로컬 오버레이 → 화면이 쓰는 룩 */
function toLook(dto: LookbookPostDto): SavedLook {
  const overlay = (overlays[dto.id] ??= {});
  const items: EntryItem[] = dto.wardrobe_items.map((link) => ({
    /* 골든 코디 구성 아이템은 옷장 아이템이 아니라 wardrobe_item_id 가 null 이다.
       연결 행 id 로 대신 채운다 — 화면에서 key 로 쓰이므로 비면 리스트가 깨진다. */
    id: link.wardrobe_item_id ?? link.link_id,
    source: 'closet',
    name: (link.snapshot.item_name as string) || '이름 없는 아이템',
    image: link.image_url || undefined,
    inCloset: link.added_to_closet_at != null,
  }));

  return {
    id: dto.id,
    image: dto.image_url || undefined,
    comment: overlay.comment ?? dto.schedule ?? undefined,
    memo: overlay.memo,
    reason: overlay.reason,
    /* 오늘의 룩에서 담은 룩은 서버가 정확히 알려준다 — 짐작보다 이걸 먼저 본다.
       (골든 코디는 구성 아이템이 있어서, 짐작에 맡기면 '내가 고른 룩'으로 뭉친다)
       나머지는 오버레이가 비었으면(다른 기기·재시작) 담긴 옷으로 짐작한다 — 옷이
       걸려 있으면 내가 고른 룩, 사진뿐이면 추천 룩 쪽에 가깝다. */
    origin:
      dto.source_type === 'GOLDEN_LOOK'
        ? dto.golden_id.startsWith(RECOMMENDATION_CARD_LOOKBOOK_PREFIX)
          ? 'ai'
          : 'daily'
        : (overlay.origin ?? (items.length > 0 ? 'closet' : 'ai')),
    goldenId: dto.golden_id || undefined,
    sourceId: overlay.sourceId,
    items: items.length ? items : undefined,
    note: dto.schedule || undefined,
    entryDate: dto.calendar?.date ?? overlay.entryDate,
    tags: dto.hashtags ?? [],
    status: dto.status,
    isPublic: dto.is_public,
    savedAt: Date.parse(dto.created_at) || 0,
  };
}

export const savedLookStore = {
  getLooks: () => savedLooks,
  getLook: (id: string) => savedLooks.find((l) => l.id === id),
  isSaved: (look: { image?: string; asset?: number }) => {
    const key = keyOf(look);
    return key != null && savedLooks.some((l) => keyOf(l) === key);
  },
  /** 이 골든 코디를 이미 담아 뒀는가. 사진(presigned)이 아니라 코디 id 로 본다. */
  getByGoldenId: (goldenId: string) =>
    goldenId ? savedLooks.find((l) => l.goldenId === goldenId) : undefined,
  /** 이 둘러보기 룩을 이미 담아 뒀는가. 위와 같은 이유로 사진이 아니라 원본 id 로 본다. */
  getBySourceId: (sourceId: string) =>
    sourceId ? savedLooks.find((l) => l.sourceId === sourceId) : undefined,
  /**
   * 저장. 사진이 같은 룩이 이미 있으면 중복 추가하지 않고 기존 것을 돌려준다.
   * origin 기본값이 'ai' 인 이유: 이 함수를 부르는 기존 자리(홈·룩 상세)가 전부 추천 룩 저장이다.
   */
  /** 내 룩북을 서버에서 받아 온다. 비로그인·데모 세션은 서버를 부르지 않는다. */
  async load(): Promise<void> {
    if (!isAuthed()) {
      loadState = { loading: false, error: null, loaded: true };
      notify();
      return;
    }
    loadState = { ...loadState, loading: true, error: null };
    notify();
    try {
      const page = await listLookbooks({ limit: 100 });
      serverLooks = page.results.map(toLook);
      loadState = { loading: false, error: null, loaded: true };
      notify();
      /* 앱을 껐다 켜거나 한참 만에 들어오면 아직 처리 중인 룩이 있을 수 있다 —
         그때도 스스로 채워지도록 여기서 다시 지켜보기를 건다. */
      for (const dto of page.results) {
        if (isProcessing(dto.status)) watchProcessing(dto.id);
      }
    } catch (error) {
      loadState = {
        loading: false,
        error: error instanceof Error ? error.message : '룩북을 불러오지 못했어요.',
        loaded: loadState.loaded,
      };
      notify();
    }
  },

  async addLook(input: {
    image?: string;
    /** 둘러보기 원본 룩 id — 저장 뒤에도 '같은 룩'인지 알아보기 위한 유일한 안정 값 */
    sourceId?: string;
    asset?: number;
    comment?: string;
    tags?: string[];
    reason?: string;
    origin?: LookOrigin;
    items?: EntryItem[];
    note?: string;
    entryDate?: string;
    /**
     * `entryDate` 날짜의 캘린더 기록을 **서버가 함께 만들지** 여부.
     *
     * 룩북에서 '캘린더에도 기록하기'를 켠 경우에만 true 다. 반대로 캘린더 화면에서
     * '룩북에도 올리기'로 들어온 경우는 캘린더 기록을 캘린더 쪽이 따로 만들므로 false —
     * 여기서도 만들면 같은 날짜에 두 번 등록해 409 가 난다.
     */
    createCalendar?: boolean;
    /** 그 날짜에 이미 캘린더 기록이 있을 때 덮어쓸지 — 사용자에게 물은 뒤에만 true */
    overwriteCalendar?: boolean;
    /** 켜면 앱 사용자 전체가 둘러보기에서 본다 */
    isPublic?: boolean;
  }): Promise<SavedLook> {
    const key = keyOf(input);
    /* 추천 룩 저장은 같은 카드를 여러 번 담지 않도록 사진으로 중복을 막는다.
       반면 직접 기록한 룩은 같은 사진을 다른 날짜에 다시 입을 수 있으므로,
       사진이 같더라도 각각의 착장 기록으로 남겨야 한다. */
    /* 사진으로 보기 전에 원본 id 로 먼저 본다 — 저장하면 서버가 사진을 자기 것으로
       복사해 주소가 달라지므로, 사진만 보면 같은 룩을 또 담게 된다. */
    const bySource = input.sourceId
      ? savedLooks.find((l) => l.sourceId === input.sourceId)
      : undefined;
    if (bySource) return bySource;
    const existing =
      input.origin === 'closet' || key == null
        ? undefined
        : savedLooks.find((l) => keyOf(l) === key);
    if (existing) return existing;

    const origin = input.origin ?? 'ai';
    const serverItems = input.items?.filter((item) => item.source === 'closet') ?? [];

    /* 서버로 보낼 수 있는가 — 올릴 사진(원격 주소 포함)이나 내 옷장 옷이 있어야 한다.
       번들 목업 이미지(asset)뿐인 룩은 올릴 실체가 없어 이 기기에만 담는다. */
    const canUpload = isAuthed() && (Boolean(input.image) || serverItems.length > 0);
    if (!canUpload) return addLocalLook(input, origin);

    const meta = {
      schedule: (input.note ?? input.comment ?? '').trim(),
      hashtags: input.tags ?? [],
      isPublic: input.isPublic ?? false,
      ...(input.createCalendar && input.entryDate
        ? { calendarDate: input.entryDate, overwriteCalendar: input.overwriteCalendar }
        : null),
    };
    const dto = input.image
      ? await createLookbookFromPhoto({
          photoUri: input.image,
          wardrobeItemIds: serverItems.map((item) => item.id),
          ...meta,
        })
      : await createLookbookFromWardrobe({
          wardrobeItemIds: serverItems.map((item) => item.id),
          ...meta,
        });

    // 서버에 자리가 없는 값들은 여기서 붙여 둔다 — toLook 이 이걸 다시 얹는다.
    overlays[dto.id] = {
      comment: input.comment,
      reason: input.reason,
      origin,
      entryDate: input.entryDate,
      sourceId: input.sourceId,
    };
    const look = toLook(dto);
    serverLooks = [look, ...serverLooks];
    notify();
    if (isProcessing(dto.status)) watchProcessing(dto.id);
    return look;
  },

  /**
   * 오늘의 룩 카드의 '저장' — 골든 코디를 내 룩북에 담는다.
   *
   * addLook 을 쓰지 않는 이유: addLook 은 표지 사진을 **다시 업로드**해 옷장
   * 파이프라인(GPU)을 태운다. 골든 코디는 서버가 이미 가진 자산이고 태깅도 끝나
   * 있어서, 그 경로로 담으면 같은 사진이 사용자 수만큼 복제되고 이미 끝난 태깅을
   * 다시 돌린다. 서버는 버킷·키를 가리키기만 하므로 왕복 한 번으로 끝난다.
   *
   * `goldenId` 는 '다른 룩'으로 돌려보던 후보를 담을 때만 준다. 생략하면 대표 룩.
   *
   * Returns: 담긴 룩과 `created`(이미 담아 둔 코디면 false).
   */
  async saveDailyLook(goldenId?: string): Promise<{ look: SavedLook; created: boolean }> {
    const { created, lookbook } = await saveTodayLook(goldenId);
    const look = toLook(lookbook);
    /* 이미 담아 둔 코디면 목록에 이미 서 있다 — 같은 id 를 두 번 넣지 않는다. */
    serverLooks = [look, ...serverLooks.filter((l) => l.id !== look.id)];
    notify();
    return { look, created };
  },

  async removeLook(id: string) {
    const local = localLooks.find((l) => l.id === id);
    if (local) {
      localLooks = localLooks.filter((l) => l.id !== id);
      notify();
      return;
    }
    await deleteLookbook(id);
    serverLooks = serverLooks.filter((l) => l.id !== id);
    delete overlays[id];
    notify();
  },

  /**
   * 메모·태그 수정. 사진과 저장 시각은 건드리지 않는다.
   * 태그는 서버에 남고, 메모는 서버에 자리가 없어 오버레이에만 남는다.
   */
  async updateLook(id: string, patch: { memo?: string; tags?: string[] }) {
    const memo = patch.memo?.trim() || undefined;
    const isLocal = localLooks.some((l) => l.id === id);

    if (!isLocal) {
      overlays[id] = { ...overlays[id], memo };
      if (patch.tags) {
        const dto = await patchLookbook(id, { hashtags: patch.tags });
        serverLooks = serverLooks.map((l) => (l.id === id ? toLook(dto) : l));
        notify();
        return;
      }
      serverLooks = serverLooks.map((l) => (l.id === id ? { ...l, memo } : l));
      notify();
      return;
    }

    localLooks = localLooks.map((l) =>
      l.id === id ? { ...l, memo, tags: patch.tags ?? l.tags } : l,
    );
    notify();
  },

  /**
   * 룩에 걸린 옷 하나를 내 옷장에 들인다.
   * 서버가 멱등이라 두 번 눌러도 안전하고, 성공하면 그 옷만 목록에서 상태를 바꾼다
   * (전체를 다시 받지 않는다 — 상세를 보는 중에 목록이 통째로 흔들릴 이유가 없다).
   */
  async addItemToCloset(lookId: string, itemId: string) {
    await addWardrobeItemToCloset(itemId);
    const mark = (look: SavedLook): SavedLook =>
      look.id === lookId
        ? {
            ...look,
            items: look.items?.map((i) => (i.id === itemId ? { ...i, inCloset: true } : i)),
          }
        : look;
    serverLooks = serverLooks.map(mark);
    localLooks = localLooks.map(mark);
    notify();
  },

  /** 전체 공개를 켜고 끈다. */
  async setPublic(id: string, isPublic: boolean) {
    const dto = await patchLookbook(id, { isPublic });
    serverLooks = serverLooks.map((l) => (l.id === id ? toLook(dto) : l));
    notify();
  },

  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getLoadState: () => loadState,
};

/** 서버에 올릴 수 없는 룩 — 예전과 같은 로컬 저장. */
function addLocalLook(
  input: {
    image?: string;
    sourceId?: string;
    asset?: number;
    comment?: string;
    tags?: string[];
    reason?: string;
    items?: EntryItem[];
    note?: string;
    entryDate?: string;
  },
  origin: LookOrigin,
): SavedLook {
  const look: SavedLook = {
    id: `local-${Date.now()}`,
    image: input.image,
    asset: input.asset,
    comment: input.comment,
    reason: input.reason,
    origin,
    sourceId: input.sourceId,
    items: input.items?.length ? input.items : undefined,
    note: input.note?.trim() || undefined,
    entryDate: input.entryDate,
    tags: input.tags ?? [],
    savedAt: Date.now(),
  };
  localLooks = [look, ...localLooks];
  notify();
  return look;
}

function isAuthed(): boolean {
  const { status, isDemo } = authStore.getState();
  // 데모 세션은 서버 토큰이 없다 — 부르면 401 이라 로컬로 남긴다.
  return status === 'authed' && !isDemo;
}

function isProcessing(status: LookbookStatus): boolean {
  return status === 'REGISTERED' || status === 'PROCESSING';
}

const PROCESSING_POLL_MS = 3_000;
const MAX_PROCESSING_POLL_MS = 3 * 60_000;
const watching = new Set<string>();

/**
 * 사진으로 올린 룩은 옷 추출이 끝나야 담긴 옷이 채워진다.
 *
 * 화면이 아니라 스토어가 맡는 이유는 캘린더와 같다 — 올리고 목록으로 돌아가는 게
 * 정상 흐름이라, 화면에 걸면 그 화면을 벗어나는 순간 추적이 끊긴다.
 */
function watchProcessing(lookbookId: string) {
  if (watching.has(lookbookId)) return;
  watching.add(lookbookId);
  const startedAt = Date.now();

  const tick = async () => {
    // 지켜보는 사이에 지워졌으면 그만둔다.
    if (!serverLooks.some((l) => l.id === lookbookId)) {
      watching.delete(lookbookId);
      return;
    }
    if (Date.now() - startedAt > MAX_PROCESSING_POLL_MS) {
      watching.delete(lookbookId);
      return;
    }

    try {
      const status = await getLookbookProcessingStatus(lookbookId);
      if (status.is_terminal) {
        watching.delete(lookbookId);
        // 상태만으로는 어떤 옷이 나왔는지 모른다 — 룩을 다시 받아야 목록이 채워진다.
        const fresh = await getLookbook(lookbookId);
        serverLooks = serverLooks.map((l) => (l.id === lookbookId ? toLook(fresh) : l));
        notify();
        return;
      }
      const current = serverLooks.find((l) => l.id === lookbookId);
      if (current && current.status !== status.status) {
        serverLooks = serverLooks.map((l) =>
          l.id === lookbookId ? { ...l, status: status.status } : l,
        );
        notify();
      }
    } catch {
      // 일시적인 실패로 추적을 끝내지 않는다 — 다음 회차에 복구된다.
    }
    setTimeout(() => void tick(), PROCESSING_POLL_MS);
  };

  setTimeout(() => void tick(), PROCESSING_POLL_MS);
}

export function useSavedLooks() {
  return useSyncExternalStore(savedLookStore.subscribe, savedLookStore.getLooks, savedLookStore.getLooks);
}

/** 목록 로딩 상태 — '내 룩북' 탭이 로딩·에러 화면을 그리는 데 쓴다. */
export function useSavedLooksState(): LoadState {
  return useSyncExternalStore(
    savedLookStore.subscribe,
    savedLookStore.getLoadState,
    savedLookStore.getLoadState,
  );
}
