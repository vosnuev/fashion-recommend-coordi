import { useSyncExternalStore } from 'react';

import { getLookVotes as readStored, saveLookVotes as writeStored } from '@/lib/secureStore';

/**
 * 룩에 남긴 평가(좋아요 / 별로예요).
 *
 * 예전에는 상세 화면의 지역 상태(useState)라, 뒤로 나갔다 들어오면 사라졌고
 * 목록은 내가 뭘 별로라 했는지 알 수가 없었다. 목록 정렬에 쓰려면 화면 밖에 있어야 한다.
 *
 * ⚠️ 서버에 자리가 없다 — 위시(state/likes.ts)와 같은 처지라 기기에 저장한다.
 *    계정이 아니라 기기에 붙으므로, 서버 API 가 생기면 그쪽으로 옮긴다.
 */

export type LookVote = 'up' | 'down';

/** 룩 id → 평가. 취소하면 키를 지운다(값 null 로 남기지 않는다). */
let votes: Record<string, LookVote> = {};

const listeners = new Set<() => void>();
/* 저장은 기다리지 않는다 — 화면은 이미 새 값을 그렸고, 늦거나 실패해도 이번 세션은 그대로다. */
const notify = () => {
  listeners.forEach((l) => l());
  void persist();
};

async function persist(): Promise<void> {
  try {
    await writeStored(JSON.stringify({ votes }));
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

export const lookVoteStore = {
  /** 앱이 뜰 때 한 번 — 기기에 적어 둔 평가를 되살린다. */
  async bootstrap(): Promise<void> {
    if (Object.keys(votes).length > 0) return;
    try {
      const raw = await readStored();
      if (!raw || Object.keys(votes).length > 0) return;
      const saved = JSON.parse(raw) as { votes?: Record<string, LookVote> };
      votes = saved.votes && typeof saved.votes === 'object' ? saved.votes : {};
      listeners.forEach((l) => l());
    } catch {
      // 저장값이 깨졌으면 빈 상태로 시작한다
    }
  },

  getVotes: () => votes,
  get: (lookId: string | undefined): LookVote | null =>
    (lookId ? votes[lookId] : undefined) ?? null,

  /** 같은 걸 다시 누르면 취소다 — 한 번 누른 평가를 되돌릴 길이 버튼 말고는 없다. */
  toggle(lookId: string, vote: LookVote): void {
    const next = { ...votes };
    if (next[lookId] === vote) delete next[lookId];
    else next[lookId] = vote;
    votes = next;
    notify();
  },

  subscribe,
};

export function useLookVotes(): Record<string, LookVote> {
  return useSyncExternalStore(subscribe, lookVoteStore.getVotes, lookVoteStore.getVotes);
}
