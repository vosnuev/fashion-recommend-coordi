import { Icon } from '@/components/icon';
import { useConfirm, useToast } from '@/components/ui';
import { router } from 'expo-router';
import { useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ContentMax, Editorial, ink, Type } from '@/constants/theme';
import { SUPPORT_EMAIL } from '@/constants/support';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { goBack } from '@/lib/goBack';
import { useAuth } from '@/state/auth';

const INK = Editorial.ink;

const PROVIDER_LABEL: Record<string, string> = {
  naver: '네이버',
  kakao: '카카오',
  google: '구글',
  apple: '애플',
};

/** 탈퇴하면 사라지는 것 — 무엇이 없어지는지 알고 누르게 한다. */
const DELETED_ON_WITHDRAW = [
  '옷장에 등록한 옷과 사진',
  '체형 치수와 촬영 사진',
  '추구미·예산 설정',
  '저장한 룩과 착장 기록',
];

/**
 * 계정 관리 — 연결된 소셜 계정 확인 · 회원 탈퇴.
 *
 * 탈퇴는 DELETE /api/v1/users/me/ 를 부른다. 서버가 옷장·체형·룩·채팅을 지우고,
 * 공유 옷장은 '방 나가기'와 같은 규칙으로 처리한다(방장이면 남은 사람에게 위임).
 *
 * ⚠️ 비밀번호 변경이 여기 없는 이유: 이 서비스는 소셜 로그인 전용이라 **계정에 비밀번호가 없다.**
 *    이메일·비밀번호 로그인이 생기면 그때 같이 만든다.
 */
