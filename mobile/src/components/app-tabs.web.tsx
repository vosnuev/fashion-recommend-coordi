import { useState } from 'react';
import { router, usePathname } from 'expo-router';
import { Tabs, TabList, TabTrigger, TabSlot, TabTriggerSlotProps } from 'expo-router/ui';
import { Pressable, View, Text, StyleSheet } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ChatPanelWidth, Editorial, Fonts, ink, onNav, SidebarWidth } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';

import { ChatConversation } from './chat/chat-conversation';
import { Icon, type IconName } from './icon';

const INK = Editorial.ink;

// 채팅은 탭이 아니라 + 버튼에서 시작한다.
const TABS = [
  { name: 'home', href: '/home', icon: 'house', label: '홈' },
  { name: 'closet', href: '/closet', icon: 'tshirt', label: '옷장' },
  { name: 'lookbook', href: '/lookbook', icon: 'book', label: '룩북' },
  { name: 'my', href: '/my', icon: 'person', label: '마이' },
] as const satisfies readonly { name: string; href: string; icon: IconName; label: string }[];

/* 하단 탭바에는 넣지 않지만 라우트로는 등록해야 하는 화면들.
   TabTrigger 가 트리에 없으면 expo-router 가 라우트를 인식하지 못해 다른 탭이 열린다.
   그래서 모바일 바에서도 숨긴 채로 등록해 둔다. */
/* 사이드바 첫 항목은 '채팅'(지난 대화 목록)이다. 목록 안에 '새 채팅' 버튼이 있어
   목록·새 대화 양쪽으로 갈 수 있다 — 반대로 새 대화로 바로 보내면 지난 대화를 열 길이 없다. */
const CHAT_TAB = { name: 'chat', href: '/chat', icon: 'bubble.left', label: '채팅' } as const;
const NEW_CHAT_TAB = { name: 'chat-mode', href: '/chat-mode', icon: 'bubble.left', label: '새 채팅' } as const;
const CALENDAR_TAB = { name: 'calendar', href: '/calendar', icon: 'calendar', label: '캘린더' } as const;
/* 사이드바 항목으로는 안 보이지만 (tabs) 안에 있어야 좌측 사이드바가 유지되는 상세·설정 화면들.
   TabTrigger 가 트리에 없으면 expo-router 가 라우트를 인식 못 해 엉뚱한 탭이 열리므로 등록만 해 둔다.
   (탭 그룹 밖 화면은 사이드바 없이 전체폭으로 떠 사이드바가 사라진다. icon 은 hidden 이라 표시 안 됨.) */
const HIDDEN_ROUTES = [
  { name: 'chat-room', href: '/chat-room', icon: 'bubble.left', label: '대화' },
  { name: 'look-detail', href: '/look-detail', icon: 'book', label: '추천 룩' },
  { name: 'rec-card', href: '/rec-card', icon: 'sparkles', label: '추천 코디' },
  { name: 'fitting', href: '/fitting', icon: 'sparkles', label: '가상 피팅' },
  { name: 'item-detail', href: '/item-detail', icon: 'tshirt', label: '아이템' },
  { name: 'saved-look', href: '/saved-look', icon: 'book', label: '저장 룩' },
  { name: 'budget', href: '/budget', icon: 'person', label: '예산' },
  { name: 'style-onboarding', href: '/style-onboarding', icon: 'person', label: '추구미' },
  { name: 'permissions', href: '/permissions', icon: 'person', label: '권한' },
  { name: 'notifications', href: '/notifications', icon: 'bell', label: '알림' },
  { name: 'support', href: '/support', icon: 'questionmark.circle', label: '도움말' },
  { name: 'terms', href: '/terms', icon: 'book', label: '약관' },
  { name: 'account', href: '/account', icon: 'person', label: '계정 관리' },
  { name: 'measure-input', href: '/measure-input', icon: 'ruler', label: '체형측정' },
  { name: 'measure-capture', href: '/measure-capture', icon: 'ruler', label: '체형촬영' },
  { name: 'measure-result', href: '/measure-result', icon: 'ruler', label: '체형결과' },
] as const satisfies readonly { name: string; href: string; icon: IconName; label: string }[];

