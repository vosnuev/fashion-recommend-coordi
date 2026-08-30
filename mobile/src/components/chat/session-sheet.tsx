import { useState } from 'react';
import { Modal, Platform, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { Icon } from '@/components/icon';
import { useConfirm, useToast } from '@/components/ui';
import { ChatPanelWidth, Editorial, ink, SidebarWidth, Type } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { chatStore, type ChatSession } from '@/state/chat';

const INK = Editorial.ink;

type ChatSessionSheetProps = {
  visible: boolean;
  session: ChatSession | undefined;
  onClose: () => void;
  /** 삭제된 뒤 할 일 — 대화 화면은 목록으로 돌아가야 한다. */
  onDeleted?: () => void;
};

/**
 * 대화 하나를 관리하는 시트 — 이름 변경과 삭제.
 * 이름은 입력창에 바로 담아 둔다(따로 '이름 변경' 단계를 거치지 않게).
 */
export function ChatSessionSheet({
  visible,
  session,
  onClose,
  onDeleted,
}: ChatSessionSheetProps) {
  const [draft, setDraft] = useState(session?.title ?? '');
  const { isDesktop, isWide } = useBreakpoint();
  const confirm = useConfirm();
  const toast = useToast();

  /* 데스크톱 웹에선 바닥에서 밀려 올라오는 시트가 아니라 가운데 다이얼로그로 뜬다.
     사이드바·지난 대화 패널이 쓰는 폭만큼 좌우를 비켜 두면, 창 크기가 바뀌어도
     늘 '대화가 놓인 열' 안에 뜬다. (사이드바는 웹 레이아웃에만 있다) */
  const asDialog = Platform.OS === 'web' && isDesktop;
  const columnInset = asDialog
    ? { marginLeft: SidebarWidth, marginRight: isWide ? ChatPanelWidth : 0 }
    : null;

  /* 시트를 열 때(또는 다른 대화로 바꿔 열 때) 입력값을 그 대화의 이름으로 되돌린다.
     effect 가 아니라 렌더 중에 맞추므로, 여는 순간 한 프레임 옛 이름이 스치지 않는다. */
  const openedFor = visible ? session?.id ?? null : null;
  const [shownFor, setShownFor] = useState(openedFor);
  if (openedFor !== shownFor) {
    setShownFor(openedFor);
    setDraft(session?.title ?? '');
  }

  if (!session) return null;

  const trimmed = draft.trim();
  const canSave = trimmed.length > 0 && trimmed !== session.title;

  /* 이름·삭제 모두 서버에 반영된다. 스토어가 화면을 먼저 바꾸고 실패하면 되돌리므로
     여기서는 결과만 알린다 — 되돌아간 이름을 보고도 성공했다고 말하면 안 된다. */
  const handleSave = async () => {
    onClose();
    try {
      await chatStore.renameSession(session.id, trimmed);
      toast('대화 이름을 바꿨어요', { variant: 'success' });
    } catch {
      toast('이름을 바꾸지 못했어요', { variant: 'error' });
    }
  };

  const handleDelete = async () => {
    /* 시트를 먼저 닫는다 — 모달 위에 모달을 띄우면 iOS 에서 확인창이 나타나지 않는다. */
    onClose();
    const ok = await confirm({
      title: '이 대화를 삭제할까요?',
      message: '주고받은 대화가 모두 사라져요.',
      confirmLabel: '삭제',
      destructive: true,
    });
    if (!ok) return;
    try {
      await chatStore.removeSession(session.id);
      toast('대화를 삭제했어요', { variant: 'success' });
      onDeleted?.();
    } catch {
      toast('대화를 삭제하지 못했어요', { variant: 'error' });
    }
  };

  return (
    <Modal
      visible={visible}
      transparent
      animationType={asDialog ? 'fade' : 'slide'}
      onRequestClose={onClose}>
      <Pressable
        style={[styles.backdrop, asDialog && styles.backdropDialog, columnInset]}
        onPress={onClose}>
        <Pressable
          style={[styles.sheet, asDialog && styles.dialog]}
          onPress={(e) => e.stopPropagation()}>
          {/* 끌어내리는 손잡이는 바닥에 붙은 시트에서만 뜻이 있다 */}
          {asDialog ? null : <View style={styles.handle} />}
          <Text style={styles.title}>대화 관리</Text>

          <Text style={styles.fieldLabel}>이름</Text>
          <TextInput
            value={draft}
            onChangeText={setDraft}
            placeholder="대화 이름"
            placeholderTextColor={ink(0.35)}
            style={styles.input}
            returnKeyType="done"
            onSubmitEditing={() => canSave && handleSave()}
          />

          <Pressable style={styles.deleteRow} onPress={handleDelete}>
            <Icon name="trash" tintColor={Editorial.danger} size={17} />
            <Text style={styles.deleteText}>대화 삭제</Text>
          </Pressable>

          <View style={styles.actions}>
            <Pressable style={styles.cancelBtn} onPress={onClose}>
              <Text style={styles.cancelText}>취소</Text>
            </Pressable>
            <Pressable
              style={[styles.saveBtn, !canSave && styles.saveBtnDisabled]}
              onPress={handleSave}
              disabled={!canSave}>
              <Text style={styles.saveText}>저장</Text>
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
  },
  /* 데스크톱 웹 — 대화 열 안에서 가운데 뜨는 카드. 회색 면이 바닥에서 밀려 올라오는
     대신 자리에서 그대로 나타난다(모달은 fade). */
  backdropDialog: {
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 24,
    backgroundColor: 'rgba(28,25,23,0.22)',
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
    marginBottom: 16,
  },
  title: { fontSize: Type.lead, fontWeight: '700', color: INK },

  fieldLabel: { fontSize: Type.caption, color: Editorial.textCaption, marginTop: 20, marginBottom: 8 },
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

  deleteRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 9,
    marginTop: 20,
    paddingVertical: 14,
    borderTopWidth: 1,
    borderTopColor: ink(0.08),
  },
  deleteText: { fontSize: Type.body, fontWeight: '600', color: Editorial.danger },

  actions: { flexDirection: 'row', gap: 10, marginTop: 12 },
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
  saveBtnDisabled: { opacity: 0.35 },
  saveText: { fontSize: Type.body, fontWeight: '600', color: '#fff' },
});
