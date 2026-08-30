import { getRun, type ApiChatRun, type ApiRunStatus } from '@/lib/chatApi';
import {
  getStylistRun,
  type ApiStylistRun,
  type StylistId,
} from '@/lib/stylistApi';

/**
 * 답변 run 이 끝날 때까지 기다린다.
 *
 * **왜 SSE 가 아니라 폴링인가.**
 * 백엔드는 /chat/runs/{id}/events/ 로 SSE 를 준다. 그런데 양쪽 플랫폼 모두 그대로는 못 쓴다.
 *   - 웹: 브라우저 `EventSource` 는 **커스텀 헤더를 못 붙인다**. 이 엔드포인트는 JWT 로
 *     소유자를 가리므로 Authorization 없이 열면 남의 run 으로 취급돼 404 가 난다.
 *     (쿠키 신원인 게스트라면 가능하지만 지금은 로그인 사용자만 붙인다.)
 *   - 네이티브: React Native 에 `EventSource` 자체가 없다.
 * 웹만 fetch 스트리밍으로 따로 파싱하는 길도 있지만, 그러면 플랫폼별로 갈라진 두 경로를
 * 각각 검증해야 한다. 답변이 보통 몇 초 안에 끝나는 작업이라 폴링 한 갈래로 통일한다.
 * (SSE 로 올리고 싶어지면 이 파일만 바꾸면 된다 — 부르는 쪽은 run 결과만 본다.)
 */

/** 더 기다려도 소용없는 상태들. NEEDS_CLARIFICATION 도 **정상 답변**이다(되묻는 것). */
const TERMINAL: ApiRunStatus[] = ['SUCCEEDED', 'NEEDS_CLARIFICATION', 'FAILED'];

export function isTerminal(status: ApiRunStatus): boolean {
  return TERMINAL.includes(status);
}

/** 답변이 실제로 생긴 경우. 화면은 이때 메시지를 다시 불러 말풍선을 채운다. */
export function isAnswered(status: ApiRunStatus): boolean {
  return status === 'SUCCEEDED' || status === 'NEEDS_CLARIFICATION';
}

/** 첫 몇 번은 촘촘히, 그 뒤엔 느슨하게. 대부분 5초 안에 끝나서 앞을 촘촘히 둔다. */
function delayFor(attempt: number): number {
  if (attempt < 5) return 700;
  if (attempt < 15) return 1500;
  return 3000;
}

/** 이만큼 지나도 안 끝나면 포기한다. 워커가 죽어 있으면 영영 PENDING 이라 상한이 필요하다. */
const TIMEOUT_MS = 120_000;

export class ChatRunTimeout extends Error {
  constructor() {
    super('답변이 오래 걸리고 있어요. 잠시 후 다시 시도해 주세요.');
    this.name = 'ChatRunTimeout';
  }
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    }
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/**
 * run 이 끝날 때까지 폴링하고 마지막 상태를 돌려준다.
 * 실패 run(FAILED)도 **예외가 아니라 값으로** 준다 — 화면이 오류 문구를 말풍선으로
 * 보여줄지 토스트로 띄울지 고를 수 있어야 해서다. 시간 초과만 예외로 던진다.
 */
export async function waitForRun(
  runId: string,
  options: { signal?: AbortSignal } = {},
): Promise<ApiChatRun> {
  const { signal } = options;
  const startedAt = Date.now();

  for (let attempt = 0; ; attempt += 1) {
    const run = await getRun(runId);
    if (isTerminal(run.status)) return run;

    if (Date.now() - startedAt > TIMEOUT_MS) throw new ChatRunTimeout();
    await sleep(delayFor(attempt), signal);
  }
}

/**
 * 스타일리스트 run 을 끝까지 기다리되, **중간 상태를 매번 넘겨준다**.
 *
 * 기본 답변과 다른 점 — 결과가 여러 개고 끝나는 시각이 제각각이다. 다 끝난 뒤 한 번에 그리면
 * 먼저 끝난 카드가 남을 기다리는 동안 빈 화면이 된다. 그래서 폴링할 때마다 onProgress 로
 * 넘겨 완료된 카드부터 채우게 한다(설계서 18장: "완료된 스타일리스트 카드부터 표시한다").
 *
 * 끝났다고 보는 기준은 run.status 가 아니라 **페르소나 전원이 끝났는지**다. 한 명이 실패해도
 * 나머지는 계속 진행되므로 run 하나의 상태만 보면 남은 카드를 놓친다.
 */
export async function waitForStylistRun(
  runId: string,
  options: {
    signal?: AbortSignal;
    onProgress?: (run: ApiStylistRun) => void;
    /** 목업이 자리를 만들 때만 쓴다 (lib/stylistApi.ts 의 getStylistRun 주석 참고). */
    hint?: { personaIds: StylistId[]; question: string };
    /**
     * 그만 기다려도 되는 조건. 기본은 '전원 종료'다.
     * 재시도·다른 추천은 이미 전원이 끝난 run 을 다시 건드리는 것이라 기본 조건으로는
     * 첫 폴링에서 바로 끝나 버린다 — 그래서 부르는 쪽이 조건을 바꿔 넘긴다.
     */
    until?: (run: ApiStylistRun) => boolean;
  } = {},
): Promise<ApiStylistRun> {
  const { signal, onProgress, hint, until } = options;
  const startedAt = Date.now();

  const settled = (run: ApiStylistRun) =>
    run.results.length > 0 &&
    run.results.every((r) => r.status === 'SUCCEEDED' || r.status === 'FAILED');

  for (let attempt = 0; ; attempt += 1) {
    const run = await getStylistRun(runId, hint);
    onProgress?.(run);

    // 결과 자리가 아직 안 생겼는데 run 이 먼저 끝나 버린 경우(=스타일리스트 실행 자체가 실패)
    if (run.results.length === 0 && isTerminal(run.status)) return run;
    if (until ? until(run) : settled(run)) return run;

    if (Date.now() - startedAt > TIMEOUT_MS) throw new ChatRunTimeout();
    await sleep(delayFor(attempt), signal);
  }
}
