import { AuthEndpoints } from '@/constants/config';
import { api, ApiError } from '@/lib/apiClient';
import { authStore, type AuthUser } from '@/state/auth';

type EmailAuthResponse = {
  access: string;
  refresh: string;
  user: AuthUser;
  is_new_user: boolean;
};

type SignupResponse = {
  email: string;
  verification_required: true;
  retry_after: number;
};

type VerificationResponse = {
  email: string;
  verified: true;
};

async function finishEmailAuth(path: string, payload: Record<string, string>) {
  const response = await api.post<EmailAuthResponse>(
    path,
    payload,
    { auth: false },
  );
  await authStore.signIn(
    { access: response.access, refresh: response.refresh },
    response.user,
  );
  return response;
}

export function signupWithEmail(email: string, password: string) {
  return api.post<SignupResponse>(
    AuthEndpoints.signup,
    { email: email.trim().toLowerCase(), password },
    { auth: false },
  );
}

export function loginWithEmail(email: string, password: string) {
  return finishEmailAuth(AuthEndpoints.login, {
    email: email.trim().toLowerCase(),
    password,
  });
}

/**
 * 이메일 소유 확인. **토큰을 받지 않는다** — 백엔드는 계정만 활성화하므로,
 * 인증을 마치면 로그인 화면으로 돌아가 이메일·비밀번호로 로그인해야 세션이 열린다.
 */
export function verifyEmail(email: string, code: string) {
  return api.post<VerificationResponse>(
    AuthEndpoints.verifyEmail,
    { email: email.trim().toLowerCase(), code },
    { auth: false },
  );
}

export function resendVerificationEmail(email: string) {
  return api.post<{ retry_after: number }>(
    AuthEndpoints.resendEmail,
    { email: email.trim().toLowerCase() },
    { auth: false },
  );
}

/**
 * 로그인 실패가 '이메일 미인증' 때문인가.
 *
 * 백엔드(EmailLoginSerializer)는 미인증을 400 + "이메일 인증을 완료해 주세요." 로 막는데,
 * 응답에 구분 코드가 없어 문구로 알아본다. **문구가 바뀌면 여기도 함께 고쳐야 한다** —
 * 못 알아보면 예전처럼 안내만 뜨고 인증하러 갈 길이 없어진다.
 */
export function isEmailUnverifiedError(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 400) return false;
  return emailAuthErrorMessage(error).includes('이메일 인증');
}

export function emailAuthErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return '서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.';

  const data = error.data as Record<string, unknown> | null;
  if (data) {
    for (const key of ['email', 'password', 'code', 'detail', 'non_field_errors']) {
      const value = data[key];
      if (Array.isArray(value) && typeof value[0] === 'string') return value[0];
      if (typeof value === 'string') return value;
    }
  }
  return error.message;
}

/**
 * 특정 필드의 서버 검증 메시지를 꺼낸다. 해당 필드 에러가 없으면 null.
 *
 * Django 의 validate_password 는 **위반한 규칙을 전부** 배열로 돌려준다.
 * 첫 줄만 보여주면 "8자 이상"만 고치고 또 거절당하는 왕복이 생기므로 전부 잇는다.
 */
export function fieldErrorMessage(error: unknown, field: string): string | null {
  if (!(error instanceof ApiError)) return null;

  const value = (error.data as Record<string, unknown> | null)?.[field];
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) {
    const lines = value.filter((line): line is string => typeof line === 'string');
    return lines.length > 0 ? lines.join('\n') : null;
  }
  return null;
}
