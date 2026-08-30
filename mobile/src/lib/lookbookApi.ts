import { Platform } from 'react-native';

import { API_BASE_URL, LookbookEndpoints } from '@/constants/config';
import { api, apiFetch, ApiError } from '@/lib/apiClient';
import { getAccessToken } from '@/lib/secureStore';
import {
  guessFileName,
  guessMimeType,
  isRemote,
  toLocalFile,
  uploadMultipart,
} from '@/lib/uploadFile';

/** 사진 등록은 옷 추출이 끝나야 COMPLETED 가 된다. 옷만 고른 룩은 처음부터 COMPLETED. */
export type LookbookStatus = 'REGISTERED' | 'PROCESSING' | 'COMPLETED' | 'FAILED';

/**
 * GOLDEN_LOOK — 오늘의 룩 카드에서 '저장'으로 담은 골든 코디. 사진 업로드도
 * 옷장 선택도 아니라 서버가 골든셋 이미지를 가리키기만 하고, 옷 추출을 거치지
 * 않아 처음부터 COMPLETED 다.
 */
export type LookbookSourceType = 'PHOTO_UPLOAD' | 'WARDROBE_SELECTED' | 'GOLDEN_LOOK';

export type LookbookProcessingErrorCode =
  | 'QUEUE_ENQUEUE_FAILED'
  | 'NO_ITEM_EXTRACTED'
  | 'IMAGE_PROCESSING_FAILED'
  | (string & {});

/** 룩에 딸린 옷 한 벌. `snapshot` 은 등록 시점의 옷장 아이템을 그대로 굳혀둔 것이다. */
export type LookbookWardrobeItem = {
  link_id: string;
  /** 골든 코디 구성 아이템은 내 옷장의 옷이 아니라 null 이고 snapshot 만 있다. */
  wardrobe_item_id: string | null;
  /** 사진에서 뽑은 옷인지, 사용자가 직접 고른 옷인지 */
  link_type: string;
  image_url: string;
  /** 이 옷이 옷장에 들어 있는 시각. null 이면 아직 옷장 밖 — '옷장에 추가'를 그린다. */
  added_to_closet_at: string | null;
  sort_order: number;
  snapshot: {
    s3_key?: string;
    item_name?: string;
    category_large?: string;
    category_small?: string;
    color?: string;
    [key: string]: unknown;
  };
};

export type LookbookPostDto = {
  id: string;
  source_type: LookbookSourceType;
  /** 오늘의 룩에서 담은 골든 코디 id. 그 외에는 빈 문자열이다. */
  golden_id: string;
  image_s3_key: string;
  /** presigned URL. 옷만 고른 룩이면 첫 아이템 이미지가 표지가 된다. */
  image_url: string;
  /** 그날의 일정 메모. 프론트의 `note` 에 해당한다. */
  schedule: string;
  tpo: string[];
  hashtags: string[];
  /** 사용자가 직접 고른 옷과 겹쳐 사진에서 다시 뽑지 않은 대분류 */
  skipped_categories: string[];
  status: LookbookStatus;
  /** 켜져 있으면 앱 사용자 전체가 둘러보기에서 본다. */
  is_public: boolean;
  /** 캘린더에도 남긴 룩이면 그 날짜가 온다. 상세는 캘린더 쪽에서 따로 조회한다. */
  calendar: { id?: string; date?: string } | null;
  wardrobe_items: LookbookWardrobeItem[];
  created_at: string;
  updated_at: string;
};

export type LookbookListResponse = {
  count: number;
  /** 다음 장이 없으면 null — 이 값이 곧 '더 있는지' 신호다. */
  next_offset: number | null;
  results: LookbookPostDto[];
};

export type LookbookProcessingStatus = {
  lookbook_id: string;
  status: LookbookStatus;
  /** 사진 등록이 아니면 false — 폴링할 필요가 없다. */
  processing_required: boolean;
  is_terminal: boolean;
  result_available: boolean;
  skipped_categories: string[];
  item_counts: { total: number; extracted: number; failed: number };
  failure: { code: LookbookProcessingErrorCode; message: string } | null;
  processing_started_at: string | null;
  processing_completed_at: string | null;
  updated_at: string;
};

/** 등록·수정에 함께 보내는 메타데이터. 서버가 받는 건 이 셋뿐이다. */
export type LookbookMetadata = {
  schedule?: string;
  tpo?: string[];
  hashtags?: string[];
  /** 켜면 앱 사용자 전체가 둘러보기에서 본다. 룩북에 친구 단위 공유는 없다. */
  isPublic?: boolean;
};

