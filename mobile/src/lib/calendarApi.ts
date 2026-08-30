import { Platform } from 'react-native';

import { API_BASE_URL, CalendarEndpoints } from '@/constants/config';
import { api, apiFetch, ApiError } from '@/lib/apiClient';
import { getAccessToken } from '@/lib/secureStore';
import {
  guessFileName,
  guessMimeType,
  isRemote,
  toLocalFile,
  uploadMultipart,
} from '@/lib/uploadFile';

/** 사진 등록은 옷 추출이 끝나야 COMPLETED 가 된다. 옷만 고른 기록은 처음부터 COMPLETED. */
export type CalendarStatus = 'REGISTERED' | 'PROCESSING' | 'COMPLETED' | 'FAILED';

export type CalendarSourceType = 'PHOTO_UPLOAD' | 'WARDROBE_SELECTED';

export type CalendarProcessingErrorCode =
  | 'QUEUE_ENQUEUE_FAILED'
  | 'NO_ITEM_EXTRACTED'
  | 'IMAGE_PROCESSING_FAILED'
  | (string & {});

/** 기록에 딸린 옷 한 벌. `snapshot` 은 등록 시점의 옷장 아이템을 그대로 굳혀둔 것이다. */
export type CalendarWardrobeItem = {
  link_id: string;
  wardrobe_item_id: string;
  image_url: string;
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

export type CalendarEntryDto = {
  id: string;
  /** 'YYYY-MM-DD' */
  date: string;
  source_type: CalendarSourceType;
  image_s3_key: string;
  /** presigned URL. 사진 없이 옷만 고른 기록이면 빈 문자열이다. */
  image_url: string;
  /** 그날의 일정 메모. 프론트의 `note` 에 해당한다. */
  schedule: string;
  tpo: string[];
  weather_snapshot: Record<string, unknown> | null;
  hashtags: string[];
  /** 입은 옷으로 이미 지정해 사진 추출에서 제외한 대분류 — 서버가 정한다. */
  skipped_categories: string[];
  status: CalendarStatus;
  wardrobe_items: CalendarWardrobeItem[];
  created_at: string;
  updated_at: string;
};

export type CalendarProcessingStatus = {
  calendar_id: string;
  status: CalendarStatus;
  /** 사진 등록이 아니면 false — 폴링할 필요가 없다. */
  processing_required: boolean;
  is_terminal: boolean;
  result_available: boolean;
  item_counts: { total: number; extracted: number; failed: number };
  failure: { code: CalendarProcessingErrorCode; message: string } | null;
  processing_started_at: string | null;
  processing_completed_at: string | null;
  updated_at: string;
};

/** 등록·수정에 함께 보내는 메타데이터. 서버가 받는 건 이 셋뿐이다. */
export type CalendarMetadata = {
  schedule?: string;
  tpo?: string[];
  hashtags?: string[];
};

/** 기간 조회 — 월 그리드가 쓴다. 응답은 배열 그대로다(페이지네이션 없음). */
export function listCalendarEntries(startDate: string, endDate: string): Promise<CalendarEntryDto[]> {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate });
  return api.get<CalendarEntryDto[]>(`${CalendarEndpoints.list}?${query}`);
}

/**
 * 특정 날짜의 기록. **기록이 없으면 서버가 404 를 준다** — 그건 오류가 아니라
 * "그날은 비어 있다"는 뜻이라 null 로 바꿔 돌려준다.
 */
