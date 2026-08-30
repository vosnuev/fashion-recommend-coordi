import { Icon, type IconName } from '@/components/icon';
import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { LoadingState, useToast } from '@/components/ui';
import { Editorial, ink, ContentMax, Fonts, Type } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { useWardrobeItems } from '@/hooks/use-wardrobe';
import { useAuth } from '@/state/auth';
import {
  CHAT_MODE_META,
  chatStore,
  formatRelativeTime,
  useChatSessions,
  useChatStatus,
  type ChatMode as Mode,
} from '@/state/chat';

const INK = Editorial.ink;

/** 카드 문구는 이 화면만의 것이고, 이름·색은 목록과 공유한다(CHAT_MODE_META). */
type ModeCard = {
  key: Mode;
  icon: IconName;
  title: string;
  desc: string;
  note: string;
};
/* 설명은 한 줄 명사구. 카드가 화면 폭을 다 쓰므로 굳이 문장으로 늘려 두 줄을 만들 이유가 없다. */
const MODES: ModeCard[] = [
  {
    key: 'taste',
    icon: 'sparkles',
    title: '추구미 반영 추천',
    desc: '취향과 무드에 맞춘 새 룩',
    note: '옷장에 없는 아이템도 추천',
  },
  {
    key: 'closet',
    icon: 'tshirt',
    title: '옷장 기반 추천',
    desc: '가진 옷으로 짜는 코디',
    note: '', // 실제 옷장 개수로 채운다 (closetNote)
  },
];

/* 여기 목록은 '이어서 하기' 지름길이다. 지난 대화를 다 뒤지는 자리는 채팅 목록이므로
   최근 것만 보여주고 나머지는 '전체 보기'로 넘긴다 — 새 대화를 고르러 온 화면이
   목록 화면으로 변해 버리면 위의 두 카드가 밀려난다. */
const RECENT_LIMIT = 6;

