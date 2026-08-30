import { useState } from 'react';
import { Modal, Platform, Pressable, StyleSheet, Text, View } from 'react-native';

import { Icon } from '@/components/icon';
import { ErrorState, LoadingState } from '@/components/ui';
import { ChatPanelWidth, Editorial, ink, SidebarWidth, Type } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import type { StylistId } from '@/lib/stylistApi';
import { stylistStore, useStylists } from '@/state/stylist';

const INK = Editorial.ink;

/**
 * 스타일리스트 선택 팝업 (설계서 6.2).
 *
 * 규칙 셋만 지키면 된다.
 *   - 1~3명. **0명은 고를 수 없다** — 완료 버튼이 잠긴다.
 *   - 처음 켤 때는 미니멀만 체크돼 있다. 두 번째부터는 이전 선택을 복원한다.
 *   - 취소하면 모드도 선택도 그대로다.
 *
 * 켜져 있을 때 다시 열면 '기본 응답으로 돌아가기'가 함께 뜬다 — 끄는 자리를 따로 만들면
 * 버튼이 둘이 되는데, 둘 다 '모드'를 가리켜 무엇이 지금 상태인지 오히려 흐려진다.
 */
export function StylistPicker({
  visible,
  active,
  selectedIds,
  onClose,
  onConfirm,
  onTurnOff,
}: {
  visible: boolean;
  /** 지금 스타일리스트 모드가 켜져 있는지 */
  active: boolean;
  /** 이 대화가 마지막으로 고른 값. 비어 있으면 회원 마지막값 → 미니멀 순으로 복원한다. */
  selectedIds: StylistId[];
  onClose: () => void;
  onConfirm: (ids: StylistId[]) => void;
  onTurnOff: () => void;
}) {
  const { stylists, minSelect, maxSelect, loading, error } = useStylists();
  const { isDesktop, isWide } = useBreakpoint();

  /* 데스크톱 웹에선 바닥에서 올라오는 시트가 아니라 대화 열 안에 뜨는 다이얼로그다
     (session-sheet.tsx 와 같은 규칙 — 화면을 옮겨도 뜨는 자리가 흔들리지 않게). */
  const asDialog = Platform.OS === 'web' && isDesktop;
  const columnInset = asDialog
    ? { marginLeft: SidebarWidth, marginRight: isWide ? ChatPanelWidth : 0 }
    : null;

  const [draft, setDraft] = useState<StylistId[]>([]);

  /* 열 때(또는 다른 대화로 바꿔 열 때) 체크 상태를 복원값으로 되돌린다.
     effect 가 아니라 렌더 중에 맞추므로 여는 순간 옛 선택이 한 프레임 스치지 않는다. */
  const openKey = visible ? selectedIds.join(',') : null;
  const [shownFor, setShownFor] = useState<string | null>(null);
  if (openKey !== shownFor) {
    setShownFor(openKey);
    if (visible) setDraft(stylistStore.restoreSelection(selectedIds));
  }

  const toggle = (id: StylistId) => {
    setDraft((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      // 꽉 찼으면 조용히 무시하지 않고 가장 먼저 고른 것을 밀어낸다 —
      // 눌렀는데 아무 일도 안 일어나면 고장으로 읽힌다.
      const next = prev.length >= maxSelect ? prev.slice(1) : prev;
      return [...next, id];
    });
  };

  const canConfirm = draft.length >= minSelect;

  return (
    <Modal
      visible={visible}
      transparent
      animationType={asDialog ? 'fade' : 'slide'}
      onRequestClose={onClose}>
      <Pressable
        style={[styles.backdrop, asDialog && styles.backdropDialog, columnInset]}
        onPress={onClose}>
        <Pressable style={[styles.sheet, asDialog && styles.dialog]} onPress={(e) => e.stopPropagation()}>
          {asDialog ? null : <View style={styles.handle} />}

          <Text style={styles.title}>추천받을 스타일리스트</Text>
          <Text style={styles.lead}>
            {minSelect}~{maxSelect}명을 고르면 각자의 관점으로 코디를 하나씩 추천해요.
          </Text>

          {loading && stylists.length === 0 ? (
            <LoadingState message="스타일리스트를 불러오는 중…" style={styles.state} />
          ) : error && stylists.length === 0 ? (
            <ErrorState
              title="스타일리스트를 불러오지 못했어요"
              description={error}
              onRetry={() => stylistStore.load({ force: true })}
              style={styles.state}
            />
          ) : (
            <View style={styles.list}>
              {stylists.map((s) => {
                const checked = draft.includes(s.id);
                return (
                  <Pressable
                    key={s.id}
                    style={[styles.row, checked && styles.rowOn]}
                    onPress={() => toggle(s.id)}
                    accessibilityRole="checkbox"
                    accessibilityState={{ checked }}>
                    <View style={[styles.check, checked && styles.checkOn]}>
                      {checked ? <Icon name="checkmark" tintColor="#fff" size={13} /> : null}
                    </View>
                    <View style={styles.rowText}>
                      <Text style={styles.rowName}>{s.display_name}</Text>
                      <Text style={styles.rowDesc}>{s.description}</Text>
                    </View>
                  </Pressable>
                );
              })}
            </View>
          )}

          {/* 왜 잠겼는지 말해 준다. 비활성 버튼만 있으면 이유를 알 수 없다. */}
          {canConfirm ? null : (
            <Text style={styles.hint}>최소 {minSelect}명은 골라야 해요.</Text>
          )}

          <View style={styles.actions}>
            <Pressable style={styles.cancelBtn} onPress={onClose}>
              <Text style={styles.cancelText}>취소</Text>
            </Pressable>
            <Pressable
              style={[styles.confirmBtn, !canConfirm && styles.confirmBtnOff]}
              disabled={!canConfirm}
              onPress={() => onConfirm(draft)}>
              <Text style={styles.confirmText}>선택 완료</Text>
            </Pressable>
          </View>

          {active ? (
            <Pressable style={styles.offRow} onPress={onTurnOff}>
              <Text style={styles.offText}>기본 응답으로 돌아가기</Text>
            </Pressable>
          ) : null}
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
    paddingBottom: 32,
  },
  dialog: {
    width: '100%',
    maxWidth: 460,
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
    marginBottom: 14,
  },
  title: { fontSize: Type.lead, fontWeight: '600', color: INK, marginTop: 4 },
  lead: { fontSize: Type.caption, color: Editorial.textCaption, marginTop: 6, lineHeight: 19 },
  state: { paddingVertical: 32 },

  list: { marginTop: 16, gap: 8 },
  row: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 12,
    padding: 14,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  /* 고른 것은 면이 아니라 테두리로 말한다 — 이 앱에서 면이 채워지는 자리는 CTA 하나뿐이다. */
  rowOn: { borderColor: Editorial.lineStrong },
  check: {
    width: 20,
    height: 20,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: Editorial.line,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 1,
  },
  checkOn: { backgroundColor: Editorial.selected, borderColor: Editorial.selected },
  rowText: { flex: 1, gap: 3 },
  rowName: { fontSize: Type.body, fontWeight: '600', color: INK },
  rowDesc: { fontSize: Type.caption, color: Editorial.textCaption, lineHeight: 18 },

  hint: { marginTop: 10, fontSize: Type.caption, color: Editorial.wine },

  actions: { flexDirection: 'row', gap: 8, marginTop: 18 },
  cancelBtn: {
    flex: 1,
    height: 48,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: Editorial.line,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cancelText: { fontSize: Type.label, color: Editorial.textCaption },
  confirmBtn: {
    flex: 1,
    height: 48,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  confirmBtnOff: { opacity: 0.35 },
  confirmText: { fontSize: Type.label, fontWeight: '600', color: '#fff' },

  offRow: { alignSelf: 'center', marginTop: 14, paddingVertical: 6, paddingHorizontal: 10 },
  offText: { fontSize: Type.caption, color: Editorial.textCaption, textDecorationLine: 'underline' },
});
