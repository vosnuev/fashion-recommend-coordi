import { useCallback, useEffect, useRef, useState } from 'react';

import {
  HISTORY_PAGE_SIZE,
  listOutfitAnalyses,
  type OutfitAnalysisListItem,
} from '@/lib/outfitHistoryApi';

type OutfitHistoryResult = {
  items: OutfitAnalysisListItem[];
  /** 서버가 알려준 전체 개수 — 화면 상단 "N건" 표기에 쓴다 */
  total: number;
  hasMore: boolean;
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
  reload: () => Promise<void>;
  loadMore: () => Promise<void>;
};

/**
 * 착장 분석 기록 목록 훅. offset 페이지네이션이라 "더 보기"로 이어 붙인다.
 *
 * 목록 API 는 JWT 필수라 비회원은 호출 자체를 하지 않는다(enabled=false → 빈 목록 그대로).
 * 최신순 정렬은 백엔드 모델 Meta.ordering 이 보장한다.
 */
export function useOutfitHistory(enabled = true): OutfitHistoryResult {
  const [items, setItems] = useState<OutfitAnalysisListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(enabled);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /* 다음 페이지 offset 은 지금까지 받은 개수다. loadMore 가 매번 새로 만들어지지 않도록
     state 대신 ref 로 읽는다(콜백이 옛 값을 붙잡고 있으면 같은 페이지를 다시 부른다). */
  const loadedRef = useRef(0);

  const load = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    setError(null);
    try {
      const res = await listOutfitAnalyses({ limit: HISTORY_PAGE_SIZE });
      setItems(res.results);
      setTotal(res.count);
      loadedRef.current = res.results.length;
    } catch (e) {
      setItems([]);
      setTotal(0);
      loadedRef.current = 0;
      setError(e instanceof Error ? e.message : '분석 기록을 불러오지 못했어요');
    } finally {
      setLoading(false);
    }
  }, [enabled]);

  const loadMore = useCallback(async () => {
    if (!enabled || loadingMore) return;
    setLoadingMore(true);
    try {
      const res = await listOutfitAnalyses({
        limit: HISTORY_PAGE_SIZE,
        offset: loadedRef.current,
      });
      /* 보는 중에 새 분석이 생기면 offset 이 밀려 같은 건이 다시 올 수 있다 — id 로 걸러낸다. */
      setItems((prev) => {
        const seen = new Set(prev.map((item) => item.id));
        const merged = [...prev, ...res.results.filter((item) => !seen.has(item.id))];
        loadedRef.current = merged.length;
        return merged;
      });
      setTotal(res.count);
    } catch (e) {
      setError(e instanceof Error ? e.message : '기록을 더 불러오지 못했어요');
    } finally {
      setLoadingMore(false);
    }
  }, [enabled, loadingMore]);

  useEffect(() => {
    load();
  }, [load]);

  return {
    items,
    total,
    hasMore: items.length < total,
    loading,
    loadingMore,
    error,
    reload: load,
    loadMore,
  };
}
