import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Platform,
  Pressable,
  ScrollView,
  StyleProp,
  StyleSheet,
  Text,
  TextInput,
  TextStyle,
  View,
  ViewStyle,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useToast } from '@/components/ui';
import { APPLE_LOGIN_ENABLED } from '@/constants/config';
import { Editorial, ink, Fonts , ContentMax} from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { useSocialLogin } from '@/hooks/use-social-login';
import {
  emailAuthErrorMessage,
  isEmailUnverifiedError,
  loginWithEmail,
  resendVerificationEmail,
} from '@/lib/emailAuth';
import type { SocialLoginResult } from '@/lib/socialLogin';
import { authStore } from '@/state/auth';

const INK = Editorial.ink;
const KAKAO = Editorial.kakao;
const NAVER = '#03C75A';

// A3 로그인 — "로그인"/소셜 누르면 앱(홈 탭)으로 진입
export default function Login() {
  const { contentStyle } = useBreakpoint();
  const { email: verifiedEmail, redirect } = useLocalSearchParams<{
    email?: string;
    redirect?: string;
  }>();
  const [email, setEmail] = useState('');
  const [pw, setPw] = useState('');
  const [show, setShow] = useState(false);
  const [emailPending, setEmailPending] = useState(false);

  const { kakao, naver, google, apple, pending } = useSocialLogin();
  const toast = useToast();

  /* 이메일 인증을 마치고 돌아오면 방금 인증한 주소를 채워 준다. 이 화면은 스택에
     남아 있어 다시 마운트되지 않을 수 있으므로 useState 초기값이 아니라 effect 로 넣는다. */
  useEffect(() => {
    if (verifiedEmail) setEmail(verifiedEmail);
  }, [verifiedEmail]);

  /* 로그인 때문에 하던 일이 끊긴 경우 그 자리로 되돌려 준다 (예: 공유 옷장 초대장).
     앱 내부 경로만 허용한다 — 외부 URL 을 그대로 열어 주면 오픈 리다이렉트가 된다. */
  const goAfterLogin = () => {
    const target = typeof redirect === 'string' ? redirect : '';
    if (target.startsWith('/') && !target.startsWith('//')) {
      router.replace(target as never);
      return;
    }
    router.replace('/home');
  };

  const enter = async () => {
    if (!email.trim() || !pw) {
      toast('이메일과 비밀번호를 입력해 주세요');
      return;
    }
    setEmailPending(true);
    try {
      const { is_new_user } = await loginWithEmail(email, pw);
      /* 가입 후 첫 로그인이면 권한 동의 → 체형 측정 → 추구미 순서로 온보딩을 태운다.
         (체형 사진 촬영에 카메라·사진 권한이 필요해 권한 화면이 먼저 온다) */
      if (is_new_user) {
        router.replace({ pathname: '/permissions', params: { onboarding: '1' } });
      } else {
        goAfterLogin();
      }
    } catch (error) {
      /* 가입은 했는데 메일 인증을 안 끝낸 경우 — 안내만 띄우면 인증하러 갈 길이 없다.
         (인증 화면은 가입 직후 한 번만 열리는 자리였다) 코드를 새로 보내고 그 화면으로 보낸다. */
      if (isEmailUnverifiedError(error)) {
        toast('이메일 인증이 남아 있어요. 인증 코드를 보낼게요', { variant: 'error' });
        /* 재발송이 실패해도(예: 60초 제한) 화면은 열어 준다 — 이미 받은 코드가 있을 수 있고,
           그 화면에도 재발송 버튼이 있다. */
        const sent = await resendVerificationEmail(email.trim().toLowerCase()).catch(() => null);
        router.push({
          pathname: '/email-verification',
          params: {
            email: email.trim().toLowerCase(),
            ...(sent?.retry_after ? { retryAfter: String(sent.retry_after) } : {}),
          },
        });
        return;
      }
      toast(emailAuthErrorMessage(error), { variant: 'error' });
    } finally {
      setEmailPending(false);
    }
  };

  /* 소셜 가입 후 첫 로그인도 이메일 가입과 같은 온보딩을 태운다.
     여기서 갈라 주지 않으면 소셜로 가입한 사람은 체형·추구미를 물어보는 자리가 아예 없다. */
  const onSocial = async (login: () => Promise<SocialLoginResult>) => {
    const result = await login();
    if (!result) return;
    if (result.isNewUser) {
      router.replace({ pathname: '/permissions', params: { onboarding: '1' } });
      return;
    }
    goAfterLogin();
  };

  // 비회원 진입: 로그인하지 않은 상태를 확정하고 홈으로. (직전 데모 세션이 남아있어도 정리)
  const browseAsGuest = () => {
    authStore.continueAsGuest();
    router.replace('/home');
  };

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top', 'bottom']} style={styles.safe}>
        <ScrollView
          contentContainerStyle={[styles.content, contentStyle(ContentMax.narrow)]}
          keyboardShouldPersistTaps="handled">
          <Text style={styles.brand}>cozy</Text>
          <Text style={styles.guide}>로그인하고 오늘의 코디를 받아보세요</Text>

          {/* 이메일 */}
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
            />
            <View style={styles.underline} />
          </View>

          {/* 비밀번호 */}
          <View style={styles.field}>
            <Text style={styles.label}>비밀번호</Text>
            <View style={styles.pwRow}>
              <TextInput
                style={[styles.input, styles.pwInput]}
                value={pw}
                onChangeText={setPw}
                placeholder="••••••••"
                placeholderTextColor={ink(0.32)}
                secureTextEntry={!show}
              />
              <Pressable hitSlop={8} onPress={() => setShow((s) => !s)}>
                <Text style={styles.showText}>{show ? '숨김' : '표시'}</Text>
              </Pressable>
            </View>
            <View style={styles.underline} />
          </View>

          <Pressable style={styles.forgot} onPress={() => router.push('/reset')}>
            <Text style={styles.forgotText}>비밀번호를 잊으셨나요?</Text>
          </Pressable>

          {/* 로그인 */}
          <Pressable style={styles.loginBtn} onPress={enter} disabled={emailPending}>
            {emailPending ? (
              <ActivityIndicator color="#ffffff" />
            ) : (
              <Text style={styles.loginText}>로그인</Text>
            )}
          </Pressable>

          {/* 가입 전에 핵심 경험을 먼저 제공한다. 옷장·마이는 로그인 후에 열린다. */}
          <Pressable style={styles.guest} onPress={browseAsGuest}>
            <Text style={styles.guestText}>로그인 없이 둘러보기</Text>
          </Pressable>
          <Text style={styles.guestHint}>홈·룩북·착장 분석을 먼저 볼 수 있어요</Text>

          {/* 또는 */}
          <View style={styles.divider}>
            <View style={styles.line} />
            <Text style={styles.orText}>또는</Text>
            <View style={styles.line} />
          </View>

          {/* 소셜 로그인 */}
          <SocialButton
            label="카카오로 계속하기"
            style={{ backgroundColor: KAKAO }}
            loading={pending === 'kakao'}
            disabled={pending !== null}
            onPress={() => onSocial(kakao)}
          />
          <SocialButton
            label="네이버로 계속하기"
            style={{ backgroundColor: NAVER }}
            textStyle={styles.socialTextLight}
            spinnerColor="#ffffff"
            loading={pending === 'naver'}
            disabled={pending !== null}
            onPress={() => onSocial(naver)}
          />
          <SocialButton
            label="Google로 계속하기"
            style={styles.socialOutline}
            loading={pending === 'google'}
            disabled={pending !== null}
            onPress={() => onSocial(google)}
          />
          {/* 애플은 iOS 전용 (App Store 정책상 소셜로그인 제공 시 필수).
              지금은 백엔드가 네이티브 애플을 못 받아 숨겨 뒀다 — config.ts APPLE_LOGIN_ENABLED */}
          {APPLE_LOGIN_ENABLED && Platform.OS === 'ios' && (
            <SocialButton
              label="Apple로 계속하기"
              style={{ backgroundColor: INK }}
              textStyle={styles.socialTextLight}
              spinnerColor="#ffffff"
              loading={pending === 'apple'}
              disabled={pending !== null}
              onPress={() => onSocial(apple)}
            />
          )}

          {/* 회원가입 */}
          <Pressable style={styles.signup} onPress={() => router.push('/signup')}>
            <Text style={styles.signupText}>
              아직 계정이 없나요? <Text style={styles.signupBold}>회원가입</Text>
            </Text>
          </Pressable>
        </ScrollView>
      </SafeAreaView>
    </View>
  );
}

