import { Icon } from '@/components/icon';
import { SmartImage } from '@/components/ui';
import { Editorial, GridCard, Type, ink } from '@/constants/theme';
import { itemDisplayName, type WardrobeApiItem, type WardrobeHashtag } from '@/lib/wardrobeApi';
import { useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from 'react-native';

type HashtagItemManageSheetProps = {
  visible: boolean;
  hashtag: WardrobeHashtag | null;
  items: WardrobeApiItem[];
  onClose: () => void;
  onSave: (
    payload: { name: string; itemIds: string[]; addItemIds: string[]; removeItemIds: string[] },
  ) => Promise<boolean>;
  onDelete?: () => Promise<boolean>;
};

const ITEM_COLUMNS = 5;
const ITEM_GAP = 8;
const ITEM_COPY_HEIGHT = 42;
const WEB_SHEET_MAX_WIDTH = 640;

function sameIds(left: Set<string>, right: Set<string>): boolean {
  return left.size === right.size && [...left].every((id) => right.has(id));
}

export function HashtagItemManageSheet({
  visible,
  hashtag,
  items,
  onClose,
  onSave,
  onDelete,
}: HashtagItemManageSheetProps) {
  const { width } = useWindowDimensions();
  const sheetMaxWidth = Platform.OS === 'web' ? WEB_SHEET_MAX_WIDTH : GridCard.maxWidth;
  const sheetWidth = Math.min(width, sheetMaxWidth);
  const cardWidth =
    (sheetWidth - GridCard.pad * 2 - ITEM_GAP * (ITEM_COLUMNS - 1)) / ITEM_COLUMNS;
  const twoRowHeight = (cardWidth + ITEM_COPY_HEIGHT) * 2 + ITEM_GAP;
  const [initialSelected, setInitialSelected] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState('');

  useEffect(() => {
    if (!visible) return;
    const next = new Set(
      items
        .filter((item) => hashtag && item.wardrobe_hashtags.some((entry) => entry.id === hashtag.id))
        .map((item) => item.id),
    );
    // 시트를 열 때 서버가 돌려준 현재 소속을 선택 초안의 기준으로 삼는다.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setInitialSelected(next);
    setSelected(new Set(next));
    setName(hashtag?.name ?? '');
  }, [hashtag, items, visible]);

  const changed = useMemo(
    () => !sameIds(initialSelected, selected) || name.trim() !== (hashtag?.name ?? ''),
    [hashtag, initialSelected, name, selected],
  );

  const toggle = (itemId: string) => {
    if (saving) return;
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  };

  const save = async () => {
    if (!changed || selected.size === 0 || !name.trim()) return;
    setSaving(true);
    try {
      const saved = await onSave({
        name: name.trim(),
        itemIds: [...selected],
        addItemIds: [...selected].filter((id) => !initialSelected.has(id)),
        removeItemIds: [...initialSelected].filter((id) => !selected.has(id)),
      });
      if (saved) onClose();
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!hashtag || !onDelete || saving) return;
    setSaving(true);
    try {
      const deleted = await onDelete();
      if (deleted) onClose();
    } finally {
      setSaving(false);
    }
  };

  const close = () => {
    if (!saving) onClose();
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={close}>
      <Pressable style={styles.backdrop} onPress={close}>
        <Pressable
          style={[styles.sheet, { width: sheetWidth }]}
          onPress={(event) => event.stopPropagation()}>
          <View style={styles.handle} />
          <View style={styles.header}>
            <View style={styles.headerCopy}>
              <Text style={styles.eyebrow}>개인 옷장 해시태그</Text>
              <Text style={styles.title} numberOfLines={1}>{hashtag ? `#${hashtag.name}` : '새 해시태그'}</Text>
              <Text style={styles.description}>
                {selected.size}벌 선택 · 한 옷에 여러 해시태그를 붙일 수 있어요.
              </Text>
            </View>
            <Pressable
              style={styles.closeButton}
              hitSlop={8}
              onPress={close}
              accessibilityLabel="해시태그 옷 관리 닫기">
              <Icon name="xmark" tintColor={Editorial.textCaption} size={16} />
            </Pressable>
          </View>

          <View style={styles.nameInputBar}>
            <Icon name="magnifyingglass" tintColor={ink(0.35)} size={16} />
            <TextInput
              value={name}
              onChangeText={setName}
              placeholder="# 없이 해시태그 입력"
              placeholderTextColor={ink(0.35)}
              maxLength={30}
              style={styles.nameInput}
              autoFocus={!hashtag}
              editable={!saving}
              returnKeyType="done"
              clearButtonMode="while-editing"
              accessibilityLabel="해시태그 이름"
            />
          </View>

          <ScrollView
            style={[styles.scroll, { maxHeight: twoRowHeight }]}
            contentContainerStyle={styles.grid}
            showsVerticalScrollIndicator={false}>
            {items.length === 0 ? (
              <View style={styles.empty}>
                <Icon name="tshirt" tintColor={Editorial.textMuted} size={30} />
                <Text style={styles.emptyTitle}>옷장에 옷을 먼저 추가해 보세요</Text>
                <Text style={styles.emptyBody}>옷을 추가하면 여기서 해시태그를 붙일 수 있어요.</Text>
              </View>
            ) : (
              items.map((item) => {
                const active = selected.has(item.id);
                return (
                  <Pressable
                    key={item.id}
                    style={[styles.card, { width: cardWidth }, active && styles.cardSelected]}
                    onPress={() => toggle(item.id)}
                    accessibilityRole="checkbox"
                    accessibilityState={{ checked: active }}
                    accessibilityLabel={`${itemDisplayName(item)} ${active ? '선택됨' : '선택 안 됨'}`}>
                    <View style={styles.imageWrap}>
                      <SmartImage
                        uri={item.image_url}
                        width="100%"
                        aspectRatio={GridCard.imageRatio}
                        radius={8}
                      />
                      <View style={[styles.check, active && styles.checkSelected]}>
                        {active ? (
                          <Icon name="checkmark" tintColor={Editorial.white} size={13} />
                        ) : null}
                      </View>
                    </View>
                    <Text style={styles.itemName} numberOfLines={1}>
                      {itemDisplayName(item)}
                    </Text>
                    <Text style={styles.itemMeta} numberOfLines={1}>
                      {[item.category_large, item.color].filter(Boolean).join(' · ')}
                    </Text>
                  </Pressable>
                );
              })
            )}
          </ScrollView>

          {hashtag && onDelete ? (
            <Pressable
              style={styles.deleteButton}
              onPress={remove}
              disabled={saving}
              accessibilityLabel={`${hashtag.name} 해시태그 삭제`}>
              <Text style={styles.deleteText}>해시태그 삭제</Text>
            </Pressable>
          ) : null}

          <View style={styles.actions}>
            <Pressable style={styles.cancelButton} onPress={close} disabled={saving}>
              <Text style={styles.cancelText}>취소</Text>
            </Pressable>
            <Pressable
              style={[styles.saveButton, (!changed || selected.size === 0 || !name.trim() || saving) && styles.saveButtonDisabled]}
              onPress={save}
              disabled={!changed || selected.size === 0 || !name.trim() || saving}>
              {saving ? (
                <ActivityIndicator color={Editorial.white} size="small" />
              ) : (
                <Text style={styles.saveText}>선택 저장</Text>
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
    alignItems: 'center',
    backgroundColor: 'rgba(28,25,23,0.35)',
  },
  sheet: {
    maxHeight: '90%',
    paddingHorizontal: GridCard.pad,
    paddingBottom: 28,
    borderTopLeftRadius: 22,
    borderTopRightRadius: 22,
    backgroundColor: Editorial.surface,
  },
  handle: {
    alignSelf: 'center',
    width: 36,
    height: 4,
    marginTop: 10,
    marginBottom: 16,
    borderRadius: 2,
    backgroundColor: Editorial.line,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 12,
    marginBottom: 16,
  },
  headerCopy: { flex: 1 },
  eyebrow: {
    marginBottom: 3,
    fontSize: Type.micro,
    fontWeight: '600',
    color: Editorial.textCaption,
  },
  title: { fontSize: 20, fontWeight: '700', color: Editorial.ink },
  description: { marginTop: 6, fontSize: Type.caption, color: Editorial.textSoft },
  closeButton: {
    width: 34,
    height: 34,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: Editorial.line,
    borderRadius: 10,
    backgroundColor: Editorial.control,
  },
  scroll: { flexGrow: 0 },
  nameInputBar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    height: 44,
    marginBottom: 14,
    paddingHorizontal: 14,
    borderWidth: 1,
    borderColor: Editorial.line,
    borderRadius: 12,
    backgroundColor: Editorial.control,
  },
  nameInput: {
    flex: 1,
    padding: 0,
    fontSize: 14,
    color: Editorial.ink,
  },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: ITEM_GAP,
    paddingBottom: 8,
  },
  card: {
    padding: 2,
    borderWidth: 1,
    borderColor: 'transparent',
    borderRadius: 10,
  },
  cardSelected: { borderColor: Editorial.selected },
  imageWrap: { position: 'relative' },
  check: {
    position: 'absolute',
    top: 5,
    right: 5,
    width: 20,
    height: 20,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1.5,
    borderColor: ink(0.28),
    borderRadius: 10,
    backgroundColor: ink(0.04),
  },
  checkSelected: {
    borderColor: Editorial.selected,
    backgroundColor: Editorial.selected,
  },
  itemName: {
    marginTop: 5,
    paddingHorizontal: 2,
    fontSize: Type.micro,
    fontWeight: '600',
    color: Editorial.ink,
  },
  itemMeta: {
    marginTop: 1,
    marginBottom: 3,
    paddingHorizontal: 2,
    fontSize: 10,
    color: Editorial.textCaption,
  },
  empty: {
    width: '100%',
    minHeight: 240,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
  },
  emptyTitle: { marginTop: 12, fontSize: Type.label, fontWeight: '600', color: Editorial.ink },
  emptyBody: { marginTop: 5, fontSize: Type.caption, color: Editorial.textCaption },
  deleteButton: {
    alignSelf: 'flex-start',
    marginTop: 12,
    paddingHorizontal: 4,
    paddingVertical: 8,
  },
  deleteText: { fontSize: Type.caption, fontWeight: '600', color: Editorial.danger },
  actions: {
    flexDirection: 'row',
    gap: 10,
    paddingTop: 14,
    borderTopWidth: 1,
    borderTopColor: Editorial.lineSoft,
  },
  cancelButton: {
    flex: 1,
    height: 48,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: Editorial.line,
    borderRadius: 12,
  },
  cancelText: { fontSize: Type.body, fontWeight: '600', color: Editorial.textCaption },
  saveButton: {
    flex: 1,
    height: 48,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 12,
    backgroundColor: Editorial.cta,
  },
  saveButtonDisabled: { opacity: 0.35 },
  saveText: { fontSize: Type.body, fontWeight: '600', color: Editorial.white },
});
