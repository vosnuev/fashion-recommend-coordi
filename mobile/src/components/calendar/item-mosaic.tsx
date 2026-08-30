import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { SmartImage } from '@/components/ui';
import { Editorial, ink } from '@/constants/theme';
import type { EntryItem } from '@/state/calendar';

/** 한 번에 보여주는 최대 칸 수 — 넘치면 마지막 칸에 +N 으로 접는다 */
const MAX_CELLS = 6;

/**
 * 개수별 줄 나누기.
 *   1·2·3 → 한 줄, 4 → 2+2, 5 → 2+3, 6 → 3+3
 * 칸 폭이 줄의 개수로 정해지므로 2칸 줄이 3칸 줄보다 크게 잡힌다(5개일 때 위가 더 큼).
 */
function splitRows(count: number): number[] {
  switch (count) {
    case 1:
      return [1];
    case 2:
      return [2];
    case 3:
      return [3];
    case 4:
      return [2, 2];
    case 5:
      return [2, 3];
    default:
      return [3, 3];
  }
}

/**
 * 담긴 옷을 여백 없이 붙인 모자이크.
 * 작은 썸네일을 줄줄이 늘어놓는 것보다 옷이 실제로 보이는 게 이 자리의 목적이라,
 * 이름표를 떼고 칸을 폭 끝까지 채운다(이름은 기록 상세에서 본다).
 */
export function ItemMosaic({ items, onPress }: { items: EntryItem[]; onPress?: () => void }) {
  const [width, setWidth] = useState(0);
  const shown = items.slice(0, MAX_CELLS);
  const overflow = items.length - shown.length;
  const rows = splitRows(shown.length);

  let cursor = 0;
  return (
    <Pressable
      style={styles.wrap}
      onPress={onPress}
      disabled={!onPress}
      onLayout={(e) => setWidth(e.nativeEvent.layout.width)}>
      {width > 0
        ? rows.map((perRow, rowIndex) => {
            const rowItems = shown.slice(cursor, cursor + perRow);
            cursor += perRow;
            /* 나누어떨어지지 않는 폭을 반올림하면 칸 사이에 실선 같은 틈이 생긴다 → 그대로 쓴다 */
            const size = width / perRow;
            return (
              <View key={rowIndex} style={styles.row}>
                {rowItems.map((it, i) => {
                  const isLast = rowIndex === rows.length - 1 && i === rowItems.length - 1;
                  return (
                    <View key={`${it.source}:${it.id}`}>
                      <SmartImage uri={it.image} width={size} height={size} radius={0} />
                      {isLast && overflow > 0 ? (
                        <View style={styles.overflow}>
                          <Text style={styles.overflowText}>+{overflow}</Text>
                        </View>
                      ) : null}
                    </View>
                  );
                })}
              </View>
            );
          })
        : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  wrap: { borderRadius: 14, overflow: 'hidden', backgroundColor: Editorial.bone },
  row: { flexDirection: 'row' },
  overflow: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: ink(0.45),
    alignItems: 'center',
    justifyContent: 'center',
  },
  overflowText: { fontSize: 18, fontWeight: '700', color: '#fff' },
});