/**
 * 탭 내비게이션 (웹).
 * 창 폭에 따라 하단 탭바(모바일) ↔ 좌측 사이드바(데스크톱)로 바뀐다.
 * 기기 종류가 아니라 폭으로 판단하므로 데스크톱에서 창을 좁혀도 하단 탭바로 되돌아간다.
 *
 * TabList 를 먼저 두는 이유: 데스크톱에선 루트가 가로 배치라 그대로 왼쪽 열이 되고,
 * 모바일에선 하단 바가 position:absolute 라 순서와 무관하게 아래에 뜬다.
 */
export default function AppTabs() {
  const { isDesktop, isWide } = useBreakpoint();
  const pathname = usePathname();
  /* 하단 바는 position:absolute 로 떠 있으므로 본문(TabSlot) 아래에 바 높이만큼 여백을 준다.
     예전에는 하단 CTA를 가진 화면 목록(CTA_ROUTES)에만 줬는데, 목록에 없는 화면은
     그대로 가렸다 — 화면이 늘 때마다 목록을 챙겨야 하는 구조였다. 이제 바가 보이면 항상 준다. */
  const [barHeight, setBarHeight] = useState(0);
  /* 채팅 패널을 띄우지 않는 화면:
     - 채팅 화면 자체(chat/chat-room/chat-mode): 대화를 고르는 자리 옆에 또 다른 대화가
       열려 있으면 어느 쪽에 말하는지 알 수 없다.
     - 상세 화면(추천룩/가상피팅/아이템상세): 이 화면들은 그 자리를 아이템 2단 배치에 쓴다.
     - 캘린더: 7열 그리드 + 선택일 상세로 앱에서 폭을 가장 많이 쓴다. 패널까지 얹으면
       날짜 칸이 세로로 길쭉해진다. 코디 추천은 화면 안의 '코디 추천받기'로 간다. */
  const showChatPanel =
    isWide &&
    ![
      '/chat',
      '/chat-room',
      '/chat-mode',
      '/calendar',
      '/look-detail',
      '/fitting',
      '/item-detail',
      // 추천 코디 상세도 그 자리를 아이템 목록에 쓴다.
      '/rec-card',
    ].includes(pathname);

  return (
    <Tabs style={[styles.root, isDesktop && styles.rootDesktop]}>
      <TabList asChild>
        {isDesktop ? (
          <Sidebar>
            <TabTrigger name={CHAT_TAB.name} href={CHAT_TAB.href} asChild>
              <SidebarItem icon={CHAT_TAB.icon} label={CHAT_TAB.label} />
            </TabTrigger>
            {TABS.map((t) => (
              <TabTrigger key={t.name} name={t.name} href={t.href} asChild>
                <SidebarItem icon={t.icon} label={t.label} />
              </TabTrigger>
            ))}
            <TabTrigger name={CALENDAR_TAB.name} href={CALENDAR_TAB.href} asChild>
              <SidebarItem icon={CALENDAR_TAB.icon} label={CALENDAR_TAB.label} />
            </TabTrigger>
            {/* 라우트 등록용 — 사이드바 항목으로는 보이지 않는다. */}
            {[NEW_CHAT_TAB, ...HIDDEN_ROUTES].map((t) => (
              <TabTrigger key={t.name} name={t.name} href={t.href} asChild>
                <SidebarItem icon={t.icon} label={t.label} hidden />
              </TabTrigger>
            ))}
          </Sidebar>
        ) : (
          <BottomBar onLayout={(e) => {
            const h = e.nativeEvent.layout.height;
            if (h !== barHeight) {
              setBarHeight(h);
            }
          }}>
            {TABS.slice(0, 2).map((t) => (
              <TabTrigger key={t.name} name={t.name} href={t.href} asChild>
                <TabItem icon={t.icon} label={t.label} />
              </TabTrigger>
            ))}
            <AskButton />
            {TABS.slice(2).map((t) => (
              <TabTrigger key={t.name} name={t.name} href={t.href} asChild>
                <TabItem icon={t.icon} label={t.label} />
              </TabTrigger>
            ))}
            {/* 라우트 등록용 — 하단 바에는 자리를 차지하지 않게 숨긴다. */}
            {[CALENDAR_TAB, CHAT_TAB, NEW_CHAT_TAB, ...HIDDEN_ROUTES].map((t) => (
              <TabTrigger key={t.name} name={t.name} href={t.href} asChild>
                <TabItem icon={t.icon} label={t.label} hidden />
              </TabTrigger>
            ))}
          </BottomBar>
        )}
      </TabList>
      <TabSlot
        style={[styles.slot, !isDesktop && barHeight > 0 ? { paddingBottom: barHeight } : null]}
      />
      {showChatPanel ? <ChatPanel /> : null}
    </Tabs>
  );
}

