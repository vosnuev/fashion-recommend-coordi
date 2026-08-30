import { useSyncExternalStore } from 'react';

import { getWishlist as readStored, saveWishlist as writeStored } from '@/lib/secureStore';

/**
 * 위시(룩) 스토어.
 *
 * 둘러보기 피드에서 하트로 담아 둔 룩이다. 내 룩북 = [올린 룩][위시] 중 오른쪽 갈래이며,
 * 추천에서 저장한 룩(saved.ts)과 화면에서 한 목록으로 합쳐 보인다.
 *
 * ⚠️ 서버에 자리가 없다(`/api/v1/likes` 부재). 기기에 저장하므로 계정이 아니라 기기에 붙는다.
 */

/** 둘러보기 피드 룩 위시(하트) */
export type LikedLook = {
  /** 피드 룩의 id (state/lookbook.ts LookPost.id) */
  id: string;
  image?: string;
  tags: string[];
  likedAt: number;
};

let likedLooks: LikedLook[] = [];

const listeners = new Set<() => void>();
/* 바뀔 때마다 기기에 적는다. 저장은 기다리지 않는다 — 화면은 이미 새 값을 그렸고,
   저장이 늦거나 실패해도 이번 세션의 동작은 달라지지 않는다. */
const notify = () => {
  listeners.forEach((l) => l());
  void persist();
};

async function persist(): Promise<void> {
  try {
    await writeStored(JSON.stringify({ likedLooks }));
  } catch {
    // 저장 실패는 조용히 넘긴다(다음 변경 때 다시 시도된다)
  }
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export const likesStore = {
  /** 앱이 뜰 때 한 번 — 기기에 적어 둔 위시를 되살린다. 이미 담긴 게 있으면 덮어쓰지 않는다. */
  async bootstrap(): Promise<void> {
    if (likedLooks.length > 0) return;
    try {
      const raw = await readStored();
      if (!raw || likedLooks.length > 0) return;
      const saved = JSON.parse(raw) as { likedLooks?: LikedLook[] };
      likedLooks = Array.isArray(saved.likedLooks) ? saved.likedLooks : [];
      listeners.forEach((l) => l());
    } catch {
      // 저장값이 깨졌으면 빈 목록으로 시작한다
    }
  },

  /* ── 좋아요 (룩) ── */
  getLikedLooks: () => likedLooks,
  isLiked: (id: string) => likedLooks.some((l) => l.id === id),
  /** 켜면 true, 끄면 false 를 돌려준다 — 호출부가 토스트 문구를 고를 수 있게. */
  toggleLook(look: { id: string; image?: string; tags?: string[] }): boolean {
    if (likedLooks.some((l) => l.id === look.id)) {
      likedLooks = likedLooks.filter((l) => l.id !== look.id);
      notify();
      return false;
    }
    likedLooks = [
      { id: look.id, image: look.image, tags: look.tags ?? [], likedAt: Date.now() },
      ...likedLooks,
    ];
    notify();
    return true;
  },

  subscribe,
};

export function useLikedLooks() {
  return useSyncExternalStore(subscribe, likesStore.getLikedLooks, likesStore.getLikedLooks);
}
