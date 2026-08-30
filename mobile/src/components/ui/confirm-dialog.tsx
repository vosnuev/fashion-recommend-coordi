import { createContext, useCallback, useContext, useRef, useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';

import { Editorial, Type } from '@/constants/theme';

type ConfirmOptions = {
  title: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** 삭제·탈퇴처럼 되돌리기 어려운 동작이면 true → 확인 버튼이 빨간색 */
  destructive?: boolean;
};

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

/**
 * 확인 다이얼로그. await confirm(...) 이 true/false를 돌려준다.
 * 예) if (await confirm({ title: '이 아이템을 삭제할까요?', destructive: true })) { ... }
 */
export function useConfirm(): ConfirmFn {
  const fn = useContext(ConfirmContext);
  if (!fn) throw new Error('useConfirm은 <ConfirmProvider> 안에서만 쓸 수 있어요');
  return fn;
}

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [opts, setOpts] = useState<ConfirmOptions | null>(null);
  const resolver = useRef<((v: boolean) => void) | null>(null);

  const confirm = useCallback<ConfirmFn>((options) => {
    setOpts(options);
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve;
    });
  }, []);

  const close = useCallback((result: boolean) => {
    resolver.current?.(result);
    resolver.current = null;
    setOpts(null);
  }, []);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Modal visible={!!opts} transparent animationType="fade" onRequestClose={() => close(false)}>
        <Pressable style={styles.backdrop} onPress={() => close(false)}>
          {/* 카드 안쪽 탭이 백드롭으로 전파돼 닫히지 않게 */}
          <Pressable style={styles.card} onPress={() => {}}>
            <Text style={styles.title}>{opts?.title}</Text>
            {opts?.message ? <Text style={styles.message}>{opts.message}</Text> : null}
            <View style={styles.actions}>
              <Pressable style={[styles.btn, styles.cancel]} onPress={() => close(false)}>
                <Text style={styles.cancelText}>{opts?.cancelLabel ?? '취소'}</Text>
              </Pressable>
              <Pressable
                style={[styles.btn, opts?.destructive ? styles.confirmDanger : styles.confirm]}
                onPress={() => close(true)}>
                <Text style={styles.confirmText}>{opts?.confirmLabel ?? '확인'}</Text>
              </Pressable>
            </View>
          </Pressable>
        </Pressable>
      </Modal>
    </ConfirmContext.Provider>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(28,25,23,0.42)',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 32,
  },
  card: {
    width: '100%',
    maxWidth: 360,
    backgroundColor: Editorial.surface,
    borderRadius: 20,
    paddingHorizontal: 24,
    paddingTop: 26,
    paddingBottom: 16,
  },
  title: { fontSize: Type.lead, fontWeight: '700', color: Editorial.ink, textAlign: 'center' },
  message: {
    fontSize: Type.footnote,
    color: Editorial.textCaption,
    textAlign: 'center',
    marginTop: 10,
    lineHeight: 21,
  },
  actions: { flexDirection: 'row', gap: 10, marginTop: 24 },
  btn: { flex: 1, height: 50, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  cancel: { backgroundColor: Editorial.surface, borderWidth: 1, borderColor: Editorial.line },
  cancelText: { fontSize: Type.label, fontWeight: '600', color: Editorial.textCaption },
  confirm: { backgroundColor: Editorial.cta },
  confirmDanger: { backgroundColor: Editorial.danger },
  confirmText: { fontSize: Type.label, fontWeight: '600', color: '#fff' },
});
