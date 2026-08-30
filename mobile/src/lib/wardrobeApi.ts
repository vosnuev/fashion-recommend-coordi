import { UploadType } from 'expo-file-system';
import { Platform } from 'react-native';

import { API_BASE_URL, WardrobeEndpoints } from '@/constants/config';
import { api, apiFetch, ApiError } from '@/lib/apiClient';
import { getImageSource } from '@/lib/resolveImageUri';
import { guessFileName, guessMimeType, isRemote, toLocalFile } from '@/lib/uploadFile';
import { getAccessToken } from '@/lib/secureStore';

/**
 * 옷장 API 호출 — 전송만 담당한다(상태·폴링은 hooks/use-wardrobe.ts).
 * 필드명은 백엔드 WardrobeItemSerializer 를 그대로 따른다(변환하지 않는다 —
 * 이름을 바꿔 두면 백엔드 스키마가 바뀔 때 어디를 고쳐야 하는지 흐려진다).
 */

/** 서버가 주는 옷장 아이템 1벌. */
export type WardrobeApiItem = {
  id: string;
  job: string | null;
  s3_key: string;
  /** presigned GET URL — 만료가 있으므로 오래 캐시하지 말 것 */
  image_url: string;
  item_name: string;
  category_large: string;
  category_small: string;
  season: string[];
  style: string[];
  color: string;
  pattern: string;
  fit: string;
  material: string;
  sleeve: string;
  length: string;
  usage: string[];
  layer_role: string;
  layer_order: number | null;
  /** 세그멘테이션 메타(raw_label·score·bbox 등) — 화면에 쓰지 않지만 디버깅에 도움 */
  seg_meta: Record<string, unknown>;
  /** false = 사용자 확인 대기. 추천 검색에서 제외된다. */
  confirmed: boolean;
  /**
   * 옷장에 들인 시각. null 이면 룩 사진에서 뽑혔지만 아직 옷장 밖이라
   * 옷장 목록에도 안 나온다 — 룩 상세에서 '옷장에 추가'를 눌러야 들어간다.
   */
  added_to_closet_at: string | null;
  created_at: string;
  wardrobe_hashtags: WardrobeHashtagSummary[];
};

export type WardrobeHashtagSummary = {
  id: string;
  name: string;
  position: number;
};

export type WardrobeSystemCategory = WardrobeHashtagSummary & {
  type: 'SYSTEM';
  item_count: number;
  mutable: false;
};

export type WardrobeHashtag = WardrobeHashtagSummary & {
  item_count: number;
  created_at: string;
  updated_at: string;
};

export type WardrobeFiltersResponse = {
  system_categories: WardrobeSystemCategory[];
  hashtags: WardrobeHashtag[];
};

export type WardrobeHashtagItemsUpdated = {
  hashtag_id: string;
  added_item_ids: string[];
  removed_item_ids: string[];
  item_count: number;
  deleted: boolean;
};

export type WardrobeItemHashtagsUpdated = {
  item_id: string;
  wardrobe_hashtags: WardrobeHashtagSummary[];
};

export type WardrobeViewPreferencesResponse = {
  group_mode: 'SYSTEM_CATEGORY' | 'HASHTAG';
  item_sort: 'ADDED_DESC' | 'COLOR_NAME_ASC';
  updated_at: string;
};

export type UploadJobStatus = 'PENDING' | 'PROCESSING' | 'DONE' | 'FAILED';

export type UploadJob = {
  id: string;
  status: UploadJobStatus;
  error_message: string;
  created_at: string;
  finished_at: string | null;
  /** DONE 이면 이 사진에서 나온 아이템들. 1장에서 여러 벌이 나올 수 있다. */
  items: WardrobeApiItem[];
};

/** PATCH 로 보낼 수 있는 필드 (WardrobeItemUpdateSerializer 와 일치) */
export type WardrobeItemPatch = Partial<{
  item_name: string;
  category_large: string;
  category_small: string;
  season: string[];
  style: string[];
  color: string;
  pattern: string;
  fit: string;
  material: string;
  sleeve: string;
  length: string;
  usage: string[];
  layer_role: string;
  layer_order: number | null;
  confirmed: boolean;
}>;

