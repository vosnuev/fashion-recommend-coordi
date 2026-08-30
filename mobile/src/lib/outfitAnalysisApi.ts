import { File, UploadType } from 'expo-file-system';
import { Platform } from 'react-native';

import { API_BASE_URL, OutfitAnalysisEndpoint } from '@/constants/config';
import { apiFetch, ApiError } from '@/lib/apiClient';
import { getAccessToken } from '@/lib/secureStore';

const REQUEST_TIMEOUT_MS = 60_000;

export type OutfitEvaluation = {
  overall_score: number;
  summary: string;
  strengths: string[];
  weather_comment: string;
  personalization_comment: string;
  styling_tips: string[];
};

export type OutfitAnalysisStatus = 'QUEUED' | 'PROCESSING' | 'SUCCEEDED' | 'FAILED';

export type OutfitAnalysisAccepted = {
  analysis_id: string;
  status: 'QUEUED';
  poll_url: string;
  poll_after_ms: number;
  estimated_seconds: number;
  claim_token: string | null;
  wardrobe_job_id: string | null;
};

/**
 * 진행 상태 조회(폴링) 응답.
 *
 * `analysis_id` 는 일부러 두지 않는다 — 실제 응답에 실려 오지도 않고(2026-08-07 실측),
 * 분석 id 는 접수 응답에서 한 번 정해진 뒤 바뀌지 않으므로 폴링이 다시 정할 것이 아니다.
 * 예전엔 여기 필수 필드로 적혀 있어서 `analysisId: response.analysis_id` 대입이 타입검사를
 * 통과했고, 첫 폴링에 id 가 null 로 지워져 결과 화면이 옷장 상태를 영영 못 읽었다.
 */
export type OutfitAnalysisResult = {
  status: OutfitAnalysisStatus;
  evaluation: OutfitEvaluation | null;
  context: Record<string, unknown> | null;
  poll_after_ms: number | null;
  detail: string | null;
  created_at: string;
  finished_at: string | null;
};

type AnalyzeOptions = {
  name?: string;
  mimeType?: string;
  lat?: number;
  lon?: number;
  saveToWardrobe?: boolean;
};

export async function startOutfitAnalysis(
  uri: string,
  options: AnalyzeOptions = {},
): Promise<OutfitAnalysisAccepted> {
  if ((options.lat === undefined) !== (options.lon === undefined)) {
    throw new Error('위도와 경도는 함께 입력해야 합니다.');
  }

  const name = options.name ?? guessFileName(uri);
  const mimeType = options.mimeType ?? guessMimeType(name);

  if (Platform.OS === 'web') {
    const blob = await fetch(uri).then((response) => {
      if (!response.ok) throw new Error('선택한 사진을 불러오지 못했어요.');
      return response.blob();
    });
    const form = new FormData();
    form.append('image', blob, name);
    if (options.lat !== undefined && options.lon !== undefined) {
      form.append('lat', String(options.lat));
      form.append('lon', String(options.lon));
    }
    form.append('save_to_wardrobe', String(options.saveToWardrobe ?? false));

    return withRequestTimeout(
      apiFetch<OutfitAnalysisAccepted>(OutfitAnalysisEndpoint, {
        method: 'POST',
        body: form,
      }),
    );
  }

  const token = await getAccessToken();
  const parameters: Record<string, string> = {
    save_to_wardrobe: String(options.saveToWardrobe ?? false),
  };
  if (options.lat !== undefined && options.lon !== undefined) {
    parameters.lat = String(options.lat);
    parameters.lon = String(options.lon);
  }

  const response = await withRequestTimeout(
    new File(uri).upload(`${API_BASE_URL}${OutfitAnalysisEndpoint}`, {
      httpMethod: 'POST',
      uploadType: UploadType.MULTIPART,
      fieldName: 'image',
      mimeType,
      parameters,
      /* iOS 기본값(background)은 백그라운드 URLSession 을 쓰는데 여기서 사유 없이 실패한다
         (UnableToUploadException). 앱이 백그라운드로 가도 이어지는 이점이 있지만, JS 인스턴스가
         앱 종료 시 복구되지 않아 어차피 쓰지 못하는 이점이라 포그라운드로 보낸다. */
      sessionType: 'foreground',
      headers: {
        Accept: 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    }),
  );

  return parseResponse<OutfitAnalysisAccepted>(response);
}

export function getOutfitAnalysis(pollUrl: string): Promise<OutfitAnalysisResult> {
  return apiFetch<OutfitAnalysisResult>(normalizePollUrl(pollUrl));
}

function normalizePollUrl(pollUrl: string): string {
  return pollUrl.startsWith(API_BASE_URL) ? pollUrl.slice(API_BASE_URL.length) : pollUrl;
}

function withRequestTimeout<T>(request: Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error('분석 접수 시간이 길어지고 있어요. 잠시 후 다시 시도해 주세요.'));
    }, REQUEST_TIMEOUT_MS);

    request.then(resolve, reject).finally(() => clearTimeout(timer));
  });
}

function parseResponse<T>(response: { status: number; body?: string }): T {
  let data: unknown = null;
  try {
    data = response.body ? JSON.parse(response.body) : null;
  } catch {
    data = response.body;
  }

  if (response.status < 200 || response.status >= 300) {
    const detail = (data as { detail?: string } | null)?.detail;
    throw new ApiError(detail ?? `착장 분석 요청에 실패했어요. (${response.status})`, response.status, data);
  }

  return data as T;
}

const MIME_BY_EXTENSION: Record<string, string> = {
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  webp: 'image/webp',
};

function guessFileName(uri: string): string {
  const lastSegment = uri.split('?')[0].split('/').pop() ?? '';
  return /\.[a-zA-Z0-9]+$/.test(lastSegment) ? lastSegment : 'outfit.jpg';
}

function guessMimeType(name: string): string {
  const extension = name.split('.').pop()?.toLowerCase() ?? '';
  return MIME_BY_EXTENSION[extension] ?? 'image/jpeg';
}
