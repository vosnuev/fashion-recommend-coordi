import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Editorial } from '@/constants/theme';

type SegmentedOption<T extends string> = { value: T; label: string };

type SegmentedToggleProps<T extends string> = {
  value: T;
  /** 두세 개까지. 더 늘어나면 검색행 폭을 넘겨 칩이 잘린다 — 그때는 목록을 다시 생각할 것. */
  options: SegmentedOption<T>[];
  onChange: (value: T) => void;
};

/**
 * 검색행 오른쪽에 붙는 세그먼티드 토글 (룩북 둘러보기/내 룩북, 옷장 내 옷장/공유 옷장).
 *
 * 선택지가 둘뿐인 곳에서 드롭다운을 쓰면 '열어서 고르는' 한 단계가 더 붙고,
 * 지금 무엇이 선택돼 있는지 나머지 선택지와 견줄 수 없다. 그래서 둘 다 이 토글로 통일한다.
 * 높이 44 는 SearchFilterBar 의 검색바와 같은 값 — 나란히 놓였을 때 위아래가 맞아야 한다.
 */
export function SegmentedToggle<T extends string>({
  value,
  options,
  onChange,
}: SegmentedToggleProps<T>) {
  return (
    <View style={styles.tabs}>
      {options.map((o) => {
        const on = o.value === value;
        return (
          <Pressable
            key={o.value}
            style={[styles.tab, on && styles.tabOn]}
            onPress={() => onChange(o.value)}
            accessibilityRole="button"
            accessibilityState={{ selected: on }}>
            <Text style={[styles.tabText, on && styles.tabTextOn]} numberOfLines={1}>
              {o.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  tabs: {
    flexDirection: 'row',
    height: 44,
    borderRadius: 12,
    padding: 4,
    alignItems: 'center',
    backgroundColor: Editorial.control,
    borderWidth: 1,
    borderColor: Editorial.line,
    flexShrink: 0,
  },
  tab: {
    paddingHorizontal: 13,
    height: 36,
    borderRadius: 9,
    alignItems: 'center',
    justifyContent: 'center',
  },
  tabOn: { backgroundColor: Editorial.selected },
  tabText: { fontSize: 13, fontWeight: '600', color: Editorial.textCaption },
  tabTextOn: { color: '#fff' },
});
