import { useEffect, useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';

import { Editorial, Fonts, ink } from '@/constants/theme';
import type { LookGenderFilter } from '@/lib/discoveryLookApi';

type LookbookFilterSheetProps = {
  visible: boolean;
  gender: LookGenderFilter;
  onClose: () => void;
  onApply: (gender: LookGenderFilter) => void;
};

const GENDERS: { label: string; value: LookGenderFilter }[] = [
  { label: '전체', value: 'ALL' },
  { label: 'WOMAN', value: 'WOMAN' },
  { label: 'MAN', value: 'MAN' },
];

export function LookbookFilterSheet({
  visible,
  gender,
  onClose,
  onApply,
}: LookbookFilterSheetProps) {
  const [draft, setDraft] = useState<LookGenderFilter>(gender);

  useEffect(() => {
    if (visible) setDraft(gender);
  }, [gender, visible]);

  const apply = () => {
    onApply(draft);
    onClose();
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable style={styles.sheet} onPress={(event) => event.stopPropagation()}>
          <View style={styles.handle} />
          <Text style={styles.title}>필터</Text>
          <Text style={styles.sectionLabel}>Gender</Text>
          <Text style={styles.hint}>보고 싶은 룩의 성별을 선택해 주세요.</Text>
          <View style={styles.genderPill} accessibilityRole="tablist">
            {GENDERS.map((option) => {
              const selected = draft === option.value;
              return (
                <Pressable
                  key={option.value}
                  style={[styles.genderOption, selected && styles.genderOptionSelected]}
                  onPress={() => setDraft(option.value)}
                  accessibilityRole="tab"
                  accessibilityState={{ selected }}>
                  <Text
                    style={[
                      styles.genderOptionText,
                      selected && styles.genderOptionTextSelected,
                    ]}>
                    {option.label}
                  </Text>
                </Pressable>
              );
            })}
          </View>

          <View style={styles.actions}>
            <Pressable style={styles.cancelButton} onPress={onClose}>
              <Text style={styles.cancelText}>취소</Text>
            </Pressable>
            <Pressable style={styles.applyButton} onPress={apply}>
              <Text style={styles.applyText}>적용</Text>
            </Pressable>
          </View>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: ink(0.35),
  },
  sheet: {
    backgroundColor: Editorial.surface,
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    paddingHorizontal: 20,
    paddingBottom: 32,
  },
  handle: {
    alignSelf: 'center',
    width: 42,
    height: 4,
    borderRadius: 2,
    backgroundColor: Editorial.line,
    marginTop: 10,
    marginBottom: 18,
  },
  title: {
    color: Editorial.ink,
    fontFamily: Fonts.sans,
    fontSize: 18,
    fontWeight: '700',
  },
  hint: {
    marginTop: 6,
    marginBottom: 12,
    color: Editorial.textCaption,
    fontFamily: Fonts.sans,
    fontSize: 13,
  },
  sectionLabel: {
    marginTop: 26,
    color: Editorial.ink,
    fontFamily: Fonts.sans,
    fontSize: 14,
    fontWeight: '600',
  },
  genderPill: {
    alignSelf: 'flex-start',
    flexDirection: 'row',
    padding: 4,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: Editorial.line,
    backgroundColor: Editorial.control,
  },
  genderOption: {
    minWidth: 88,
    height: 42,
    paddingHorizontal: 22,
    borderRadius: 999,
    alignItems: 'center',
    justifyContent: 'center',
  },
  genderOptionSelected: { backgroundColor: Editorial.selected },
  genderOptionText: {
    color: Editorial.ink,
    fontFamily: Fonts.sans,
    fontSize: 14,
    fontWeight: '600',
  },
  genderOptionTextSelected: { color: Editorial.white },
  actions: { flexDirection: 'row', gap: 10, marginTop: 28 },
  cancelButton: {
    flex: 1,
    height: 50,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: Editorial.line,
    alignItems: 'center',
    justifyContent: 'center',
  },
  applyButton: {
    flex: 1,
    height: 50,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cancelText: {
    color: Editorial.textCaption,
    fontFamily: Fonts.sans,
    fontSize: 14,
    fontWeight: '600',
  },
  applyText: {
    color: Editorial.white,
    fontFamily: Fonts.sans,
    fontSize: 14,
    fontWeight: '600',
  },
});