export type WardrobeItemQuery = {
  category_large?: string;
  /** 확인 대기(false)만, 또는 확정(true)만 */
  confirmed?: boolean;
};

/**
 * 사진 1장 업로드 → 처리 job 접수(202). 아이템은 아직 없다.
 *
 * ⚠️ 네이티브에서 `FormData.append('image', { uri, name, type })`(예전 RN 관용구)를 쓰면
 * `Unsupported FormDataPart implementation` 으로 실패한다 — Expo SDK 54+ 의 전역 fetch 가
 * 표준(WinterCG) 구현이라 uri 객체 파트를 받지 않고 Blob/File 만 받기 때문이다.
 * 그래서 네이티브는 expo-file-system 의 네이티브 멀티파트 업로드를 쓴다.
 */
export async function uploadWardrobePhoto(
  uri: string,
  opts: {
    name?: string;
    mimeType?: string;
    skipProcessing?: boolean;
    itemName?: string;
    category?: string;
    /* '공유 옷장' 토글로 고른 방. 업로드 시작 시점에 서버로 넘겨 예약으로 남긴다 —
       기기에 들고 있으면 PC 에서 올리고 폰에서 확정할 때 공유가 사라진다. */
    sharedRoomId?: string;
  } = {},
): Promise<{ job_id: string; status: UploadJobStatus }> {
  const name = opts.name ?? guessFileName(uri, 'wardrobe.jpg');
  const type = opts.mimeType ?? guessMimeType(name);

  if (Platform.OS === 'web') {
    /* 남의 도메인 이미지는 CORS 때문에 그대로 fetch 하면 막힌다 —
       화면에서 쓰는 것과 같은 프록시를 태워 받아온다. */
    const source = isRemote(uri) ? (getImageSource(uri)?.uri ?? uri) : uri;
    const blob = await fetch(source).then((r) => r.blob());
    const form = new FormData();
    form.append('image', blob, name);
    if (opts.skipProcessing) form.append('skip_processing', 'true');
    if (opts.itemName) form.append('item_name', opts.itemName);
    if (opts.category) form.append('category_large', opts.category);
    if (opts.sharedRoomId) form.append('shared_room_id', opts.sharedRoomId);
    return apiFetch<{ job_id: string; status: UploadJobStatus }>(WardrobeEndpoints.uploads, {
      method: 'POST',
      body: form,
    });
  }

  /* 네이티브 경로는 apiClient 를 타지 않으므로 인증 헤더를 직접 붙인다. */
  const token = await getAccessToken();
  const { file, downloaded } = await toLocalFile(uri, name);
  try {
    const res = await file.upload(`${API_BASE_URL}${WardrobeEndpoints.uploads}`, {
      httpMethod: 'POST',
      uploadType: UploadType.MULTIPART,
      fieldName: 'image',
      mimeType: type,
      parameters: {
        ...(opts.skipProcessing ? { skip_processing: 'true' } : {}),
        ...(opts.itemName ? { item_name: opts.itemName } : {}),
        ...(opts.category ? { category_large: opts.category } : {}),
        ...(opts.sharedRoomId ? { shared_room_id: opts.sharedRoomId } : {}),
      },
      headers: {
        Accept: 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    });
    return parseUploadResponse(res);
  } finally {
    // 내려받은 임시 파일만 지운다. 사용자가 고른 사진은 우리 것이 아니다.
    if (downloaded) {
      try {
        file.delete();
      } catch {
        // 캐시 정리 실패는 업로드 성패와 무관하다 — 조용히 넘어간다.
      }
    }
  }
}

function parseUploadResponse(res: {
  status: number;
  body?: string;
}): { job_id: string; status: UploadJobStatus } {
  let data: unknown = null;
  try {
    data = res.body ? JSON.parse(res.body) : null;
  } catch {
    data = res.body;
  }

  if (res.status < 200 || res.status >= 300) {
    const payload = data as Record<string, unknown> | null;
    const fieldError = payload
      ? Object.values(payload).flat().find((value) => typeof value === 'string')
      : null;
    const detail = typeof payload?.detail === 'string' ? payload.detail : null;
    throw new ApiError(detail ?? (fieldError as string | null) ?? `업로드 실패 (${res.status})`, res.status, data);
  }

  return data as { job_id: string; status: UploadJobStatus };
}

/** 처리 상태 조회 — DONE 이 되면 items 가 채워진다. */
export function getUploadJob(jobId: string): Promise<UploadJob> {
  return api.get<UploadJob>(WardrobeEndpoints.uploadJob(jobId));
}

/* ── 일괄 등록(batch) — 인앱 브라우저에서 긁어온 외부 상품 ────────────────
   사진을 올리는 대신 **이미지 주소**를 넘긴다. 이미지는 서버가 받아 S3 에 저장하고
   Qwen VL 태깅 워커를 태운다. 자세한 계약은 constants/config.ts 의 WardrobeEndpoints 주석. */

/** 백엔드 WARDROBE_BATCH_MAX_ITEMS 기본값. 넘겨 보내면 요청 전체가 400 이다. */
export const WARDROBE_BATCH_MAX_ITEMS = 30;

/** URLField(max_length=2048) — 더 긴 주소는 400 을 부른다. */
export const WARDROBE_BATCH_MAX_LINK_LENGTH = 2048;

/** CharField(max_length=120) */
export const WARDROBE_BATCH_MAX_NAME_LENGTH = 120;

/**
 * 일괄 등록에 넣는 상품 1건.
 * image_link 만 필수고, 나머지는 **아는 것만** 넣는다 — 비워 둔 자리는 서버 모델이 채운다.
 * 값이 taxonomy 와 어긋나면 배치 전체가 400 이므로 추측으로 채우지 말 것.
 */
export type WardrobeBatchItemInput = {
  image_link: string;
  item_name?: string;
  category_large?: string;
  category_small?: string;
  season?: string[];
  style?: string[];
  color?: string;
  pattern?: string;
  fit?: string;
  material?: string;
  sleeve?: string;
  length?: string;
  usage?: string[];
  layer_role?: string;
  layer_order?: number | null;
  confirmed?: boolean;
};

/** PARTIAL = 일부만 성공. DONE/PARTIAL/FAILED 가 종료 상태다. */
export type WardrobeBatchStatus = 'PENDING' | 'PROCESSING' | 'DONE' | 'PARTIAL' | 'FAILED';

/** 배치 안의 job 1개 = 이미지 1장. 단건 업로드 job 에 원본 파일명이 붙은 형태. */
export type WardrobeBatchJob = UploadJob & {
  job_id: string;
  /** 이미지 주소에서 뽑은 원본 파일명 — 처리 중에는 이것 말고 보여줄 게 없다 */
  file_name: string;
};

/** POST 응답(202). 접수 결과일 뿐 아직 아이템은 없다. */
export type WardrobeBatchCreated = {
  batch_id: string;
  status: WardrobeBatchStatus;
  total_count: number;
  accepted: { job_id: string; image_link: string }[];
  /** 이미지를 못 받았거나 큐 적재에 실패한 건. reason: image_fetch_failed | upload_failed | enqueue_failed */
  rejected: { image_link: string; reason: string }[];
  poll_url: string;
  poll_after_ms: number;
  estimated_seconds: number;
};

export type WardrobeBatch = {
  batch_id: string;
  status: WardrobeBatchStatus;
  source: string;
  counts: { total: number; pending: number; done: number; failed: number };
  /** 0~1 */
  progress: number;
  /** null 이면 더 물어볼 필요 없다(종료) */
  poll_after_ms: number | null;
  created_at: string;
  finished_at: string | null;
  jobs: WardrobeBatchJob[];
};

/**
 * 상품 여러 건을 한 번에 접수(202).
 *
 * source 는 등록 경로 꼬리표다(백엔드 정규식 `^[a-z][a-z0-9_-]{0,19}$`).
 * 나중에 "어디서 들어온 옷인지"를 세는 데 쓰이므로 화면마다 다른 값을 넣지 말 것.
 */
export function createWardrobeBatch(
  items: WardrobeBatchItemInput[],
  source = 'in_app_browser',
): Promise<WardrobeBatchCreated> {
  return api.post<WardrobeBatchCreated>(WardrobeEndpoints.batches, { source, items });
}

/** 배치 진행 상태. 종료되면 poll_after_ms 가 null 로 온다. */
export function getWardrobeBatch(batchId: string): Promise<WardrobeBatch> {
  return api.get<WardrobeBatch>(WardrobeEndpoints.batch(batchId));
}

export function listWardrobeItems(query: WardrobeItemQuery = {}): Promise<WardrobeApiItem[]> {
  const params = new URLSearchParams();
  if (query.category_large) params.set('category_large', query.category_large);
  if (query.confirmed !== undefined) params.set('confirmed', String(query.confirmed));
  const qs = params.toString();
  return api.get<WardrobeApiItem[]>(
    qs ? `${WardrobeEndpoints.items}?${qs}` : WardrobeEndpoints.items,
  );
}

/** 개인 옷장의 기본·사용자 카테고리. 사용자 카테고리는 서버가 영속 상태의 원본이다. */
export function listWardrobeFilters(): Promise<WardrobeFiltersResponse> {
  return api.get<WardrobeFiltersResponse>(WardrobeEndpoints.categories);
}

export function createWardrobeHashtag(name: string, itemIds: string[]): Promise<WardrobeHashtag> {
  return api.post<WardrobeHashtag>(WardrobeEndpoints.hashtags, { name, item_ids: itemIds });
}

export function renameWardrobeHashtag(hashtagId: string, name: string): Promise<WardrobeHashtag> {
  return api.patch<WardrobeHashtag>(WardrobeEndpoints.hashtag(hashtagId), { name });
}

export function deleteWardrobeHashtag(hashtagId: string): Promise<unknown> {
  return api.delete(WardrobeEndpoints.hashtag(hashtagId));
}

export function updateWardrobeHashtagItems(
  hashtagId: string,
  changes: { add_item_ids: string[]; remove_item_ids: string[] },
): Promise<WardrobeHashtagItemsUpdated> {
  return api.patch<WardrobeHashtagItemsUpdated>(
    WardrobeEndpoints.hashtagItems(hashtagId),
    changes,
  );
}

/** 아이템 상세에서 입력한 개인 옷장 해시태그 이름 집합으로 전체 교체한다. */
export function replaceWardrobeItemHashtags(
  itemId: string,
  names: string[],
): Promise<WardrobeItemHashtagsUpdated> {
  return api.put<WardrobeItemHashtagsUpdated>(
    WardrobeEndpoints.itemHashtags(itemId),
    { names },
  );
}

export function reorderWardrobeHashtags(hashtagIds: string[]): Promise<{ hashtags: WardrobeHashtag[] }> {
  return api.put<{ hashtags: WardrobeHashtag[] }>(WardrobeEndpoints.hashtagOrder, {
    hashtag_ids: hashtagIds,
  });
}

export function getWardrobeViewPreferences(): Promise<WardrobeViewPreferencesResponse> {
  return api.get<WardrobeViewPreferencesResponse>(WardrobeEndpoints.viewPreferences);
}

export function patchWardrobeViewPreferences(
  preferences: Partial<Pick<WardrobeViewPreferencesResponse, 'group_mode' | 'item_sort'>>,
): Promise<WardrobeViewPreferencesResponse> {
  return api.patch<WardrobeViewPreferencesResponse>(WardrobeEndpoints.viewPreferences, preferences);
}

/* 백엔드가 단건 조회(GET items/{id}/)를 아직 구현하지 않았다 — allow 는 PATCH·DELETE 뿐이라
   405 가 온다. 한 번 405 를 보면 그 뒤로는 바로 목록에서 찾는다(불필요한 왕복 제거).
   백엔드에 GET 이 생기면 이 플래그가 계속 false 로 남아 원래 경로를 쓴다. */
let detailGetUnsupported = false;

async function findItemInList(itemId: string): Promise<WardrobeApiItem> {
  const found = (await listWardrobeItems()).find((i) => i.id === itemId);
  if (!found) throw new ApiError('아이템을 찾을 수 없어요', 404, null);
  return found;
}

export async function getWardrobeItem(itemId: string): Promise<WardrobeApiItem> {
  if (detailGetUnsupported) return findItemInList(itemId);
  try {
    return await api.get<WardrobeApiItem>(WardrobeEndpoints.item(itemId));
  } catch (e) {
    if (e instanceof ApiError && e.status === 405) {
      detailGetUnsupported = true;
      return findItemInList(itemId);
    }
    throw e;
  }
}

/**
 * 태그 수정. confirmed:true 를 함께 보내면 확정까지 한 번에 된다.
 *
 * 확정 응답에는 `shared_room_id` 가 실려 온다 — 등록할 때 켜 둔 공유 예약을 서버가
 * 이 순간 소진하기 때문이다. 공유가 안 됐으면(방을 나갔다거나) null 이며,
 * 그 경우에도 확정 자체는 성공이다.
 */
export function patchWardrobeItem(
  itemId: string,
  patch: WardrobeItemPatch,
): Promise<WardrobeApiItem & { shared_room_id?: string | null }> {
  return api.patch<WardrobeApiItem & { shared_room_id?: string | null }>(
    WardrobeEndpoints.item(itemId),
    patch,
  );
}

export function deleteWardrobeItem(itemId: string): Promise<unknown> {
  return api.delete(WardrobeEndpoints.item(itemId));
}

/**
 * 룩 사진에서 뽑힌 옷을 내 옷장에 들인다.
 * 서버가 멱등이라 두 번 눌러도 처음 들인 시각이 그대로다.
 */
export function addWardrobeItemToCloset(itemId: string): Promise<WardrobeApiItem> {
  return api.post<WardrobeApiItem>(WardrobeEndpoints.addToCloset(itemId), {});
}

/** 아이템을 화면에 보여줄 이름 — 서버가 이름을 비워 보낼 수 있다(캡셔닝 실패 등). */
export function itemDisplayName(item: WardrobeApiItem): string {
  return item.item_name || item.category_small || item.category_large;
}

/* ── 파일 이름·형식 추정 ─────────────────────────────────
   백엔드가 확장자·content-type 으로 형식을 거르므로(jpeg/png/webp/heic) 최소한은 맞춰 보낸다. */

// ── 공유 옷장 (Shared Wardrobe) API ──
export type SharedReferenceUnavailableReason =
  | 'NOT_CONFIRMED'
  | 'VECTOR_NOT_READY';

export type SharedRoom = {
  id: string;
  title: string;
  invite_code: string | null;
  code_expires_at: string | null;
  created_at: string;
  role?: 'owner' | 'member';
};

/**
 * 공유 옷장에 등장하는 사용자.
 *
 * `username` 은 로그인 방식별 내부 식별자다 (이메일 가입 `email_<uuid>`,
 * 소셜 `<provider>_<id>`). 화면에는 **절대 쓰지 않는다** — 이메일 가입자 아바타가
 * 전부 'e' 로 보이던 원인이다. 표시용 이름은 서버가 정하는 `display_name`
 * (= nickname 우선, 없으면 username) 을 쓴다.
 */
export type SharedRoomUser = {
  id: number;
  username: string;
  nickname: string;
  display_name: string;
  email: string;
};

export type SharedRoomMember = {
  id: number;
  user: SharedRoomUser;
  role: 'owner' | 'member';
  joined_at: string;
};

/** 화면에 쓸 이름. 구버전 서버가 display_name 을 안 주면 nickname → username 순으로 물러난다. */
export function sharedUserDisplayName(user: SharedRoomUser | null | undefined): string {
  if (!user) return '멤버';
  return user.display_name?.trim() || user.nickname?.trim() || user.username || '멤버';
}

export type SharedRoomItem = {
  id: string;
  registered_by: SharedRoomUser | null;
  wardrobe_item: WardrobeApiItem;
  reference_eligible: boolean;
  reference_unavailable_reason: SharedReferenceUnavailableReason | null;
  created_at: string;
};

export function createSharedRoom(title: string): Promise<SharedRoom> {
  return api.post<SharedRoom>('/api/v1/shared-wardrobes/', { title });
}

export function renameSharedRoom(roomId: string, title: string): Promise<SharedRoom> {
  return api.patch<SharedRoom>(`/api/v1/shared-wardrobes/${roomId}/`, { title });
}

export function deleteSharedRoom(roomId: string, deletePersonalItems = false): Promise<unknown> {
  return api.delete(
    `/api/v1/shared-wardrobes/${roomId}/?delete_personal_items=${deletePersonalItems}`,
  );
}

export function joinSharedRoom(inviteCode: string): Promise<{ room_id: string; title: string; status: string }> {
  return api.post<{ room_id: string; title: string; status: string }>('/api/v1/shared-wardrobes/join/', { invite_code: inviteCode });
}

/** 초대장 미리보기 응답 — 방 UUID·실명·이메일은 오지 않는다(비로그인에게 노출 금지). */
export type SharedRoomPreviewMember = {
  /** 가입 순서(0~5). 아바타 색을 여기서 뽑는다 — 배열 위치가 아니라 이 값을 쓸 것. */
  index: number;
  label: string;
  role: 'owner' | 'member';
};

export type SharedRoomPreviewItem = {
  image_url: string;
  item_name: string;
  category_large: string;
  color: string;
  owner_index: number;
  owner_label: string;
};

export type SharedRoomPreview = {
  title: string;
  member_count: number;
  capacity: number;
  can_join: boolean;
  /** true 면 초대 코드가 만료된 것 — items 는 빈 배열로 온다. */
  expired: boolean;
  members: SharedRoomPreviewMember[];
  items: SharedRoomPreviewItem[];
};

/**
 * 초대 코드로 방을 구경(읽기 전용). 비로그인 방문자용이라 인증 헤더를 붙이지 않는다 —
 * 붙이면 만료된 토큰일 때 401 → 세션 종료 흐름으로 튄다.
 */
export function previewSharedRoom(code: string): Promise<SharedRoomPreview> {
  return api.get<SharedRoomPreview>(
    `/api/v1/shared-wardrobes/preview/?code=${encodeURIComponent(code)}`,
    { auth: false },
  );
}

export function refreshInviteCode(roomId: string): Promise<{ room_id: string; invite_code: string; code_expires_at: string }> {
  return api.post<{ room_id: string; invite_code: string; code_expires_at: string }>(`/api/v1/shared-wardrobes/${roomId}/refresh-code/`);
}

export function leaveSharedRoom(roomId: string, deleteMyItems: boolean = true): Promise<unknown> {
  return api.post(`/api/v1/shared-wardrobes/${roomId}/leave/`, { delete_my_items: deleteMyItems });
}

export function listSharedRoomItems(roomId: string): Promise<SharedRoomItem[]> {
  return api.get<SharedRoomItem[]>(`/api/v1/shared-wardrobes/${roomId}/items/`);
}

export function registerItemToSharedRoom(roomId: string, wardrobeItemId: string): Promise<SharedRoomItem> {
  return api.post<SharedRoomItem>(`/api/v1/shared-wardrobes/${roomId}/items/`, {
    wardrobe_item_id: wardrobeItemId,
  });
}

export function unregisterItemFromSharedRoom(roomId: string, wardrobeItemId: string): Promise<unknown> {
  return api.delete(`/api/v1/shared-wardrobes/${roomId}/items/?wardrobe_item_id=${wardrobeItemId}`);
}

export function listSharedRoomMembers(roomId: string): Promise<SharedRoomMember[]> {
  return api.get<SharedRoomMember[]>(`/api/v1/shared-wardrobes/${roomId}/members/`);
}

export function getMySharedRooms(): Promise<SharedRoom[]> {
  return api.get<SharedRoom[]>('/api/v1/shared-wardrobes/');
}
