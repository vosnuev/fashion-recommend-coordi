import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

/**
 * 토큰 저장소.
 * - 네이티브(iOS/Android): expo-secure-store (Keychain/Keystore, 암호화 저장)
 * - 웹(EAS Hosting 체험용 빌드): SecureStore 미지원 → localStorage 로 폴백
 * - 키체인 접근 불가 시(예: 코드서명 없는 시뮬레이터 빌드): 메모리 폴백
 *   → 세션은 유지되지만 앱 재시작 시 사라진다. 서명된 실기기/개발빌드에선 키체인 정상 동작.
 *
 * SecureStore 키는 [A-Za-z0-9._-] 만 허용하므로 밑줄 표기를 쓴다.
 */
const ACCESS_KEY = 'auth_access_token';
const REFRESH_KEY = 'auth_refresh_token';
/* 데모 세션 표식. 토큰이 없는 세션이라 이 값이 없으면 새로고침·앱 재시작에서 그냥 사라진다. */
const DEMO_KEY = 'auth_demo_session';
const OUTFIT_ANALYSIS_KEY = 'outfit_analysis_job';
const OUTFIT_CLAIM_KEY = 'outfit_claim_tokens';
const PENDING_SHARE_KEY = 'wardrobe_pending_share';
/* 룩북 위시(하트로 담은 룩) — 서버에 자리가 없어 기기에 둔다(아래 저장 함수 주석 참고). */
const WISHLIST_KEY = 'wishlist_v1';
/** 룩에 남긴 좋아요/별로예요. 서버에 자리가 없어 기기에 둔다(위시와 같은 처지). */
const LOOK_VOTES_KEY = 'look_votes_v1';

const isWeb = Platform.OS === 'web';

// 키체인 접근 실패 시 폴백용 (서명 정상 빌드에선 쓰이지 않음)
const memoryStore = new Map<string, string>();

async function setItem(key: string, value: string): Promise<void> {
  if (isWeb) {
    localStorage.setItem(key, value);
    return;
  }
  try {
    await SecureStore.setItemAsync(key, value);
  } catch (e) {
    console.warn('[secureStore] 키체인 저장 실패 → 메모리 폴백:', e);
    memoryStore.set(key, value);
  }
}

async function getItem(key: string): Promise<string | null> {
  if (isWeb) {
    return localStorage.getItem(key);
  }
  try {
    return await SecureStore.getItemAsync(key);
  } catch {
    return memoryStore.get(key) ?? null;
  }
}

async function deleteItem(key: string): Promise<void> {
  if (isWeb) {
    localStorage.removeItem(key);
    return;
  }
  try {
    await SecureStore.deleteItemAsync(key);
  } catch {
    // 키체인 접근 불가 시 무시 (메모리만 정리)
  }
  memoryStore.delete(key);
}

/** 로그인 성공 시: access + refresh 동시 저장 */
export async function saveTokens(access: string, refresh: string): Promise<void> {
  await Promise.all([setItem(ACCESS_KEY, access), setItem(REFRESH_KEY, refresh)]);
}

/** access 재발급 시: access 만 갱신 */
export async function saveAccessToken(access: string): Promise<void> {
  await setItem(ACCESS_KEY, access);
}

export function getAccessToken(): Promise<string | null> {
  return getItem(ACCESS_KEY);
}

export function getRefreshToken(): Promise<string | null> {
  return getItem(REFRESH_KEY);
}

/**
 * 로그아웃/세션만료 시: 전부 삭제.
 * 데모 표식은 건드리지 않는다 — 데모는 토큰이 없어 401 이 정상이라, 여기서 같이 지우면
 * API 한 번 실패했다고 데모 세션이 풀린다. (해제는 signOut/continueAsGuest 가 명시적으로 한다)
 */
export async function clearTokens(): Promise<void> {
  await Promise.all([deleteItem(ACCESS_KEY), deleteItem(REFRESH_KEY)]);
}

/** 데모 세션 표식 — 토큰 없는 체험 로그인을 새로고침 후에도 복원하기 위한 최소 흔적 */
export function saveDemoFlag(): Promise<void> {
  return setItem(DEMO_KEY, '1');
}

export async function hasDemoFlag(): Promise<boolean> {
  return (await getItem(DEMO_KEY)) === '1';
}

export function clearDemoFlag(): Promise<void> {
  return deleteItem(DEMO_KEY);
}

/** 비회원도 앱 재실행 후 진행 중인 착장 분석을 복구하기 위한 최소 상태 저장소. */
export function saveOutfitAnalysisJob(value: string): Promise<void> {
  return setItem(OUTFIT_ANALYSIS_KEY, value);
}

export function getOutfitAnalysisJob(): Promise<string | null> {
  return getItem(OUTFIT_ANALYSIS_KEY);
}

export function clearOutfitAnalysisJob(): Promise<void> {
  return deleteItem(OUTFIT_ANALYSIS_KEY);
}

/**
 * 비로그인으로 접수한 분석의 claim 토큰 보관함.
 * 진행 중 작업(위 OUTFIT_ANALYSIS_KEY)은 1건만 들고 있다가 새 분석을 시작하면 지워지는데,
 * 토큰은 로그인 시점까지 살아 있어야 해서 따로 쌓는다.
 */
export function saveOutfitClaimTokens(value: string): Promise<void> {
  return setItem(OUTFIT_CLAIM_KEY, value);
}

export function getOutfitClaimTokens(): Promise<string | null> {
  return getItem(OUTFIT_CLAIM_KEY);
}

export function clearOutfitClaimTokens(): Promise<void> {
  return deleteItem(OUTFIT_CLAIM_KEY);
}

/**
 * 예전 공유 예약함(itemId → roomId)의 잔재를 지운다.
 *
 * 예약은 이제 서버(`wardrobe_item.pending_share_room`)가 들고 있다 — 기기에 두면
 * PC 에서 올리고 폰에서 확정할 때 공유가 사라지기 때문이다. 남은 값은 아무도 읽지
 * 않으므로, 앱이 뜰 때 한 번 치워 저장소를 깨끗이 둔다.
 * 이 정리 호출은 구버전 사용자가 모두 넘어간 뒤 지워도 된다.
 */
export function clearLegacyPendingShare(): Promise<void> {
  return deleteItem(PENDING_SHARE_KEY);
}

/**
 * 룩북 위시 보관함.
 *
 * ⚠️ **토큰과 달리 비밀이 아니다.** 여기 두는 이유는 하나 — 둘러보기 피드의 하트를
 *    받아 줄 서버 자리가 없어서(`/api/v1/likes` 부재) 기기 말고는 둘 데가 없다.
 *    이미 붙어 있는 저장소를 쓰는 편이 새 의존성(AsyncStorage)을 더하는 것보다 낫다.
 *
 * 계정이 아니라 **기기에 붙는다.** 서버 API 가 생기면 이 쌍을 지우고 스토어 본문만 바꾸면 된다.
 */
export function saveWishlist(value: string): Promise<void> {
  return setItem(WISHLIST_KEY, value);
}

export function getWishlist(): Promise<string | null> {
  return getItem(WISHLIST_KEY);
}

export function saveLookVotes(value: string): Promise<void> {
  return setItem(LOOK_VOTES_KEY, value);
}

export function getLookVotes(): Promise<string | null> {
  return getItem(LOOK_VOTES_KEY);
}