/**
 * 넓은 화면(≥1280)에서 본문 오른쪽에 상주하는 채팅 패널.
 * 옷장·룩북을 보면서 바로 물어볼 수 있게 한다. 폭은 고정 — 대화는 한 줄이 길면 읽기 어렵다.
 */
function ChatPanel() {
  // 이 패널이 어느 화면 옆에 붙어 있는지 — 대화를 넓혀 봤다가 돌아올 자리다.
  const pathname = usePathname();
  return (
    <View style={styles.chatPanel}>
      <View style={styles.chatPanelHeader}>
        <Text style={styles.chatPanelTitle}>코지에게 물어보기</Text>
        <Pressable
          hitSlop={8}
          /* 이 패널은 다른 화면 옆에 붙어 있다 — 넓혀 보고 나면 보던 화면으로 돌아와야 한다. */
          onPress={() => router.push({ pathname: '/chat-room', params: { from: pathname } })}
          accessibilityLabel="대화 전체 보기">
          <Icon name="arrow.right" tintColor={ink(0.45)} size={16} />
        </Pressable>
      </View>
      <View style={styles.chatPanelDivider} />
      <ChatConversation variant="panel" />
    </View>
  );
}

/* ── 데스크톱: 좌측 사이드바 ─────────────────────────────── */

function Sidebar({ children, ...props }: React.ComponentProps<typeof View>) {
  return (
    <View {...props} style={styles.sidebar}>
      {/* 워드마크로 홈에 간다 — 웹에서 로고를 누르면 첫 화면으로 가는 게 당연한 동작이다.
          replace 가 아니라 navigate: replace 는 탭 스택을 갈아치워 뒤로가기가 어긋난다(lib/goBack 참고). */}
      <Pressable
        onPress={() => router.navigate('/(tabs)/home')}
        accessibilityRole="link"
        accessibilityLabel="홈으로"
        style={styles.sidebarBrandLink}>
        <Text style={styles.sidebarBrand}>cozy</Text>
      </Pressable>

      <View style={styles.sidebarNav}>{children}</View>
    </View>
  );
}

function SidebarItem({
  icon,
  label,
  isFocused,
  hidden,
  ...props
}: TabTriggerSlotProps & { icon: IconName; label: string; hidden?: boolean }) {
  /* 선택 표시는 글자·아이콘의 색과 굵기가 전부 맡는다. 면도 테두리도 쓰지 않는다. */
  const color = isFocused ? Editorial.white : onNav(0.78);
  return (
    <Pressable
      {...props}
      style={[styles.sidebarItem, hidden && styles.hiddenTrigger]}>
      <Icon name={icon} tintColor={color} size={20} />
      <Text style={[styles.sidebarLabel, { color, fontWeight: isFocused ? '600' : '500' }]}>
        {label}
      </Text>
    </Pressable>
  );
}

/* ── 모바일: 하단 탭바 ──────────────────────────────────── */

function BottomBar({ children, ...props }: React.ComponentProps<typeof View>) {
  const insets = useSafeAreaInsets();
  return (
    <View
      {...props}
      nativeID="cozy-tabbar"
      style={[styles.bar, { paddingBottom: Math.max(insets.bottom, 8) }]}>
      {children}
    </View>
  );
}

