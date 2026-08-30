import { useCallback, useMemo, useState } from 'react';

export function formatFilterLabel(selected: string[]): string {
  if (selected.length === 0) return '전체';
  if (selected.length === 1) return selected[0];
  return `${selected[0]} 외 ${selected.length - 1}`;
}

export function useMultiSelectFilter(initial: string[] = []) {
  const [selected, setSelected] = useState<string[]>(initial);

  const toggle = useCallback((option: string) => {
    if (option === '전체') {
      setSelected([]);
      return;
    }
    setSelected((prev) =>
      prev.includes(option) ? prev.filter((x) => x !== option) : [...prev, option],
    );
  }, []);

  const reset = useCallback(() => setSelected([]), []);

  const prune = useCallback((valid: string[]) => {
    setSelected((prev) => {
      const next = prev.filter((x) => valid.includes(x));

      // 유효한 선택값이 그대로라면 기존 참조를 유지한다. 호출부의 유효 목록이
      // 렌더마다 새 배열이어도 불필요한 상태 갱신과 effect 반복이 발생하지 않는다.
      return next.length === prev.length ? prev : next;
    });
  }, []);

  const isActive = useCallback(
    (option: string) => (option === '전체' ? selected.length === 0 : selected.includes(option)),
    [selected],
  );

  const matches = useCallback(
    (value: string) => selected.length === 0 || selected.includes(value),
    [selected],
  );

  const label = useMemo(() => formatFilterLabel(selected), [selected]);

  return { selected, toggle, reset, prune, isActive, matches, label };
}
