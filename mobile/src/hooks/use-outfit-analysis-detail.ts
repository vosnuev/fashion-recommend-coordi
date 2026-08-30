import { useCallback, useEffect, useRef, useState } from 'react';

import { getOwnedOutfitAnalysis, type OutfitAnalysisOwnedDetail } from '@/lib/outfitHistoryApi';

/** 옷장 등록은 GPU 파이프라인이라 몇 분이 걸린다 — 평가 폴링(3초)보다 느슨하게 본다. */
const WARDROBE_POLL_MS = 5_000;
/**
 * 무한 폴링 방지 상한. 처음엔 회의록의 "약 5분" 추정으로 8분을 잡았는데 실측이 그보다 길어
 * 폴링이 먼저 끊겼다 — 그러면 화면은 영영 "처리 중"으로 남는다. 상한은 넉넉히 두고,
 * 걸렸을 때는 stalled 로 알려 사용자가 직접 새로고침할 수 있게 한다.
 */
const MAX_POLL_MS = 15 * 60 * 1000;



type Result = {
  analysis: OutfitAnalysisOwnedDetail | null;
  loading: boolean;
  error: string | null;
  /** 옷장 등록이 안 끝났는데 폴링 상한에 걸렸다 — 화면은 무한 스피너 대신 새로고침을 권해야 한다. */
  stalled: boolean;
  reload: () => Promise<void>;
};

function wardrobePending(analysis: OutfitAnalysisOwnedDetail | null): boolean {
  const status = analysis?.wardrobe?.status;
  return status === 'PENDING' || status === 'PROCESSING';
}

/**
 * 분석 기록 상세.
 *
 * 옷장 연계가 진행 중이면 같은 엔드포인트를 다시 부른다 — 백엔드가 상세 응답에 옷장 job 상태를
 * 함께 실어주므로 옷장 API 를 따로 폴링할 필요가 없다.
 * 화면을 벗어나면 멈춘다. 서버 작업은 그대로 진행되고, 다시 들어오면 그때 상태를 받는다.
 */
export function useOutfitAnalysisDetail(analysisId: string | undefined): Result {
  const [analysis, setAnalysis] = useState<OutfitAnalysisOwnedDetail | null>(null);
  const [loading, setLoading] = useState(Boolean(analysisId));
  const [error, setError] = useState<string | null>(null);
  const [stalled, setStalled] = useState(false);
  /* 폴링 회차. 응답이 실패하거나 내용이 같아도 이 값이 늘어 다음 회차가 예약된다. */
  const [pollCount, setPollCount] = useState(0);

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startedAtRef = useRef(0);
  /* 화면을 떠난 뒤 도착한 응답으로 상태를 건드리지 않는다. */
  const aliveRef = useRef(true);

  const load = useCallback(async () => {
    if (!analysisId) return;
    setLoading(true);
    setError(null);
    setStalled(false);
    startedAtRef.current = Date.now();
    try {
      const res = await getOwnedOutfitAnalysis(analysisId);
      if (!aliveRef.current) return;
      setAnalysis(res);
    } catch (e) {
      if (!aliveRef.current) return;
      setAnalysis(null);
      setError(e instanceof Error ? e.message : '분석 기록을 불러오지 못했어요');
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, [analysisId]);

  useEffect(() => {
    aliveRef.current = true;
    startedAtRef.current = Date.now();
    load();
    return () => {
      aliveRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [load]);

  // 옷장 등록이 끝날 때까지만 다시 부른다
  useEffect(() => {
    if (!analysisId) return;
    if (!wardrobePending(analysis)) return;
    if (Date.now() - startedAtRef.current > MAX_POLL_MS) {
      // 여기서 그냥 멈추면 화면은 계속 "처리 중"으로 보인다 — 멈췄다는 걸 알린다.
      setStalled(true);
      return;
    }

    timerRef.current = setTimeout(() => {
      if (!aliveRef.current) return;
      getOwnedOutfitAnalysis(analysisId)
        .then((res) => {
          if (aliveRef.current) setAnalysis(res);
        })
        // 일시적인 실패로 화면을 에러로 바꾸지 않는다 — 다음 회차에 복구된다.
        .catch(() => {})
        /* 성공이든 실패든 회차를 세어 다음 폴링을 반드시 예약한다.
           예전엔 재예약이 analysis 가 바뀌는 것에만 달려 있어서, 응답이 실패하거나
           내용이 그대로면 효과가 다시 돌지 않아 폴링이 조용히 죽었다 — 그러면 화면은
           영영 "처리 중"이다. */
        .finally(() => {
          if (aliveRef.current) setPollCount((n) => n + 1);
        });
    }, WARDROBE_POLL_MS);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [analysis, analysisId, pollCount]);

  return { analysis, loading, error, stalled, reload: load };
}
