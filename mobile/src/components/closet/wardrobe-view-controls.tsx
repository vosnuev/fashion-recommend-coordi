import { Icon } from '@/components/icon';
import { Editorial, Type, ink } from '@/constants/theme';
import type { WardrobeHashtag } from '@/lib/wardrobeApi';
import type { WardrobeGroupMode, WardrobeItemSort } from '@/lib/wardrobeSections';
import { useEffect, useRef, useState } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

type Props = {
  visible: boolean;
  groupMode: WardrobeGroupMode;
  itemSort: WardrobeItemSort;
  hashtags: WardrobeHashtag[];
  onClose: () => void;
  onGroupModeChange: (value: WardrobeGroupMode) => void;
  onItemSortChange: (value: WardrobeItemSort) => void;
  onHashtagOrderChange: (ids: string[]) => Promise<void>;
};

const GROUP_OPTIONS: { value: WardrobeGroupMode; label: string; detail: string }[] = [
  { value: 'SYSTEM_CATEGORY', label: '기본 카테고리별', detail: '상의·하의처럼 옷 종류별로 묶어요.' },
  { value: 'HASHTAG', label: '해시태그별', detail: '내가 붙인 해시태그별로 묶어요.' },
];
const SORT_OPTIONS: { value: WardrobeItemSort; label: string }[] = [
  { value: 'ADDED_DESC', label: '최근 추가순' },
  { value: 'COLOR_NAME_ASC', label: '색상·이름순' },
];

