import { useLocalSearchParams } from 'expo-router';

import { LookComposer } from '@/components/look/look-composer';
import { todayKey } from '@/state/calendar';

/**
 * 착장 기록 작성·수정 — 하루치 기록을 한 화면에서 끝낸다.
 * 폼은 룩 올리기(look-add)와 같은 것을 쓰고, 날짜를 넘겨 캘린더 모드로 연다.
 */
export default function CalendarEntryScreen() {
  const params = useLocalSearchParams<{ date?: string }>();
  return <LookComposer date={params.date ?? todayKey()} />;
}