/**
 * 룩북에 올리면서 같은 날 캘린더에도 남기는 옵션.
 *
 * 캘린더를 따로 부르지 않는다 — 서버가 한 번의 등록으로 둘을 함께 만든다.
 * 그 날짜에 이미 기록이 있으면 `overwrite` 없이는 409 다. 사용자에게 먼저 묻고 켠다.
 */
export type LookbookCalendarLink = {
  /** 'YYYY-MM-DD' */
  calendarDate?: string;
  overwriteCalendar?: boolean;
};

export function listLookbooks(params?: {
  hashtag?: string;
  status?: LookbookStatus;
  limit?: number;
  offset?: number;
}): Promise<LookbookListResponse> {
  const query = new URLSearchParams();
  if (params?.hashtag) query.set('hashtag', params.hashtag);
  if (params?.status) query.set('status', params.status);
  if (params?.limit != null) query.set('limit', String(params.limit));
  if (params?.offset != null) query.set('offset', String(params.offset));
  const suffix = query.toString();
  return api.get<LookbookListResponse>(
    suffix ? `${LookbookEndpoints.list}?${suffix}` : LookbookEndpoints.list,
  );
}

export function getLookbook(lookbookId: string): Promise<LookbookPostDto> {
  return api.get<LookbookPostDto>(LookbookEndpoints.detail(lookbookId));
}

export function getLookbookProcessingStatus(lookbookId: string): Promise<LookbookProcessingStatus> {
  return api.get<LookbookProcessingStatus>(LookbookEndpoints.processingStatus(lookbookId));
}

/**
 * 옷장 아이템만 골라 등록 — 사진이 없는 룩. 즉시 완료되므로 폴링이 필요 없다.
 * `wardrobeItemIds` 는 비울 수 없다(서버가 400). 표지는 첫 아이템 이미지가 된다.
 */
export function createLookbookFromWardrobe(
  input: { wardrobeItemIds: string[] } & LookbookMetadata & LookbookCalendarLink,
): Promise<LookbookPostDto> {
  return api.post<LookbookPostDto>(LookbookEndpoints.wardrobe, {
    wardrobe_item_ids: input.wardrobeItemIds,
    schedule: input.schedule ?? '',
    tpo: input.tpo ?? [],
    hashtags: input.hashtags ?? [],
    is_public: input.isPublic ?? false,
    ...(input.calendarDate
      ? { calendar_date: input.calendarDate, overwrite_calendar: input.overwriteCalendar ?? false }
      : null),
  });
}

/**
 * 전체 공개된 룩 피드 — 앱 '둘러보기'가 읽는 목록.
 * 앱이 기본으로 주는 룩과 사용자가 공개한 룩이 함께 온다. 비회원도 볼 수 있다.
 */
export function listPublicLookbooks(params?: {
  hashtag?: string;
  limit?: number;
  offset?: number;
}): Promise<LookbookListResponse> {
  const query = new URLSearchParams();
  if (params?.hashtag) query.set('hashtag', params.hashtag);
  if (params?.limit !== undefined) query.set('limit', String(params.limit));
  if (params?.offset !== undefined) query.set('offset', String(params.offset));
  const qs = query.toString();
  return api.get<LookbookListResponse>(
    qs ? `${LookbookEndpoints.publicFeed}?${qs}` : LookbookEndpoints.publicFeed,
  );
}

/**
 * 사진으로 등록. 옷장 아이템을 함께 걸 수 있다(사진에서 못 뽑는 옷을 직접 지정하는 용도).
 *
 * 응답은 202 이고 `status` 가 REGISTERED/PROCESSING 이다 — 옷 추출이 끝나야 목록이 채워지므로
 * `getLookbookProcessingStatus` 로 지켜봐야 한다.
 *
 * 추천 룩을 저장하는 경우처럼 `photoUri` 가 원격 주소일 수 있다. 기기 안의 파일이 아니면
 * 그대로 올릴 수 없어 캐시에 한 번 내려받는다(다 쓰면 지운다).
 */
