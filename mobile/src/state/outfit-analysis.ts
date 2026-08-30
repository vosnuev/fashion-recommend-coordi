import { useSyncExternalStore } from 'react';
import { AppState } from 'react-native';

import { ApiError } from '@/lib/apiClient';
import {
  getOutfitAnalysis,
  startOutfitAnalysis,
  type OutfitAnalysisStatus,
  type OutfitEvaluation,
} from '@/lib/outfitAnalysisApi';
import {
  clearOutfitAnalysisJob,
  getOutfitAnalysisJob,
  saveOutfitAnalysisJob,
} from '@/lib/secureStore';

export type OutfitAnalysisPhase = 'SUBMITTING' | OutfitAnalysisStatus;

export type OutfitAnalysisJob = {
  analysisId: string | null;
  phase: OutfitAnalysisPhase;
  pollUrl: string | null;
  pollAfterMs: number;
  estimatedSeconds: number | null;
  claimToken: string | null;
  /**
   * 같은 사진을 옷장 등록에도 넘겼을 때 생기는 job id. 안 넘겼으면 null.
   * 옷장 처리는 평가보다 훨씬 오래 걸려(수 분) 결과 화면에서 기다리지 않는다 —
   * 진행 상황과 뽑아낸 아이템은 분석 기록 상세에서 본다.
   */
  wardrobeJobId: string | null;
  photoUri: string;
  evaluation: OutfitEvaluation | null;
  detail: string | null;
  createdAt: string;
  finishedAt: string | null;
};

type Snapshot = {
  hydrated: boolean;
  job: OutfitAnalysisJob | null;
};

const DEFAULT_POLL_MS = 2_000;
const MIN_POLL_MS = 1_000;
let snapshot: Snapshot = { hydrated: false, job: null };
let bootstrapPromise: Promise<void> | null = null;
let pollTimer: ReturnType<typeof setTimeout> | null = null;
let pollInFlight = false;
let appStateSubscribed = false;
const listeners = new Set<() => void>();

function emit(next: Snapshot) {
  snapshot = next;
  listeners.forEach((listener) => listener());
}

function isPending(job: OutfitAnalysisJob | null): boolean {
  return job?.phase === 'SUBMITTING' || job?.phase === 'QUEUED' || job?.phase === 'PROCESSING';
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 400) {
    return '사진 형식이나 용량을 확인하고 다시 선택해 주세요.';
  }
  if (error instanceof ApiError && error.status === 503) {
    return '분석 서비스가 잠시 바빠요. 잠시 후 다시 시도해 주세요.';
  }
  if (error instanceof ApiError) return error.message;
  return error instanceof Error && error.message
    ? error.message
    : '서버에 연결하지 못했어요. 네트워크를 확인해 주세요.';
}

async function persist(job: OutfitAnalysisJob | null) {
  if (!job) {
    await clearOutfitAnalysisJob();
    return;
  }
  await saveOutfitAnalysisJob(JSON.stringify(job));
}

function cancelPoll() {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = null;
}

function schedulePoll(delayMs: number) {
  cancelPoll();
  pollTimer = setTimeout(() => void pollNow(), Math.max(MIN_POLL_MS, delayMs));
}

