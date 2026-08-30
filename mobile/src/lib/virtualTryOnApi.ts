import { Platform } from 'react-native';

import { API_BASE_URL, DailyLookVirtualTryOnEndpoint } from '@/constants/config';
import { ApiError, apiFetch } from '@/lib/apiClient';
import { getAccessToken } from '@/lib/secureStore';
import { guessFileName, guessMimeType, uploadMultipart } from '@/lib/uploadFile';

export type VirtualTryOnStatus = 'QUEUED' | 'PROCESSING' | 'SUCCEEDED' | 'FAILED';

/**
 * 가상 피팅 작업 상태. **접수(POST)와 조회(GET)가 같은 본문**을 준다.
 *
 * 생성은 수십 초~2분이라 서버가 기다려 주지 않는다(예전에는 기다리다 프록시가
 * 끊어 524가 났다). 접수만 받고 워커가 만들며, 화면은 poll_after_ms 간격으로
 * 다시 물어본다. 결과는 서버에 남으므로 화면을 나갔다 와도 GET 으로 되살린다.
 *
 * `status`가 null 이면 이 룩으로 아직 한 번도 만든 적이 없다는 뜻이다.
 */
export type VirtualTryOnJob = {
  job_id: string | null;
  status: VirtualTryOnStatus | null;
  mode: string;
  /** 어느 룩을 입혔는지 (빈 값이면 대표 룩) */
  golden_id: string;
  /** presigned URL — 조회마다 새로 서명되므로 캐시하면 만료된다 */
  image_url: string | null;
  cache_hit: boolean;
  /** 생성 중일 때만 값이 있다 */
  poll_after_ms: number | null;
  /** 상태별 사용자 안내 문구 */
  detail: string | null;
};

/** 아직 만드는 중이라 계속 물어봐야 하는 상태 */
export function isVirtualTryOnPending(job: VirtualTryOnJob | null): boolean {
  return job?.status === 'QUEUED' || job?.status === 'PROCESSING';
}

/** 그 룩의 마지막 가상 피팅. 화면 재진입 시 이걸로 복원한다. */
export function getVirtualTryOn(
  lookId: string,
  goldenId?: string,
): Promise<VirtualTryOnJob> {
  const qs = goldenId ? `?golden_id=${encodeURIComponent(goldenId)}` : '';
  return apiFetch<VirtualTryOnJob>(`${DailyLookVirtualTryOnEndpoint(lookId)}${qs}`);
}

/**
 * 오늘의 룩을 **사진 속 본인**에게 입힌다 (mode='person').
 *
 * 마네킹이 아니다 — 얼굴·체형·포즈·배경은 그대로 두고 옷만 바꾼다. 서버는
 * body-measure 로 입력한 체형 판정을 프롬프트에 함께 넣되, 옷이 그 체형에 맞게
 * 앉도록 하는 데만 쓴다(사람을 그 수치대로 고쳐 그리지 않는다).
 *
 * `goldenId` 는 '다른 룩'으로 돌려보던 후보를 입어볼 때 준다. 생략하면 서버가
 * 대표 룩을 쓰므로, 화면이 후보를 보여주고 있다면 **반드시 넘겨야** 한다 —
 * 안 넘기면 화면에서 고른 룩과 입은 룩이 달라진다.
 * 서버는 이 값이 그 사용자의 오늘 후보 안에 있는지 확인한다(아니면 404).
 */
export async function fitDailyLookToMannequin(
  lookId: string,
  personUri: string,
  goldenId?: string,
): Promise<VirtualTryOnJob> {
  const form = new FormData();
  const name = guessFileName(personUri, 'person.jpg');

  if (Platform.OS === 'web') {
    const response = await fetch(personUri);
    if (!response.ok) throw new Error('선택한 사진을 불러오지 못했습니다.');
    form.append('person_image', await response.blob(), name);
    form.append('mode', 'person');
    if (goldenId) form.append('golden_id', goldenId);
    return apiFetch<VirtualTryOnJob>(DailyLookVirtualTryOnEndpoint(lookId), {
      method: 'POST',
      body: form,
    });
  }

  form.append(
    'person_image',
    { uri: personUri, name, type: guessMimeType(name) } as unknown as Blob,
  );
  form.append('mode', 'person');
  if (goldenId) form.append('golden_id', goldenId);
  const response = await uploadMultipart(
    `${API_BASE_URL}${DailyLookVirtualTryOnEndpoint(lookId)}`,
    form,
    /* 이제 서버가 접수만 하고 바로 답하므로 10분을 잡아둘 이유가 없다.
       업로드(사진 한 장)만 끝나면 되는 시간이다. */
    { token: await getAccessToken(), timeoutMs: 60 * 1000 },
  );
  let body: unknown = null;
  try {
    body = response.body ? JSON.parse(response.body) : null;
  } catch {
    body = response.body;
  }
  if (response.status < 200 || response.status >= 300) {
    const detail = (body as { detail?: string } | null)?.detail;
    throw new ApiError(detail ?? `가상 착장 요청 실패 (${response.status})`, response.status, body);
  }
  return body as VirtualTryOnJob;
}
