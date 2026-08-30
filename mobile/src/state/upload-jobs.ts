import { useSyncExternalStore } from 'react';

import { bumpWardrobeRevision } from '@/state/wardrobe-revision';

import {
  createWardrobeBatch,
  getUploadJob,
  getWardrobeBatch,
  registerItemToSharedRoom,
  uploadWardrobePhoto,
  type WardrobeBatchCreated,
  type WardrobeBatchItemInput,
  type WardrobeBatchStatus,
} from '@/lib/wardrobeApi';

/**
 * 옷 등록 진행 상황 — 화면이 아니라 여기서 돌린다.
 *
 * 등록은 서버가 큐에 넣고 처리하므로, 사용자가 등록 화면을 닫아도 처리는 계속된다.
 * 폴링을 화면에 두면 화면을 닫는 순간 결과를 놓치므로 스토어로 올렸다.
 * 옷장 화면이 이걸 구독해 '등록 중'을 보여주고, 끝나면 목록을 새로 고친다.
 *
 * 두 갈래를 같은 규칙으로 다룬다.
 *   · 사진 1장 업로드 (uploadJobs.start)        — 앨범·카메라
 *   · 상품 여러 건 일괄 등록 (uploadJobs.startBatch) — 인앱 브라우저 가져오기
 */

export type UploadPhase = 'uploading' | 'processing' | 'failed';

export type UploadJobState = {
  /** 화면에서 구분하기 위한 로컬 키 (서버 job_id 는 접수 후에 생긴다) */
  key: string;
  phase: UploadPhase;
  error?: string;
};

/** 가져오기 배치 하나의 진행 상황. 서버 counts 를 그대로 옮겨 담는다. */
export type ImportBatchState = {
  batchId: string;
  status: WardrobeBatchStatus;
  total: number;
  done: number;
  failed: number;
  /** 진행 상태를 더 못 물어본 이유(조회 실패·너무 오래 걸림). 배치 자체는 서버에서 계속 돈다. */
  error?: string;
};

/** 폴링 간격·한도 — 누끼+캡셔닝이 GPU 큐를 타므로 즉시 끝나지 않는다. */
const POLL_INTERVAL_MS = 1500;
const POLL_TIMEOUT_MS = 120_000;

/**
 * 배치 폴링을 포기하는 시점. 한 장에 8초(서버 추정) × 최대 30장이면 4분이고,
 * GPU 큐가 밀리면 더 걸린다. 넉넉히 두되 무한정 묻지는 않는다.
 * 간격은 우리가 정하지 않고 서버가 준 poll_after_ms 를 따른다.
 */
const BATCH_POLL_TIMEOUT_MS = 10 * 60_000;

let jobs: UploadJobState[] = [];
let batches: ImportBatchState[] = [];
/** 하나 끝날 때마다 증가. 옷장이 이 값을 보고 목록을 다시 불러오고 성공 토스트를 띄운다. */
let completed = 0;
let seq = 0;

const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((l) => l());
}

function update(key: string, patch: Partial<UploadJobState>) {
  jobs = jobs.map((j) => (j.key === key ? { ...j, ...patch } : j));
  notify();
}

function drop(key: string) {
  jobs = jobs.filter((j) => j.key !== key);
  notify();
}

function updateBatch(batchId: string, patch: Partial<ImportBatchState>) {
  batches = batches.map((b) => (b.batchId === batchId ? { ...b, ...patch } : b));
  notify();
}

/**
 * 배치가 끝날 때까지 따라간다. 화면이 닫혀도 계속된다.
 * 끝난 배치는 목록에 남겨 둔다 — "몇 벌 담겼는지"를 알려주고 사용자가 닫게 한다.
 */
function trackBatch(batchId: string, firstDelayMs: number) {
  const deadline = Date.now() + BATCH_POLL_TIMEOUT_MS;

  /* 재귀 setTimeout — setInterval 은 응답이 간격보다 느릴 때 요청이 겹친다. */
  const poll = async () => {
    try {
      const batch = await getWardrobeBatch(batchId);
      const before = batches.find((b) => b.batchId === batchId)?.done ?? 0;
      updateBatch(batchId, {
        status: batch.status,
        total: batch.counts.total,
        done: batch.counts.done,
        failed: batch.counts.failed,
        error: undefined,
      });
      /* 새 옷이 들어왔으면 옷장 목록만 조용히 다시 불러오게 한다. 배치는 옷이 여러 벌 들어와서
         completed 를 쓰면 한 건마다 토스트가 떠 시끄럽다 — 진행 표시는 배치 자체가 한다.
         신호는 삭제 등 다른 경로와 공유한다(state/wardrobe-revision.ts). */
      if (batch.counts.done > before) bumpWardrobeRevision();
      if (batch.poll_after_ms === null) return; // 종료 — 더 묻지 않는다
      if (Date.now() > deadline) {
        updateBatch(batchId, {
          error: '처리가 오래 걸리고 있어요. 잠시 후 옷장을 새로고침해 주세요.',
        });
        return;
      }
      setTimeout(poll, batch.poll_after_ms);
    } catch (e) {
      /* 직전 진행 상황은 남긴 채 멈춘다. 사용자가 닫을 수 있게 사유만 붙인다. */
      updateBatch(batchId, {
        error: e instanceof Error ? e.message : '진행 상태를 확인하지 못했어요',
      });
    }
  };
  setTimeout(poll, firstDelayMs);
}

