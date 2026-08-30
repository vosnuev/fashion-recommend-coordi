import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

import {
  getWardrobeViewPreferences,
  patchWardrobeViewPreferences,
} from '@/lib/wardrobeApi';
import type { WardrobeGroupMode, WardrobeItemSort } from '@/lib/wardrobeSections';

const VERSION = 'v2';

export type WardrobeViewPreferences = {
  group_mode: WardrobeGroupMode;
  item_sort: WardrobeItemSort;
};

export const DEFAULT_WARDROBE_VIEW_PREFERENCES: WardrobeViewPreferences = {
  group_mode: 'SYSTEM_CATEGORY',
  item_sort: 'ADDED_DESC',
};

function storageKey(userId: number | string, pending = false): string {
  const suffix = pending ? '.pending' : '';
  return Platform.OS === 'web'
    ? `wardrobe:view-preferences:${VERSION}:${userId}${suffix}`
    : `wardrobe.view-preferences.${VERSION}.${userId}${suffix}`;
}

function isPreferences(value: unknown): value is WardrobeViewPreferences {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<WardrobeViewPreferences>;
  return (
    (candidate.group_mode === 'SYSTEM_CATEGORY' || candidate.group_mode === 'HASHTAG') &&
    (candidate.item_sort === 'ADDED_DESC' || candidate.item_sort === 'COLOR_NAME_ASC')
  );
}

async function readLocal(userId: number | string): Promise<WardrobeViewPreferences | null> {
  try {
    const raw = Platform.OS === 'web'
      ? localStorage.getItem(storageKey(userId))
      : await SecureStore.getItemAsync(storageKey(userId));
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return isPreferences(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

async function writeLocal(
  userId: number | string,
  preferences: WardrobeViewPreferences,
  pending = false,
): Promise<void> {
  const value = JSON.stringify(preferences);
  try {
    if (Platform.OS === 'web') localStorage.setItem(storageKey(userId, pending), value);
    else await SecureStore.setItemAsync(storageKey(userId, pending), value);
  } catch {
    // 보기 설정 캐시 실패가 옷장 탐색을 막아서는 안 된다.
  }
}

async function clearPending(userId: number | string): Promise<void> {
  try {
    if (Platform.OS === 'web') localStorage.removeItem(storageKey(userId, true));
    else await SecureStore.deleteItemAsync(storageKey(userId, true));
  } catch {
    // 다음 저장/복원에서 다시 동기화를 시도한다.
  }
}

/** 로컬 캐시를 먼저 쓰고, 로그인 서버 설정을 원본으로 복원한다. */
export async function loadWardrobeViewPreferences(
  userId: number | string,
): Promise<WardrobeViewPreferences> {
  const local = await readLocal(userId);
  try {
    const server = await getWardrobeViewPreferences();
    const preferences: WardrobeViewPreferences = {
      group_mode: server.group_mode,
      item_sort: server.item_sort,
    };
    await writeLocal(userId, preferences);
    await clearPending(userId);
    return preferences;
  } catch {
    return local ?? DEFAULT_WARDROBE_VIEW_PREFERENCES;
  }
}

/** 화면은 즉시 로컬 저장하고, 서버 실패 시 pending 캐시로 다음 동기화를 예약한다. */
export async function saveWardrobeViewPreferences(
  userId: number | string,
  preferences: WardrobeViewPreferences,
): Promise<void> {
  await writeLocal(userId, preferences);
  try {
    await patchWardrobeViewPreferences(preferences);
    await clearPending(userId);
  } catch {
    await writeLocal(userId, preferences, true);
  }
}
