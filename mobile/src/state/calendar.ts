import { useSyncExternalStore } from 'react';

import { ApiError } from '@/lib/apiClient';
import {
  createCalendarFromPhoto,
  createCalendarFromWardrobe,
  deleteCalendarEntry,
  getCalendarEntry,
  getCalendarEntryByDate,
  getCalendarProcessingStatus,
  linkCalendarItems,
  listCalendarEntries,
  patchCalendarEntry,
  unlinkCalendarItem,
  type CalendarEntryDto,
} from '@/lib/calendarApi';
import type { WardrobeItem, WardrobeSource } from '@/constants/wardrobe';
import type { AllowedHashtag } from '@/state/lookbook';

/**
 * 착장 캘린더 기록 — 날짜 하나에 기록 하나.
 *
 * 한 기록은 '그날의 룩 사진'과 '입은 옷 목록'을 **함께** 담는다. 둘은 배타적 선택이 아니라
 * 같은 하루를 다른 각도로 남긴 것이라, 사진만 있는 날도 옷만 있는 날도 유효하다.
 *
 * **서버가 진실이다.** 다만 서버 스키마에 자리가 없는 개념이 셋 있다 —
 * 친구 공개(`shared`), 룩북 연결(`lookId`), 그리고 내 옷장이 아닌 옷(친구 옷장·앱 카탈로그).
 * 이것들은 날짜별 로컬 오버레이에 둔다. 전에도 스토어 전체가 메모리였으니 세션 한정인 건
 * 그대로고, 서버가 가진 것과 프론트에만 있는 것이 섞이지 않는다는 점만 달라졌다.
 */

export type EntryItem = {
  id: string;
  source: WardrobeSource;
  name: string;
  image?: string;
  /** 친구 옷장에서 가져온 옷의 주인 */
  owner?: string;
  /**
   * 이 옷이 내 옷장에 들어 있는가.
   * 룩 사진에서 뽑힌 옷은 사용자가 '옷장에 추가'를 누르기 전까지 false 다 —
   * 그때만 룩 상세가 추가 버튼을 그린다. (서버 added_to_closet_at)
   */
  inCloset?: boolean;
};

export type CalendarEntry = {
  /** 서버 기록 id(UUID). 수정·삭제에 쓴다. */
  id: string;
  /** 'YYYY-MM-DD' */
  date: string;
  photo?: string;
  items: EntryItem[];
  /**
   * 그날 무슨 일정이었는지 — '팀 회의', '친구 결혼식'처럼 자유롭게 적는다.
   * 서버의 `schedule` 이다. 해시태그와 따로 두는 이유: 태그는 고르는 것이고
   * 일정은 그날에만 있는 사실이라 남의 태그 체계에 끼워 맞출 수 없다.
   */
  note?: string;
  tags: AllowedHashtag[];
  /** 사진 등록은 옷 추출이 끝나야 COMPLETED 다. 옷만 고른 기록은 처음부터 완료. */
  status: CalendarEntryDto['status'];
  /** 함께 쓰는 옷장 친구에게 공개 여부 — 서버에 자리가 없어 로컬 전용 */
  shared: boolean;
  /** 같이 만들어진 룩북 룩(state/saved.ts SavedLook.id) — 서버에 자리가 없어 로컬 전용 */
  lookId?: string;
  /** 외부 공유 링크용 코드 — 기록당 한 번 만들어 고정한다(링크가 매번 바뀌면 안 되므로) */
  shareCode: string;
  updatedAt: number;
};

const WEEKDAY_LABELS = ['일', '월', '화', '수', '목', '금', '토'];

const pad = (n: number) => String(n).padStart(2, '0');

/** (2026, 7, 8) → '2026-07-08' */
export function toDateKey(year: number, month: number, day: number): string {
  return `${year}-${pad(month)}-${pad(day)}`;
}

export function todayKey(): string {
  const now = new Date();
  return toDateKey(now.getFullYear(), now.getMonth() + 1, now.getDate());
}

/** '2026-07-08' → { year, month, day } */
export function parseDateKey(key: string) {
  const [year, month, day] = key.split('-').map(Number);
  return { year, month, day };
}

