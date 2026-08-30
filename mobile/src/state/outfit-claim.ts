import { CLAIM_MAX_ITEMS, claimOutfitAnalyses } from '@/lib/outfitHistoryApi';
import {
  clearOutfitClaimTokens,
  getOutfitClaimTokens,
  saveOutfitClaimTokens,
} from '@/lib/secureStore';
import { authStore } from '@/state/auth';
import { outfitAnalysisStore } from '@/state/outfit-analysis';

/**
 * 비로그인으로 접수한 착장 분석을, 로그인하는 순간 계정으로 옮겨오기 위한 토큰 보관함.
 *
 * 익명 분석은 DB에 user=NULL 로 남고 기록 목록 API 는 user=request.user 로 거르기 때문에,
 * claim 을 거치지 않으면 로그인해도 서랍에 영영 안 나타난다.
 *
 * 진행 중 작업 스토어(outfit-analysis)는 **1건만** 들고 있다가 새 분석을 시작하면 clear 되는데,
 * 토큰은 로그인 시점까지 살아 있어야 한다. 그래서 그 스토어를 구독만 하고 토큰은 여기 따로 쌓는다
 * (구독이라 outfit-analysis.ts 를 고칠 일이 없다).
 *
 * ⚠️ 토큰 수명이 짧다 — 백엔드 OUTFIT_CLAIM_TTL_MINUTES 기본 60분이다. 조회 24시간과 다르므로
 *    "어제 분석하고 오늘 가입" 은 넘어오지 않는다. 서버 설정을 올리면 아래 상수도 같이 올린다.
 */
const CLAIM_TTL_MS = 60 * 60 * 1000;

type StoredToken = {
  token: string;
  /** 발급 시각(ms). 서버가 만료를 판단하지만, 죽은 토큰을 계속 보내지 않으려고 앱에서도 거른다. */
  issuedAt: number;
};

let tokens: StoredToken[] = [];
let hydrated = false;
let flushing = false;
let bootstrapPromise: Promise<void> | null = null;
/** 게스트 → 로그인 전이를 잡기 위한 직전 상태. 매 알림마다 flush 하면 안 된다. */
let wasClaimable = false;

/** 아직 살아 있을 법한 토큰만 남긴다. */
function alive(list: StoredToken[]): StoredToken[] {
  const cutoff = Date.now() - CLAIM_TTL_MS;
  return list.filter((entry) => entry.issuedAt > cutoff);
}

/** 실제 JWT 를 가진 세션인지. 데모 세션은 토큰이 없어 claim 을 부르면 401 이다. */
function isClaimable(): boolean {
  const { status, isDemo } = authStore.getState();
  return status === 'authed' && !isDemo;
}

function persist(): Promise<void> {
  return tokens.length ? saveOutfitClaimTokens(JSON.stringify(tokens)) : clearOutfitClaimTokens();
}

/**
 * 접수 응답에서 받은 토큰을 쌓아 둔다.
 * 백엔드 issue_token 은 주인이 없는 분석에만 토큰을 주므로, 값이 있다는 것 자체가
 * "비로그인으로 접수한 건" 이라는 뜻이다 — 로그인 여부를 따로 볼 필요가 없다.
 */
function collect(token: string | null | undefined): void {
  if (!token || tokens.some((entry) => entry.token === token)) return;
  // 오래된 것부터 버린다. 서버가 한 번에 받는 개수(OUTFIT_CLAIM_MAX_ITEMS)를 넘기면 400 이다.
  tokens = alive([...tokens, { token, issuedAt: Date.now() }]).slice(-CLAIM_MAX_ITEMS);
  void persist();
}

/**
 * 모아둔 토큰을 한 번에 넘긴다. 로그인 직후에 호출된다.
 *
 * 성공하면 결과와 무관하게 비운다 — 서버가 skipped 로 돌려준 건(만료·위조·이미 소유)은
 * 다시 보내도 같은 답이라 들고 있을 이유가 없다. 반대로 네트워크 실패면 그대로 두고
 * 다음 기회에 다시 시도한다.
 */
async function flush(): Promise<void> {
  if (flushing || !isClaimable()) return;
  const pending = alive(tokens);
  if (pending.length === 0) {
    if (tokens.length) {
      tokens = [];
      await persist();
    }
    return;
  }

  flushing = true;
  try {
    await claimOutfitAnalyses(pending.map((entry) => entry.token));
    tokens = [];
    await persist();
  } catch {
    // 조용히 넘어간다 — 기록이 조금 늦게 붙는 것뿐이라 사용자에게 알릴 실패가 아니다.
  } finally {
    flushing = false;
  }
}

/**
 * 앱 시작 시 1회. 저장된 토큰을 읽고 두 스토어를 구독한다.
 * 이미 로그인 상태로 켜졌는데 토큰이 남아 있으면(앱이 꺼진 사이 로그인 등) 바로 넘긴다.
 */
function bootstrap(): Promise<void> {
  if (bootstrapPromise) return bootstrapPromise;
  bootstrapPromise = (async () => {
    const raw = await getOutfitClaimTokens();
    if (raw) {
      try {
        tokens = alive(JSON.parse(raw) as StoredToken[]);
      } catch {
        tokens = [];
        await clearOutfitClaimTokens();
      }
    }
    hydrated = true;
    wasClaimable = isClaimable();

    outfitAnalysisStore.subscribe(() => {
      collect(outfitAnalysisStore.getSnapshot().job?.claimToken);
    });

    authStore.subscribe(() => {
      const claimable = isClaimable();
      // 게스트 → 로그인으로 넘어온 순간에만 보낸다
      if (claimable && !wasClaimable) void flush();
      wasClaimable = claimable;
    });

    // 접수 직후 저장된 토큰이 있을 수 있으니 지금 상태도 한 번 훑는다
    collect(outfitAnalysisStore.getSnapshot().job?.claimToken);
    if (wasClaimable) await flush();
  })();
  return bootstrapPromise;
}

export const outfitClaimStore = {
  bootstrap,
  flush,
  /** 테스트·디버깅용 — 지금 몇 건이 대기 중인지 */
  pendingCount: () => (hydrated ? alive(tokens).length : 0),
};
