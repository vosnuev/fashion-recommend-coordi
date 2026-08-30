import { API_BASE_URL, AuthEndpoints } from '@/constants/config';
import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  saveAccessToken,
  saveTokens,
} from '@/lib/secureStore';

/**
 * 앱 전역 HTTP 클라이언트.
 *  - base URL 자동 prepend
 *  - 로그인 상태면 Authorization: Bearer <access> 자동 부착
 *  - 401 이 오면 refresh 토큰으로 access 재발급 후 원요청 1회 재시도
 *  - 재발급까지 실패하면 토큰을 지우고 onUnauthorized 콜백을 호출(=세션 종료)
 *
 * 참고 프로젝트(SKN28-4th-4team)의 frontend/services/apiClient.js 인터셉터
 * 패턴을 React Native + SecureStore 환경으로 옮긴 것.
 */

/**
 * 요청 제한 시간.
 *
 * 없으면 서버가 응답을 안 줄 때 화면이 **영원히 로딩에 머문다** — 초대장은 "여는 중…",
 * 로그인 콜백은 "로그인하고 있어요…" 에서 멈춰 사용자 눈에는 아무것도 안 뜨는 화면이다.
 * (2026-08-20 실제로 겪음: dev 서버가 간헐적으로 30초 이상 응답하지 않았다.)
 * 기다리다 실패로 끝나야 화면이 "다시 시도"를 보여줄 수 있다.
 *
 * ⚠️ 넉넉해야 한다. 짧게 잡으면 **느리지만 성공하던 요청까지 실패로 바꾼다** —
 * 20초로 뒀다가 룩북 둘러보기(응답 22~42초)가 통째로 빈 화면이 됐다.
 * 지금 dev 백엔드의 실측 최악값(로그인 37초·둘러보기 42초)을 담고도 남게 잡는다.
 */
const DEFAULT_TIMEOUT_MS = 45_000;
/** 사진 업로드는 느린 게 정상이라 따로 길게 둔다. */
const UPLOAD_TIMEOUT_MS = 120_000;

/** 시간 초과는 status 0 으로 만든다 — HTTP 응답이 아예 없었다는 뜻이다. */
export const TIMEOUT_STATUS = 0;

export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(message: string, status: number, data: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

// 세션 만료(재발급 실패) 시 호출. auth 스토어가 등록한다. (순환 import 회피용 콜백)
let onUnauthorizedCb: (() => void) | null = null;
export function onUnauthorized(cb: () => void): void {
  onUnauthorizedCb = cb;
}

type RequestOptions = Omit<RequestInit, 'body' | 'headers'> & {
  /** 객체면 JSON 으로 직렬화되어 전송된다 */
  body?: unknown;
  headers?: Record<string, string>;
  /** false 면 Authorization 헤더를 붙이지 않는다 (기본 true) */
  auth?: boolean;
  /** 제한 시간(ms). 0 이면 안 건다 — 오래 걸려도 되는 요청에만 쓸 것. */
  timeoutMs?: number;
  /** 내부용: 401 재시도 여부 (직접 넘기지 말 것) */
  _retried?: boolean;
};

async function parseBody(res: Response): Promise<unknown> {
  const type = res.headers.get('content-type') ?? '';
  if (type.includes('application/json')) {
    return res.json().catch(() => null);
  }
  const text = await res.text().catch(() => '');
  return text || null;
}

function errorMessage(data: unknown, status: number): string {
  if (!data || typeof data !== 'object') return `요청 실패 (${status})`;
  const record = data as Record<string, unknown>;
  for (const key of ['detail', 'message', ...Object.keys(record)]) {
    const value = record[key];
    if (typeof value === 'string' && value) return value;
    if (Array.isArray(value) && typeof value[0] === 'string') return value[0];
    if (value && typeof value === 'object') {
      const nested = Object.values(value as Record<string, unknown>).flat();
      const first = nested.find((item) => typeof item === 'string');
      if (typeof first === 'string') return first;
    }
  }
  return `요청 실패 (${status})`;
}

/**
 * 제한 시간을 걸어 fetch 한다. 시간이 다 되면 요청을 끊고 ApiError 로 바꾼다.
 *
 * 밖에서 넘어온 signal 이 있으면 그쪽 취소도 함께 받는다 — 화면을 떠나며 취소한 요청까지
 * "시간 초과"로 보고하면 이미 사라진 화면의 오류를 띄우게 된다. 그 경우는 원래 오류를 그대로 던진다.
 */
async function withTimeout(
  timeoutMs: number,
  run: (signal: AbortSignal | undefined) => Promise<Response>,
  external?: AbortSignal | null,
): Promise<Response> {
  if (timeoutMs <= 0) return run(external ?? undefined);

  const controller = new AbortController();
  const onExternalAbort = () => controller.abort();
  external?.addEventListener('abort', onExternalAbort);
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    return await run(controller.signal);
  } catch (e) {
    if (controller.signal.aborted && !external?.aborted) {
      throw new ApiError(
        '서버가 응답하지 않아요. 잠시 후 다시 시도해 주세요.',
        TIMEOUT_STATUS,
        null,
      );
    }
    throw e;
  } finally {
    clearTimeout(timer);
    external?.removeEventListener('abort', onExternalAbort);
  }
}

