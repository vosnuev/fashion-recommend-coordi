import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useToast } from '@/components/ui';
import { Editorial, Fonts, ContentMax } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import {
  emailAuthErrorMessage,
  resendVerificationEmail,
  verifyEmail,
} from '@/lib/emailAuth';

export default function EmailVerificationScreen() {
  const { contentStyle } = useBreakpoint();
  const { email, retryAfter } = useLocalSearchParams<{ email?: string; retryAfter?: string }>();
  const normalizedEmail = email?.trim().toLowerCase() ?? '';
  const [code, setCode] = useState('');
  const [seconds, setSeconds] = useState(Number(retryAfter) || 60);
  const [submitting, setSubmitting] = useState(false);
  const toast = useToast();

  useEffect(() => {
    if (seconds <= 0) return;
    const timer = setInterval(() => setSeconds((value) => Math.max(0, value - 1)), 1000);
    return () => clearInterval(timer);
  }, [seconds]);

  const submit = async () => {
    if (!normalizedEmail || !/^\d{6}$/.test(code)) {
      toast('6자리 숫자 인증 코드를 입력해 주세요.', { variant: 'error' });
      return;
    }
    setSubmitting(true);
    try {
      await verifyEmail(normalizedEmail, code);
      toast('이메일 인증을 마쳤어요. 로그인해 주세요.');
      /* 인증 API 는 토큰을 주지 않는다 — 로그인 화면으로 돌려보내 세션을 열게 한다.
         navigate 는 스택에 남아 있는 로그인 화면으로 돌아가며 params 만 갱신한다. */
      router.navigate({ pathname: '/login', params: { email: normalizedEmail } });
    } catch (error) {
      toast(emailAuthErrorMessage(error), { variant: 'error' });
    } finally {
      setSubmitting(false);
    }
  };

  const resend = async () => {
    try {
      const response = await resendVerificationEmail(normalizedEmail);
      setSeconds(response.retry_after);
      toast('인증 메일을 다시 보냈어요.');
    } catch (error) {
      toast(emailAuthErrorMessage(error), { variant: 'error' });
    }
  };

  return (
    <View style={styles.container}>
      <SafeAreaView style={styles.safe}>
        <View style={[styles.content, contentStyle(ContentMax.narrow)]}>
          <Text style={styles.brand}>COZY</Text>
          <Text style={styles.title}>이메일을 확인해 주세요</Text>
          <Text style={styles.description}>
            {normalizedEmail}로 보낸 6자리 인증 코드를 입력해 주세요. 인증이 끝나면 로그인 화면에서
            로그인하면 가입이 마무리돼요.
          </Text>
          <TextInput
            style={styles.input}
            value={code}
            onChangeText={(value) => setCode(value.replace(/\D/g, '').slice(0, 6))}
            placeholder="000000"
            keyboardType="number-pad"
            textContentType="oneTimeCode"
            maxLength={6}
            autoFocus
          />
          <Pressable style={styles.primary} onPress={submit} disabled={submitting}>
            {submitting ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryText}>인증하고 계속하기</Text>}
          </Pressable>
          <Pressable onPress={resend} disabled={seconds > 0 || submitting}>
            <Text style={[styles.resend, seconds > 0 && styles.disabled]}>
              {seconds > 0 ? `${seconds}초 후 다시 보내기` : '인증 메일 다시 보내기'}
            </Text>
          </Pressable>
        </View>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  safe: { flex: 1 },
  content: { flex: 1, width: '100%', alignSelf: 'center', paddingHorizontal: 24, paddingTop: 36 },
  brand: { fontSize: 21, color: Editorial.ink },
  title: { fontFamily: Fonts.serif, fontSize: 30, color: Editorial.ink, marginTop: 48 },
  description: { fontSize: 14, lineHeight: 22, color: Editorial.textCaption, marginTop: 14 },
  input: { fontSize: 30, letterSpacing: 10, textAlign: 'center', borderBottomWidth: 1, borderBottomColor: Editorial.ink, paddingVertical: 18, marginTop: 42 },
  primary: { height: 60, borderRadius: 30, backgroundColor: Editorial.ink, alignItems: 'center', justifyContent: 'center', marginTop: 32 },
  primaryText: { color: '#fff', fontSize: 16, fontWeight: '700' },
  resend: { color: Editorial.ink, textAlign: 'center', marginTop: 22, fontSize: 14 },
  disabled: { color: Editorial.textCaption },
});
