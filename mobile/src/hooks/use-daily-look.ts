import { useCallback, useEffect, useRef, useState } from 'react';

import { getTodayLook, isDailyLookPending, type DailyLook } from '@/lib/dailyLookApi';

/** 서버가 poll_after_ms 를 안 주는 비정상 응답에 대비한 안전값 */
const FALLBACK_POLL_MS = 2_000;
/**
 * 무한 폴링 방지 상한. 생성은 보통 수 초~수십 초에 끝난다 — 이걸 넘겼다는 건
 * 워커가 멈춰 있다는 뜻이라 그만두고 `stalled` 로 알린다. 조용히 멈추기만 하면
 * 화면은 영원히 "만드는 중" 스켈레톤에 갇힌다.
 */
const MAX_POLL_MS = 3 * 60 * 1000;

type DailyLookHook = {
  look: DailyLook | null;
  loading: boolean;
  error: string | null;
  /** 폴링 상한을 넘겨 포기했다 — 화면은 '생성 중'이 아니라 '준비하지 못함'으로 그린다 */
  stalled: boolean;
  reload: () => Promise<void>;
};

/**
 * 오늘의 룩 훅 — 조회하고, 생성 중이면 서버가 알려준 간격(poll_after_ms)으로 폴링한다.
 *
 * 폴링 종료는 서버 계약(DailyLookTodayView 문서)을 따른다:
 *   SUCCEEDED → result 표시 / EMPTY → 재시도해도 같은 결과이므로 중단 /
 *   FAILED → 자동 재시도 없음. 화면을 벗어나면 멈추고, 서버 생성은 그대로 진행된다.
 * 재예약을 회차 카운터로 보장하는 구조는 use-outfit-analysis-detail 과 같다
 * (응답이 실패하거나 내용이 그대로면 효과가 다시 돌지 않아 폴링이 조용히 죽는 문제).
 *
 * `seed` 는 홈 API(GET /api/v1/home/)가 같이 내려준 상태다. 세 값을 구분한다:
 *   - `undefined`: 시드 제공자가 아직 응답 전 → **자체 조회를 미룬다.**
 *     여기서 곧바로 조회하면 홈과 같은 것을 두 번 묻게 되고, 그 왕복 동안
 *     화면이 "아직 모름"으로 남아 목업이 끼어들 틈이 생긴다.
 *   - `DailyLook`: 그대로 첫 값으로 쓴다(왕복 0회). 생성 중이면 이어서 폴링한다.
 *   - `null`: 시드가 없다(홈 선반영 실패/시드를 안 쓰는 화면) → 직접 조회한다.
 * 기본값이 `null` 이라, 시드를 안 넘기는 호출부(룩 상세 등)는 예전과 똑같이 동작한다.
 */
export function useDailyLook(
  enabled = true,
  seed: DailyLook | null | undefined = null,
): DailyLookHook {
  const [look, setLook] = useState<DailyLook | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const [stalled, setStalled] = useState(false);
  /* 폴링 회차. 응답이 실패하거나 내용이 같아도 이 값이 늘어 다음 회차가 예약된다. */
  const [pollCount, setPollCount] = useState(0);

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startedAtRef = useRef(0);
  /* 화면을 떠난 뒤 도착한 응답으로 상태를 건드리지 않는다. */
  const aliveRef = useRef(true);
  /* 마지막으로 반영한 시드. 참조로 비교해 홈이 다시 불러왔을 때만 갈아끼운다 —
     값으로 비교하면 폴링으로 이미 더 새로운 상태를 받아둔 것을 되돌릴 수 있다. */
  const seedRef = useRef<DailyLook | null>(null);
  /* 시드를 한 번이라도 받았는가. 받았다면 자체 조회는 하지 않는다. */
  const seededRef = useRef(false);

  const load = useCallback(async () => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    setStalled(false);
    startedAtRef.current = Date.now();
    try {
      const res = await getTodayLook();
      if (!aliveRef.current) return;
      setLook(res);
    } catch (e) {
      if (!aliveRef.current) return;
      setLook(null);
      setError(e instanceof Error ? e.message : '오늘의 룩을 불러오지 못했어요');
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, [enabled]);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  // 첫 값 확보: 시드가 있으면 그것을, 없으면 직접 조회. (시드 제공자 응답 전에는 대기)
  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    if (seed === undefined) return; // 홈 응답 대기 중
    if (seed !== null) {
      if (seed === seedRef.current) return; // 같은 시드를 다시 반영하지 않는다
      seedRef.current = seed;
      seededRef.current = true;
      startedAtRef.current = Date.now();
      setStalled(false);
      setLook(seed);
      setLoading(false);
      return;
    }
    if (seededRef.current) return; // 시드를 받아둔 뒤의 null 은 무시
    load();
  }, [enabled, seed, load]);

  // 생성이 끝날 때까지만 다시 부른다 (EMPTY/FAILED 는 재시도해도 같다 — 서버 계약)
  useEffect(() => {
    if (!enabled || !isDailyLookPending(look)) return;
    if (Date.now() - startedAtRef.current > MAX_POLL_MS) {
      /* 워커가 멈춰 있다. 화면이 "만드는 중"에 갇히지 않도록 알린다. */
      setStalled(true);
      return;
    }

    timerRef.current = setTimeout(() => {
      if (!aliveRef.current) return;
      getTodayLook()
        .then((res) => {
          if (aliveRef.current) setLook(res);
        })
        // 일시적인 실패로 화면을 에러로 바꾸지 않는다 — 다음 회차에 복구된다.
        .catch(() => {})
        .finally(() => {
          if (aliveRef.current) setPollCount((n) => n + 1);
        });
    }, look?.poll_after_ms ?? FALLBACK_POLL_MS);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [look, enabled, pollCount]);

  return { look, loading, error, stalled, reload: load };
}
