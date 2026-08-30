import { router, type Href } from 'expo-router';
import { useEffect, useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ChatConversation } from '@/components/chat/chat-conversation';
import { SessionList } from '@/components/chat/session-list';
import { ChatSessionSheet } from '@/components/chat/session-sheet';
import { Icon } from '@/components/icon';
import { EmptyState, LoadingState } from '@/components/ui';
import { ChatPanelWidth, ContentMax, Editorial, ink } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { backTo, goBack, goTo } from '@/lib/goBack';
import { chatStore, CHAT_MODE_META, useChatSession, useChatStatus, useLatestSession } from '@/state/chat';

const INK = Editorial.ink;

/**
 * 대화 화면 본체 — 대화 + 지난 대화 목록.
 *
 * 다른 화면이 오른쪽에 '코지에게 물어보기' 패널을 두는 것과 같은 리듬으로,
 * 채팅에서는 그 자리에 **지난 대화 목록**을 둔다(같은 폭·같은 왼쪽 테두리).
 * 패널을 둘 만큼 넓지 않으면 목록은 헤더의 햄버거로 띄운다.
 *
 * /chat 과 /chat-room 이 같은 것을 그리므로 이 컴포넌트를 공유한다.
 */
export function ChatRoomView({
  sessionId,
  showBack = false,
  from,
}: {
  sessionId?: string;
  /** 다른 화면에서 밀고 들어온 경우에만 뒤로가기를 둔다 (탭으로 들어온 /chat 은 갈 곳이 없다) */
  showBack?: boolean;
  /** 들어온 자리. 뒤로가기는 여기로 돌아간다 (룩 상세·저장 룩이 쓰는 것과 같은 패턴). */
  from?: string;
}) {
  const { contentStyle, isWide } = useBreakpoint();

  /**
   * 뒤로가기가 갈 자리와, 버튼을 보여줄지.
   *
   * **넓은 화면의 '/(tabs)/chat' 은 목록이 아니라 '가장 최근 대화'** 다(app/(tabs)/chat.tsx).
   * 방금 보던 대화가 대개 최근이라 눌러도 같은 화면이 나오고, 버튼이 고장 난 것처럼 읽힌다.
   * 넓은 화면에는 목록이 오른쪽 패널로 이미 옆에 있으니 돌아갈 자리가 없는 게 맞다 —
   * 그럴 땐 버튼을 두지 않는다. 판단은 **목적지**로 한다(from 유무가 아니라):
   * 목록에서 들어오면 from 이 '/(tabs)/chat' 이라 넓은 화면에선 역시 제자리이기 때문이다.
   * 좁은 화면의 '/(tabs)/chat' 은 진짜 목록 화면이라 그대로 둔다.
   */
  const backTarget = from ?? '/(tabs)/chat';
  const backVisible = showBack && !(isWide && backTarget === '/(tabs)/chat');

  /* 옷 상세·저장한 룩처럼 id 없이 들어오는 입구가 있다 → 가장 최근 대화로 이어 붙인다. */
  const requested = useChatSession(sessionId);
  const latest = useLatestSession();
  const session = requested ?? latest;
  const { loadedOnce } = useChatStatus();

  /**
   * 목록을 여기서도 받아온다.
   *
   * ⚠️ 지난 대화 패널(SessionList)이 목록을 받아 오지만, 그 패널은 **대화가 있을 때만**
   *    그려진다. 그래서 목록을 한 번도 못 받은 채로 들어오면
   *    "대화 없음 → 패널 안 뜸 → 목록을 받을 일이 영영 없음" 으로 갇힌다.
   *    넓은 화면의 /chat 이 대화가 있는데도 "이어갈 대화가 없어요" 를 띄우던 원인이다
   *    (좁은 화면은 SessionList 가 바로 떠서 멀쩡했다 — 그래서 폭에 따라 달라 보였다).
   *    이미 받아 뒀으면 건너뛴다. 갱신은 패널 쪽이 계속 맡는다.
   */
  useEffect(() => {
    if (!loadedOnce) chatStore.loadSessions().catch(() => {});
  }, [loadedOnce]);

  const [managing, setManaging] = useState(false);
  const [listOpen, setListOpen] = useState(false);

  /* 아직 한 번도 못 받아온 것과 정말 없는 것은 다르다. 받아오기 전에 "대화 없음" 을 그리면
     대화가 있는 사람에게도 그 화면이 먼저 보인다 (SessionList 의 firstLoad 와 같은 이유). */
  if (!session && !loadedOnce) {
    return (
      <View style={styles.container}>
        <LoadingState message="대화를 불러오는 중…" />
      </View>
    );
  }

  /* 이어 붙일 대화가 없으면(전부 삭제했거나 처음이거나) 모드부터 고르게 한다.
     여기서 대화를 몰래 만들면, 마지막 대화를 지우고 나온 직후 빈 '새 대화'가 되살아난다. */
  if (!session) {
    return (
      <View style={styles.container}>
        <SafeAreaView edges={['top']} style={styles.headerSafe}>
          <View style={[styles.header, contentStyle(ContentMax.narrow)]}>
            {backVisible ? (
              <Pressable
                hitSlop={12}
                accessibilityLabel="뒤로"
                onPress={() => goBack(backTo(from, '/(tabs)/home'))}>
                <Icon name="chevron.left" tintColor={INK} size={20} />
              </Pressable>
            ) : null}
          </View>
        </SafeAreaView>
        <EmptyState
          icon="bubble.left.and.bubble.right"
          title="첫 대화를 시작해 보세요"
          description="추천 방식을 고르면 바로 시작할 수 있어요."
          actionLabel="새 채팅 시작하기"
          onAction={() => router.push('/chat-mode')}
        />
      </View>
    );
  }

  const mode = CHAT_MODE_META[session.mode];

  return (
    <View style={styles.row}>
      <View style={styles.main}>
        <SafeAreaView edges={['top']} style={styles.headerSafe}>
          <View style={[styles.header, contentStyle(ContentMax.narrow)]}>
            <View style={styles.headerSide}>
              {backVisible ? (
                <Pressable
                  hitSlop={12}
                  accessibilityLabel="뒤로"
                  onPress={() => goBack(backTarget as Href)}>
                  <Icon name="chevron.left" tintColor={INK} size={20} />
                </Pressable>
              ) : null}
              {/* 패널이 없는 폭에서만 — 목록은 여기서 띄운다 */}
              {isWide ? null : (
                <Pressable
                  hitSlop={12}
                  onPress={() => setListOpen(true)}
                  accessibilityLabel="지난 대화 목록">
                  <Icon name="line.3.horizontal" tintColor={INK} size={20} />
                </Pressable>
              )}
            </View>

            <View style={styles.headerCenter}>
              <Text style={styles.headerTitle} numberOfLines={1}>{session.title}</Text>
              <View style={styles.modeBadge}>
                <View style={[styles.modeDot, { backgroundColor: mode.tint }]} />
                <Text style={styles.modeText}>{mode.label}</Text>
              </View>
            </View>

            <View style={[styles.headerSide, styles.headerSideEnd]}>
              {/* 새 대화는 목록 패널이 아니라 여기서 시작한다 — 지금 대화 옆이 가장 가까운 자리다. */}
              <Pressable
                hitSlop={12}
                onPress={() => router.push('/chat-mode')}
                accessibilityLabel="새 채팅">
                <Icon name="plus" tintColor={INK} size={19} />
              </Pressable>
              <Pressable
                hitSlop={12}
                onPress={() => setManaging(true)}
                accessibilityLabel="대화 관리">
                <Icon name="ellipsis" tintColor={ink(0.5)} size={18} />
              </Pressable>
            </View>
          </View>
        </SafeAreaView>
        <View style={styles.divider} />

        <ChatConversation variant="screen" sessionId={session.id} />
      </View>

      {/* 넓은 화면 — 다른 화면의 코지 패널과 같은 자리에 지난 대화 목록.
          제목과 구분선은 두지 않는다. 검색창이 바로 '무엇을 찾는 자리'인지 말해 준다. */}
      {isWide ? (
        <View style={styles.panel}>
          <SessionList variant="panel" activeId={session.id} />
        </View>
      ) : null}

      {/* 좁은 화면 — 햄버거로 띄우는 목록 */}
      <Modal
        visible={listOpen}
        animationType="slide"
        onRequestClose={() => setListOpen(false)}>
        <View style={styles.container}>
          <SafeAreaView edges={['top']} style={styles.headerSafe}>
            <View style={styles.overlayHead}>
              <Text style={styles.overlayTitle}>지난 대화</Text>
              <Pressable
                hitSlop={12}
                onPress={() => setListOpen(false)}
                accessibilityLabel="닫기">
                <Icon name="xmark" tintColor={INK} size={18} />
              </Pressable>
            </View>
          </SafeAreaView>
          <View style={styles.divider} />
          <SessionList
            variant="screen"
            activeId={session.id}
            onOpened={() => setListOpen(false)}
          />
        </View>
      </Modal>

      <ChatSessionSheet
        visible={managing}
        session={session}
        onClose={() => setManaging(false)}
        onDeleted={() => goTo('/(tabs)/chat')}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  /* 대화(남는 폭) + 목록 패널(고정 폭) */
  row: { flex: 1, flexDirection: 'row', backgroundColor: Editorial.page },
  main: { flex: 1, minWidth: 0 },
  container: { flex: 1, backgroundColor: Editorial.page },

  headerSafe: { backgroundColor: Editorial.page },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 10,
  },
  /* 좌우 묶음을 같은 최소 폭으로 둬야 가운데 제목이 실제로 가운데 온다 */
  headerSide: { minWidth: 44, flexDirection: 'row', alignItems: 'center', gap: 14 },
  headerSideEnd: { justifyContent: 'flex-end' },
  headerCenter: { flex: 1, alignItems: 'center', gap: 3 },
  headerTitle: { fontSize: 15, fontWeight: '600', color: INK },
  modeBadge: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  modeDot: { width: 5, height: 5, borderRadius: 2.5 },
  modeText: { fontSize: 11, color: Editorial.textCaption },
  divider: { height: 1, backgroundColor: ink(0.08) },

  /* 코지 패널과 같은 폭·테두리 — 화면을 옮겨도 오른쪽 열의 자리가 흔들리지 않게 */
  panel: {
    width: ChatPanelWidth,
    flexShrink: 0,
    borderLeftWidth: 1,
    borderLeftColor: ink(0.08),
    backgroundColor: Editorial.page,
  },
  overlayHead: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    height: 52,
    paddingHorizontal: 20,
  },
  overlayTitle: { fontSize: 15, fontWeight: '600', color: INK },
});