export const uploadJobs = {
  getJobs: () => jobs,
  getBatches: () => batches,
  getCompleted: () => completed,

  /** 사진 한 장을 올리고 처리가 끝날 때까지 따라간다. 화면이 닫혀도 계속된다. */
  start(uri: string, opts?: { name?: string; mimeType?: string; sharedRoomId?: string; sharedRoomIds?: string[]; skipProcessing?: boolean; itemName?: string; category?: string }): Promise<void> {
    return new Promise<void>((resolve) => {
      const key = `u${++seq}`;
      jobs = [...jobs, { key, phase: 'uploading' }];
      notify();

      const roomIds = opts?.sharedRoomIds && opts.sharedRoomIds.length > 0
        ? opts.sharedRoomIds
        : (opts?.sharedRoomId ? [opts.sharedRoomId] : []);
      const primaryRoomId = roomIds[0];
      const extraRoomIds = roomIds.slice(1);

      (async () => {
        let jobId: string;
        try {
          jobId = (await uploadWardrobePhoto(uri, {
            name: opts?.name,
            mimeType: opts?.mimeType,
            skipProcessing: opts?.skipProcessing,
            itemName: opts?.itemName,
            category: opts?.category,
            /* 첫 번째 방은 백엔드 pending_share_room 예약으로 넘긴다 */
            sharedRoomId: primaryRoomId,
          })).job_id;
        } catch (e) {
          update(key, {
            phase: 'failed',
            error: e instanceof Error ? e.message : '사진을 올리지 못했어요',
          });
          resolve();
          return;
        }
        update(key, { phase: 'processing' });

        const startedAt = Date.now();
        /* 재귀 setTimeout — setInterval 은 응답이 간격보다 느릴 때 요청이 겹친다. */
        const poll = async () => {
          try {
            const job = await getUploadJob(jobId);
            if (job.status === 'DONE') {
              /* 추가로 선택한 공유 방들이 있으면 생성된 아이템에 대해 직접 등록한다 */
              if (extraRoomIds.length > 0 && job.items?.length) {
                for (const item of job.items) {
                  for (const rId of extraRoomIds) {
                    try {
                      await registerItemToSharedRoom(rId, item.id);
                    } catch (e) {
                      if (__DEV__) console.warn('추가 방 공유 실패:', rId, e);
                    }
                  }
                }
              }
              drop(key);
              completed += 1;
              notify();
              resolve();
              return;
            }
            if (job.status === 'FAILED') {
              update(key, {
                phase: 'failed',
                error: job.error_message || '사진을 처리하지 못했어요',
              });
              resolve();
              return;
            }
            if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
              update(key, {
                phase: 'failed',
                error: '처리가 오래 걸리고 있어요. 잠시 후 옷장을 새로고침해 주세요.',
              });
              resolve();
              return;
            }
            setTimeout(poll, POLL_INTERVAL_MS);
          } catch (e) {
            update(key, {
              phase: 'failed',
              error: e instanceof Error ? e.message : '처리 상태를 확인하지 못했어요',
            });
            resolve();
          }
        };
        setTimeout(poll, POLL_INTERVAL_MS);
      })();
    });
  },

  /**
   * 인앱 브라우저에서 고른 상품들을 한 번에 접수하고 끝까지 따라간다.
   *
   * 접수(POST)는 **기다려야 한다** — 서버가 그 자리에서 이미지를 하나씩 내려받아
   * S3 에 올린 뒤에야 202 를 준다. 장수가 많으면 십여 초가 걸린다.
   * 그래서 이 함수만 async 이고(화면이 버튼을 잠글 수 있게), 그 뒤 태깅 진행은
   * 다른 등록과 똑같이 백그라운드에서 따라간다.
   *
   * 접수 자체가 실패하면(전부 거절 등) 던진다 — 화면이 사유를 보여줄 수 있어야 한다.
   */
  async startBatch(items: WardrobeBatchItemInput[]): Promise<WardrobeBatchCreated> {
    const created = await createWardrobeBatch(items);
    batches = [
      ...batches,
      {
        batchId: created.batch_id,
        status: created.status,
        // total_count 는 보낸 건수 전체다. 이미지를 못 받은 건은 곧바로 실패로 잡힌다.
        total: created.total_count,
        done: 0,
        failed: created.rejected.length,
      },
    ];
    notify();
    trackBatch(created.batch_id, created.poll_after_ms);
    return created;
  },

  /** 실패 알림을 사용자가 닫는다 */
  dismiss(key: string) {
    drop(key);
  },

  /** 끝난(또는 멈춘) 배치 알림을 사용자가 닫는다 */
  dismissBatch(batchId: string) {
    batches = batches.filter((b) => b.batchId !== batchId);
    notify();
  },

  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};

export function useUploadJobs(): UploadJobState[] {
  return useSyncExternalStore(uploadJobs.subscribe, uploadJobs.getJobs, uploadJobs.getJobs);
}

/** 가져오기 배치 진행 상황 — 옷장 화면이 진행/결과 줄을 그린다. */
export function useImportBatches(): ImportBatchState[] {
  return useSyncExternalStore(uploadJobs.subscribe, uploadJobs.getBatches, uploadJobs.getBatches);
}

/** 등록이 하나 끝날 때마다 값이 바뀐다 — 목록을 다시 불러올 신호. */
export function useUploadCompleted(): number {
  return useSyncExternalStore(uploadJobs.subscribe, uploadJobs.getCompleted, uploadJobs.getCompleted);
}

/** 아직 처리 중인 배치인지 */
export function isBatchRunning(batch: ImportBatchState): boolean {
  return !batch.error && (batch.status === 'PENDING' || batch.status === 'PROCESSING');
}
