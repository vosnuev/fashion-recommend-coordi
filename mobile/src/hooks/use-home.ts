import { useCallback, useEffect, useState } from 'react';

import { HomeEndpoint } from '@/constants/config';
import { api } from '@/lib/apiClient';
import type { DailyLook } from '@/lib/dailyLookApi';

/**
 * 홈 화면 데이터. 백엔드 HomeResponse(api/apps/home) 와 필드명을 맞춘다.
 * weather 는 실제(기상청), quick_recommends/closet_count/saved_look_count 는 현재 mock 자리채움
 * (백엔드가 필드명을 최종형태로 고정 → 실제 로직으로 바뀌어도 프론트는 안 고쳐도 됨).
 */
export type HomeWeather = {
  region: string | null;
  temperature: number | null;
  sky_state: string | null;
  is_stale: boolean;
  observed_at: string | null;
};

/**
 * 기온 구간 템플릿. **추천이 아니다** — '오늘 날씨엔 이런 옷' 수준의 보조 문구다.
 * 실제 추천은 daily_look 이며, 이 값을 추천 카드로 승격시키면 아직 만들어지지도 않은
 * 추천이 완성된 것처럼 보인다(그래서 생성 중일 때의 힌트로만 쓴다).
 */
export type HomeTodayLook = {
  comment: string;
  tags: string[];
  image?: string | null;
};

export type HomeData = {
  nickname: string;
  weather: HomeWeather;
  today_look: HomeTodayLook;
  /**
   * 오늘의 룩(추천 API)의 **현재 상태**. 홈 진입이 곧 생성 트리거라 백엔드가
   * 걸어두는 김에 같은 시리얼라이저로 실어 보낸다 — 덕분에 첫 프레임부터
   * "생성 중 / 완성 / 없음"을 구분해 그릴 수 있고, 완성돼 있으면 왕복이 한 번 준다.
   * null 은 "룩이 없음"이 아니라 "상태를 알 수 없음"(선반영 실패) — 이때는
   * useDailyLook 이 조회 엔드포인트로 직접 물어본다.
   */
  daily_look: DailyLook | null;
  quick_recommends: string[];
  closet_count: number;
  saved_look_count: number;
};

export type Coords = { lat: number; lon: number };

type HomeResult = {
  data: HomeData | null;
  error: string | null;
  loading: boolean;
  reload: () => Promise<void>;
};

/**
 * 홈 화면 데이터 훅.
 * - coords 를 주면 위치 기반 날씨, 없으면 백엔드가 서울 기본값으로 응답한다.
 * - 로딩/에러 상태와 재시도(reload) 를 제공한다. (JWT 는 apiClient 가 자동 부착)
 */
export function useHome(coords?: Coords, enabled = true): HomeResult {
  const [data, setData] = useState<HomeData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const qs = coords ? `?lat=${coords.lat}&lon=${coords.lon}` : '';
      const res = await api.get<HomeData>(`${HomeEndpoint}${qs}`);
      setData(res);
    } catch (e) {
      setData(null);
      setError(e instanceof Error ? e.message : '홈 데이터를 불러오지 못했어요');
    } finally {
      setLoading(false);
    }
    // 객체 참조가 아니라 좌표값이 바뀔 때만 재요청
  }, [coords?.lat, coords?.lon, enabled]);

  useEffect(() => {
    load();
  }, [load]);

  return { data, error, loading, reload: load };
}