// C3 모드 선택 — 새 대화의 추천 방식 고르기
export default function ChatMode() {
  /* 이 화면은 대화로 가는 경유지다. 들어온 자리를 그대로 대화방에 넘긴다 —
     여기를 돌아갈 자리로 삼으면 뒤로가기가 '다시 모드 고르기'가 돼 빈 대화가 하나 더 생긴다. */
  const { from } = useLocalSearchParams<{ from?: string }>();
  const { contentStyle } = useBreakpoint();
  const { isLoggedIn, isDemo } = useAuth();
  const hasMemberSession = isLoggedIn && !isDemo;
  const toast = useToast();
  /* 옷장 화면과 **같은 조건으로** 센다(필터 없음). confirmed=true 로 거르면 0 이 나온다 —
     백엔드에서 직접 넣은 옷은 확인 단계를 거치지 않아 확정 표시가 없기 때문이다(closet.tsx 참고).
     여기서만 다르게 세면 옷장엔 18벌인데 "옷을 먼저 등록해 주세요" 라고 말하게 된다. */
  const { items, loading } = useWardrobeItems({}, hasMemberSession);

  /* 개수를 고정값으로 두면 옷장이 비어 있어도 "42개로 조합"이라고 말하게 된다.
     불러오는 중이거나 비회원이면 개수를 빼고, 옷이 없으면 먼저 등록하라고 알린다. */
  const closetNote = !hasMemberSession
    ? '로그인하면 내 옷으로 조합해요'
    : loading
      ? '내 옷장으로 조합'
      : items.length === 0
        ? '옷을 먼저 등록해 주세요'
        : `내 옷장 ${items.length}개로 조합`;

  /* 지난 대화는 서버에 있다. 들어올 때마다 받아온다 — 다른 기기에서 이어가던 대화가
     여기 없으면 새로 시작하는 수밖에 없다. 비회원은 대화 자체가 없으니 부르지 않는다
     (부르면 401 만 받고 아무것도 못 그린다). */
  const sessions = useChatSessions();
  const { loadedOnce, error } = useChatStatus();
  useEffect(() => {
    if (hasMemberSession) chatStore.loadSessions();
  }, [hasMemberSession]);

  const recent = sessions.slice(0, RECENT_LIMIT);
  /* 못 불러왔을 때 이 화면에 오류를 띄우지 않는다 — 여기서 할 일(새 대화 시작)은
     목록이 없어도 그대로 되고, 다시 시도할 자리는 채팅 목록 화면이다. */
  const showRecent = hasMemberSession && error === null;
  const recentLoading = showRecent && !loadedOnce;

  /* 여기서 세션을 만들고 대화 화면으로 넘긴다. replace 인 이유 — 모드 선택은 대화로 가는
     경유지라, 대화에서 뒤로 가면 이 화면이 아니라 목록으로 돌아가야 한다.

     세션 생성이 서버 호출이라 즉시 끝나지 않는다. 만드는 동안 카드를 잠가 두는 이유 —
     두 번 누르면 빈 대화가 두 개 생기고, 그중 하나는 아무도 찾지 않는다. */
  const [starting, setStarting] = useState<Mode | null>(null);

  const startChat = async (mode: Mode) => {
    if (starting) return;
    if (mode === 'closet' && !hasMemberSession) {
      toast('옷장 기반 추천은 로그인 후 이용할 수 있어요.');
      router.push({ pathname: '/login', params: { redirect: '/chat-mode' } });
      return;
    }
    setStarting(mode);
    try {
      const session = await chatStore.createSession(mode, { asGuest: !hasMemberSession });
      router.replace({
        pathname: '/chat-room',
        params: { id: session.id, ...(from ? { from } : {}) },
      });
    } catch {
      toast('대화를 시작하지 못했어요', { variant: 'error' });
      setStarting(null);
    }
    /* 성공하면 replace 로 이 화면 자체가 스택에서 빠지므로 잠금을 풀 필요가 없다.
       (풀어 두면 화면이 사라지는 찰나에 카드가 다시 눌리는 상태가 된다) */
  };

  /* 지난 대화도 경유지 규칙을 그대로 따른다(replace) — push 로 열면 대화에서 뒤로 갔을 때
     방금 지나온 '어떻게 추천받을까요?'가 다시 나온다. */
  const openSession = (id: string) => {
    if (starting) return;
    router.replace({ pathname: '/chat-room', params: { id, ...(from ? { from } : {}) } });
  };

  return (
    <View style={styles.container}>
      {/* 닫기 버튼은 두지 않는다 — 이 화면에서도 탭바(데스크톱은 사이드바)가 그대로 보여
          나갈 길이 이미 있고, 화면 구석의 ✕ 는 무엇을 닫는지 읽히지 않는다. */}
      <SafeAreaView edges={['top']} style={styles.safe}>
        {/* 카드 두 장에 지난 대화까지 붙어 폰에서는 한 화면을 넘긴다 */}
        <ScrollView style={styles.scroll} showsVerticalScrollIndicator={false}>
          <View style={[styles.head, contentStyle(ContentMax.narrow)]}>
            <Text style={styles.eyebrow}>NEW CHAT</Text>
            <Text style={styles.title}>어떻게 추천받을까요?</Text>
            <Text style={styles.lead}>대화를 시작할 방식을 골라주세요.</Text>
          </View>

          <View style={[styles.cards, contentStyle(ContentMax.narrow)]}>
            {MODES.map((m) => (
              <Pressable
                key={m.key}
                style={[styles.card, starting !== null && starting !== m.key && styles.cardDimmed]}
                disabled={starting !== null}
                onPress={() => startChat(m.key)}>
                <View style={styles.cardHead}>
                  <View style={styles.cardIcon}>
                    {starting === m.key ? (
                      <ActivityIndicator size="small" color={CHAT_MODE_META[m.key].tint} />
                    ) : (
                      <Icon name={m.icon} tintColor={CHAT_MODE_META[m.key].tint} size={24} />
                    )}
                  </View>
                  <Text style={styles.cardTitle}>{m.title}</Text>
                </View>
                <Text style={styles.cardDesc}>{m.desc}</Text>
                {/* 점·화살표를 두지 않는다 — 카드 전체가 이미 누를 수 있고, 이 줄은 조건 한 마디다 */}
                <Text style={styles.cardNote}>{m.key === 'closet' ? closetNote : m.note}</Text>
              </Pressable>
            ))}
          </View>

          {/* 지난 대화 — 새로 시작하는 대신 이어서 할 수 있게. 대화가 하나도 없으면
              머리만 남으므로 통째로 감춘다(첫 사용자에겐 카드 두 장만 보인다). */}
          {showRecent && (recentLoading || recent.length > 0) ? (
            <View style={[styles.recent, contentStyle(ContentMax.narrow)]}>
              <View style={styles.recentHead}>
                <Text style={styles.recentTitle}>지난 대화</Text>
                {/* 목록 화면은 push 로 연다 — replace 로는 이동이 조용히 무시된다(웹에서 확인).
                    경유지 규칙에서 벗어나지만, 여기서 뒤로 가면 대화 목록이 아니라
                    방금 지나온 모드 선택으로 돌아가는 게 오히려 자연스럽다. */}
                {recent.length > 0 ? (
                  <Pressable hitSlop={8} onPress={() => router.push('/chat')}>
                    <Text style={styles.recentAll}>전체 보기</Text>
                  </Pressable>
                ) : null}
              </View>

              {recentLoading ? (
                <LoadingState message="지난 대화를 불러오는 중…" style={styles.recentLoading} />
              ) : (
                recent.map((s) => {
                  const { tint } = CHAT_MODE_META[s.mode];
                  return (
                    <Pressable
                      key={s.id}
                      style={styles.session}
                      disabled={starting !== null}
                      onPress={() => openSession(s.id)}>
                      <View style={[styles.sessionIcon, { backgroundColor: `${tint}14` }]}>
                        <Icon name="bubble.left.and.bubble.right" tintColor={tint} size={15} />
                      </View>
                      <Text style={styles.sessionTitle} numberOfLines={1}>
                        {s.title}
                      </Text>
                      <Text style={styles.sessionTime}>{formatRelativeTime(s.updatedAt)}</Text>
                      <Icon name="chevron.right" tintColor={ink(0.25)} size={13} />
                    </Pressable>
                  );
                })
              )}
            </View>
          ) : null}
        </ScrollView>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  safe: { flex: 1 },
  scroll: { flex: 1 },

  /** 대화를 만드는 동안 고르지 않은 카드는 흐리게 — 지금 무엇이 진행 중인지 한눈에 보이게. */
  cardDimmed: { opacity: 0.4 },

  // ✕ 를 없앤 만큼 제목이 화면 위쪽에 붙지 않게 여백을 옮겨 왔다.
  head: { paddingHorizontal: 24, paddingTop: 28, paddingBottom: 8 },
  eyebrow: { fontSize: 11, letterSpacing: 2, color: Editorial.textCaption, fontWeight: '600' },
  title: { fontFamily: Fonts.serif, fontSize: 28, color: INK, marginTop: 10 },
  lead: { fontSize: 14, color: Editorial.textCaption, marginTop: 10 },

  /* 두 카드를 위아래로 쌓는다. 나란히 두면 폰에서 카드 폭이 절반이라
     '추구미 반영 추천' 같은 제목이 두 줄로 쪼개진다. */
  cards: { paddingHorizontal: 24, paddingTop: 24, gap: 14 },
  card: {
    borderWidth: 1,
    borderColor: ink(0.1),
    borderRadius: 20,
    padding: 20,
    gap: 12,
  },
  /* 카드가 가로로 넓어졌으니 아이콘을 제목 옆에 둔다 — 위에 쌓으면 카드가 길어져
     두 장이 한 화면에 안 들어온다. */
  cardHead: { flexDirection: 'row', alignItems: 'center', gap: 14 },
  cardIcon: {
    width: 52,
    height: 52,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    // 배경은 두 카드 공통. 모드별로 다른 건 아이콘 글리프뿐이다.
    backgroundColor: Editorial.surface,
  },
  cardTitle: { fontFamily: Fonts.serif, fontSize: 20, color: INK },
  /* 설명·조건 줄이 작아서 키웠다. 크기 차이만으로는 둘의 위계가 안 서므로
     설명은 색을 한 단계 진하게(textSoft) 두어 조건 줄과 갈라놓는다. */
  cardDesc: { fontSize: Type.body, color: Editorial.textSoft, lineHeight: 22 },
  cardNote: { fontSize: Type.footnote, color: Editorial.textCaption, fontWeight: '500', marginTop: 4 },

  /* 지난 대화는 카드보다 한 단 낮게 — 테두리 없는 줄로 두어 '고르는 곳'은 위라는 게 읽히게. */
  recent: { paddingHorizontal: 24, paddingTop: 30, paddingBottom: 28 },
  recentHead: { flexDirection: 'row', alignItems: 'center', marginBottom: 4 },
  recentTitle: { flex: 1, fontSize: 13, fontWeight: '600', color: Editorial.textCaption },
  recentAll: { fontSize: 13, fontWeight: '500', color: Editorial.textSoft },
  recentLoading: { paddingVertical: 28 },
  session: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 11,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: ink(0.06),
  },
  sessionIcon: {
    width: 32,
    height: 32,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  sessionTitle: { flex: 1, fontSize: 14.5, color: INK },
  sessionTime: { fontSize: 11, color: Editorial.textCaption },
});