// 소셜 로그인 버튼 — 로딩 중이면 스피너, 아니면 라벨
function SocialButton({
  label,
  onPress,
  loading,
  disabled,
  style,
  textStyle,
  spinnerColor = INK,
}: {
  label: string;
  onPress: () => void;
  loading: boolean;
  disabled: boolean;
  style?: StyleProp<ViewStyle>;
  textStyle?: StyleProp<TextStyle>;
  spinnerColor?: string;
}) {
  return (
    <Pressable style={[styles.social, style]} onPress={onPress} disabled={disabled}>
      {loading ? (
        <ActivityIndicator color={spinnerColor} />
      ) : (
        <Text style={[styles.socialText, textStyle]}>{label}</Text>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  safe: { flex: 1 },
  content: { paddingHorizontal: 30, paddingTop: 16, paddingBottom: 30 },

  brand: { fontFamily: Fonts.serif, fontSize: 26, color: INK, marginTop: 12 },
  guide: { fontSize: 15, color: Editorial.ink, marginTop: 46 },

  field: { marginTop: 28 },
  label: { fontSize: 10, fontWeight: '500', color: Editorial.textCaption, letterSpacing: 0.2 },
  input: { marginTop: 10, fontSize: 14, color: Editorial.ink, padding: 0 },
  pwRow: { flexDirection: 'row', alignItems: 'center' },
  pwInput: { flex: 1 },
  showText: { fontSize: 12, color: Editorial.textCaption },
  underline: { marginTop: 10, height: 1, backgroundColor: ink(0.15) },

  forgot: { alignSelf: 'flex-end', marginTop: 16 },
  forgotText: { fontSize: 12, color: Editorial.textCaption },

  loginBtn: {
    height: 48,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 24,
  },
  loginText: { color: '#ffffff', fontSize: 15, fontWeight: '500' },

  divider: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 26 },
  line: { flex: 1, height: 1, backgroundColor: ink(0.12) },
  orText: { fontSize: 11, color: Editorial.textCaption },

  social: {
    height: 46,
    borderRadius: 999,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 12,
  },
  socialOutline: { backgroundColor: Editorial.surface, borderWidth: 1, borderColor: ink(0.14) },
  socialText: { fontSize: 14, fontWeight: '500', color: Editorial.ink },
  socialTextLight: { color: '#ffffff' },

  guest: {
    height: 48,
    borderRadius: 999,
    backgroundColor: Editorial.surface,
    borderWidth: 1,
    borderColor: ink(0.14),
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 12,
  },
  guestText: { fontSize: 15, fontWeight: '500', color: Editorial.textSoft },
  guestHint: { alignSelf: 'center', marginTop: 10, fontSize: 12, color: Editorial.textCaption },
  signup: { alignSelf: 'center', marginTop: 26 },
  signupText: { fontSize: 13, color: Editorial.textCaption },
  signupBold: { color: Editorial.ink, fontWeight: '500' },
});
