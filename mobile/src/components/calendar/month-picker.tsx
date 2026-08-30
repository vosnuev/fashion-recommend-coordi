import { useRef, useState, type RefObject } from 'react';
import {
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';

import { Icon } from '@/components/icon';
import { Editorial, ink, Type } from '@/constants/theme';

/** 누른 자리(달 이름)의 화면 좌표 — 드롭다운을 그 아래에 붙이기 위해 받는다 */
export type Anchor = { x: number; y: number; width: number; height: number };

const ROW_HEIGHT = 44;
const PANEL_WIDTH = 220;
const LIST_HEIGHT = 264; // 6줄
/** 오늘 기준 몇 해까지 목록에 담을지 — 과거 기록을 되짚는 쪽이 잦아 뒤로 더 길게 */
const YEARS_BACK = 3;
const YEARS_FORWARD = 1;

const MONTHS = Array.from({ length: 12 }, (_, i) => i + 1);

/**
 * 년·월 고르기 드롭다운 — 달 이름을 누르면 그 아래로 펼쳐진다.
 *
 * 년과 월을 두 칸으로 나눠 각자 굴린다. 한 줄에 이어 붙이면 목록이 수십 줄이 되고,
 * 격자로 만들면 년도 바꾸는 화살표가 또 필요해진다.
 * 년을 고르면 목록만 바뀌고, 월을 고르는 순간 이동한다(마지막 한 번의 탭이 곧 확정).
 */
export function MonthPicker({
  visible,
  anchor,
  year,
  month,
  onClose,
  onSelect,
}: {
  visible: boolean;
  anchor: Anchor | null;
  year: number;
  month: number;
  onClose: () => void;
  onSelect: (year: number, month: number) => void;
}) {
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      {/* 열 때마다 새로 붙인다 — 안쪽 상태(고르던 년도)와 스크롤 위치가 매번 초기화돼야 한다 */}
      {visible ? (
        <Panel
          anchor={anchor}
          year={year}
          month={month}
          onClose={onClose}
          onSelect={onSelect}
        />
      ) : null}
    </Modal>
  );
}

function Panel({
  anchor,
  year,
  month,
  onClose,
  onSelect,
}: {
  anchor: Anchor | null;
  year: number;
  month: number;
  onClose: () => void;
  onSelect: (year: number, month: number) => void;
}) {
  const { width: windowWidth, height: windowHeight } = useWindowDimensions();
  const yearScroll = useRef<ScrollView>(null);
  const monthScroll = useRef<ScrollView>(null);
  const [draftYear, setDraftYear] = useState(year);

  const today = new Date();
  const thisYear = today.getFullYear();
  const thisMonth = today.getMonth() + 1;

  // 화살표로 목록 밖의 해까지 갔을 수 있다 → 보고 있는 해는 항상 포함시킨다
  const from = Math.min(thisYear - YEARS_BACK, year);
  const to = Math.max(thisYear + YEARS_FORWARD, year);
  const years = Array.from({ length: to - from + 1 }, (_, i) => from + i);

  // 앵커가 없으면(측정 실패) 화면 위쪽 가운데에 띄운다
  const top = anchor ? anchor.y + anchor.height + 8 : 120;
  const left = anchor
    ? Math.min(
        Math.max(12, anchor.x + anchor.width / 2 - PANEL_WIDTH / 2),
        windowWidth - PANEL_WIDTH - 12,
      )
    : windowWidth / 2 - PANEL_WIDTH / 2;
  const listHeight = Math.min(LIST_HEIGHT, Math.max(176, windowHeight - top - 80));

  /** 고른 줄이 목록 가운데쯤 오도록 — 위로 두 줄 남긴다 */
  const scrollTo = (ref: RefObject<ScrollView | null>, index: number) =>
    ref.current?.scrollTo({ y: Math.max(0, (index - 2) * ROW_HEIGHT), animated: false });

  const apply = (y: number, m: number) => {
    onSelect(y, m);
    onClose();
  };

  return (
    <Pressable style={styles.backdrop} onPress={onClose}>
      <Pressable
        style={[styles.panel, { top, left, width: PANEL_WIDTH }]}
        onPress={(e) => e.stopPropagation()}>
        <View style={[styles.columns, { height: listHeight }]}>
          <ScrollView
            ref={yearScroll}
            style={styles.col}
            showsVerticalScrollIndicator={false}
            onLayout={() => scrollTo(yearScroll, years.indexOf(draftYear))}>
            {years.map((y) => {
              const on = y === draftYear;
              return (
                <Pressable key={y} style={styles.row} onPress={() => setDraftYear(y)}>
                  <Text style={[styles.rowText, on && styles.rowTextOn]}>{y}년</Text>
                  {y === thisYear && !on ? <View style={styles.todayDot} /> : null}
                </Pressable>
              );
            })}
          </ScrollView>

          <View style={styles.divider} />

          <ScrollView
            ref={monthScroll}
            style={styles.col}
            showsVerticalScrollIndicator={false}
            onLayout={() => scrollTo(monthScroll, month - 1)}>
            {MONTHS.map((m) => {
              const on = draftYear === year && m === month;
              const isThisMonth = draftYear === thisYear && m === thisMonth;
              return (
                <Pressable key={m} style={styles.row} onPress={() => apply(draftYear, m)}>
                  <Text style={[styles.rowText, on && styles.rowTextOn]}>{m}월</Text>
                  {on ? (
                    <Icon name="checkmark" tintColor={Editorial.ink} size={14} />
                  ) : isThisMonth ? (
                    <View style={styles.todayDot} />
                  ) : null}
                </Pressable>
              );
            })}
          </ScrollView>
        </View>

        <Pressable style={styles.todayBtn} onPress={() => apply(thisYear, thisMonth)}>
          <Text style={styles.todayText}>이번 달로</Text>
        </Pressable>
      </Pressable>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: ink(0.18) },
  panel: {
    position: 'absolute',
    backgroundColor: Editorial.surface,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: Editorial.line,
    paddingVertical: 6,
    overflow: 'hidden',
    /* 떠 있는 판이라는 걸 그림자로 알린다 — 면 색이 배경과 같아 테두리만으로는 약하다 */
    shadowColor: Editorial.ink,
    shadowOpacity: 0.14,
    shadowRadius: 24,
    shadowOffset: { width: 0, height: 12 },
    elevation: 8,
  },

  columns: { flexDirection: 'row' },
  col: { flex: 1 },
  divider: { width: 1, backgroundColor: Editorial.lineSoft },
  row: {
    height: ROW_HEIGHT,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
  },
  rowText: { fontSize: Type.footnote, color: Editorial.textSoft },
  rowTextOn: { color: Editorial.ink, fontWeight: '700' },
  todayDot: { width: 5, height: 5, borderRadius: 2.5, backgroundColor: ink(0.3) },

  todayBtn: {
    alignItems: 'center',
    paddingVertical: 12,
    borderTopWidth: 1,
    borderTopColor: Editorial.lineSoft,
  },
  todayText: { fontSize: Type.caption, color: Editorial.textCaption },
});
