import { router, type Href } from 'expo-router';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ContentMax, Editorial, Fonts, ink } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';

const INK = Editorial.ink;

/**
 * 비회원이 개인 데이터 화면(옷장·마이)에 들어왔을 때 대신 보여주는 안내.
 *
 * 탭 자체를 막지 않고 화면 안에서 안내한다 — 눌리지 않는 탭은 고장으로 읽히고,
 * 웹에서는 URL 로 바로 들어올 수도 있어 화면 단에서 막는 편이 확실하다.
 */
export function LoginGate({ title, body }: { title: string; body: string }) {
  const { contentStyle } = useBreakpoint();

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top']} style={styles.safe}>
        {/* 홈의 첫 착장 카드와 같은 폭(ContentMax.card)을 쓴다.
            탭을 옮겨 다닐 때 카드가 넓어졌다 좁아졌다 하면 화면이 흔들려 보인다. */}
        <View style={[styles.content, contentStyle(ContentMax.card)]}>
          <View style={styles.panel}>
            <Text style={styles.eyebrow}>MEMBERS ONLY</Text>
            <Text style={styles.title}>{title}</Text>
            <Text style={styles.body}>{body}</Text>

            <View style={styles.actions}>
              <Pressable style={styles.primary} onPress={() => router.push('/login' as Href)}>
                <Text style={styles.primaryText} numberOfLines={1}>
                  로그인하기
                </Text>
              </Pressable>
              <Pressable style={styles.secondary} onPress={() => router.replace('/(tabs)/home')}>
                <Text style={styles.secondaryText} numberOfLines={1}>
                  둘러보기 계속하기
                </Text>
              </Pressable>
            </View>
          </View>
        </View>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  safe: { flex: 1 },
  content: {
    flex: 1,
    paddingHorizontal: 20,
    /* 홈 카드와 세로 시작점을 맞춘다.
       홈 = content paddingTop 16 + 헤더 높이 40(아바타) + gap 24 = 80.
       여기엔 헤더가 없으므로 그 높이만큼 위 여백으로 대신한다.
       홈 헤더를 손보면 이 값도 같이 맞춰야 한다. */
    paddingTop: 80,
  },

  panel: {
    borderRadius: 28,
    backgroundColor: Editorial.surface,
    borderWidth: 1, borderColor: Editorial.line,
    paddingHorizontal: 28,
    paddingVertical: 34,
    alignItems: 'flex-start',
  },
  eyebrow: { fontSize: 10, letterSpacing: 1.7, fontWeight: '600', color: Editorial.textCaption },
  title: { marginTop: 16, fontFamily: Fonts.serif, fontSize: 26, lineHeight: 34, color: INK },
  body: { marginTop: 12, fontSize: 15, lineHeight: 23, color: Editorial.textCaption },

  // 주/보조 버튼을 한 줄에 나란히. flex:1 로 폭을 반씩 나눠 가진다.
  actions: { marginTop: 28, alignSelf: 'stretch', flexDirection: 'row', gap: 10 },
  primary: {
    flex: 1,
    height: 50,
    borderRadius: 999,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Editorial.cta,
  },
  primaryText: { color: '#ffffff', fontSize: 14, fontWeight: '600' },
  secondary: {
    flex: 1,
    height: 50,
    borderRadius: 999,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Editorial.surface,
    borderWidth: 1,
    borderColor: ink(0.14),
  },
  secondaryText: { fontSize: 14, fontWeight: '600', color: Editorial.textSoft },
});
