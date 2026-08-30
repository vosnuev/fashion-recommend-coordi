import { Editorial, ink } from '@/constants/theme';
import { Icon } from '@/components/icon';
import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

const INK = Editorial.ink;

type CategoryEditSheetProps = {
  visible: boolean;
  title: string;
  /** 첫 항목은 항상 '전체' (고정) */
  categories: string[];
  /** 삭제할 수 없는 카테고리. 옷장은 전체 기본 카테고리, 다른 화면은 기본값인 '전체'만 잠근다. */
  lockedCategories?: string[];
  onClose: () => void;
  onSave: (categories: string[]) => boolean | void | Promise<boolean | void>;
  /** 사용자 카테고리 행에서 해당 카테고리의 옷 선택 화면을 연다. */
  onManageCategory?: (categoryName: string) => void;
  addPlaceholder?: string;
  lockedHint?: string;
};

export function CategoryEditSheet({
  visible,
  title,
  categories,
  lockedCategories = ['전체'],
  onClose,
  onSave,
  onManageCategory,
  addPlaceholder = '새 카테고리',
  lockedHint = "'전체'는 항상 맨 앞에 유지돼요.",
}: CategoryEditSheetProps) {
  const [draft, setDraft] = useState<string[]>(categories);
  const [newName, setNewName] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (visible) {
      // 열릴 때마다 서버에서 복원된 최신 카테고리로 편집 초안을 다시 만든다.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDraft(categories);
      setNewName('');
    }
  }, [visible, categories]);

  const addCategory = () => {
    const name = newName.trim();
    if (!name || draft.includes(name)) return;
    setDraft((prev) => [...prev, name]);
    setNewName('');
  };

  const removeCategory = (name: string) => {
    setDraft((prev) => prev.filter((c) => c !== name));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const saved = await onSave(draft);
      if (saved !== false) onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable style={styles.sheet} onPress={(e) => e.stopPropagation()}>
          <View style={styles.handle} />
          <Text style={styles.title}>{title}</Text>
          <Text style={styles.hint}>{lockedHint}</Text>

          <ScrollView style={styles.list} showsVerticalScrollIndicator={false}>
            {draft.map((name) => {
              const locked = lockedCategories.includes(name);
              return (
                <View key={name} style={locked ? styles.fixedRow : styles.row}>
                  <Text style={locked ? styles.fixedLabel : styles.rowLabel}>{name}</Text>
                  {locked ? (
                    <Text style={styles.fixedBadge}>고정</Text>
                  ) : (
                    <View style={styles.rowActions}>
                      {onManageCategory ? (
                        <Pressable
                          hitSlop={6}
                          onPress={() => onManageCategory(name)}
                          style={styles.manageBtn}
                          accessibilityLabel={`${name} 옷 관리`}>
                          <Text style={styles.manageText}>옷 관리</Text>
                          <Icon name="chevron.right" tintColor={ink(0.45)} size={12} />
                        </Pressable>
                      ) : null}
                      <Pressable
                        hitSlop={8}
                        onPress={() => removeCategory(name)}
                        style={styles.removeBtn}
                        accessibilityLabel={`${name} 삭제`}>
                        <Icon name="trash" tintColor={ink(0.4)} size={16} />
                      </Pressable>
                    </View>
                  )}
                </View>
              );
            })}
          </ScrollView>

          <View style={styles.addRow}>
            <TextInput
              value={newName}
              onChangeText={setNewName}
              placeholder={addPlaceholder}
              placeholderTextColor={ink(0.35)}
              style={styles.addInput}
              returnKeyType="done"
              onSubmitEditing={addCategory}
            />
            <Pressable
              style={[styles.addBtn, !newName.trim() && styles.addBtnDisabled]}
              onPress={addCategory}
              disabled={!newName.trim()}>
              <Icon name="plus" tintColor="#fff" size={18} />
            </Pressable>
          </View>

          <View style={styles.actions}>
            <Pressable style={styles.cancelBtn} onPress={onClose} disabled={saving}>
              <Text style={styles.cancelText}>취소</Text>
            </Pressable>
            <Pressable style={[styles.saveBtn, saving && styles.saveBtnDisabled]} onPress={handleSave} disabled={saving}>
              {saving ? (
                <ActivityIndicator color="#fff" size="small" />
              ) : (
                <Text style={styles.saveText}>저장</Text>
              )}
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
    backgroundColor: 'rgba(28,25,23,0.35)',
  },
  sheet: {
    backgroundColor: Editorial.surface,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 20,
    paddingBottom: 32,
    maxHeight: '72%',
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
  title: { fontSize: 17, fontWeight: '700', color: INK },
  hint: { fontSize: 12, color: Editorial.textCaption, marginTop: 6, marginBottom: 16 },
  list: { flexGrow: 0, maxHeight: 280 },
  fixedRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: ink(0.08),
  },
  fixedLabel: { fontSize: 15, fontWeight: '600', color: Editorial.textCaption },
  fixedBadge: {
    fontSize: 11,
    fontWeight: '600',
    color: Editorial.textCaption,
    backgroundColor: Editorial.surface,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: ink(0.06),
  },
  rowLabel: { fontSize: 15, color: INK },
  rowActions: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  manageBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
    paddingHorizontal: 9,
    paddingVertical: 6,
    borderRadius: 8,
    backgroundColor: Editorial.control,
  },
  manageText: { fontSize: 12, fontWeight: '600', color: Editorial.textCaption },
  removeBtn: { padding: 4 },
  addRow: { flexDirection: 'row', gap: 10, marginTop: 16 },
  addInput: {
    flex: 1,
    height: 44,
    paddingHorizontal: 14,
    borderRadius: 12,
    backgroundColor: Editorial.surface,
    borderWidth: 1, borderColor: Editorial.line,
    fontSize: 14,
    color: INK,
  },
  addBtn: {
    width: 44,
    height: 44,
    borderRadius: 12,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  addBtnDisabled: { opacity: 0.35 },
  actions: { flexDirection: 'row', gap: 10, marginTop: 20 },
  cancelBtn: {
    flex: 1,
    height: 48,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: ink(0.14),
    alignItems: 'center',
    justifyContent: 'center',
  },
  cancelText: { fontSize: 15, fontWeight: '600', color: Editorial.textCaption },
  saveBtn: {
    flex: 1,
    height: 48,
    borderRadius: 12,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  saveBtnDisabled: { opacity: 0.65 },
  saveText: { fontSize: 15, fontWeight: '600', color: '#fff' },
});
