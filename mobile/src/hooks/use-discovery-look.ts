import { getDiscoveryLook, type DiscoveryLookDto } from '@/lib/discoveryLookApi';
import { useCallback, useEffect, useState } from 'react';

/**
 * 둘러보기(큐레이션) 룩의 id 인가.
 *
 * 상세 화면은 '오늘의 룩'과 둘러보기 룩을 같은 라우트로 받는다. 어느 쪽인지에 따라
 * 기다리는 대상도, 실패했을 때 할 말도 달라서 판정을 한 곳에 둔다.
 */
export function isDiscoveryLookId(id?: string): boolean {
  return !!id && (id.startsWith('curated-') || id.startsWith('naver-'));
}

type DiscoveryLookResult = {
  look: DiscoveryLookDto | null;
  /** 조회 중. 이 동안 화면은 **목업으로 물러나면 안 된다** — 아래 주석 참고. */
  loading: boolean;
  failed: boolean;
  reload: () => void;
};

/**
 * 둘러보기 룩 단건 조회.
 *
 * loading·failed 를 함께 돌려주는 이유: 예전에는 룩만 돌려줘서 호출부가 "아직 안 온 것"과
 * "없는 것"을 구분하지 못했다. 그 결과 상세 화면이 조회가 끝날 때까지 번들 목업 룩을
 * 그리고, 응답이 오면 사진이 통째로 바뀌었다 — 방금 본 것이 가짜였다는 인상만 남는다.
 */
export function useDiscoveryLook(id?: string): DiscoveryLookResult {
  const [look, setLook] = useState<DiscoveryLookDto | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    let active = true;
    setLook(null);
    setFailed(false);
    if (!isDiscoveryLookId(id)) {
      setLoading(false);
      return () => {
        active = false;
      };
    }
    setLoading(true);
    void getDiscoveryLook(id!)
      .then((result) => {
        if (!active) return;
        setLook(result);
        setLoading(false);
      })
      .catch(() => {
        if (!active) return;
        setLook(null);
        setFailed(true);
        setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [id, attempt]);

  return { look, loading, failed, reload };
}