/** '2026-07-08' → '7월 8일 (수)' */
export function formatDateLabel(key: string): string {
  const { year, month, day } = parseDateKey(key);
  const weekday = WEEKDAY_LABELS[new Date(year, month - 1, day).getDay()];
  return `${month}월 ${day}일 (${weekday})`;
}

function makeShareCode(): string {
  return Math.random().toString(36).slice(2, 10);
}

/** 공유 링크 — 친구가 링크만으로 이 착장을 볼 수 있는 주소(백엔드 연동 전 목업 도메인) */
export function lookShareUrl(code: string): string {
  return `https://cozy.app/look/${code}`;
}

/** 옷장/카탈로그 아이템 → 기록에 담기는 형태 */
export function toEntryItem(item: WardrobeItem, source: WardrobeSource): EntryItem {
  return { id: item.id, source, name: item.name, image: item.image, owner: item.owner };
}

/** 기록 안에서 옷을 구분하는 키 — 소스가 다르면 id 가 겹쳐도 다른 옷이다 */
export function entryItemKey(item: { source: WardrobeSource; id: string }): string {
  return `${item.source}:${item.id}`;
}

/** 캘린더 API는 개인 옷 FK만 받는다. 공유 옷·상품을 기록하는 쓰기 계약은 아직 없다. */
function isServerItem(item: EntryItem): boolean {
  return item.source === 'closet';
}

/** 서버 스키마에 자리가 없어 날짜별로 따로 들고 있는 것들 */
type Overlay = {
  shared: boolean;
  lookId?: string;
  /** 내 옷장이 아니라 서버로 못 보낸 옷 */
  localItems: EntryItem[];
  shareCode: string;
};

let entries: Record<string, CalendarEntry> = {};
const overlays: Record<string, Overlay> = {};
/* 빈 날 인사이트에서 옷을 눌러 기록 화면을 열 때 그 옷을 실어 보내는 자리.
   URL 파라미터로 옷 전체를 넘기면 이름·사진까지 붙어 지저분해진다(draft-item.ts 와 같은 방식). */
let seededItems: EntryItem[] | null = null;

const listeners = new Set<() => void>();

function notify() {
  entries = { ...entries };
  listeners.forEach((l) => l());
}

/** 사진에서 옷을 뽑아내는 건 GPU 파이프라인이라 몇 분이 걸린다. */
const PROCESSING_POLL_MS = 5_000;
/** 무한 폴링 방지 상한. 여기 걸리면 지켜보기를 접고, 다음에 달을 불러올 때 다시 본다. */
const MAX_PROCESSING_POLL_MS = 10 * 60 * 1000;

/** 지금 지켜보고 있는 기록 id — 같은 기록에 감시자가 둘 붙지 않게 한다. */
const watching = new Set<string>();

function isProcessing(status: CalendarEntryDto['status']): boolean {
  return status === 'REGISTERED' || status === 'PROCESSING';
}

/**
 * 사진 등록 뒤 옷 추출이 끝날 때까지 지켜본다.
 *
 * 화면이 아니라 스토어가 맡는 이유: 저장하고 캘린더로 돌아가는 게 정상 흐름이라
 * 화면에 걸어두면 그 화면을 벗어나는 순간 추적이 끊긴다. 끝나면 기록을 다시 받아
 * 담긴 옷을 채워 넣는다.
 */
function watchProcessing(calendarId: string, date: string) {
  if (watching.has(calendarId)) return;
  watching.add(calendarId);
  const startedAt = Date.now();

  const tick = async () => {
    // 지켜보는 사이에 지워졌거나 다른 기록으로 바뀌었으면 그만둔다.
    if (entries[date]?.id !== calendarId) {
      watching.delete(calendarId);
      return;
    }
    if (Date.now() - startedAt > MAX_PROCESSING_POLL_MS) {
      watching.delete(calendarId);
      return;
    }

    try {
      const status = await getCalendarProcessingStatus(calendarId);
      if (status.is_terminal) {
        watching.delete(calendarId);
        // 상태만으로는 어떤 옷이 나왔는지 모른다 — 기록을 다시 받아야 목록이 채워진다.
        const fresh = await getCalendarEntry(calendarId);
        if (entries[date]?.id === calendarId) {
          entries[date] = toEntry(fresh);
          notify();
        }
        return;
      }
      // 상태 문구가 REGISTERED → PROCESSING 으로 바뀌는 것도 화면에 비친다.
      const current = entries[date];
      if (current && current.status !== status.status) {
        entries[date] = { ...current, status: status.status };
        notify();
      }
    } catch {
      // 일시적인 실패로 추적을 끝내지 않는다 — 다음 회차에 복구된다.
    }
    setTimeout(() => void tick(), PROCESSING_POLL_MS);
  };

  setTimeout(() => void tick(), PROCESSING_POLL_MS);
}

