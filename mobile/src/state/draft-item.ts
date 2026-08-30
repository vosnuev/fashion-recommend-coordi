import { useSyncExternalStore } from 'react';

/** 아이템 등록 화면과 가져오기 화면 사이의 사진 1장 임시 저장소. */
let photo: string | null = null;
let libraryItem: { name: string; category: string } | null = null;
const listeners = new Set<() => void>();

export const draftItem = {
  getPhoto: () => photo,
  getLibraryItem: () => libraryItem,
  setPhoto(next: string | null) {
    photo = next;
    if (next === null) libraryItem = null;
    listeners.forEach((listener) => listener());
  },
  setLibraryItem(next: { name: string; category: string } | null) {
    libraryItem = next;
  },
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};

export function useDraftPhoto() {
  return useSyncExternalStore(draftItem.subscribe, draftItem.getPhoto, draftItem.getPhoto);
}
