import { useCallback, useEffect, useRef, useState } from 'react';

import {
  fitDailyLookToMannequin,
  getVirtualTryOn,
  isVirtualTryOnPending,
  type VirtualTryOnJob,
} from '@/lib/virtualTryOnApi';

/** 서버가 poll_after_ms 를 안 주는 비정상 응답 대비 */
const FALLBACK_POLL_MS = 5_000;
/**
 * 무한 폴링 방지 상한. 이미지 생성은 보통 수십 초~2분이다. 이걸 넘겼다는 건 워커가
 * 멈춰 있다는 뜻이라 그만두고 `stalled` 로 알린다 — 조용히 멈추면 화면은 영원히
 * "생성 중"에 갇힌다.
 */
const MAX_POLL_MS = 5 * 60 * 1000;

type VirtualTryOnHook = {
  job: VirtualTryOnJob | null;
  /** 첫 조회(재진입 복원) 중 */
  loading: boolean;
  /** 사진을 올려 접수하는 중 */
  submitting: boolean;
  error: string | null;
  /** 폴링 상한을 넘겨 포기했다 */
  stalled: boolean;
  submit: (personUri: string) => Promise<void>;
  reload: () => Promise<void>;
};

/**
 * 가상 피팅 — 접수하고, 만드는 동안 서버가 알려준 간격으로 물어본다.
 *
 * **화면에 들어오면 먼저 조회한다.** 그 룩의 마지막 작업이 서버에 있으므로,
 * 나갔다 온 사용자는 사진을 다시 고르지 않아도 생성 중·완성된 결과를 그대로 본다.
 * (작업 id 를 앱이 들고 있지 않는 이유이기도 하다 — 기기를 바꿔도 이어진다.)
 *
 * 재예약을 회차 카운터로 보장하는 구조는 use-daily-look 과 같다. 응답이 실패하거나
 * 내용이 그대로면 효과가 다시 돌지 않아 폴링이 조용히 죽는 문제를 막는다.
 */
export function useVirtualTryOn(lookId?: string, goldenId?: string): VirtualTryOnHook {
  const [job, setJob] = useState<VirtualTryOnJob | null>(null);
  const [loading, setLoading] = useState(Boolean(lookId));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stalled, setStalled] = useState(false);
  const [pollCount, setPollCount] = useState(0);

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startedAtRef = useRef(0);
  /* 화면을 떠난 뒤 도착한 응답으로 상태를 건드리지 않는다. */
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const fetchOnce = useCallback(async () => {
    if (!lookId) return;
    try {
      const next = await getVirtualTryOn(lookId, goldenId);
      if (!aliveRef.current) return;
      setJob(next);
      setError(null);
    } catch (e) {
      if (!aliveRef.current) return;
      setError(e instanceof Error ? e.message : '가상 피팅 상태를 불러오지 못했어요.');
    }
  }, [lookId, goldenId]);

  /* 진입·재진입 복원 */
  useEffect(() => {
    if (!lookId) {
      setJob(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setStalled(false);
    startedAtRef.current = 0;
    void fetchOnce().finally(() => {
      if (aliveRef.current) setLoading(false);
    });
  }, [lookId, fetchOnce]);

  /* 생성 중이면 서버가 준 간격으로 다시 물어본다 */
  useEffect(() => {
    if (!lookId || !isVirtualTryOnPending(job) || stalled) return;
    if (startedAtRef.current === 0) startedAtRef.current = Date.now();
    if (Date.now() - startedAtRef.current > MAX_POLL_MS) {
      setStalled(true);
      return;
    }
    const wait = job?.poll_after_ms ?? FALLBACK_POLL_MS;
    timerRef.current = setTimeout(() => {
      void fetchOnce().finally(() => {
        /* 응답이 실패했거나 내용이 같아도 다음 회차가 예약되도록 카운터를 올린다. */
        if (aliveRef.current) setPollCount((n) => n + 1);
      });
    }, wait);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [lookId, job, stalled, pollCount, fetchOnce]);

  const submit = useCallback(
    async (personUri: string) => {
      if (!lookId) throw new Error('추천 룩 정보를 찾을 수 없어요.');
      setSubmitting(true);
      setError(null);
      setStalled(false);
      startedAtRef.current = 0;
      try {
        const accepted = await fitDailyLookToMannequin(lookId, personUri, goldenId);
        if (!aliveRef.current) return;
        /* 접수 응답이 곧 첫 상태다(캐시 적중이면 이미 SUCCEEDED). 여기서 폴링이 시작된다. */
        setJob(accepted);
        setPollCount((n) => n + 1);
      } finally {
        if (aliveRef.current) setSubmitting(false);
      }
    },
    [lookId, goldenId],
  );

  return { job, loading, submitting, error, stalled, submit, reload: fetchOnce };
}
