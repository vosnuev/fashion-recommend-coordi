import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { Animated, Platform, Pressable, StyleSheet, Text, View } from 'react-native';

import { Icon } from '@/components/icon';
import { Editorial, Type } from '@/constants/theme';
import { useBottomTabInset } from '@/hooks/use-bottom-tab-inset';

type ToastVariant = 'default' | 'success' | 'error';
type ToastOptions = { variant?: ToastVariant; duration?: number };
type ToastFn = (message: string, options?: ToastOptions) => void;

const ToastContext = createContext<ToastFn | null>(null);

/** "저장됐어요 / 삭제됐어요" 같은 짧은 피드백. useToast()로 어디서든 호출. */
export function useToast(): ToastFn {
  const fn = useContext(ToastContext);
  if (!fn) throw new Error('useToast는 <ToastProvider> 안에서만 쓸 수 있어요');
  return fn;
}

type ToastState = { message: string; variant: ToastVariant } | null;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toast, setToast] = useState<ToastState>(null);
  const anim = useRef(new Animated.Value(0)).current;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tabInset = useBottomTabInset();

  const hide = useCallback(() => {
    Animated.timing(anim, { toValue: 0, duration: 180, useNativeDriver: true }).start(() =>
      setToast(null),
    );
  }, [anim]);

  const show = useCallback<ToastFn>(
    (message, options) => {
      if (timer.current) clearTimeout(timer.current);
      setToast({ message, variant: options?.variant ?? 'default' });
      anim.setValue(0);
      Animated.timing(anim, { toValue: 1, duration: 220, useNativeDriver: true }).start();
      timer.current = setTimeout(hide, options?.duration ?? 2200);
    },
    [anim, hide],
  );

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  return (
    <ToastContext.Provider value={show}>
      {children}
      {toast ? (
        <View style={styles.overlay} pointerEvents="box-none">
          <Animated.View
            style={[
              styles.toast,
              toast.variant === 'error' && styles.toastError,
              {
                bottom: tabInset + 16,
                opacity: anim,
                transform: [
                  { translateY: anim.interpolate({ inputRange: [0, 1], outputRange: [16, 0] }) },
                ],
              },
            ]}>
            {toast.variant === 'success' ? (
              <Icon name="checkmark.circle.fill" tintColor="#fff" size={17} />
            ) : null}
            <Text style={styles.text} numberOfLines={2}>
              {toast.message}
            </Text>
            <Pressable hitSlop={10} onPress={hide}>
              <Icon name="checkmark" tintColor="rgba(255,255,255,0.55)" size={14} />
            </Pressable>
          </Animated.View>
        </View>
      ) : null}
    </ToastContext.Provider>
  );
}

const styles = StyleSheet.create({
  overlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    alignItems: 'center',
    justifyContent: 'flex-end',
  },
  toast: {
    position: 'absolute',
    maxWidth: 420,
    marginHorizontal: 20,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 9,
    paddingHorizontal: 18,
    paddingVertical: 14,
    borderRadius: 14,
    backgroundColor: Editorial.ink,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.22,
    shadowRadius: 20,
    elevation: 10,
    ...Platform.select({ web: { left: 0, right: 0 } }),
  },
  toastError: { backgroundColor: Editorial.danger },
  text: { flex: 1, fontSize: Type.footnote, color: '#fff', fontWeight: '500', lineHeight: 20 },
});
