import { Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Editorial, Fonts } from '@/constants/theme';

// 에디토리얼 본 팔레트 (라이트 고정)
const INK = Editorial.ink;

export type StubAction = {
  label: string;
  onPress: () => void;
  /** false면 텍스트 링크(보조) 버튼. 기본은 잉크 채운 버튼 */
  primary?: boolean;
};

/**
 * 아직 디자인을 안 채운 "뼈대" 화면.
 * 프로토타입 단계에서 화면이 존재하고 이동되는지 확인하는 용도.
 */
export function ScreenStub({
  eyebrow,
  title,
  actions,
}: {
  eyebrow: string;
  title: string;
  actions?: StubAction[];
}) {
  return (
    <View style={styles.container}>
      <SafeAreaView style={styles.center}>
        <Text style={styles.eyebrow}>{eyebrow}</Text>
        <Text style={styles.title}>{title}</Text>
        <Text style={styles.note}>
          아직 뼈대 화면이에요.{'\n'}곧 Figma 디자인으로 채울 예정.
        </Text>
        {actions?.length ? (
          <View style={styles.actions}>
            {actions.map((a) =>
              a.primary === false ? (
                <Pressable key={a.label} onPress={a.onPress} style={styles.linkBtn}>
                  <Text style={styles.linkText}>{a.label}</Text>
                </Pressable>
              ) : (
                <Pressable key={a.label} onPress={a.onPress} style={styles.button}>
                  <Text style={styles.buttonText}>{a.label}</Text>
                </Pressable>
              ),
            )}
          </View>
        ) : null}
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingHorizontal: 32,
  },
  eyebrow: { fontSize: 11, letterSpacing: 2, color: Editorial.textCaption, fontWeight: '500' },
  title: { fontFamily: Fonts.serif, fontSize: 34, color: INK },
  note: {
    fontSize: 13,
    color: Editorial.textCaption,
    textAlign: 'center',
    lineHeight: 20,
    marginTop: 6,
  },
  actions: { marginTop: 24, gap: 12, alignItems: 'center', alignSelf: 'stretch' },
  button: {
    height: 48,
    paddingHorizontal: 28,
    minWidth: 200,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  buttonText: { color: '#ffffff', fontSize: 14, fontWeight: '500' },
  linkBtn: { paddingVertical: 6 },
  linkText: { color: Editorial.textCaption, fontSize: 13 },
});
