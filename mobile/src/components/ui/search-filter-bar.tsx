import { Editorial, ink } from '@/constants/theme';
import { Icon, type IconName } from '@/components/icon';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { type ReactNode, useRef, useState } from 'react';
import {
  type NativeScrollEvent,
  type NativeSyntheticEvent,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

const INK = Editorial.ink;
const PAD = 20;

type SearchFilterBarProps = {
  query: string;
  onQueryChange: (value: string) => void;
  searchPlaceholder: string;
  options: string[];
  onToggle: (option: string) => void;
  isActive: (option: string) => boolean;
  /** 검색행 오른쪽에 붙는 컨트롤 (예: 내 옷/공유 드롭다운) */
  trailing?: ReactNode;
  /** 검색행과 카테고리 칩 사이에 끼우는 영역 (예: 둘러보기/내 룩북 세그먼트) */
  middle?: ReactNode;
  /** 기본 필터 칩 아래에 놓는 보조 필터 줄(개인 옷장 해시태그 등). */
  afterChips?: ReactNode;
  /** false면 검색·칩을 숨기고 trailing만 표시 */
  showFilters?: boolean;
  /** 카테고리 칩 줄만 숨긴다(검색·middle은 유지) */
  showChips?: boolean;
  /** 카테고리 편집 시트 열기 */
  onEditCategories?: () => void;
  /**
   * 칩에 아이콘을 달고 싶을 때 (옵션 이름 → 아이콘).
   * 같은 줄에 선 다른 칩과 성격이 다른 칩(예: '위시')은 글자만으로 가르기 어려워,
   * 그 칩에만 표식을 준다.
   */
  chipIcons?: Partial<Record<string, IconName>>;
};

export function SearchFilterBar({
  query,
  onQueryChange,
  searchPlaceholder,
  options,
  onToggle,
  isActive,
  trailing,
  middle,
  afterChips,
  showFilters = true,
  showChips = true,
  onEditCategories,
  chipIcons,
}: SearchFilterBarProps) {
  const { isMobile } = useBreakpoint();
  const chipScrollRef = useRef<ScrollView>(null);
  const [chipViewportWidth, setChipViewportWidth] = useState(0);
  const [chipContentWidth, setChipContentWidth] = useState(0);
  const [chipOffset, setChipOffset] = useState(0);
  const hasMoreCategories =
    isMobile && chipContentWidth > chipViewportWidth + chipOffset + 8;
  const hasPreviousCategories = isMobile && chipOffset > 8;

  const handleChipScroll = (event: NativeSyntheticEvent<NativeScrollEvent>) => {
    setChipOffset(event.nativeEvent.contentOffset.x);
  };

  const showNextCategories = () => {
    const nextOffset = Math.min(
      chipOffset + Math.max(chipViewportWidth * 0.72, 180),
      Math.max(0, chipContentWidth - chipViewportWidth),
    );
    chipScrollRef.current?.scrollTo({ x: nextOffset, animated: true });
  };

  const showPreviousCategories = () => {
    const previousOffset = Math.max(
      0,
      chipOffset - Math.max(chipViewportWidth * 0.72, 180),
    );
    chipScrollRef.current?.scrollTo({ x: previousOffset, animated: true });
  };

  return (
    <>
      <View style={styles.searchRow}>
        {showFilters ? (
          <View style={styles.searchBar}>
            <Icon name="magnifyingglass" tintColor={ink(0.35)} size={16} />
            <TextInput
              value={query}
              onChangeText={onQueryChange}
              placeholder={searchPlaceholder}
              placeholderTextColor={ink(0.35)}
              style={styles.searchInput}
              returnKeyType="search"
              clearButtonMode="while-editing"
            />
          </View>
        ) : (
          <View style={styles.searchBarSpacer} />
        )}
        {trailing}
      </View>

      {middle}

      {showFilters && showChips ? (
        <View style={styles.chipViewport}>
          <ScrollView
            ref={chipScrollRef}
            horizontal
            showsHorizontalScrollIndicator={false}
            style={styles.chipScroll}
            contentContainerStyle={styles.chipRow}
            onLayout={(event) => setChipViewportWidth(event.nativeEvent.layout.width)}
            onContentSizeChange={(width) => setChipContentWidth(width)}
            onScroll={handleChipScroll}
            scrollEventThrottle={16}>
            {onEditCategories ? (
              <Pressable
                style={styles.editChip}
                onPress={onEditCategories}
                accessibilityLabel="카테고리 수정">
                <Icon name="slider.horizontal.3" tintColor={ink(0.45)} size={16} />
              </Pressable>
            ) : null}
            {options.map((c) => {
              const on = isActive(c);
              return (
                <Pressable
                  key={c}
                  onPress={() => onToggle(c)}
                  style={[styles.chip, on && styles.chipOn]}>
                  {chipIcons?.[c] ? (
                    <Icon
                      name={chipIcons[c]!}
                      tintColor={on ? '#fff' : Editorial.textCaption}
                      size={13}
                    />
                  ) : null}
                  <Text style={[styles.chipText, on && styles.chipTextOn]}>{c}</Text>
                </Pressable>
              );
            })}
          </ScrollView>
          {hasPreviousCategories ? (
            <Pressable
              style={[styles.categoryNavigationButton, styles.previousCategoriesButton]}
              onPress={showPreviousCategories}
              accessibilityLabel="이전 카테고리 보기">
              <Icon name="chevron.left" tintColor={INK} size={18} />
            </Pressable>
          ) : null}
          {hasMoreCategories ? (
            <Pressable
              style={[styles.categoryNavigationButton, styles.nextCategoriesButton]}
              onPress={showNextCategories}
              accessibilityLabel="다음 카테고리 보기">
              <Icon name="chevron.right" tintColor={INK} size={18} />
            </Pressable>
          ) : null}
        </View>
      ) : null}
      {showFilters ? afterChips : null}
    </>
  );
}

const styles = StyleSheet.create({
  searchRow: { flexDirection: 'row', gap: 10, paddingHorizontal: PAD, marginBottom: 18 },
  searchBar: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    height: 44,
    paddingHorizontal: 14,
    borderRadius: 12,
    backgroundColor: Editorial.control,
    borderWidth: 1, borderColor: Editorial.line,
  },
  searchBarSpacer: { flex: 1 },
  searchInput: {
    flex: 1,
    fontSize: 14,
    color: INK,
    padding: 0,
  },

  chipViewport: { height: 60, position: 'relative' },
  chipScroll: { flexGrow: 0, height: 60 },
  chipRow: { paddingHorizontal: PAD, gap: 8, paddingBottom: 20, alignItems: 'center' },
  chip: {
    height: 36,
    paddingHorizontal: 15,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: ink(0.12),
    /* 아이콘이 붙는 칩이 있어 가로로 세운다 — 아이콘이 없으면 글자만 남아 종전과 같다. */
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 5,
  },
  chipOn: { backgroundColor: Editorial.selected, borderColor: Editorial.selected },
  chipText: { fontSize: 13, lineHeight: 18, color: Editorial.textCaption, fontWeight: '500' },
  chipTextOn: { color: '#fff' },
  editChip: {
    width: 36,
    height: 36,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: ink(0.12),
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Editorial.control,
  },
  categoryNavigationButton: {
    position: 'absolute',
    top: 0,
    width: 38,
    height: 38,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: ink(0.14),
    backgroundColor: Editorial.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
  previousCategoriesButton: { left: 6 },
  nextCategoriesButton: { right: 6 },
});
