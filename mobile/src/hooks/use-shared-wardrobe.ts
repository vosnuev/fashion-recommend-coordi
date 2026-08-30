/**
 * 공유 옷장(친구 옷) 아이템 목록.
 *
 * 옷장 화면은 방을 칩으로 골라 한 방씩 보지만, 여기(옷 고르기)는 고를 자리가 없다 —
 * 한 벌을 꾸리는 동안 방을 오가게 하느니 **참여한 방의 옷을 모두 합쳐** 보여준다.
 */
import { useCallback, useEffect, useState } from 'react';

import type { WardrobeItem } from '@/constants/wardrobe';
import {
  getMySharedRooms,
  listSharedRoomItems,
  sharedUserDisplayName,
  type SharedRoomItem,
} from '@/lib/wardrobeApi';
import { useAuth } from '@/state/auth';

export type SharedWardrobeResult = {
  items: WardrobeItem[];
  loading: boolean;
  error: string | null;
  /** 참여한 공유 옷장이 하나도 없는 상태 — '옷이 없음'과 문구가 달라야 한다 */
  hasRoom: boolean;
  reload: () => void;
};

export function useSharedWardrobeItems(enabled = true): SharedWardrobeResult {
  const { user: me } = useAuth();
  const myId = me?.id;
  const [items, setItems] = useState<WardrobeItem[]>([]);
  const [hasRoom, setHasRoom] = useState(false);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setError(null);

    (async () => {
      const rooms = await getMySharedRooms();
      if (!alive) return;
      setHasRoom(rooms.length > 0);
      if (rooms.length === 0) return [];
      /* 방 하나가 실패하면 통째로 오류로 본다 — 일부만 빠진 목록은
         "친구 옷이 사라졌다"로 읽혀 더 헷갈린다. */
      const perRoom = await Promise.all(rooms.map((room) => listSharedRoomItems(room.id)));
      return perRoom.flat();
    })()
      .then((list) => {
        if (!alive || !list) return;
        setItems(list.map((shared) => toPickerItem(shared, myId)));
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setError(e instanceof Error ? e.message : '공유 옷장을 불러오지 못했어요');
      })
      .finally(() => {
        if (alive) setLoading(false);
      });

    return () => {
      alive = false;
    };
  }, [enabled, myId, tick]);

  return { items, loading, error, hasRoom, reload };
}

/** 공유 아이템 → 시트가 그리는 모양. id 는 옷 자체(wardrobe_item)의 것을 쓴다. */
function toPickerItem(shared: SharedRoomItem, myId: number | undefined): WardrobeItem {
  const mine = shared.registered_by?.id === myId;
  return {
    id: shared.wardrobe_item.id,
    name: shared.wardrobe_item.item_name || '옷',
    category: shared.wardrobe_item.category_large,
    // tone 은 사진이 없을 때의 대체 면 색. 공유 아이템은 사진이 있어 쓰이지 않는다.
    tone: 0.12,
    image: shared.wardrobe_item.image_url,
    owner: mine ? '나' : shared.registered_by ? sharedUserDisplayName(shared.registered_by) : undefined,
  };
}