function TabItem({
  icon,
  label,
  isFocused,
  hidden,
  ...props
}: TabTriggerSlotProps & { icon: IconName; label: string; hidden?: boolean }) {
  const color = isFocused ? Editorial.white : onNav(0.78);
  return (
    <Pressable {...props} style={[styles.item, hidden && styles.hiddenTrigger]}>
      <Icon name={icon} tintColor={color} size={22} />
      <Text style={[styles.label, { color, fontWeight: isFocused ? '600' : '500' }]}>{label}</Text>
    </Pressable>
  );
}

function AskButton() {
  return (
    <View style={styles.askSlot}>
      <Pressable
        style={styles.askButton}
        onPress={() => router.push('/chat-mode')}
        accessibilityLabel="질문하기">
        <Icon name="plus" tintColor={INK} size={20} />
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Editorial.page },
  rootDesktop: { flexDirection: 'row' },
  slot: { flex: 1, minWidth: 0 },
  hiddenTrigger: { display: 'none' },

  // 우측 채팅 패널 (≥1280)
  chatPanel: {
    width: ChatPanelWidth,
    borderLeftWidth: 1,
    borderLeftColor: ink(0.08),
    backgroundColor: Editorial.page,
  },
  chatPanelHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    height: 52,
    paddingHorizontal: 16,
  },
  chatPanelTitle: { fontSize: 14, fontWeight: '600', color: INK },
  chatPanelDivider: { height: 1, backgroundColor: ink(0.08) },

  // 데스크톱 사이드바
  sidebar: {
    width: SidebarWidth,
    /* 테두리를 두지 않는다 — 어두운 면과 오트 본문이 6.4:1 로 갈려 경계가 이미 또렷하다.
       여기에 선을 더하면 이음새만 두꺼워 보인다. */
    backgroundColor: Editorial.nav,
    paddingHorizontal: 16,
    paddingTop: 28,
    gap: 8,
  },
  /* 누르는 영역은 글자만큼만 — 사이드바 폭을 다 먹으면 옆 빈 자리를 눌러도 홈으로 가서
     '잘못 눌렀나' 싶어진다. 여백(marginBottom)은 링크가 갖고, 글자는 자리만 잡는다. */
  sidebarBrandLink: { alignSelf: 'flex-start', marginBottom: 20 },
  sidebarBrand: {
    fontFamily: Fonts.serif,
    fontSize: 24,
    color: Editorial.white,
    paddingHorizontal: 10,
  },
  sidebarNav: { gap: 2 },
  sidebarItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    height: 42,
    paddingHorizontal: 10,
    borderRadius: 10,
  },
  sidebarLabel: { fontSize: 14, letterSpacing: -0.1 },

  // 모바일 하단 탭바 — 콘텐츠 위에 떠 있는 글래스 바 (backdrop-filter 는 global.css #cozy-tabbar)
  bar: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    /* TabList 가 TabSlot 보다 먼저 오므로(데스크톱에서 왼쪽 열이 되기 위해) 그냥 두면
       뒤에 오는 콘텐츠가 위에 덮여 탭바가 보이지 않는다. */
    zIndex: 10,
    flexDirection: 'row',
    alignItems: 'center',
    /* 어두운 면. 불투명하게 둔다 — 반투명이면 뒤로 지나가는 사진이 비쳐 바 색이 흔들린다.
       테두리는 두지 않는다: 오트 본문과 6.4:1 로 갈려 경계가 이미 또렷하다. */
    backgroundColor: Editorial.nav,
    paddingTop: 8,
  },
  item: { flex: 1, alignItems: 'center', gap: 3, paddingVertical: 2 },
  label: { fontSize: 10.5, letterSpacing: 0.2 },
  askSlot: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  askButton: {
    width: 36,
    height: 36,
    borderRadius: 8,
    backgroundColor: Editorial.surface,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: ink(0.08),
  },
});
