import { useCallback, useEffect, useState } from 'react';

import {
  createWardrobeHashtag,
  deleteWardrobeItem,
  getWardrobeItem,
  listWardrobeFilters,
  listWardrobeItems,
  patchWardrobeItem,
  type WardrobeFiltersResponse,
  type WardrobeApiItem,
  type WardrobeItemPatch,
  type WardrobeItemQuery,
} from '@/lib/wardrobeApi';

/**
 * 옷장 데이터 훅. 전송은 lib/wardrobeApi.ts, 상태·폴링은 여기.
 * useHome 과 같은 모양({ data, loading, error, reload })을 유지한다.
 */

type ItemsResult = {
  items: WardrobeApiItem[];
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
  /** 로딩 표시 없이 다시 불러온다 — 이미 그려진 목록을 깜빡이지 않고 서버와 맞춘다 */
  refresh: () => Promise<void>;
  /** 서버 왕복 없이 목록에서 지운다 — 삭제 직후 화면이 먼저 반응하도록 */
  removeLocal: (itemId: string) => void;
  /** 수정 결과를 목록에 반영 */
  replaceLocal: (item: WardrobeApiItem) => void;
};

export function useWardrobeItems(query: WardrobeItemQuery = {}, enabled = true): ItemsResult {
  const [items, setItems] = useState<WardrobeApiItem[]>([]);
  // 끄고 시작하면 첫 화면이 로딩으로 깜빡이지 않는다(비회원 등).
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);

  /* 첫 setState 를 await 뒤로 미룬다 — effect 안에서 동기적으로 상태를 바꾸면
     렌더 중 갱신이 되어 react-hooks 규칙에 걸리고, 한 프레임 낭비된다. */
  const load = useCallback(async () => {
    if (!enabled) return;
    try {
      const next = await listWardrobeItems(query);
      setItems(next);
      setError(null);
    } catch (e) {
      setItems([]);
      setError(e instanceof Error ? e.message : '옷장을 불러오지 못했어요');
    } finally {
      setLoading(false);
    }
    // 객체 참조가 아니라 실제 조건값이 바뀔 때만 재요청
  }, [query.category_large, query.confirmed, enabled]);

  useEffect(() => {
    /* 마운트 시 데이터 가져오기 — 규칙은 load() 안의 setState 를 정적으로 잡지만,
       상태는 응답이 온 뒤에 바뀐다(렌더 중 갱신이 아니다). use-home.ts 도 같은 형태. */
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  /** 다시 시도 — 사용자 조작에서 부르므로 여기선 로딩 표시를 켜도 된다. */
  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    await load();
  }, [load]);

  const removeLocal = useCallback((itemId: string) => {
    setItems((prev) => prev.filter((i) => i.id !== itemId));
  }, []);

  const replaceLocal = useCallback((item: WardrobeApiItem) => {
    setItems((prev) => prev.map((i) => (i.id === item.id ? item : i)));
  }, []);

  return { items, loading, error, reload, refresh: load, removeLocal, replaceLocal };
}

type CategoriesResult = {
  data: WardrobeFiltersResponse | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<WardrobeFiltersResponse | null>;
};

/** 기본 카테고리와 개인 옷장 해시태그는 서버 응답으로 매번 복원한다. */
export function useWardrobeFilters(enabled = true): CategoriesResult {
  const [data, setData] = useState<WardrobeFiltersResponse | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (): Promise<WardrobeFiltersResponse | null> => {
    if (!enabled) return null;
    try {
      const next = await listWardrobeFilters();
      setData(next);
      setError(null);
      return next;
    } catch (e) {
      setError(e instanceof Error ? e.message : '카테고리를 불러오지 못했어요');
      return null;
    } finally {
      setLoading(false);
    }
  }, [enabled]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    return load();
  }, [load]);

  return { data, loading, error, reload };
}

type ItemResult = {
  item: WardrobeApiItem | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
  /** 수정 결과를 화면에 즉시 반영 */
  setItem: (item: WardrobeApiItem) => void;
};

/** 아이템 한 벌. 상세 화면에서 쓴다. */
export function useWardrobeItem(itemId: string | undefined): ItemResult {
  const [item, setItem] = useState<WardrobeApiItem | null>(null);
  const [loading, setLoading] = useState(Boolean(itemId));
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!itemId) {
      setError('아이템을 찾을 수 없어요');
      setLoading(false);
      return;
    }
    try {
      setItem(await getWardrobeItem(itemId));
      setError(null);
    } catch (e) {
      setItem(null);
      setError(e instanceof Error ? e.message : '아이템을 불러오지 못했어요');
    } finally {
      setLoading(false);
    }
  }, [itemId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    await load();
  }, [load]);

  return { item, loading, error, reload, setItem };
}

/**
 * 태그 확인·수정 후 확정. 화면에서 바로 쓰도록 얇게 감쌌다.
 *
 * 공유 예약 소진은 **서버가 같은 요청 안에서** 한다 — 등록할 때 '공유 옷장에 공유'를
 * 켠 옷은 그때는 미확정이라 거부됐고, 확정된 지금이 유일하게 공유 가능한 시점이다.
 * 예전엔 이 예약을 기기(secureStore)에 들고 있어서 PC 에서 올리고 폰에서 확정하면
 * 공유가 통째로 사라졌다. 지금은 `wardrobe_item.pending_share_room` 이 들고 있다.
 *
 * 공유는 곁가지라 실패해도 확정 결과를 그대로 돌려준다 (`sharedRoomId`가 null일 뿐).
 */
export async function confirmWardrobeItem(
  itemId: string,
  patch: WardrobeItemPatch = {},
): Promise<{ item: WardrobeApiItem; sharedRoomId: string | null }> {
  const item = await patchWardrobeItem(itemId, { ...patch, confirmed: true });
  return { item, sharedRoomId: item.shared_room_id ?? null };
}

export {
  createWardrobeHashtag,
  deleteWardrobeItem,
  patchWardrobeItem,
};
