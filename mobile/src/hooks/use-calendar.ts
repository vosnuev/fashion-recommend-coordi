import { useCallback, useEffect, useState } from 'react';

import { listCalendarEntries } from '@/lib/calendarApi';
import { calendarStore, toDateKey, useCalendarEntries, type CalendarEntry } from '@/state/calendar';

/**
 * 캘린더 데이터 훅 — "언제 불러올지"와 로딩·오류 표시를 맡는다.
 * 전송은 lib/calendarApi.ts, 기록 보관과 사진 처리 추적은 state/calendar.ts.
 * useWardrobeItems 와 같은 모양({ ..., loading, error, reload })을 유지한다.
 */

type RangeResult = {
  /** 날짜('YYYY-MM-DD') → 기록. 하루에 하나뿐이라 맵으로 둔다. */
  entries: Record<string, CalendarEntry>;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
};

/** 달의 1일과 말일을 'YYYY-MM-DD' 로 돌려준다. */
export function monthRange(year: number, month: number): { start: string; end: string } {
  const lastDay = new Date(year, month, 0).getDate();
  return { start: toDateKey(year, month, 1), end: toDateKey(year, month, lastDay) };
}

/**
 * 기간을 서버에서 받아 스토어에 채운다.
 *
 * 기록은 캘린더·날짜선택 시트·룩 작성기가 함께 보므로 데이터는 스토어가 들고,
 * 이 훅은 "언제 불러올지"와 로딩·오류 표시만 맡는다.
 */
export function useCalendarRange(
  startDate: string,
  endDate: string,
  enabled = true,
): RangeResult {
  const entries = useCalendarEntries();
  // 끄고 시작하면 첫 화면이 로딩으로 깜빡이지 않는다(비회원 등).
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!enabled) return;
    try {
      await calendarStore.loadRange(startDate, endDate);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : '캘린더를 불러오지 못했어요');
    } finally {
      setLoading(false);
    }
  }, [startDate, endDate, enabled]);

  useEffect(() => {
    /* 마운트 시 데이터 가져오기 — 상태는 응답이 온 뒤에 바뀐다(렌더 중 갱신이 아니다).
       use-wardrobe.ts 도 같은 형태. */
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  const reload = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    await load();
  }, [load, enabled]);

  return { entries, loading, error, reload };
}

/** 월 그리드용 — 그 달 1일부터 말일까지. */
export function useCalendarMonth(year: number, month: number, enabled = true): RangeResult {
  const { start, end } = monthRange(year, month);
  return useCalendarRange(start, end, enabled);
}

/** 입었던 옷을 셀 때 돌아보는 기간. 계절이 바뀌면 옷도 바뀌므로 너무 길게 잡지 않는다. */
const FREQUENT_WINDOW_DAYS = 90;

export type FrequentItem = {
  id: string;
  name: string;
  image?: string;
  count: number;
  /** 마지막으로 입은 날('YYYY-MM-DD'). 한 번만 입은 옷은 횟수 대신 이 날짜를 보여준다. */
  lastWorn: string;
};

type FrequentResult = {
  items: FrequentItem[];
  loading: boolean;
};

/**
 * 최근에 입었던 옷 몇 개.
 *
 * 빈 날에 "그날 뭘 입었는지" 채워 넣는 지름길로 쓴다 — 날짜가 비는 이유는 정보가 없어서가
 * 아니라 기록이 귀찮아서라, 읽을거리보다 입력을 줄이는 쪽이 쓸모 있다.
 *
 * 기록 수로 막지 않는다. 한 벌만 입었어도 그 한 벌이 다음 기록의 지름길이 되고,
 * 기다리게 하면 기록이 쌓일 일 자체가 없다. 대신 **부르는 이름을 바꾼다** —
 * 두 번 이상 입은 옷이 있어야 '자주', 아니면 '최근에'다(화면 쪽에서 판단).
 *
 * 스토어를 거치지 않고 따로 조회한다. 스토어는 보고 있는 달만 담는데 빈도는 더 긴 기간을
 * 봐야 하고, 여기서 스토어를 채우면 달 이동과 서로 덮어쓴다.
 */
export function useFrequentItems(enabled = true, topN = 3): FrequentResult {
  const [items, setItems] = useState<FrequentItem[]>([]);
  const [loading, setLoading] = useState(enabled);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;

    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - FREQUENT_WINDOW_DAYS);
    const key = (d: Date) => toDateKey(d.getFullYear(), d.getMonth() + 1, d.getDate());

    listCalendarEntries(key(start), key(end))
      .then((list) => {
        if (!alive) return;
        const counts = new Map<string, FrequentItem>();
        for (const entry of list) {
          for (const link of entry.wardrobe_items) {
            const prev = counts.get(link.wardrobe_item_id);
            if (prev) {
              prev.count += 1;
              // 같은 옷이 여러 날에 걸쳐 있으면 가장 나중 날을 남긴다.
              if (entry.date > prev.lastWorn) prev.lastWorn = entry.date;
              continue;
            }
            counts.set(link.wardrobe_item_id, {
              id: link.wardrobe_item_id,
              name: (link.snapshot.item_name as string) || '이름 없는 아이템',
              image: link.image_url || undefined,
              count: 1,
              lastWorn: entry.date,
            });
          }
        }
        /* 많이 입은 순. 횟수가 같으면 최근에 입은 것이 앞선다 —
           한 번씩만 입은 옷들뿐일 때 순서가 뒤죽박죽으로 보이지 않게. */
        setItems(
          [...counts.values()]
            .sort((a, b) => b.count - a.count || b.lastWorn.localeCompare(a.lastWorn))
            .slice(0, topN),
        );
      })
      // 인사이트는 곁다리라 실패해도 화면에 오류를 띄우지 않는다 — 조용히 감춘다.
      .catch(() => {})
      .finally(() => {
        if (alive) setLoading(false);
      });

    return () => {
      alive = false;
    };
  }, [enabled, topN]);

  return { items, loading };
}