export async function getCalendarEntryByDate(date: string): Promise<CalendarEntryDto | null> {
  try {
    const query = new URLSearchParams({ date });
    return await api.get<CalendarEntryDto>(`${CalendarEndpoints.byDate}?${query}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export function getCalendarEntry(calendarId: string): Promise<CalendarEntryDto> {
  return api.get<CalendarEntryDto>(CalendarEndpoints.detail(calendarId));
}

export function getCalendarProcessingStatus(calendarId: string): Promise<CalendarProcessingStatus> {
  return api.get<CalendarProcessingStatus>(CalendarEndpoints.processingStatus(calendarId));
}

/**
 * 옷장 아이템만 골라 등록 — 사진이 없는 기록. 즉시 완료되므로 폴링이 필요 없다.
 * `wardrobeItemIds` 는 비울 수 없다(서버가 400).
 */
export function createCalendarFromWardrobe(input: {
  date: string;
  wardrobeItemIds: string[];
} & CalendarMetadata): Promise<CalendarEntryDto> {
  return api.post<CalendarEntryDto>(CalendarEndpoints.wardrobe, {
    date: input.date,
    wardrobe_item_ids: input.wardrobeItemIds,
    schedule: input.schedule ?? '',
    tpo: input.tpo ?? [],
    hashtags: input.hashtags ?? [],
  });
}

/**
 * 사진으로 등록. 옷장 아이템을 함께 걸 수 있다(사진에서 못 뽑는 옷을 직접 지정하는 용도).
 *
 * 응답은 202 이고 `status` 가 REGISTERED/PROCESSING 이다 — 옷 추출이 끝나야 목록이 채워지므로
 * `getCalendarProcessingStatus` 로 지켜봐야 한다.
 */
export async function createCalendarFromPhoto(input: {
  date: string;
  photoUri: string;
  wardrobeItemIds?: string[];
  name?: string;
  mimeType?: string;
} & CalendarMetadata): Promise<CalendarEntryDto> {
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
      apiFetch<CalendarEntryDto>(CalendarEndpoints.photo, { method: 'POST', body: form }),
    );
  }

  /* 원격 주소는 기기 안의 파일이 아니라 그대로 못 올린다 — 캐시에 한 번 내려받는다. */
  const local = isRemote(input.photoUri) ? await toLocalFile(input.photoUri, name) : null;
  const uri = local ? local.file.uri : input.photoUri;
  try {
    // React Native 의 FormData 는 파일을 { uri, name, type } 로 받는다(XHR 전용).
    form.append('image', { uri, name, type: mimeType } as unknown as Blob);
    appendPhotoFields(form, input);
    const token = await getAccessToken();
    const response = await uploadMultipart(`${API_BASE_URL}${CalendarEndpoints.photo}`, form, {
      token,
      timeoutMs: UPLOAD_TIMEOUT_MS,
    });
    return parseUploadResponse<CalendarEntryDto>(response);
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

/** 일정·TPO·해시태그만 고친다. 사진을 바꾸거나 옷을 더하는 건 PATCH 로 못 한다(삭제 후 재등록). */
export function patchCalendarEntry(
  calendarId: string,
  patch: CalendarMetadata,
): Promise<CalendarEntryDto> {
  return api.patch<CalendarEntryDto>(CalendarEndpoints.detail(calendarId), patch);
}

/**
 * 입은 옷을 기록에 **더한다** — 연결만 만든다. 사진도 기록 id 도 그대로다.
 *
 * 이미 걸린 옷은 서버가 건너뛰므로(멱등) 화면에 있는 옷을 통째로 보내도 된다.
 * 이 API 가 없던 동안 옷을 더하려면 기록을 지우고 다시 만들어야 했는데, 사진 기록에서는
 * 그게 곧 재분석이라 같은 옷이 옷장에 한 벌 더 생겼다.
 * 처리 중(REGISTERED/PROCESSING)인 기록은 서버가 409 로 막는다.
 */
export function linkCalendarItems(
  calendarId: string,
  wardrobeItemIds: string[],
): Promise<CalendarEntryDto> {
  return api.post<CalendarEntryDto>(CalendarEndpoints.items(calendarId), {
    wardrobe_item_ids: wardrobeItemIds,
  });
}

/**
 * 입은 옷 하나를 기록에서 뺀다 — **연결만 끊는다.** 캘린더 기록도 옷장 아이템도
 * 서버에 그대로 남고, 응답으로 갱신된 기록 전체가 돌아온다.
 * 처리 중(REGISTERED/PROCESSING)인 기록은 서버가 409 로 막는다.
 */
export function unlinkCalendarItem(
  calendarId: string,
  wardrobeItemId: string,
): Promise<CalendarEntryDto> {
  return api.delete<CalendarEntryDto>(CalendarEndpoints.item(calendarId, wardrobeItemId));
}

/** 처리 중인 기록은 서버가 409 로 막는다. */
export function deleteCalendarEntry(calendarId: string): Promise<unknown> {
  return api.delete(CalendarEndpoints.detail(calendarId));
}

/**
 * multipart 는 값이 전부 문자열이라 배열은 **같은 키를 여러 번** 붙여 보낸다.
 *
 * `필드[0]` 같은 인덱스 표기는 쓸 수 없다 — 서버 시리얼라이저가 StrictObjectInputMixin 이라
 * 선언되지 않은 이름을 먼저 걷어내고(`"허용되지 않은 필드입니다"`), DRF 의 HTML 리스트
 * 파싱까지 가지도 못한다. 2026-08-11 실측으로 확인.
 */
function appendPhotoFields(
  form: FormData,
  input: { date: string; wardrobeItemIds?: string[] } & CalendarMetadata,
) {
  form.append('date', input.date);
  form.append('schedule', input.schedule ?? '');
  input.wardrobeItemIds?.forEach((id) => form.append('wardrobe_item_ids', id));
  input.tpo?.forEach((value) => form.append('tpo', value));
  input.hashtags?.forEach((value) => form.append('hashtags', value));
}

/** 업로드 상한. 없으면 응답이 안 올 때 화면이 영영 "저장 중"으로 남는다. */
const UPLOAD_TIMEOUT_MS = 60_000;

function withUploadTimeout<T>(request: Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error('사진 저장이 오래 걸리고 있어요. 잠시 후 다시 시도해 주세요.'));
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
    throw new ApiError(
      detail ?? `캘린더 기록 저장에 실패했어요. (${response.status})`,
      response.status,
      data,
    );
  }
  return data as T;
}