export default function AccountScreen() {
  const { contentStyle } = useBreakpoint();
  const { user, withdraw: withdrawAccount } = useAuth();
  const confirm = useConfirm();
  const toast = useToast();

  const accounts = user?.social_accounts ?? [];
  const [leaving, setLeaving] = useState(false);

  const withdraw = async () => {
    const ok = await confirm({
      title: '정말 탈퇴할까요?',
      message: '옷장·체형·설정이 모두 지워지고 되돌릴 수 없어요.',
      confirmLabel: '탈퇴',
      destructive: true,
    });
    if (!ok) return;
    /* 지우는 동안 두 번 누르지 못하게 잠근다 — 두 번째 요청은 이미 사라진 계정이라
       401 로 떨어지고, 사용자에게는 실패한 것처럼 보인다. */
    if (leaving) return;
    setLeaving(true);
    try {
      await withdrawAccount();
      /* 세션이 사라졌으니 탭 화면에 머무를 수 없다. 로그인 화면으로 확정 이동한다. */
      toast('탈퇴가 완료되었어요', { variant: 'success' });
      router.replace('/login');
    } catch (e) {
      toast(e instanceof Error ? e.message : '탈퇴하지 못했어요', { variant: 'error' });
      setLeaving(false);
    }
  };

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top']} style={styles.headerSafe}>
        <View style={[styles.header, contentStyle(ContentMax.narrow)]}>
          <Pressable hitSlop={12} onPress={() => goBack('/(tabs)/my')}>
            <Icon name="chevron.left" tintColor={INK} size={20} />
          </Pressable>
          <Text style={styles.headerTitle}>계정 관리</Text>
        </View>
      </SafeAreaView>

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={[
          styles.content,
          { paddingBottom: 24 },
          contentStyle(ContentMax.narrow),
        ]}>
        <Text style={styles.sectionTitle}>로그인 방법</Text>
        <View style={styles.card}>
          {accounts.length > 0 ? (
            accounts.map((a, i) => (
              <View key={a.provider}>
                <View style={styles.row}>
                  <Text style={styles.rowLabel}>
                    {PROVIDER_LABEL[a.provider] ?? a.provider}로 로그인
                  </Text>
                  <Text style={styles.rowHint} numberOfLines={1}>
                    {/* 빈 문자열도 '없음'이다 — `??` 로는 못 걸러 빈 줄이 남는다 */}
                    {a.email?.trim() || '이메일 미제공'}
                  </Text>
                </View>
                {i < accounts.length - 1 ? <View style={styles.line} /> : null}
              </View>
            ))
          ) : (
            <View style={styles.row}>
              <Text style={styles.rowLabel}>연결된 소셜 계정이 없어요</Text>
            </View>
          )}
        </View>
        <Text style={styles.note}>
          이 서비스는 소셜 로그인으로만 들어와요. 계정에 따로 비밀번호가 없어서 비밀번호 변경도
          없어요.
        </Text>

        <Text style={styles.sectionTitle}>회원 탈퇴</Text>
        <View style={styles.card}>
          <Text style={styles.deleteLead}>탈퇴하면 이런 것들이 사라져요</Text>
          {DELETED_ON_WITHDRAW.map((d) => (
            <View key={d} style={styles.bulletRow}>
              <View style={styles.bullet} />
              <Text style={styles.bulletText}>{d}</Text>
            </View>
          ))}
          <Text style={styles.deleteTail}>
            잠깐 쉬고 싶은 거라면 로그아웃만 해도 돼요. 계정은 그대로 남아요.
          </Text>
        </View>

        <Pressable
          style={[styles.withdrawBtn, leaving && styles.withdrawBtnBusy]}
          disabled={leaving}
          onPress={withdraw}>
          {leaving ? (
            <ActivityIndicator size="small" color={Editorial.wine} />
          ) : (
            <Text style={styles.withdrawText}>회원 탈퇴</Text>
          )}
        </Pressable>

        <Text style={styles.note}>
          문제가 생겨 계정을 지우지 못했다면 {SUPPORT_EMAIL} 로 알려주세요.
        </Text>

        <Pressable style={styles.logoutLink} onPress={() => router.replace('/(tabs)/my')}>
          <Text style={styles.logoutLinkText}>마이로 돌아가기</Text>
        </Pressable>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  headerSafe: { backgroundColor: Editorial.page },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 10,
  },
  headerTitle: { fontSize: Type.label, fontWeight: '600', color: INK },

  content: { paddingHorizontal: 20, paddingTop: 8 },
  sectionTitle: {
    fontSize: Type.caption,
    fontWeight: '600',
    color: Editorial.textCaption,
    marginTop: 26,
    marginBottom: 10,
  },
  card: {
    borderWidth: 1,
    borderColor: Editorial.line,
    borderRadius: 16,
    paddingVertical: 4,
  },
  line: { height: 1, backgroundColor: ink(0.07), marginHorizontal: 14 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 14,
  },
  rowLabel: { flex: 1, fontSize: Type.footnote, color: INK },
  rowHint: { fontSize: Type.micro, color: Editorial.textCaption, maxWidth: '45%' },

  note: {
    fontSize: Type.micro,
    color: Editorial.textMuted,
    lineHeight: 18,
    marginTop: 10,
    paddingHorizontal: 2,
  },

  deleteLead: {
    fontSize: Type.caption,
    fontWeight: '600',
    color: INK,
    paddingHorizontal: 14,
    paddingTop: 10,
    paddingBottom: 8,
  },
  bulletRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 14, paddingVertical: 4 },
  bullet: { width: 3, height: 3, borderRadius: 2, backgroundColor: ink(0.35) },
  bulletText: { fontSize: Type.caption, color: Editorial.textSoft },
  deleteTail: {
    fontSize: Type.micro,
    color: Editorial.textCaption,
    lineHeight: 18,
    paddingHorizontal: 14,
    paddingTop: 12,
    paddingBottom: 10,
  },

  /** 지우는 동안 — 눌린 상태가 아니라 '진행 중'으로 보이게 흐리게 둔다. */
  withdrawBtnBusy: { opacity: 0.6 },
  withdrawBtn: {
    marginTop: 14,
    height: 48,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: Editorial.wine,
    alignItems: 'center',
    justifyContent: 'center',
  },
  withdrawText: { fontSize: Type.footnote, fontWeight: '600', color: Editorial.wine },

  logoutLink: { marginTop: 26, alignItems: 'center', paddingVertical: 10 },
  logoutLinkText: { fontSize: Type.caption, color: Editorial.textCaption },
});
