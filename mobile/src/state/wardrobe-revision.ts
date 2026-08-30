import { useSyncExternalStore } from 'react';

/**
 * 옷장 목록이 서버와 어긋난 순간을 알리는 신호.
 *
 * 옷장 화면은 탭 스택에 남아 있어 상세에서 옷을 지우고 돌아와도 다시 마운트되지 않는다.
 * 그래서 "목록을 바꾼 쪽"이 여기서 값을 올리고, 목록을 그리는 쪽이 그 값을 구독해
 * 조용히 다시 불러온다. 화면끼리 콜백을 넘기지 않으려고 스토어로 뺐다.
 *
 * 값 자체에는 뜻이 없다 — 바뀌었다는 사실만이 신호다.
 */
let revision = 0;

const listeners = new Set<() => void>();

/** 목록을 바꿔 놓은 쪽이 부른다 (삭제·일괄 등록 등). */
export function bumpWardrobeRevision(): void {
  revision += 1;
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

const getRevision = () => revision;

/** 옷장 목록을 그리는 화면이 구독한다 — 값이 바뀌면 다시 불러올 신호. */
export function useWardrobeRevision(): number {
  return useSyncExternalStore(subscribe, getRevision, getRevision);
}
