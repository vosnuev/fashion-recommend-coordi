import { useEffect, useState } from 'react';

import { PursuitEndpoint } from '@/constants/config';
import { api } from '@/lib/apiClient';
import { fetchBodyBasic } from '@/state/measure';

/**
 * 마이 메뉴 힌트용 요약 — "설정했나 / 무엇으로 설정했나"만 알면 되는 값들.
 *
 * 화면별 스토어를 안 쓰고 여기서 따로 부르는 이유: 체형·추구미는 **서버에 저장돼 있고**
 * 앱을 다시 켜면 로컬 스토어가 비어 있다. 그 상태로 힌트를 그리면 이미 측정을 마친 사람에게도
 * 계속 "측정하기"라고 말하게 된다(그게 원래 문제였다).
 *
 * 실패하면 조용히 미설정으로 둔다 — 힌트 한 줄 때문에 마이 화면 전체를 에러로 만들 이유가 없다.
 */

export type ProfileSummary = {
  /** 저장된 키(cm) — 없으면 null */
  height: number | null;
  weight: number | null;
  /** 추구미에서 '좋아요'로 고른 항목 수 */
  pursuitCount: number;
  loading: boolean;
};

type PursuitPayload = { preferred?: Record<string, string[]> } | null;

function countSelections(payload: PursuitPayload): number {
  if (!payload?.preferred) return 0;
  return Object.values(payload.preferred).reduce(
    (sum, values) => sum + (Array.isArray(values) ? values.length : 0),
    0,
  );
}

export function useProfileSummary(): ProfileSummary {
  const [summary, setSummary] = useState<Omit<ProfileSummary, 'loading'>>({
    height: null,
    weight: null,
    pursuitCount: 0,
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;

    /* 둘은 서로를 기다릴 이유가 없다 — 하나가 느리다고 나머지 힌트까지 늦게 뜨면 안 된다. */
    Promise.allSettled([
      fetchBodyBasic(),
      api.get<PursuitPayload>(PursuitEndpoint),
    ]).then(([body, pursuit]) => {
      if (!alive) return;
      setSummary({
        height: body.status === 'fulfilled' ? body.value.height : null,
        weight: body.status === 'fulfilled' ? body.value.weight : null,
        pursuitCount: pursuit.status === 'fulfilled' ? countSelections(pursuit.value) : 0,
      });
      setLoading(false);
    });

    return () => {
      alive = false;
    };
  }, []);

  return { ...summary, loading };
}
