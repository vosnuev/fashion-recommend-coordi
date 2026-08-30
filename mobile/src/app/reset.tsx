import { goBack } from '@/lib/goBack';
import { useState } from 'react';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Editorial, ink, Fonts , ContentMax} from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';

const INK = Editorial.ink;

// A5 비밀번호 재설정 — 이메일 입력 → 발송 완료 상태
export default function Reset() {
  const { contentStyle } = useBreakpoint();
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top', 'bottom']} style={styles.safe}>
        {/* 뒤로 */}
        <View style={styles.top}>
          <Pressable hitSlop={12} onPress={() => goBack('/login')}>
            <Text style={styles.back}>‹ 뒤로</Text>
          </Pressable>
        </View>

        <ScrollView
          contentContainerStyle={[styles.content, contentStyle(ContentMax.narrow)]}
          keyboardShouldPersistTaps="handled">
          {sent ? (
            <>
              <View style={styles.mark}>
                <Text style={styles.markIcon}>✓</Text>
              </View>
              <Text style={styles.title}>메일을 보냈어요</Text>
              <Text style={styles.guide}>
                <Text style={styles.emailStrong}>{email || '입력한 주소'}</Text>로{'\n'}
                재설정 링크를 보냈어요. 메일함을 확인해 주세요.
              </Text>
              <Pressable style={styles.cta} onPress={() => goBack('/login')}>
                <Text style={styles.ctaText}>로그인으로 돌아가기</Text>
              </Pressable>
              <Pressable style={styles.resend} onPress={() => setSent(false)}>
                <Text style={styles.resendText}>메일이 안 왔나요? 다시 보내기</Text>
              </Pressable>
            </>
          ) : (
            <>
              <Text style={styles.title}>비밀번호 재설정</Text>
              <Text style={styles.guide}>
                가입한 이메일 주소로{'\n'}재설정 링크를 보내드릴게요.
              </Text>

              <View style={styles.field}>
                <Text style={styles.label}>이메일</Text>
                <TextInput
                  style={styles.input}
                  value={email}
                  onChangeText={setEmail}
                  placeholder="you@example.com"
                  placeholderTextColor={ink(0.32)}
                  autoCapitalize="none"
                  keyboardType="email-address"
                  autoFocus
                />
                <View style={styles.underline} />
              </View>

              <Pressable
                style={[styles.cta, email.length === 0 && styles.ctaDisabled]}
                disabled={email.length === 0}
                onPress={() => setSent(true)}>
                <Text style={styles.ctaText}>재설정 링크 보내기</Text>
              </Pressable>
            </>
          )}
        </ScrollView>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  safe: { flex: 1 },
  top: { paddingHorizontal: 24, paddingTop: 6 },
  back: { fontSize: 15, color: Editorial.textCaption },
  content: { paddingHorizontal: 30, paddingTop: 40, paddingBottom: 30 },

  title: { fontFamily: Fonts.serif, fontSize: 28, color: INK },
  guide: { fontSize: 15, color: Editorial.textCaption, lineHeight: 23, marginTop: 14 },
  emailStrong: { color: Editorial.ink, fontWeight: '600' },

  field: { marginTop: 40 },
  label: { fontSize: 10, fontWeight: '500', color: Editorial.textCaption, letterSpacing: 0.2 },
  input: { marginTop: 10, fontSize: 15, color: Editorial.ink, padding: 0 },
  underline: { marginTop: 10, height: 1, backgroundColor: ink(0.15) },

  cta: {
    height: 52,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 40,
  },
  ctaDisabled: { backgroundColor: ink(0.22) },
  ctaText: { color: '#ffffff', fontSize: 15, fontWeight: '500' },

  mark: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: INK,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 24,
  },
  markIcon: { color: '#fff', fontSize: 26, fontWeight: '700' },
  resend: { alignSelf: 'center', marginTop: 20, paddingVertical: 4 },
  resendText: { fontSize: 13, color: Editorial.textCaption },
});
