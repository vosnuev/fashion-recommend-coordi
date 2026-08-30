import { LookComposer } from '@/components/look/look-composer';

/**
 * 룩 올리기 — 내 룩북에 룩을 남긴다.
 * 폼은 착장 기록(calendar-entry)과 같은 것을 쓰고, 날짜를 넘기지 않아 룩북 모드로 열린다.
 */
export default function LookAddScreen() {
  return <LookComposer />;
}