async function pollNow(): Promise<void> {
  const current = snapshot.job;
  if (!current?.pollUrl || !isPending(current) || current.phase === 'SUBMITTING' || pollInFlight) return;

  pollInFlight = true;
  try {
    const response = await getOutfitAnalysis(current.pollUrl);
    /* analysisId·pollUrl·claimToken·wardrobeJobId 는 접수 때 정해진 뒤 바뀌지 않는다 —
       폴링은 진행 상태만 갱신하고 신원은 ...current 로 그대로 물려받는다. */
    const next: OutfitAnalysisJob = {
      ...current,
      phase: response.status,
      evaluation: response.evaluation,
      detail: response.detail,
      pollAfterMs: response.poll_after_ms ?? current.pollAfterMs,
      createdAt: response.created_at || current.createdAt,
      finishedAt: response.finished_at,
    };
    emit({ ...snapshot, job: next });
    await persist(next);
    if (isPending(next)) schedulePoll(next.pollAfterMs);
  } catch (error) {
    if (error instanceof ApiError && (error.status === 404 || error.status === 410)) {
      const latest = snapshot.job;
      if (latest) {
        const expired: OutfitAnalysisJob = {
          ...latest,
          phase: 'FAILED',
          detail: '이 분석 결과를 조회할 수 있는 기간이 지났어요. 새 사진으로 다시 분석해 주세요.',
          finishedAt: new Date().toISOString(),
        };
        emit({ ...snapshot, job: expired });
        await persist(expired);
      }
      return;
    }
    // 일시적인 네트워크 오류로 서버 작업 자체를 실패 처리하지 않고 다음 폴링에서 복구한다.
    const latest = snapshot.job;
    if (latest && isPending(latest)) {
      emit({ ...snapshot, job: { ...latest, detail: errorMessage(error) } });
      schedulePoll(Math.max(latest.pollAfterMs, 5_000));
    }
  } finally {
    pollInFlight = false;
  }
}

async function bootstrap(): Promise<void> {
  if (bootstrapPromise) return bootstrapPromise;
  bootstrapPromise = (async () => {
    const raw = await getOutfitAnalysisJob();
    let job: OutfitAnalysisJob | null = null;
    if (raw) {
      try {
        job = JSON.parse(raw) as OutfitAnalysisJob;
      } catch {
        await clearOutfitAnalysisJob();
      }
    }
    emit({ hydrated: true, job });
    if (job && isPending(job)) void pollNow();

    if (!appStateSubscribed) {
      appStateSubscribed = true;
      AppState.addEventListener('change', (state) => {
        if (state === 'active') void pollNow();
      });
    }
  })();
  return bootstrapPromise;
}

/**
 * 분석 접수. `saveToWardrobe` 를 켜면 같은 사진이 옷장 등록 파이프라인에도 들어간다.
 * 옷장은 사용자 소유 데이터라 백엔드가 비로그인 요청에서는 이 값을 무시한다.
 */
async function start(photoUri: string, saveToWardrobe = false): Promise<void> {
  if (isPending(snapshot.job)) throw new Error('진행 중인 착장 분석이 있어요.');
  cancelPoll();

  const submitting: OutfitAnalysisJob = {
    analysisId: null,
    phase: 'SUBMITTING',
    pollUrl: null,
    pollAfterMs: DEFAULT_POLL_MS,
    estimatedSeconds: null,
    claimToken: null,
    wardrobeJobId: null,
    photoUri,
    evaluation: null,
    detail: null,
    createdAt: new Date().toISOString(),
    finishedAt: null,
  };
  emit({ hydrated: true, job: submitting });

  try {
    const accepted = await startOutfitAnalysis(photoUri, { saveToWardrobe });
    const queued: OutfitAnalysisJob = {
      ...submitting,
      analysisId: accepted.analysis_id,
      phase: accepted.status,
      pollUrl: accepted.poll_url,
      pollAfterMs: accepted.poll_after_ms || DEFAULT_POLL_MS,
      estimatedSeconds: accepted.estimated_seconds,
      claimToken: accepted.claim_token,
      wardrobeJobId: accepted.wardrobe_job_id,
    };
    emit({ ...snapshot, job: queued });
    await persist(queued);
    schedulePoll(queued.pollAfterMs);
  } catch (error) {
    const failed: OutfitAnalysisJob = {
      ...submitting,
      phase: 'FAILED',
      detail: errorMessage(error),
      finishedAt: new Date().toISOString(),
    };
    emit({ ...snapshot, job: failed });
    await persist(failed);
    throw error;
  }
}

async function clear(): Promise<void> {
  cancelPoll();
  emit({ hydrated: true, job: null });
  await persist(null);
}

export const outfitAnalysisStore = {
  bootstrap,
  start,
  clear,
  pollNow,
  isPending,
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getSnapshot() {
    return snapshot;
  },
};

export function useOutfitAnalysis() {
  return useSyncExternalStore(
    outfitAnalysisStore.subscribe,
    outfitAnalysisStore.getSnapshot,
    outfitAnalysisStore.getSnapshot,
  );
}