// 동시에 여러 요청이 401 을 받아도 refresh 는 한 번만 수행하도록 공유한다.
let refreshPromise: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  const refresh = await getRefreshToken();
  if (!refresh) return null;

  try {
    const res = await withTimeout(DEFAULT_TIMEOUT_MS, (signal) =>
      fetch(`${API_BASE_URL}${AuthEndpoints.refresh}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ refresh }),
        signal,
      }),
    );
    if (!res.ok) return null;

    const data = (await parseBody(res)) as { access?: string; refresh?: string } | null;
    if (!data?.access) return null;

    /* 백엔드가 refresh 토큰을 회전시킨다(SIMPLE_JWT: ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_
       ROTATION). 재발급 응답에 새 refresh 가 함께 오고 **쓰던 refresh 는 그 즉시 블랙리스트에
       오른다**. 그래서 새 access 만 저장하고 새 refresh 를 버리면, 첫 재발급은 되지만 두 번째
       재발급에서 이미 죽은 토큰을 보내 401 → 강제 로그아웃이 된다.
       (access 수명이 30분이라 로그인 한 시간쯤 뒤 어느 화면에서든 튕기는 증상이었다.) */
    if (data.refresh) {
      await saveTokens(data.access, data.refresh);
    } else {
      await saveAccessToken(data.access);
    }
    return data.access;
  } catch {
    return null;
  }
}

export async function apiFetch<T = unknown>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { body, auth = true, headers, _retried, timeoutMs, ...rest } = options;

  const token = auth ? await getAccessToken() : null;

  // FormData(멀티파트 파일 업로드)면 JSON 직렬화하지 않고 Content-Type 도 직접 지정하지 않는다.
  // (fetch 가 boundary 를 포함한 multipart/form-data 헤더를 자동으로 붙이게 둔다.)
  const isFormData = typeof FormData !== 'undefined' && body instanceof FormData;

  const res = await withTimeout(
    timeoutMs ?? (isFormData ? UPLOAD_TIMEOUT_MS : DEFAULT_TIMEOUT_MS),
    (signal) =>
      fetch(`${API_BASE_URL}${path}`, {
        ...rest,
        signal,
        headers: {
          Accept: 'application/json',
          ...(body !== undefined && !isFormData ? { 'Content-Type': 'application/json' } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...headers,
        },
        body:
          body === undefined ? undefined : isFormData ? (body as FormData) : JSON.stringify(body),
      }),
    rest.signal,
  );

  // 401 → refresh 1회 시도 후 원요청 재시도
  if (res.status === 401 && auth && !_retried) {
    if (!refreshPromise) {
      refreshPromise = refreshAccessToken().finally(() => {
        refreshPromise = null;
      });
    }
    const newAccess = await refreshPromise;

    if (newAccess) {
      return apiFetch<T>(path, { ...options, _retried: true });
    }

    // 재발급 실패 → 세션 종료
    await clearTokens();
    onUnauthorizedCb?.();
    throw new ApiError('세션이 만료되었습니다. 다시 로그인해 주세요.', 401, null);
  }

  const data = await parseBody(res);

  if (!res.ok) {
    throw new ApiError(errorMessage(data, res.status), res.status, data);
  }

  return data as T;
}

/** 자주 쓰는 메서드 단축 헬퍼 */
export const api = {
  get: <T = unknown>(path: string, opts?: RequestOptions) =>
    apiFetch<T>(path, { ...opts, method: 'GET' }),
  post: <T = unknown>(path: string, body?: unknown, opts?: RequestOptions) =>
    apiFetch<T>(path, { ...opts, method: 'POST', body }),
  put: <T = unknown>(path: string, body?: unknown, opts?: RequestOptions) =>
    apiFetch<T>(path, { ...opts, method: 'PUT', body }),
  patch: <T = unknown>(path: string, body?: unknown, opts?: RequestOptions) =>
    apiFetch<T>(path, { ...opts, method: 'PATCH', body }),
  delete: <T = unknown>(path: string, opts?: RequestOptions) =>
    apiFetch<T>(path, { ...opts, method: 'DELETE' }),
};
