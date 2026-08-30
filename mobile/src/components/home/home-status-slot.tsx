import type { ComponentType } from 'react';

import { AnalysisStatusCard } from './analysis-status-card';

/**
 * 홈 상태 슬롯 — 착장 분석 진행, 완료 알림처럼 **일시적인** 상태 카드가 들어가는 자리.
 *
 * 홈은 특정 기능의 소유가 아니라 여러 기능이 올라타는 바닥이다. 카드를 home.tsx 에 직접
 * 박으면 기능 담당자들이 같은 파일 같은 줄에서 계속 부딪힌다. 그래서 **자리만 여기서 잡고
 * 내용은 각자 컴포넌트 파일**에 둔다.
 *
 * 카드를 추가할 때는 home.tsx 를 열지 말고:
 *   1. components/home/ 아래에 자기 카드 컴포넌트를 만든다
 *   2. 보여줄 게 없을 때는 **반드시 null 을 반환**하게 한다 (안 그러면 평소에도 홈이 길어진다)
 *   3. 아래 SLOT_CARDS 에 한 줄 추가한다
 *
 * 항상 보이는 진입점(예: 분석 기록 서랍)은 여기가 아니라 헤더에 둔다 — 상시 요소가 본문에
 * 쌓이면 오늘의 룩 카드가 화면 밖으로 밀린다.
 */
type SlotCard = {
  /** 중복 등록을 알아보기 위한 식별자. 기능 이름을 그대로 쓴다. */
  id: string;
  Component: ComponentType;
};

const SLOT_CARDS: SlotCard[] = [
  { id: 'outfit-analysis', Component: AnalysisStatusCard },
];

/**
 * 카드가 없으면 빈 프래그먼트를 돌려준다 — View 로 감싸면 내용이 없어도 부모의 gap 이
 * 붙어 홈 위쪽에 빈 공간이 생긴다.
 */
export function HomeStatusSlot() {
  return (
    <>
      {SLOT_CARDS.map(({ id, Component }) => (
        <Component key={id} />
      ))}
    </>
  );
}
