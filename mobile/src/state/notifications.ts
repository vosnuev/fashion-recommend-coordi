import { useSyncExternalStore } from 'react';

/**
 * 알림 설정 경량 스토어 (prefs.ts 와 같은 모듈 스토어 패턴).
 * 프로토타입: 메모리 보관(앱 재시작 시 기본값). 추후 실제 푸시 토픽 구독과 연동.
 */
export type NotiKey = 'dailyLook' | 'weather' | 'savedUpdates' | 'marketing';
export type NotiSettings = Record<NotiKey, boolean>;

let state: NotiSettings = {
  dailyLook: true,
  weather: true,
  savedUpdates: true,
  marketing: false,
};
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export const notiStore = {
  get: () => state,
  set(key: NotiKey, on: boolean) {
    state = { ...state, [key]: on };
    emit();
  },
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};

/** 알림 설정 구독 */
export function useNotifications() {
  return useSyncExternalStore(notiStore.subscribe, notiStore.get, notiStore.get);
}