function overlayFor(date: string): Overlay {
  overlays[date] ??= { shared: false, localItems: [], shareCode: makeShareCode() };
  return overlays[date];
}

/**
 * 기록을 지우지 않고 고칠 수 있는 변경인가 — **사진이 그대로면** 그렇다.
 *
 * 옷을 더한 것은 연결 추가 API로, 뺀 것은 연결 해제 API로, 메타데이터는 PATCH 로
 * 끝난다. 오직 사진이 바뀔 때만 삭제 후 재등록이 필요하다(서버에 사진 교체가 없다).
 * 예전에는 옷을 더하는 것도 삭제 후 재등록이었는데, 사진 기록에서는 그것이 곧 같은
 * 사진의 재분석이라 같은 옷이 서로 다른 두 벌로 옷장에 쌓였다.
 */
function canEditInPlace(prev: CalendarEntry, photo: string | undefined): boolean {
  return (prev.photo ?? '') === (photo ?? '');
}

/** 서버에 자리가 없는 것들을 날짜별 오버레이에 반영한다. */
function applyOverlay(input: {
  date: string;
  items: EntryItem[];
  shared: boolean;
  lookId?: string;
}) {
  const overlay = overlayFor(input.date);
  overlay.shared = input.shared;
  overlay.lookId = input.lookId ?? overlay.lookId;
  overlay.localItems = input.items.filter((item) => !isServerItem(item));
}

/** 서버 응답 + 로컬 오버레이 → 화면이 쓰는 기록 */
function toEntry(dto: CalendarEntryDto): CalendarEntry {
  const overlay = overlayFor(dto.date);
  const serverItems: EntryItem[] = dto.wardrobe_items.map((link) => ({
    id: link.wardrobe_item_id,
    source: 'closet',
    name: (link.snapshot.item_name as string) || '이름 없는 아이템',
    image: link.image_url || undefined,
  }));

  return {
    id: dto.id,
    date: dto.date,
    photo: dto.image_url || undefined,
    items: [...serverItems, ...overlay.localItems],
    note: dto.schedule || undefined,
    tags: dto.hashtags as AllowedHashtag[],
    status: dto.status,
    shared: overlay.shared,
    lookId: overlay.lookId,
    shareCode: overlay.shareCode,
    updatedAt: Date.parse(dto.updated_at) || 0,
  };
}