export async function createLookbookFromPhoto(
  input: {
    photoUri: string;
    wardrobeItemIds?: string[];
    name?: string;
    mimeType?: string;
  } & LookbookMetadata &
    LookbookCalendarLink,
): Promise<LookbookPostDto> {
  const name = input.name ?? guessFileName(input.photoUri);
  const mimeType = input.mimeType ?? guessMimeType(name);

  const form = new FormData();

  if (Platform.OS === 'web') {
    const blob = await fetch(input.photoUri).then((response) => {
      if (!response.ok) throw new Error('선택한 사진을 불러오지 못했어요.');
      return response.blob();
    });
    form.append('image', blob, name);
    appendPhotoFields(form, input);
    return withUploadTimeout(
      apiFetch<LookbookPostDto>(LookbookEndpoints.photo, { method: 'POST', body: form }),
    );
  }

  const local = isRemote(input.photoUri) ? await toLocalFile(input.photoUri, name) : null;
  const uri = local ? local.file.uri : input.photoUri;
  try {
    // React Native 의 FormData 는 파일을 { uri, name, type } 로 받는다(XHR 전용).
    form.append('image', { uri, name, type: mimeType } as unknown as Blob);
    appendPhotoFields(form, input);
    const token = await getAccessToken();
    const response = await uploadMultipart(`${API_BASE_URL}${LookbookEndpoints.photo}`, form, {
      token,
      timeoutMs: UPLOAD_TIMEOUT_MS,
    });
    return parseUploadResponse<LookbookPostDto>(response);
  } finally {
    // 내려받은 임시 파일만 지운다. 사용자가 고른 사진은 우리 것이 아니다.
    if (local?.downloaded) {
      try {
        local.file.delete();
      } catch {
        // 캐시 파일이라 못 지워도 그냥 둔다.
      }
    }
  }
}

/** 일정·TPO·해시태그만 고친다. 사진과 옷 구성은 PATCH 로 못 바꾼다(삭제 후 재등록). */
export function patchLookbook(
  lookbookId: string,
  patch: LookbookMetadata,
): Promise<LookbookPostDto> {
  /* 서버는 선언한 필드만 받고 낯선 키가 오면 400 이다(StrictObjectInputMixin).
     그래서 보낼 것만 골라 담고, 이름이 다른 isPublic 은 여기서 바꿔 준다. */
  const body: Record<string, unknown> = {};
  if (patch.schedule !== undefined) body.schedule = patch.schedule;
  if (patch.tpo !== undefined) body.tpo = patch.tpo;
  if (patch.hashtags !== undefined) body.hashtags = patch.hashtags;
  if (patch.isPublic !== undefined) body.is_public = patch.isPublic;
  return api.patch<LookbookPostDto>(LookbookEndpoints.detail(lookbookId), body);
}

/** 처리 중인 룩은 서버가 409 로 막는다. */
export function deleteLookbook(lookbookId: string): Promise<unknown> {
  return api.delete(LookbookEndpoints.detail(lookbookId));
}

/** 캘린더와 겹치는 날짜라 서버가 막은 경우인지. 사용자에게 "바꿀까요?"를 물을 신호다. */
export function isCalendarConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

/**
 * multipart 는 값이 전부 문자열이라 배열은 **같은 키를 여러 번** 붙여 보낸다.
 * 인덱스 표기(`필드[0]`)는 서버 시리얼라이저가 미선언 필드로 걷어낸다 — calendarApi 와 같은 제약.
 */
function appendPhotoFields(
  form: FormData,
  input: { wardrobeItemIds?: string[] } & LookbookMetadata & LookbookCalendarLink,
) {
  form.append('schedule', input.schedule ?? '');
  input.wardrobeItemIds?.forEach((id) => form.append('wardrobe_item_ids', id));
  input.tpo?.forEach((value) => form.append('tpo', value));
  input.hashtags?.forEach((value) => form.append('hashtags', value));
  form.append('is_public', input.isPublic ? 'true' : 'false');
  if (input.calendarDate) {
    form.append('calendar_date', input.calendarDate);
    form.append('overwrite_calendar', input.overwriteCalendar ? 'true' : 'false');
  }
}

/** 업로드 상한. 없으면 응답이 안 올 때 화면이 영영 "저장 중"으로 남는다. */
const UPLOAD_TIMEOUT_MS = 60_000;

function withUploadTimeout<T>(request: Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error('룩 저장이 오래 걸리고 있어요. 잠시 후 다시 시도해 주세요.'));
    }, UPLOAD_TIMEOUT_MS);
    request.then(resolve, reject).finally(() => clearTimeout(timer));
  });
}

/** XHR 응답을 DTO 로. 실패면 서버가 준 본문을 그대로 ApiError 에 실어 보낸다. */
function parseUploadResponse<T>(response: { status: number; body: string }): T {
  let data: unknown = null;
  try {
    data = response.body ? JSON.parse(response.body) : null;
  } catch {
    data = response.body;
  }

  if (response.status < 200 || response.status >= 300) {
    const detail = (data as { detail?: string } | null)?.detail;
    throw new ApiError(detail ?? `룩 저장에 실패했어요. (${response.status})`, response.status, data);
  }
  return data as T;
}
