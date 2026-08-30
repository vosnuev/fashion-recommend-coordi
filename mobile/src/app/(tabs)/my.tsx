import { Icon, type IconName } from '@/components/icon';
import { router } from 'expo-router';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Avatar, LoginGate } from '@/components/ui';
import { displayName, profilePhoto } from '@/lib/userProfile';
import { ink, ContentMax, Editorial } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { useProfileSummary } from '@/hooks/use-profile-summary';
import { useAuth } from '@/state/auth';
import { usePrefs } from '@/state/prefs';

const INK = Editorial.ink;

type Row = {
  icon: IconName;
  label: string;
  hint?: string;
  onPress: () => void;
};

// H1 마이 탭 — 프로필 요약 + 설정 메뉴
export default function MyScreen() {
  const { contentStyle } = useBreakpoint();
  const prefs = usePrefs();
  const { user, isLoggedIn, signOut } = useAuth();
  const profile = useProfileSummary();

  /* 힌트는 "설정했나"를 한눈에 알리는 자리다. 불러오는 중에 '측정하기'라고 단정하면
     이미 측정을 마친 사람에게 거짓말이 되므로, 값이 올 때까지는 아무 말도 하지 않는다. */
  const bodyHint = profile.loading
    ? undefined
    : profile.height != null
      ? [`${profile.height}cm`, profile.weight != null ? `${profile.weight}kg` : null]
          .filter(Boolean)
          .join(' · ')
      : '측정하기';

  const pursuitHint = profile.loading
    ? undefined
    : profile.pursuitCount > 0
      ? `${profile.pursuitCount}개 선택`
      : '설정하기';
  /* 아직 이름이 없으면 지어내지 않고 비워 둔다 — 아래에서 '이름 설정하기'로 안내한다. */
  const name = prefs.nickname || displayName(user);
  /* `??` 는 빈 문자열을 통과시킨다 — 카카오는 이메일 제공 동의를 안 받으면 `""` 를 주므로
     이름 아래가 빈 줄이 됐다. 값이 '있는지'가 아니라 '보여줄 만한지'로 판단한다.
     제공받지 못한 경우엔 가짜 주소 대신 사실대로 적는다. */
  const email = user?.email?.trim() || '이메일 미제공';

  const groups: { title: string; rows: Row[] }[] = [
    {
      title: '내 정보',
      rows: [
        {
          icon: 'figure.stand',
          label: '체형 정보',
          hint: bodyHint,
          onPress: () => router.push({ pathname: '/measure-input', params: { returnTo: 'my' } }),
        },
        {
          icon: 'sparkles',
          label: '추구미·선호도',
          hint: pursuitHint,
          onPress: () => router.push({ pathname: '/style-onboarding', params: { returnTo: 'my' } }),
        },
        {
          icon: 'wallet',
          label: '예산',
          hint: Object.keys(prefs.categoryBudgets).length > 0
            ? `${Object.keys(prefs.categoryBudgets).length}개 카테고리`
            : '기본값 적용 중',
          onPress: () => router.push('/budget'),
        },
      ],
    },
    {
      title: '설정',
      rows: [
        { icon: 'bell', label: '알림 설정', onPress: () => router.push('/notifications') },
        { icon: 'lock', label: '데이터·권한 관리', onPress: () => router.push('/permissions') },
        { icon: 'questionmark.circle', label: '도움말·문의', onPress: () => router.push('/support') },
        { icon: 'book', label: '약관·정책', onPress: () => router.push('/terms') },
        { icon: 'person', label: '계정 관리', onPress: () => router.push('/account') },
      ],
    },
  ];

  // 마이는 계정 화면이라 비회원에게 보여줄 것이 없다. (훅 순서 유지를 위해 전부 호출한 뒤 분기)
  if (!isLoggedIn) {
    return (
      <LoginGate
        title="내 정보는 로그인하고 볼 수 있어요"
        body="체형·추구미 같은 설정은 계정에 저장돼요."
      />
    );
  }

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top']} style={styles.safe}>
        <ScrollView
          showsVerticalScrollIndicator={false}
          contentContainerStyle={[styles.content, { paddingBottom: 24 }, contentStyle(ContentMax.wide)]}>
          {/* 프로필 — 테두리로 감싸지 않는다. 화면에 하나뿐인 머리라 굳이 구분할 상대가 없다. */}
          <View style={styles.profile}>
            <Avatar name={name} {...profilePhoto(user)} size={52} />
            <View style={styles.profileText}>
              {/* 이름이 없는 계정(막 가입)은 이름 자리가 그대로 편집 유도가 된다. */}
              <Text style={[styles.name, !name && styles.namePlaceholder]}>
                {name || '이름 설정하기'}
              </Text>
              <Text style={styles.email} numberOfLines={1}>{email}</Text>
            </View>
            <Pressable
              style={styles.editBtn}
              hitSlop={8}
              onPress={() => router.push('/edit-profile')}>
              <Icon name="pencil" tintColor={ink(0.55)} size={14} />
              <Text style={styles.editText}>편집</Text>
            </Pressable>
          </View>

          {/* 메뉴 그룹 */}
          {groups.map((g) => (
            <View key={g.title} style={styles.group}>
              <Text style={styles.groupTitle}>{g.title}</Text>
              <View style={styles.card}>
                {g.rows.map((r, i) => (
                  <Pressable key={r.label} onPress={r.onPress}>
                    <View style={styles.row}>
                      <View style={styles.rowIcon}>
                        <Icon name={r.icon} tintColor={INK} size={18} />
                      </View>
                      <Text style={styles.rowLabel}>{r.label}</Text>
                      {r.hint ? <Text style={styles.rowHint}>{r.hint}</Text> : null}
                      <Icon name="chevron.right" tintColor={ink(0.25)} size={14} />
                    </View>
                    {i < g.rows.length - 1 ? <View style={styles.rowLine} /> : null}
                  </Pressable>
                ))}
              </View>
            </View>
          ))}

          {/* 이동만 하면 세션이 남는다 — 토큰·데모 표식을 먼저 폐기하고 로그인으로 보낸다. */}
          <Pressable
            style={styles.logout}
            onPress={async () => {
              await signOut();
              router.replace('/login');
            }}>
            <Text style={styles.logoutText}>로그아웃</Text>
          </Pressable>
          <Text style={styles.version}>cozy · v0.1.0</Text>
        </ScrollView>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  safe: { flex: 1 },
  content: { paddingHorizontal: 20, paddingTop: 16 },

  /* 감싸는 카드가 없으니 좌우 여백은 content 가 이미 준다 — 위아래만 띄운다. */
  profile: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingVertical: 12,
  },
  profileText: { flex: 1, minWidth: 0 },
  name: { fontSize: 18, fontWeight: '700', color: INK, letterSpacing: -0.3 },
  /* 아직 정하지 않은 값이라 본문 이름과 같은 무게로 읽히면 안 된다. */
  namePlaceholder: { fontWeight: '600', color: Editorial.textCaption },
  email: { fontSize: 12, color: Editorial.textCaption, marginTop: 2 },
  editBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 7,
    borderRadius: 999,
    backgroundColor: Editorial.surfaceSoft,
    borderWidth: 1, borderColor: Editorial.line,
  },
  editText: { fontSize: 12, color: Editorial.textCaption, fontWeight: '600' },

  group: { marginTop: 28 },
  groupTitle: { fontSize: 12, fontWeight: '600', color: Editorial.textCaption, marginBottom: 10, marginLeft: 4 },
  card: {
    borderWidth: 1,
    borderColor: ink(0.1),
    borderRadius: 16,
    overflow: 'hidden',
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingHorizontal: 14,
    paddingVertical: 15,
  },
  rowIcon: {
    width: 34,
    height: 34,
    borderRadius: 10,
    backgroundColor: Editorial.control,
    alignItems: 'center',
    justifyContent: 'center',
  },
  rowLabel: { flex: 1, fontSize: 14.5, color: Editorial.ink, fontWeight: '500' },
  rowHint: { fontSize: 12.5, color: Editorial.textCaption },
  rowLine: { height: 1, backgroundColor: ink(0.07), marginLeft: 60 },

  logout: { alignSelf: 'center', marginTop: 30, paddingVertical: 8 },
  logoutText: { fontSize: 13.5, color: Editorial.textCaption },
  version: { alignSelf: 'center', fontSize: 11, color: Editorial.textMuted, marginTop: 8 },
});