export const calendarStore = {
  getEntries: () => entries,
  getEntry: (date: string): CalendarEntry | undefined => entries[date],

  /** 기간(보통 한 달)을 서버에서 받아 반영한다. 그 기간에 없는 날은 지운다. */
  async loadRange(startDate: string, endDate: string): Promise<void> {
    const list = await listCalendarEntries(startDate, endDate);
    const loaded = new Set(list.map((dto) => dto.date));
    for (const date of Object.keys(entries)) {
      if (date >= startDate && date <= endDate && !loaded.has(date)) delete entries[date];
    }
    for (const dto of list) entries[dto.date] = toEntry(dto);
    notify();
    /* 앱을 껐다 켰거나 한참 만에 들어오면 아직 처리 중인 기록이 있을 수 있다 —
       그때도 스스로 채워지도록 여기서 다시 지켜보기를 건다. */
    for (const dto of list) {
      if (isProcessing(dto.status)) watchProcessing(dto.id, dto.date);
    }
  },

  /**
   * 기록 저장. 사진이 있으면 사진 경로로, 없으면 옷장 경로로 등록한다.
   *
   * 수정은 되도록 기록을 지우지 않고 처리한다 — 옷 빼기는 연결 해제, 메타는 PATCH.
   * 사진이 바뀌거나 옷이 **추가**될 때만 지우고 다시 만든다(서버에 그 경로가 없다).
   * 그 경우엔 기록 id 가 새로 발급된다(처리 중인 기록은 서버가 삭제를 409 로 막는다).
   */
  async saveEntry(input: {
    date: string;
    photo?: string;
    items: EntryItem[];
    note?: string;
    tags: AllowedHashtag[];
    shared: boolean;
    lookId?: string;
  }): Promise<CalendarEntry> {
    const serverItems = input.items.filter(isServerItem);

    if (!input.photo && serverItems.length === 0) {
      throw new Error('사진을 넣거나 내 옷장에서 옷을 골라주세요.');
    }

    const meta = {
      schedule: input.note?.trim() || '',
      hashtags: input.tags,
    };
    const wardrobeItemIds = serverItems.map((item) => item.id);

    /* 메모리에 없어도 서버에는 있을 수 있다(다른 달 날짜). 확인하지 않고 만들면 409 다. */
    const prev = await calendarStore.findEntry(input.date);

    /* 기록을 지우지 않고 고칠 수 있으면 절대 지우지 않는다.
       - 옷을 빼고 더한 것: 연결 해제·추가 API 가 **연결만** 손댄다. 옷장 아이템은
         서버에 그대로 남고, 기록 id 와 사진도 바뀌지 않는다.
       - 일정·해시태그: PATCH.
       예전에는 옷 하나만 빼도 삭제 후 재등록이었다 — 재등록이 실패하면 기록이
       통째로 사라져, '입은 옷 빼기'가 기록 삭제가 되는 사고가 있었다. */
    if (prev && canEditInPlace(prev, input.photo)) {
      const keptIds = new Set(serverItems.map((item) => item.id));
      const prevIds = new Set(prev.items.filter(isServerItem).map((item) => item.id));
      const removedIds = [...prevIds].filter((id) => !keptIds.has(id));
      const addedIds = serverItems.map((item) => item.id).filter((id) => !prevIds.has(id));
      /* 연결 변경을 먼저, PATCH 를 마지막에 — 마지막 응답이 최신 기록 전체라
         중간에 화면을 갱신할 필요가 없다. 도중에 실패해도 기록은 살아 있고,
         다시 저장하면 남은 것부터 이어진다.
         빼기를 먼저 하는 이유: 옷을 통째로 바꾸는 경우 먼저 더하면 잠깐 두 배로
         늘어난 상태가 서버에 남는다. */
      for (const wardrobeItemId of removedIds) {
        await unlinkCalendarItem(prev.id, wardrobeItemId);
      }
      if (addedIds.length > 0) await linkCalendarItems(prev.id, addedIds);
      const dto = await patchCalendarEntry(prev.id, meta);
      applyOverlay(input);
      const patched = toEntry(dto);
      entries[input.date] = patched;
      notify();
      return patched;
    }

    // 같은 날짜에 기록이 있으면 서버가 409 로 막는다 — 사진이 바뀔 때만 여기로
    // 온다(서버에 사진 교체가 없다). 지우고 다시 만든다.
    if (prev) await deleteCalendarEntry(prev.id);

    const dto = input.photo
      ? await createCalendarFromPhoto({
          date: input.date,
          photoUri: input.photo,
          wardrobeItemIds,
          ...meta,
        })
      : await createCalendarFromWardrobe({
          date: input.date,
          wardrobeItemIds,
          ...meta,
        });

    applyOverlay(input);

    const next = toEntry(dto);
    entries[input.date] = next;
    notify();
    /* 사진 등록은 202 로 돌아오고 옷 목록이 비어 있다 — 추출이 끝나면 채워 넣는다. */
    if (isProcessing(dto.status)) watchProcessing(dto.id, dto.date);
    return next;
  },

  /**
   * 룩북 등록이 **함께 만든** 캘린더 기록을 스토어에 들인다.
   *
   * 캘린더 화면에서 '룩북에도 올리기'를 켜면 등록은 룩북 API 한 번으로 끝난다
   * (calendar_date). 그 응답은 룩 기준이라 캘린더 화면이 쓸 기록은 여기서 다시 받는다.
   * 서버에 자리가 없는 값(친구 공개·룩 연결·내 옷장 밖 옷)은 saveEntry 와 같은
   * 오버레이에 남긴다.
   */
  async adoptLinkedEntry(input: {
    date: string;
    items: EntryItem[];
    shared: boolean;
    lookId?: string;
  }): Promise<CalendarEntry | undefined> {
    applyOverlay(input);
    const dto = await getCalendarEntryByDate(input.date);
    if (!dto) return undefined;

    const next = toEntry(dto);
    entries[input.date] = next;
    notify();
    /* 사진 등록은 202 로 돌아오고 옷 목록이 비어 있다 — 추출이 끝나면 채워 넣는다. */
    if (isProcessing(dto.status)) watchProcessing(dto.id, dto.date);
    return next;
  },

  async removeEntry(date: string): Promise<void> {
    const entry = entries[date];
    if (!entry) return;
    await deleteCalendarEntry(entry.id);
    delete entries[date];
    delete overlays[date];
    notify();
  },

  /**
   * 그 날짜의 기록을 찾는다 — 메모리에 없으면 서버까지 확인한다.
   *
   * 스토어에는 보고 있는 달만 올라와 있어서, 룩북에서 다른 달 날짜를 고르면
   * 기록이 있는데도 없는 것처럼 보인다. 그대로 저장하면 서버가 409 로 막는다.
   */
  async findEntry(date: string): Promise<CalendarEntry | undefined> {
    const known = entries[date];
    if (known) return known;
    const dto = await getCalendarEntryByDate(date);
    if (!dto) return undefined;
    entries[date] = toEntry(dto);
    notify();
    return entries[date];
  },

  /** 기록 화면을 열면서 미리 담아둘 옷을 넘긴다. */
  seedItems(items: EntryItem[]) {
    seededItems = items;
  },

  /** 담아둔 옷을 꺼낸다. 한 번 쓰면 비운다 — 다음에 빈손으로 열었을 때 남아 있으면 안 된다. */
  takeSeededItems(): EntryItem[] | null {
    const taken = seededItems;
    seededItems = null;
    return taken;
  },

  /** 친구 공개 여부 — 서버에 자리가 없어 로컬에만 남는다. */
  setShared(date: string, shared: boolean) {
    const prev = entries[date];
    if (!prev) return;
    overlayFor(date).shared = shared;
    entries[date] = { ...prev, shared };
    notify();
  },

  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};