export function WardrobeViewControls({
  visible,
  groupMode,
  itemSort,
  hashtags,
  onClose,
  onGroupModeChange,
  onItemSortChange,
  onHashtagOrderChange,
}: Props) {
  const [ordered, setOrdered] = useState(hashtags);
  const orderedRef = useRef(hashtags);
  const drag = useRef<{ index: number; startY: number } | null>(null);

  useEffect(() => {
    if (visible) {
      orderedRef.current = hashtags;
      setOrdered(hashtags);
    }
  }, [hashtags, visible]);

  const move = (from: number, to: number) => {
    const current = orderedRef.current;
    if (from === to || to < 0 || to >= current.length) return;
    const next = [...current];
    const [picked] = next.splice(from, 1);
    next.splice(to, 0, picked);
    // responder release가 React의 다음 렌더보다 먼저 와도 마지막 순서를 저장할 수 있어야 한다.
    orderedRef.current = next;
    setOrdered(next);
    drag.current = drag.current ? { ...drag.current, index: to } : null;
  };

  const finishOrder = async () => {
    drag.current = null;
    const ids = orderedRef.current.map((row) => row.id);
    if (ids.join() !== hashtags.map((row) => row.id).join()) {
      await onHashtagOrderChange(ids);
    }
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable style={styles.sheet} onPress={(event) => event.stopPropagation()}>
          <View style={styles.handle} />
          <View style={styles.header}>
            <View>
              <Text style={styles.eyebrow}>개인 옷장</Text>
              <Text style={styles.title}>보기 설정</Text>
            </View>
            <Pressable style={styles.close} onPress={onClose} accessibilityLabel="보기 설정 닫기">
              <Icon name="xmark" tintColor={Editorial.textCaption} size={16} />
            </Pressable>
          </View>
          <ScrollView showsVerticalScrollIndicator={false}>
            <Text style={styles.sectionLabel}>묶기</Text>
            <View style={styles.optionStack}>
              {GROUP_OPTIONS.map((option) => {
                const active = option.value === groupMode;
                return (
                  <Pressable
                    key={option.value}
                    style={[styles.row, active && styles.rowActive]}
                    onPress={() => onGroupModeChange(option.value)}>
                    <View style={styles.rowCopy}>
                      <Text style={[styles.rowTitle, active && styles.rowTitleActive]}>{option.label}</Text>
                      <Text style={styles.rowDetail}>{option.detail}</Text>
                    </View>
                    <View style={[styles.radio, active && styles.radioActive]} />
                  </Pressable>
                );
              })}
            </View>

            <Text style={[styles.sectionLabel, styles.spaced]}>정렬</Text>
            <View style={styles.segment}>
              {SORT_OPTIONS.map((option) => {
                const active = option.value === itemSort;
                return (
                  <Pressable
                    key={option.value}
                    style={[styles.segmentItem, active && styles.segmentItemActive]}
                    onPress={() => onItemSortChange(option.value)}>
                    <Text style={[styles.segmentText, active && styles.segmentTextActive]}>{option.label}</Text>
                  </Pressable>
                );
              })}
            </View>

            <View style={styles.orderHeader}>
              <Text style={styles.sectionLabel}>해시태그 순서</Text>
              <Text style={styles.orderHint}>손잡이를 끌어 즉시 저장</Text>
            </View>
            {ordered.length === 0 ? (
              <Text style={styles.empty}>옷에 해시태그를 붙이면 여기서 순서를 바꿀 수 있어요.</Text>
            ) : (
              <View style={styles.orderList}>
                {ordered.map((hashtag, index) => (
                  <View key={hashtag.id} style={styles.orderRow}>
                    <Text style={styles.hashtagName}>#{hashtag.name}</Text>
                    <View
                      style={styles.dragHandle}
                      onStartShouldSetResponder={() => true}
                      onResponderGrant={(event) => {
                        drag.current = { index, startY: event.nativeEvent.pageY };
                      }}
                      onResponderMove={(event) => {
                        const current = drag.current;
                        if (!current) return;
                        const delta = event.nativeEvent.pageY - current.startY;
                        if (delta > 34) {
                          move(current.index, current.index + 1);
                          drag.current = { index: current.index + 1, startY: event.nativeEvent.pageY };
                        } else if (delta < -34) {
                          move(current.index, current.index - 1);
                          drag.current = { index: current.index - 1, startY: event.nativeEvent.pageY };
                        }
                      }}
                      onResponderRelease={() => void finishOrder()}
                      onResponderTerminate={() => void finishOrder()}>
                      <Icon name="line.3.horizontal" tintColor={ink(0.38)} size={19} />
                    </View>
                  </View>
                ))}
              </View>
            )}
          </ScrollView>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: 'flex-end', alignItems: 'center', backgroundColor: 'rgba(28,25,23,0.35)' },
  sheet: { width: '100%', maxWidth: 460, maxHeight: '88%', paddingHorizontal: 20, paddingBottom: 28, borderTopLeftRadius: 22, borderTopRightRadius: 22, backgroundColor: Editorial.surface },
  handle: { alignSelf: 'center', width: 36, height: 4, marginTop: 10, marginBottom: 16, borderRadius: 2, backgroundColor: Editorial.line },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 22 },
  eyebrow: { fontSize: Type.micro, fontWeight: '600', color: Editorial.textCaption },
  title: { marginTop: 3, fontSize: 21, fontWeight: '700', color: Editorial.ink },
  close: { width: 34, height: 34, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: Editorial.line, borderRadius: 10 },
  sectionLabel: { fontSize: Type.caption, fontWeight: '700', color: Editorial.ink },
  spaced: { marginTop: 24 },
  optionStack: { gap: 8, marginTop: 10 },
  row: { minHeight: 66, flexDirection: 'row', alignItems: 'center', paddingHorizontal: 15, borderWidth: 1, borderColor: Editorial.line, borderRadius: 14 },
  rowActive: { borderColor: Editorial.lineStrong, backgroundColor: Editorial.control },
  rowCopy: { flex: 1 },
  rowTitle: { fontSize: Type.body, fontWeight: '600', color: Editorial.textSoft },
  rowTitleActive: { color: Editorial.ink },
  rowDetail: { marginTop: 4, fontSize: Type.micro, color: Editorial.textCaption },
  radio: { width: 18, height: 18, borderWidth: 1.5, borderColor: Editorial.lineStrong, borderRadius: 9 },
  radioActive: { borderWidth: 5, borderColor: Editorial.selected },
  segment: { flexDirection: 'row', gap: 6, marginTop: 10, padding: 4, borderRadius: 13, backgroundColor: Editorial.control },
  segmentItem: { flex: 1, height: 38, alignItems: 'center', justifyContent: 'center', borderRadius: 10 },
  segmentItemActive: { backgroundColor: Editorial.selected },
  segmentText: { fontSize: Type.caption, fontWeight: '600', color: Editorial.textCaption },
  segmentTextActive: { color: Editorial.white },
  orderHeader: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', marginTop: 26 },
  orderHint: { fontSize: Type.micro, color: Editorial.textCaption },
  orderList: { gap: 7, marginTop: 10, paddingBottom: 8 },
  orderRow: { height: 50, flexDirection: 'row', alignItems: 'center', paddingLeft: 14, borderWidth: 1, borderColor: Editorial.line, borderRadius: 12 },
  hashtagName: { flex: 1, fontSize: Type.body, fontWeight: '600', color: Editorial.ink },
  dragHandle: { width: 52, height: 48, alignItems: 'center', justifyContent: 'center' },
  empty: { marginTop: 10, padding: 16, fontSize: Type.caption, lineHeight: 20, color: Editorial.textCaption, backgroundColor: Editorial.control, borderRadius: 12 },
});
