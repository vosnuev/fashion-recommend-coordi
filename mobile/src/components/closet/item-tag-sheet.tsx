import { useState } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { useToast } from '@/components/ui';
import {
  CATEGORY_LARGE,
  CATEGORY_SMALL,
  COLORS,
  FITS,
  LENGTHS,
  MATERIALS,
  PATTERNS,
  SEASONS,
  SLEEVES,
  STYLES,
  type CategoryLarge,
} from '@/constants/wardrobe-taxonomy';
import { ContentMax, Editorial, ink, Type } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { patchWardrobeItem, type WardrobeApiItem, type WardrobeItemPatch } from '@/lib/wardrobeApi';

const INK = Editorial.ink;

/** 한 줄에 흐르는 선택 칩들. 단일 선택은 다시 누르면 해제된다(값 없음도 유효한 상태다). */
function ChipRow({
  options,
  selected,
  onToggle,
}: {
  options: readonly string[];
  selected: string[];
  onToggle: (value: string) => void;
}) {
  return (
    <View style={styles.chipRow}>
      {options.map((o) => {
        const on = selected.includes(o);
        return (
          <Pressable
            key={o}
            style={[styles.chip, on && styles.chipOn]}
            onPress={() => onToggle(o)}>
            <Text style={[styles.chipText, on && styles.chipTextOn]}>{o}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {children}
    </View>
  );
}

/**
 * 아이템 태그 편집.
 *
 * AI 가 붙인 태그를 사람이 고치는 자리다. 선택지는 백엔드 taxonomy 를 그대로 쓴다 —
 * 대분류·소분류 짝이 맞지 않으면 서버가 400 을 주므로 대분류를 바꿀 때 소분류를 비운다.
 * 값 없음(빈 문자열)도 유효하다: AI 가 못 맞힌 항목을 억지로 채우게 하지 않는다.
 */
export function ItemTagSheet({
  visible,
  item,
  onClose,
  onSaved,
}: {
  visible: boolean;
  item: WardrobeApiItem;
  onClose: () => void;
  onSaved: (updated: WardrobeApiItem) => void;
}) {
  const { isDesktop } = useBreakpoint();
  const toast = useToast();
  const [saving, setSaving] = useState(false);

  const [draft, setDraft] = useState<WardrobeItemPatch>({});

  /* 시트를 열 때(또는 다른 아이템으로 바꿔 열 때) 편집값을 비운다.
     effect 가 아니라 렌더 중에 맞춘다 — 여는 순간 옛 값이 한 프레임 스치지 않게. */
  const openedFor = visible ? item.id : null;
  const [shownFor, setShownFor] = useState(openedFor);
  if (openedFor !== shownFor) {
    setShownFor(openedFor);
    setDraft({});
  }

  /** 편집값이 있으면 그것, 없으면 서버 값 */
  const val = <K extends keyof WardrobeApiItem & keyof WardrobeItemPatch>(key: K) =>
    (draft[key] ?? item[key]) as WardrobeApiItem[K];

  const single = (key: 'color' | 'pattern' | 'fit' | 'material' | 'sleeve' | 'length') => ({
    selected: val(key) ? [val(key) as string] : [],
    onToggle: (v: string) =>
      setDraft((d) => ({ ...d, [key]: val(key) === v ? '' : v })),
  });

  const multi = (key: 'season' | 'style') => ({
    selected: val(key) as string[],
    onToggle: (v: string) => {
      const cur = val(key) as string[];
      setDraft((d) => ({
        ...d,
        [key]: cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v],
      }));
    },
  });

  const large = val('category_large') as string;
  const smalls = CATEGORY_SMALL[large as CategoryLarge] ?? [];

  const save = async () => {
    if (Object.keys(draft).length === 0) {
      onClose();
      return;
    }
    setSaving(true);
    try {
      const updated = await patchWardrobeItem(item.id, draft);
      onSaved(updated);
      toast('태그를 수정했어요', { variant: 'success' });
      onClose();
    } catch (e) {
      toast(e instanceof Error ? e.message : '수정하지 못했어요', { variant: 'error' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      visible={visible}
      transparent
      animationType={isDesktop ? 'fade' : 'slide'}
      onRequestClose={onClose}>
      <Pressable
        style={[styles.backdrop, isDesktop && styles.backdropDialog]}
        onPress={onClose}>
        <Pressable
          style={[styles.sheet, isDesktop && styles.dialog]}
          onPress={(e) => e.stopPropagation()}>
          {isDesktop ? null : <View style={styles.handle} />}
          <Text style={styles.title}>태그 수정</Text>

          <ScrollView style={styles.scroll} showsVerticalScrollIndicator={false}>
            <Section title="이름">
              <TextInput
                value={val('item_name') as string}
                onChangeText={(t) => setDraft((d) => ({ ...d, item_name: t }))}
                placeholder="예) 크림 울 니트"
                placeholderTextColor={ink(0.35)}
                style={styles.input}
              />
            </Section>

            <Section title="분류">
              <ChipRow
                options={CATEGORY_LARGE}
                selected={[large]}
                /* 대분류를 바꾸면 소분류는 짝이 깨지므로 함께 비운다 */
                onToggle={(v) =>
                  setDraft((d) => ({
                    ...d,
                    category_large: v,
                    category_small: v === large ? (val('category_small') as string) : '',
                  }))
                }
              />
            </Section>

            {smalls.length > 0 ? (
              <Section title="세부 분류">
                <ChipRow
                  options={smalls}
                  selected={val('category_small') ? [val('category_small') as string] : []}
                  onToggle={(v) =>
                    setDraft((d) => ({
                      ...d,
                      category_small: val('category_small') === v ? '' : v,
                    }))
                  }
                />
              </Section>
            ) : null}

            <Section title="색">
              <ChipRow options={COLORS} {...single('color')} />
            </Section>
            <Section title="패턴">
              <ChipRow options={PATTERNS} {...single('pattern')} />
            </Section>
            <Section title="핏">
              <ChipRow options={FITS} {...single('fit')} />
            </Section>
            <Section title="소재">
              <ChipRow options={MATERIALS} {...single('material')} />
            </Section>
            <Section title="소매">
              <ChipRow options={SLEEVES} {...single('sleeve')} />
            </Section>
            <Section title="기장">
              <ChipRow options={LENGTHS} {...single('length')} />
            </Section>
            <Section title="계절 (여러 개)">
              <ChipRow options={SEASONS} {...multi('season')} />
            </Section>
            <Section title="스타일 (여러 개)">
              <ChipRow options={STYLES} {...multi('style')} />
            </Section>

          </ScrollView>

          <View style={styles.actions}>
            <Pressable style={styles.cancelBtn} onPress={onClose} disabled={saving}>
              <Text style={styles.cancelText}>취소</Text>
            </Pressable>
            <Pressable style={styles.saveBtn} onPress={save} disabled={saving}>
              <Text style={styles.saveText}>{saving ? '저장 중…' : '저장'}</Text>
            </Pressable>
          </View>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(28,25,23,0.35)' },
  backdropDialog: {
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 24,
    backgroundColor: 'rgba(28,25,23,0.22)',
  },
  sheet: {
    backgroundColor: Editorial.surface,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 20,
    paddingBottom: 24,
    maxHeight: '86%',
  },
  dialog: {
    width: '100%',
    maxWidth: ContentMax.card,
    maxHeight: '84%',
    borderRadius: 20,
    paddingTop: 24,
    shadowColor: Editorial.ink,
    shadowOffset: { width: 0, height: 12 },
    shadowOpacity: 0.14,
    shadowRadius: 32,
    elevation: 12,
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
  title: { fontSize: Type.lead, fontWeight: '700', color: INK, marginBottom: 4 },

  scroll: { flexGrow: 0 },
  section: { marginTop: 18, gap: 8 },
  sectionTitle: { fontSize: Type.caption, color: Editorial.textCaption, fontWeight: '600' },
  input: {
    height: 46,
    paddingHorizontal: 14,
    borderRadius: 12,
    backgroundColor: Editorial.surface,
    borderWidth: 1,
    borderColor: Editorial.line,
    fontSize: Type.body,
    color: INK,
  },

  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 7 },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: ink(0.14),
  },
  chipOn: { backgroundColor: Editorial.selected, borderColor: Editorial.selected },
  chipText: { fontSize: Type.caption, color: Editorial.textCaption, fontWeight: '500' },
  chipTextOn: { color: '#fff' },

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
  cancelText: { fontSize: Type.body, fontWeight: '600', color: Editorial.textCaption },
  saveBtn: {
    flex: 1,
    height: 48,
    borderRadius: 12,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  saveText: { fontSize: Type.body, fontWeight: '600', color: '#fff' },
});