/** 등록 실패를 사용자 문구로 — 날짜 충돌만 따로 짚어준다. */
export function calendarErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    return '그 날짜에 이미 기록이 있어요. 새로고침한 뒤 다시 시도해 주세요.';
  }
  if (error instanceof ApiError && error.status === 413) {
    return '사진 용량이 너무 커요. 15MB 이하로 올려주세요.';
  }
  /* DRF 검증 오류는 `{ 필드: [설명] }` 로 온다. apiClient 는 detail/message 만 보고
     "요청 실패 (400)" 으로 뭉개므로, 어느 필드가 왜 거절됐는지는 여기서 풀어준다. */
  if (error instanceof ApiError && error.data && typeof error.data === 'object') {
    const fields = Object.entries(error.data as Record<string, unknown>)
      .filter(([key]) => key !== 'detail' && key !== 'message')
      .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(' ') : String(value)}`);
    if (fields.length > 0) return fields.join('\n');
  }
  return error instanceof Error && error.message
    ? error.message
    : '기록을 저장하지 못했어요. 잠시 후 다시 시도해 주세요.';
}

export function useCalendarEntries(): Record<string, CalendarEntry> {
  return useSyncExternalStore(
    calendarStore.subscribe,
    calendarStore.getEntries,
    calendarStore.getEntries,
  );
}

export function useCalendarEntry(date: string): CalendarEntry | undefined {
  return useCalendarEntries()[date];
}
