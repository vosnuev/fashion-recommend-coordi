import { useMemo, useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';

import { Icon } from '@/components/icon';
import { ContentMax, Editorial, Fonts, ink, Type } from '@/constants/theme';
import { formatDateLabel, parseDateKey, toDateKey, todayKey, useCalendarEntries } from '@/state/calendar';

const INK = Editorial.ink;
const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];

/**
 * 날짜 하나 고르기 — 룩을 올리면서 '어느 날 착장인지' 정할 때 쓴다.
 *
 * 기존 MonthPicker(년·월만 고름)와 따로 두는 이유: 저기는 달력 화면이 어느 달을 보여줄지
 * 정하는 것이고, 여기는 기록이 붙을 하루를 정하는 것이라 고르는 대상 자체가 다르다.
 * 이미 기록이 있는 날에는 점을 찍는다 — 모르고 고르면 덮어쓰기 확인을 한 번 더 만나기 때문에,
 * 고르기 전에 미리 보이는 편이 낫다.
 */
export function DatePickerSheet({
  visible,
  value,
  onClose,
  onSelect,
}: {
  visible: boolean;
  /** 지금 골라져 있는 날짜 'YYYY-MM-DD' */
  value: string;
  onClose: () => void;
  onSelect: (date: string) => void;
}) {
  const entries = useCalendarEntries();
  const today = todayKey();

  const [view, setView] = useState(() => {
    const { year, month } = parseDateKey(value);
    return { year, month };
  });
  const [wasVisible, setWasVisible] = useState(visible);

  /* 다시 열 때는 고른 날짜가 있는 달부터 보여준다 — 지난번에 넘겨둔 달에서 시작하면
     지금 값이 화면 밖에 있어 무엇이 골라져 있는지 안 보인다. */
  if (visible !== wasVisible) {
    setWasVisible(visible);
    if (visible) {
      const { year, month } = parseDateKey(value);
      setView({ year, month });
    }
  }

  const cells = useMemo(() => {
    const first = new Date(view.year, view.month - 1, 1).getDay();
    const days = new Date(view.year, view.month, 0).getDate();
    return [
      ...Array<number | null>(first).fill(null),
      ...Array.from({ length: days }, (_, i) => i + 1),
    ];
  }, [view]);

  const moveMonth = (delta: number) => {
    const d = new Date(view.year, view.month - 1 + delta, 1);
    setView({ year: d.getFullYear(), month: d.getMonth() + 1 });
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable style={styles.sheet} onPress={(e) => e.stopPropagation()}>
          <View style={styles.handle} />
          <Text style={styles.title}>날짜 고르기</Text>
          <Text style={styles.subtitle}>{formatDateLabel(value)}</Text>

          <View style={styles.monthRow}>
            <Pressable hitSlop={10} onPress={() => moveMonth(-1)} accessibilityLabel="이전 달">
              <Icon name="chevron.left" tintColor={ink(0.4)} size={16} />
            </Pressable>
            <Text style={styles.monthText}>
              {view.year}년 {view.month}월
            </Text>
            <Pressable hitSlop={10} onPress={() => moveMonth(1)} accessibilityLabel="다음 달">
              <Icon name="chevron.right" tintColor={ink(0.4)} size={16} />
            </Pressable>
          </View>

          <View style={[styles.calendarWidth, styles.weekHeader]}>
            {WEEKDAYS.map((d, i) => (
              <Text
                key={d}
                style={[styles.weekday, i === 0 && styles.weekdaySun]}>
                {d}
              </Text>
            ))}
          </View>

          <View style={[styles.calendarWidth, styles.grid]}>
            {cells.map((day, idx) => {
              if (day === null) return <View key={`e${idx}`} style={styles.cell} />;
              const key = toDateKey(view.year, view.month, day);
              const on = key === value;
              const has = Boolean(entries[key]);
              return (
                <Pressable
                  key={day}
                  style={styles.cell}
                  onPress={() => {
                    onSelect(key);
                    onClose();
                  }}>
                  <View style={[styles.dayInner, on && styles.dayInnerOn]}>
                    <Text
                      style={[
                        styles.dayNum,
                        key === today && styles.dayNumToday,
                        on && styles.dayNumOn,
                      ]}>
                      {day}
                    </Text>
                    {has ? <View style={[styles.dot, on && styles.dotOn]} /> : null}
                  </View>
                </Pressable>
              );
            })}
          </View>

          <Pressable
            style={styles.todayBtn}
            onPress={() => {
              onSelect(today);
              onClose();
            }}>
            <Text style={styles.todayText}>오늘로</Text>
          </Pressable>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: 'flex-end', backgroundColor: ink(0.35) },
  sheet: {
    width: '100%',
    maxWidth: ContentMax.narrow,
    alignSelf: 'center',
    backgroundColor: Editorial.surface,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 20,
    paddingBottom: 28,
  },
  handle: {
    alignSelf: 'center',
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: ink(0.12),
    marginTop: 10,
    marginBottom: 16,
  },
  title: { fontSize: Type.lead, fontWeight: '700', color: INK },
  subtitle: { fontSize: Type.caption, color: Editorial.textCaption, marginTop: 4 },

  monthRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 20,
    paddingTop: 18,
    paddingBottom: 10,
  },
  monthText: { fontFamily: Fonts.serif, fontSize: 17, color: INK },

  weekHeader: { flexDirection: 'row', paddingBottom: 4 },
  weekday: {
    flex: 1,
    textAlign: 'center',
    fontSize: Type.micro,
    color: Editorial.textCaption,
    fontWeight: '500',
  },
  weekdaySun: { color: '#c0392b' },

  /* 칸이 정사각이라 폭을 풀어 두면 넓은 화면에서 칸이 같이 커져 시트가 화면을 통째로 덮는다.
     달력이 커진다고 고르기 쉬워지지도 않으므로 폰 폭 언저리에서 묶고 가운데 세운다.
     ⚠️ 요일 헤더와 날짜 그리드에 **함께** 붙여야 한다 — 한쪽만 묶으면 열이 어긋난다. */
  calendarWidth: { width: '100%', maxWidth: 420, alignSelf: 'center' },
  grid: { flexDirection: 'row', flexWrap: 'wrap' },
  cell: { width: `${100 / 7}%`, aspectRatio: 1, alignItems: 'center', justifyContent: 'center' },
  dayInner: {
    width: 38,
    height: 38,
    borderRadius: 19,
    alignItems: 'center',
    justifyContent: 'center',
  },
  dayInnerOn: { backgroundColor: Editorial.selected },
  dayNum: { fontSize: Type.footnote, color: Editorial.textSoft },
  dayNumToday: { fontWeight: '700', color: INK, textDecorationLine: 'underline' },
  dayNumOn: { color: '#fff', fontWeight: '700' },
  /* 이미 기록이 있는 날 */
  dot: {
    position: 'absolute',
    bottom: 5,
    width: 4,
    height: 4,
    borderRadius: 2,
    backgroundColor: ink(0.45),
  },
  dotOn: { backgroundColor: '#fff' },

  todayBtn: {
    alignSelf: 'center',
    marginTop: 12,
    paddingHorizontal: 18,
    height: 36,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: Editorial.line,
    alignItems: 'center',
    justifyContent: 'center',
  },
  todayText: { fontSize: Type.caption, fontWeight: '600', color: INK },
});
