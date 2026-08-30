import { useCallback, useState } from 'react';

/**
 * 당겨서 새로고침 — `reload()` 를 감싸 스피너 상태만 얹는다.
 *
 * 화면마다 `const [refreshing, setRefreshing] = useState(false)` 를 반복해 두면
 * 실패했을 때 스피너를 끄는 걸 빠뜨리기 쉬워서(그러면 영영 도는 것처럼 보인다) 한 곳으로 모았다.
 *
 * 서버에서 가져오는 것이 있는 화면에만 붙인다. 로컬 스토어만 쓰는 목록에 달면 당겨도
 * 다시 불러올 것이 없어 스피너만 도는 시늉이 된다.
 *
 * 예) const { refreshing, onRefresh } = useRefresh(reload);
 *     <ScrollView refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}>
 */
export function useRefresh(reload: () => Promise<unknown>) {
  const [refreshing, setRefreshing] = useState(false);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await reload();
    } finally {
      // 실패해도 스피너는 반드시 멈춘다 — 에러 표시는 화면이 따로 맡는다.
      setRefreshing(false);
    }
  }, [reload]);

  return { refreshing, onRefresh };
}
