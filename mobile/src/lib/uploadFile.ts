import { File, Paths } from 'expo-file-system';

import { getImageSource } from '@/lib/resolveImageUri';

/**
 * 네이티브 멀티파트 업로드에 공통으로 쓰는 파일 준비 유틸.
 * 옷장·캘린더가 같은 방식으로 사진을 올려서 한곳에 둔다.
 */

const MIME_BY_EXT: Record<string, string> = {
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  webp: 'image/webp',
  heic: 'image/heic',
};

export function isRemote(uri: string): boolean {
  return /^https?:/i.test(uri);
}

export function guessFileName(uri: string, fallback = 'upload.jpg'): string {
  const last = uri.split('?')[0].split('/').pop() ?? '';
  return /\.[a-zA-Z0-9]+$/.test(last) ? last : fallback;
}

export function guessMimeType(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() ?? '';
  return MIME_BY_EXT[ext] ?? 'image/jpeg';
}

/**
 * 네이티브 멀티파트 업로드 — XHR 로 직접 보낸다.
 *
 * 전역 `fetch` 는 Expo winter fetch 라 RN 의 `{ uri, name, type }` 파트를 못 받고
 * (`Unsupported FormDataPart implementation`), RN Blob 은 ArrayBuffer 로 못 만든다.
 * XHR 은 이 파트를 네이티브로 처리하고 **같은 키를 여러 번** 붙일 수 있어,
 * 배열 필드(`wardrobe_item_ids` 등)를 서버가 원하는 형태로 보낼 수 있는 유일한 길이다.
 *
 * ⚠️ apiClient 를 타지 않으므로 인증 헤더는 호출자가 넣고, 401 자동 재발급은 없다.
 */
export function uploadMultipart(
  url: string,
  form: FormData,
  options: { token?: string | null; timeoutMs?: number } = {},
): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    // 게스트 채팅은 HttpOnly 쿠키가 신원 증명이다. 웹의 크로스 오리진 업로드에도 포함한다.
    xhr.withCredentials = true;
    xhr.setRequestHeader('Accept', 'application/json');
    if (options.token) xhr.setRequestHeader('Authorization', `Bearer ${options.token}`);
    // Content-Type 은 직접 넣지 않는다 — boundary 를 XHR 이 붙여야 한다.
    xhr.timeout = options.timeoutMs ?? 60_000;
    xhr.onload = () => resolve({ status: xhr.status, body: xhr.responseText });
    xhr.onerror = () => reject(new Error('네트워크 문제로 사진을 올리지 못했어요.'));
    xhr.ontimeout = () =>
      reject(new Error('사진 저장이 오래 걸리고 있어요. 잠시 후 다시 시도해 주세요.'));
    xhr.send(form as unknown as Document);
  });
}

/**
 * 업로드에 쓸 로컬 파일을 만든다.
 *
 * `File` 은 기기 안의 파일을 가리키는 것이라 `https://...` 주소를 그대로 넣으면 올라가지 않는다.
 * 룩의 구성 아이템이나 쇼핑몰에서 가져온 사진은 전부 원격 주소라, 캐시에 한 번 내려받아
 * 진짜 파일로 만든 뒤 올린다.
 *
 * `downloaded` 가 true 면 호출한 쪽이 업로드 후 지워야 한다.
 */
export async function toLocalFile(
  uri: string,
  name: string,
): Promise<{ file: File; downloaded: boolean }> {
  if (!isRemote(uri)) return { file: new File(uri), downloaded: false };

  /* 이름이 겹치면 내려받기가 실패하므로 매번 다른 이름을 쓴다.
     (핀터레스트처럼 핫링크를 막는 곳은 화면에서 쓰는 것과 같은 헤더를 붙여야 받아진다) */
  const dest = new File(Paths.cache, `upload-${Date.now()}-${name}`);
  const source = getImageSource(uri);
  const file = await File.downloadFileAsync(uri, dest, {
    headers: (source && 'headers' in source ? source.headers : undefined) as
      | Record<string, string>
      | undefined,
  });
  return { file, downloaded: true };
}
