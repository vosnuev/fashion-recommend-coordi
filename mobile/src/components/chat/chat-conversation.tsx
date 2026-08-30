import { router } from 'expo-router';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Animated,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  type NativeSyntheticEvent,
  type TextInputKeyPressEventData,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { StylistCardGroup } from '@/components/chat/stylist-cards';
import { StylistPicker } from '@/components/chat/stylist-picker';
import { ClosetItemSelectSheet } from '@/components/chat/closet-item-select-sheet';
import { SharedItemPicker } from '@/components/chat/shared-item-picker';
import { Icon } from '@/components/icon';
import { SmartImage, useToast } from '@/components/ui';
import { ContentMax, Editorial, Fonts, ink, Type } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { shouldSubmitChatInputOnKeyPress } from '@/lib/chatInput';
import { pickOutfitPhoto } from '@/lib/pickItemPhoto';
import type { StylistId } from '@/lib/stylistApi';
import {
  chatStore,
  STYLE_FALLBACK_NOTE,
  useChatSession,
  type ChatMessage,
  type ChatReferencePick,
} from '@/state/chat';
import { stylistStore } from '@/state/stylist';

const INK = Editorial.ink;
const BONE = Editorial.bone;

const QUICK = ['더 캐주얼하게', '다른 색으로', '아우터 추천', '신발만 바꿔줘'];

type WebTextInputKeyPressEvent = NativeSyntheticEvent<
  TextInputKeyPressEventData & {
    isComposing?: boolean;
    keyCode?: number;
    shiftKey?: boolean;
  }
> & {
  keyCode?: number;
  shiftKey?: boolean;
};

/** 사이드 패널에서 쓰는 시작 인사 — 넓은 화면에선 옷장을 보며 바로 물어보는 흐름이다. */
const PANEL_SEED: ChatMessage[] = [
  {
    id: 'p1',
    role: 'ai',
    kind: 'text',
    text: '무엇을 입을지 고민되면 물어보세요.\n옷장을 보면서 바로 추천해드릴게요.',
  },
];

// 타이핑 표시 — 점 3개가 순차로 밝아지는 애니메이션
function TypingDots() {
  /* ref 세 개가 아니라 useMemo 하나로 — ref 값을 렌더 중에 읽으면 안 된다(react-hooks/refs). */
  const dots = useMemo(
    () => [new Animated.Value(0.3), new Animated.Value(0.3), new Animated.Value(0.3)],
    [],
  );
  useEffect(() => {
    const anims = dots.map((d, i) =>
      Animated.loop(
        Animated.sequence([
          Animated.delay(i * 180),
          Animated.timing(d, { toValue: 1, duration: 320, useNativeDriver: true }),
          Animated.timing(d, { toValue: 0.3, duration: 320, useNativeDriver: true }),
          Animated.delay((2 - i) * 180),
        ]),
      ),
    );
    anims.forEach((a) => a.start());
    return () => anims.forEach((a) => a.stop());
  }, [dots]);

  return (
    <View style={styles.typing}>
      {dots.map((d, i) => (
        <Animated.View key={i} style={[styles.typingDot, { opacity: d }]} />
      ))}
    </View>
  );
}

/**
 * 대화 본문(메시지 · 빠른 프롬프트 · 입력창).
 *
 * 두 곳에서 쓴다:
 *   - variant="screen" : /chat-room 화면. 헤더는 화면 쪽이 그린다. sessionId 로 세션에 붙는다.
 *   - variant="panel"  : 넓은 화면(≥1280)에서 우측에 상주하는 패널. 들어올 때는 세션이 없고,
 *                        **첫 질문을 보낼 때 대화를 하나 만든다.** 옷장을 보며 묻는 자리라
 *                        옷장 기반 모드로 연다.
 *
 * 패널은 폭이 이미 고정이라 본문 최대 폭을 걸지 않고, 하단 SafeArea 여백도 쓰지 않는다.
 */
export function ChatConversation({
  variant = 'screen',
  sessionId,
}: {
  variant?: 'screen' | 'panel';
  sessionId?: string;
}) {
  const isPanel = variant === 'panel';
  const { contentStyle } = useBreakpoint();
  // 패널은 자체 폭이 고정이라 최대 폭 제한이 필요 없다.
  const widthStyle = isPanel ? null : contentStyle(ContentMax.narrow);

  const [text, setText] = useState('');
  /* 패널이 첫 질문에서 만들어 낸 대화. 화면 변형은 항상 sessionId 를 받으므로 쓰이지 않는다. */
  const [panelSessionId, setPanelSessionId] = useState<string | undefined>(undefined);
  const activeId = sessionId ?? panelSessionId;
  const session = useChatSession(activeId);
  /* 아직 대화가 없는 패널에는 시작 인사만 보여준다. 대화가 생기면 서버가 넣어 둔
     인사 메시지가 그 자리를 대신한다.
     timeline 을 보는 이유 — 스타일리스트 카드와 모드 구분선은 서버 대화에 없고 앱이
     끼워 넣는 것이다(state/chat.ts 의 ChatSession.timeline 주석). */
  const messages = session?.timeline ?? PANEL_SEED;
  /* 타이핑 표시는 답변을 기다리는 '지금'만의 상태라 저장하지 않는다 (state/chat.ts 참고). */
  const [typing, setTyping] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const toast = useToast();

  const [closetSelectOpen, setClosetSelectOpen] = useState(false);
  const [sharedPickerOpen, setSharedPickerOpen] = useState(false);
  /** 보내기 전에 골라 둔 참고 옷. 전송에 성공하면 비우고, 실패하면 남겨 다시 시도하게 한다. */
  const [reference, setReference] = useState<ChatReferencePick | null>(null);

  /**
   * 스타일리스트 카드가 채워지는 상황 — 진행이 바뀔 때마다 달라지는 짧은 글자로 만든다.
   * 말풍선 배열 전체를 effect 의 의존성으로 걸면 상관없는 변화에도 화면이 끌려 내려간다.
   */
  const stylistProgress = messages
    .map((m) => (m.kind === 'stylist' ? m.cards.map((c) => c.status[0]).join('') : ''))
    .join('');
  /** 아직 안 끝난 카드가 있는지 (P=PENDING, R=RUNNING) */
  const stylistPending = /[PR]/.test(stylistProgress);

  const scrollRef = useRef<ScrollView>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  /* 카드가 하나씩 채워지는 동안 시야가 따라가게 한다. 안 따라가면 먼저 끝난 카드만 보이고
     뒤에 붙는 카드는 화면 밖에서 쌓여, '완료된 것부터 보여준다'가 무의미해진다. */
  useEffect(() => {
    if (!stylistProgress) return;
    const t = setTimeout(() => scrollRef.current?.scrollToEnd({ animated: true }), 60);
    return () => clearTimeout(t);
  }, [stylistProgress]);

  const scrollToEnd = () => {
    const t = setTimeout(() => scrollRef.current?.scrollToEnd({ animated: true }), 60);
    timers.current.push(t);
  };

  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  /* 대화를 열면 내용을 받아온다. 이미 받아 둔 대화는 스토어가 알아서 건너뛴다.
     목록에서 넘어온 세션은 제목·시각만 있고 말풍선이 비어 있어 이 호출이 채운다. */
  useEffect(() => {
    if (!activeId) return;
    chatStore.loadMessages(activeId).catch(() => {
      toast('대화를 불러오지 못했어요', { variant: 'error' });
    });
  }, [activeId, toast]);

  /**
   * 이전 대화 더 보기.
   *
   * 대화를 열 때는 최근 50개만 받는다(state/chat.ts 의 loadMessages). 스크롤이 위에 닿을
   * 때 자동으로 받아오는 방법도 있지만, 붙인 만큼 화면이 밀려 읽던 자리를 잃는다.
   * 눌러서 받으면 사용자가 그 이동을 예상한다.
   */
  const loadOlder = async () => {
    if (!activeId || loadingOlder) return;
    setLoadingOlder(true);
    try {
      await chatStore.loadOlderMessages(activeId);
    } catch (e) {
      toast(e instanceof Error ? e.message : '이전 대화를 불러오지 못했어요', {
        variant: 'error',
      });
    } finally {
      setLoadingOlder(false);
    }
  };

  /* 패널은 대화 없이 열리므로 첫 입력에서 하나 만든다. 옷장을 보며 묻는 자리라 옷장 기반. */
  const ensureSession = async (): Promise<string> => {
    if (activeId) return activeId;
    const created = await chatStore.createSession('closet');
    setPanelSessionId(created.id);
    return created.id;
  };

  /**
   * 질문 보내기.
   *
   * 답변은 서버가 바로 주지 않는다 — 질문이 접수되면 실행(run)이 하나 생기고, 별도 워커가
   * 답을 만들 때까지 기다려야 한다(state/chat.ts 의 sendText). 그 동안 타이핑 표시를 띄운다.
   *
   * 입력창은 **보내기 전에** 비운다. 기다리는 동안 글자가 남아 있으면 다시 눌러 같은 질문이
   * 두 번 가기 쉽다. 실패하면 되돌려 다시 보낼 수 있게 한다.
   */
  const send = async () => {
    const t = text.trim();
    if (!t || typing) return;

    const ref = reference;
    setText('');
    setTyping(true);
    scrollToEnd();
    try {
      /* 실패해도 토스트를 띄우지 않는다 — 사유는 대화 안에 한 줄로 남고, 토스트까지 겹치면
         같은 말을 두 번 하면서 정작 사라지는 쪽(토스트)만 눈에 띈다.
         단 참고 옷 실패는 대화에 남지 않아(요청이 접수조차 안 된다) 토스트로 알린다. */
      await chatStore.sendText(await ensureSession(), t, {
        reference: ref ?? undefined,
      });
      // 보낸 참고는 다음 질문까지 끌고 가지 않는다.
      setReference(null);
    } catch (e) {
      /* 입력 문장과 고른 옷을 그대로 남긴다 — 다른 옷으로 바꾸거나 그대로 다시 보낼 수 있게. */
      setText(t);
      toast(e instanceof Error ? e.message : '메시지를 보내지 못했어요', { variant: 'error' });
    } finally {
      setTyping(false);
      scrollToEnd();
    }
  };

  const handleInputKeyPress = (event: NativeSyntheticEvent<TextInputKeyPressEventData>) => {
    const webEvent = event as WebTextInputKeyPressEvent;
    const { nativeEvent } = webEvent;
    const shouldSubmit = shouldSubmitChatInputOnKeyPress(Platform.OS, {
      key: nativeEvent.key,
      shiftKey: webEvent.shiftKey ?? nativeEvent.shiftKey,
      isComposing: nativeEvent.isComposing,
      keyCode: webEvent.keyCode ?? nativeEvent.keyCode,
    });
    if (!shouldSubmit) return;

    event.preventDefault();
    void send();
  };

  /**
   * 사진 넣기 — 갤러리에서 고른 사진을 올리고 무드까지 읽어낸다.
   *
   * 대화가 없는 패널에서 사진부터 넣을 수 있으므로 여기서도 세션을 먼저 만든다.
   * 분석이 끝날 때까지 타이핑 표시를 띄운다 — 답변을 기다리는 것과 같은 성격이라
   * 같은 표시를 쓴다.
   */
  const attachPhoto = async () => {
    if (typing) return;
    let uri: string | null = null;
    try {
      uri = await pickOutfitPhoto();
    } catch {
      toast('사진을 불러오지 못했어요', { variant: 'error' });
      return;
    }
    if (!uri) return; // 고르다 취소 — 아무 일도 일어나지 않는다

    setTyping(true);
    scrollToEnd();
    try {
      await chatStore.attachPhoto(await ensureSession(), uri);
    } catch (e) {
      toast(e instanceof Error ? e.message : '사진을 올리지 못했어요', { variant: 'error' });
    } finally {
      setTyping(false);
      scrollToEnd();
    }
  };

  /** 무드 카드의 두 버튼. 어느 카드가 진행 중인지 알아야 그 카드만 잠글 수 있다. */
  const [deciding, setDeciding] = useState<string | null>(null);

  const decideMood = async (attachmentId: string, decision: 'APPROVE' | 'REJECT') => {
    if (!activeId || deciding) return;
    setDeciding(attachmentId);
    try {
      await chatStore.decideMood(activeId, attachmentId, decision);
    } catch (e) {
      toast(e instanceof Error ? e.message : '반영하지 못했어요', { variant: 'error' });
    } finally {
      setDeciding(null);
    }
  };

  /* ── 스타일리스트 모드 ─────────────────────────────
     모드 버튼은 대화방을 옮기지도 새로 만들지도 않는다. 다음 질문을 어떻게 답할지만 바꾼다. */

  const [pickerOpen, setPickerOpen] = useState(false);
  const stylistOn = session?.responseMode === 'STYLIST';
  const selectedIds = session?.selectedPersonaIds ?? [];
  const selectedNames = stylistStore.displayNames(selectedIds);

  const openPicker = () => {
    // 목록은 대화 중에 바뀌는 값이 아니라 한 번만 받아 둔다. 실패하면 팝업이 오류를 보여준다.
    stylistStore.load().catch(() => {});
    setPickerOpen(true);
  };

  const handleSelectClosetItems = async (
    selection: {
      kind: 'items' | 'hashtags';
      items: { id: string; image: string; name: string }[];
      hashtagIds: string[];
      hashtagNames: string[];
    },
  ) => {
    if ((selection.items.length === 0 && selection.hashtagIds.length === 0) || typing) return;

    if (selection.kind === 'items') {
      const item = selection.items[0];
      if (!item) return;
      setReference({
        referenceType: 'WARDROBE_ITEM',
        referenceItemId: item.id,
        imageUrl: item.image ?? null,
        itemName: item.name || '옷',
        ownerName: '내 옷',
      });
      return;
    }

    setTyping(true);
    try {
      const names = selection.hashtagNames.map((name) => `#${name}`).join(', ');
      await chatStore.sendText(
        await ensureSession(),
        `${names} 해시태그에 있는 옷을 기준으로 코디를 추천해줘.`,
        {
          wardrobeScope: {
            hashtag_ids: selection.hashtagIds,
            match_mode: 'REQUIRED',
          },
        },
      );
    } catch (e) {
      toast(e instanceof Error ? e.message : '선택한 옷으로 추천을 요청하지 못했어요', {
        variant: 'error',
      });
    } finally {
      setTyping(false);
      scrollToEnd();
    }
  };

  /* 모드 저장이 실패하면 이전 모드와 선택을 그대로 둔다(설계서 19장).
     스토어가 서버 응답을 받은 뒤에만 세션을 바꾸므로 여기서 되돌릴 것은 없다. */
  const enableStylists = async (ids: StylistId[]) => {
    setPickerOpen(false);
    // 0명은 팝업이 막지만, 여기까지 왔다면 켜지 않는다 — 빈 배열을 보내면 서버가 400 을 낸다.
    if (ids.length === 0) return;
    try {
      await chatStore.setResponseMode(await ensureSession(), 'STYLIST', ids);
    } catch (e) {
      toast(e instanceof Error ? e.message : '모드를 바꾸지 못했어요', { variant: 'error' });
    }
  };

  const disableStylists = async () => {
    setPickerOpen(false);
    // 대화가 없으면 끌 것도 없다. 끄자고 대화를 새로 만들지는 않는다.
    if (!activeId) return;
    try {
      await chatStore.setResponseMode(activeId, 'DEFAULT');
    } catch (e) {
      toast(e instanceof Error ? e.message : '모드를 바꾸지 못했어요', { variant: 'error' });
    }
  };

  const selectCard = async (runId: string, personaId: StylistId) => {
    if (!activeId) return;
    try {
      const result = await chatStore.saveStylistCard(activeId, runId, personaId);
      toast(
        result.renderStarted
          ? '내 룩에 저장했고 코디 이미지를 만들고 있어요'
          : '내 룩에는 저장했지만 이미지는 만들지 못했어요',
        { variant: result.renderStarted ? 'success' : 'error' },
      );
    } catch (e) {
      toast(e instanceof Error ? e.message : '저장하지 못했어요', { variant: 'error' });
    }
  };

  const retryCardRender = async (runId: string, personaId: StylistId) => {
    if (!activeId) return;
    try {
      const started = await chatStore.retryStylistRender(activeId, runId, personaId);
      if (!started) {
        toast('이미지 작업을 접수하지 못했어요', { variant: 'error' });
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : '이미지 작업을 접수하지 못했어요', {
        variant: 'error',
      });
    }
  };

  /* 다른 추천·재실행은 카드가 스스로 진행 상태를 보여주므로(받는 중… / 뼈대) 토스트로
     또 알리지 않는다. 실패했을 때만 말한다. */
  const alternativeCard = async (runId: string, personaId: StylistId) => {
    if (!activeId) return;
    try {
      await chatStore.alternativeStylist(activeId, runId, personaId);
    } catch (e) {
      toast(e instanceof Error ? e.message : '다른 추천을 받지 못했어요', { variant: 'error' });
    }
  };

  const retryCard = async (runId: string, personaId: StylistId) => {
    if (!activeId) return;
    try {
      await chatStore.retryStylist(activeId, runId, personaId);
    } catch (e) {
      toast(e instanceof Error ? e.message : '다시 시도하지 못했어요', { variant: 'error' });
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={0}
      {...{
        // Web HTML5 Drag and drop
        onDragOver: (e: any) => {
          if (Platform.OS === 'web') {
            e.preventDefault();
          }
        },
        onDrop: (e: any) => {
          if (Platform.OS === 'web') {
            e.preventDefault();
            try {
              const dataStr = e.dataTransfer.getData('text/plain');
              if (!dataStr) return;
              const item = JSON.parse(dataStr);
              if (!item?.id) return;
              if (!item.shared) {
                if (item.image) {
                  void handleSelectClosetItems({
                    kind: 'items',
                    items: [item],
                    hashtagIds: [],
                    hashtagNames: [],
                  });
                }
                return;
              }
              setReference({
                referenceType: 'SHARED_WARDROBE_ITEM',
                referenceItemId: item.id,
                imageUrl: item.image ?? null,
                itemName: item.name || '옷',
                ownerName: item.owner || '멤버',
              });
            } catch (err) {
              console.error('Drop parsing error:', err);
            }
          }
        }
      }}>
      <ScrollView
        ref={scrollRef}
        style={styles.flex}
        contentContainerStyle={[styles.messages, widthStyle]}
        keyboardShouldPersistTaps="handled">
        {session?.olderCursor ? (
          <Pressable style={styles.older} onPress={loadOlder} disabled={loadingOlder}>
            <Text style={styles.olderText}>
              {loadingOlder ? '불러오는 중…' : '이전 대화 더 보기'}
            </Text>
          </Pressable>
        ) : null}

        {messages.map((m) => {
          if (m.role === 'user') {
            return (
              <View key={m.id} style={styles.userRow}>
                {m.kind === 'image' ? (
                  <View style={styles.userImage}>
                    {m.uri ? (
                      <SmartImage
                        uri={m.uri}
                        width="100%"
                        radius={0}
                        contentFit="cover"
                        style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }}
                      />
                    ) : (
                      <Icon name="photo" tintColor={ink(0.3)} size={30} />
                    )}
                  </View>
                ) : m.kind === 'reference' ? (
                  /* 참고한 공유 옷 + 요청 문장. 친구 옷은 참고 대상이지 코디 구성 아이템이
                     아니므로 '포함'이라는 말을 쓰지 않는다. */
                  <View style={styles.refBubble}>
                    <View style={styles.refHead}>
                      <SmartImage uri={m.imageUrl} width={44} height={44} radius={8} />
                      <View style={styles.refHeadText}>
                        <Text style={styles.refLabel}>공유 옷 참고</Text>
                        <Text style={styles.refName} numberOfLines={1}>
                          {m.ownerName}님의 {m.itemName}
                        </Text>
                        {m.roomName ? (
                          <Text style={styles.refRoom} numberOfLines={1}>
                            {m.roomName}
                          </Text>
                        ) : null}
                      </View>
                    </View>
                    {m.text ? <Text style={styles.refText}>{m.text}</Text> : null}
                  </View>
                ) : (
                  <View style={styles.userBubble}>
                    <Text style={styles.userText}>{m.text}</Text>
                  </View>
                )}
              </View>
            );
          }
          /* 오류는 코지가 한 말이 아니므로 말풍선·아바타를 주지 않는다 —
             답변인 척하면 실패한 것을 답으로 읽게 된다. */
          if (m.kind === 'error') {
            return (
              <View key={m.id} style={styles.errorBlock}>
                <View style={styles.errorRow}>
                  <Icon name="exclamationmark.triangle" tintColor={Editorial.wine} size={13} />
                  <Text style={styles.errorText}>{m.text}</Text>
                </View>
                {m.action === 'OPEN_WARDROBE' ? (
                  <Pressable
                    style={styles.errorCta}
                    onPress={() => router.push('/(tabs)/closet')}
                    accessibilityRole="button">
                    <Icon name="plus" tintColor="#fff" size={13} />
                    <Text style={styles.errorCtaText}>옷장에 옷 추가하기</Text>
                  </Pressable>
                ) : null}
              </View>
            );
          }
          /* 모드가 바뀐 자리. 오간 말이 아니라 상태 표시라 오류 줄과 같은 결로 —
             말풍선도 아바타도 주지 않는다. 여기부터 답하는 방식이 달라졌다는 표시일 뿐이다. */
          if (m.kind === 'mode') {
            return (
              <View key={m.id} style={styles.modeMarkRow}>
                <View style={styles.modeMarkLine} />
                <Text style={styles.modeMarkText}>
                  {m.mode === 'STYLIST'
                    ? `스타일리스트 추천 · ${m.names.join(' · ')}`
                    : '기본 추천'}
                </Text>
                <View style={styles.modeMarkLine} />
              </View>
            );
          }
          /* 스타일리스트 카드는 코지 아바타를 달지 않는다 — 카드마다 답한 사람이 따로 있고,
             그 이름이 카드 머리에 이미 붙어 있다. 폭도 아바타만큼 들이지 않고 다 쓴다. */
          if (m.kind === 'stylist') {
            return (
              <StylistCardGroup
                key={m.id}
                cards={m.cards}
                onSelect={(personaId) => selectCard(m.runId, personaId)}
                onAlternative={(personaId) => alternativeCard(m.runId, personaId)}
                onRetry={(personaId) => retryCard(m.runId, personaId)}
                onRenderRetry={(personaId) => retryCardRender(m.runId, personaId)}
                wardrobeBased={session?.mode === 'closet'}
                onOpenWardrobe={() => router.push('/(tabs)/closet')}
              />
            );
          }
          return (
            <View key={m.id} style={styles.aiRow}>
              <View style={styles.avatar}>
                <Text style={styles.avatarMark}>c</Text>
              </View>
              <View style={styles.aiCol}>
                {m.kind === 'rec' ? (
                  <View style={styles.recGroup}>
                    {/* 눌러서 상세로 — 구매 링크·코디 이미지·피드백은 그쪽에 있다.
                        (/look-detail 은 목업이라 쓰지 않는다. 방금 받은 추천과 상관없는 룩이 열린다.) */}
                    <Pressable
                      style={styles.recCard}
                      onPress={() =>
                        router.push({
                          pathname: '/rec-card',
                          params: {
                            resultId: m.resultId,
                            cardId: m.cardId,
                            /* 돌아올 자리를 함께 넘긴다 — 안 넘기면 상세가 기본값인 대화 '목록'으로
                               되돌려, 보고 있던 대화방을 한 칸 더 지나친다.
                               (패널에서 연 대화는 sessionId 가 없어 목록으로 두는 게 맞다) */
                            ...(activeId ? { from: `/chat-room?id=${activeId}` } : {}),
                          },
                        })
                      }>
                      <View style={styles.recBody}>
                      <Text style={styles.recTitle}>{m.title}</Text>
                      {/* 공유 옷을 참고했으면 무엇과 비슷한 것인지 먼저 말한다.
                          참고 안 한 추천은 배지가 null 이라 아무것도 안 그린다. */}
                      {m.referenceBadge ? (
                        <View style={styles.refMatch}>
                          <View style={styles.refBadge}>
                            <Text style={styles.refBadgeText}>{m.referenceBadge.label}</Text>
                          </View>
                          {/* fallback 은 실패가 아니라 정상 결과다 — 경고색·경고아이콘을 쓰지 않는다. */}
                          {m.referenceBadge.isStyleFallback ? (
                            <Text style={styles.refFallback}>{STYLE_FALLBACK_NOTE}</Text>
                          ) : null}
                        </View>
                      ) : null}
                      {/* 아이템 한 줄 — 사진이 있는 것만 그리고, 없으면 이름으로 대신한다 */}
                      <View style={styles.recItems}>
                        {m.items.map((item) => (
                          <View key={item.id} style={styles.recItem}>
                            <SmartImage
                              uri={item.imageUrl}
                              width={64}
                              height={64}
                              radius={10}
                              style={styles.recItemImage}
                            />
                            <Text style={styles.recItemName} numberOfLines={2}>
                              {item.name}
                            </Text>
                            {/* 옷장 옷은 살 필요가 없다는 것이 가격보다 중요한 정보다 */}
                            <Text style={styles.recItemMeta}>
                              {item.fromWardrobe
                                ? '내 옷장'
                                : item.price != null
                                  ? `${item.price.toLocaleString()}원`
                                  : '새 상품'}
                            </Text>
                          </View>
                        ))}
                      </View>

                      {m.totalPrice ? (
                        <Text style={styles.recTotal}>
                          새로 사면 {m.totalPrice.toLocaleString()}원
                        </Text>
                      ) : null}

                      <View style={styles.recCta}>
                        <Text style={styles.recCtaText}>코디 자세히 보기</Text>
                        <Icon name="chevron.right" tintColor={INK} size={12} />
                      </View>
                      </View>
                    </Pressable>
                    {m.rationale ? (
                      <Text style={styles.recRationale}>{m.rationale}</Text>
                    ) : null}
                  </View>
                ) : m.kind === 'mood' ? (
                  <View style={styles.moodCard}>
                    <Text style={styles.moodLead}>사진에서 이런 무드가 보여요</Text>
                    {m.summary ? <Text style={styles.moodSummary}>{m.summary}</Text> : null}
                    <View style={styles.recTags}>
                      {m.tags.map((t) => (
                        <View key={t} style={styles.recTag}>
                          <Text style={styles.recTagText}>{t}</Text>
                        </View>
                      ))}
                    </View>
                    {/* 한 번 고른 뒤엔 버튼을 치우고 결과만 남긴다 — 결정은 서버에 저장돼
                        다시 열어도 그대로고, 같은 카드를 두 번 고를 일이 없다. */}
                    {m.decision === null ? (
                      <View style={styles.moodBtns}>
                        <Pressable
                          style={styles.moodPrimary}
                          disabled={deciding === m.attachmentId}
                          onPress={() => decideMood(m.attachmentId, 'APPROVE')}>
                          <Text style={styles.moodPrimaryText}>
                            {deciding === m.attachmentId ? '반영하는 중…' : '이 무드로 추천받기'}
                          </Text>
                        </Pressable>
                        <Pressable
                          style={styles.moodGhost}
                          disabled={deciding === m.attachmentId}
                          onPress={() => decideMood(m.attachmentId, 'REJECT')}>
                          <Text style={styles.moodGhostText}>아니에요</Text>
                        </Pressable>
                      </View>
                    ) : (
                      <Text style={styles.moodDecided}>
                        {m.decision === 'APPROVED'
                          ? '이 무드를 반영했어요. 이제 어떤 자리에 입을지 말해주세요.'
                          : '이 무드는 반영하지 않았어요.'}
                      </Text>
                    )}
                  </View>
                ) : (
                  <View style={styles.aiBubble}>
                    <Text style={styles.aiText}>{m.text}</Text>
                  </View>
                )}
              </View>
            </View>
          );
        })}

        {/* 스타일리스트 카드가 이미 깔렸으면 타이핑 점은 띄우지 않는다 — 지금 답을 만드는 건
            코지가 아니라 스타일리스트들이고, 그 진행은 카드가 각자 보여주고 있다.
            (질문을 접수하는 잠깐은 카드가 아직 없어서 점이 뜬다. 그때는 맞는 표시다.) */}
        {typing && !stylistPending ? (
          <View style={styles.aiRow}>
            <View style={styles.avatar}>
              <Text style={styles.avatarMark}>c</Text>
            </View>
            <View style={styles.aiCol}>
              <View style={[styles.aiBubble, styles.typingBubble]}>
                <TypingDots />
              </View>
            </View>
          </View>
        ) : null}
      </ScrollView>

      {/* 모드 버튼 + 빠른 프롬프트.
          모드 버튼은 스크롤 밖에 고정한다 — 지금 어떤 방식으로 답하는지는 늘 보여야 한다. */}
      <View style={[styles.toolRow, widthStyle]}>
        <Pressable
          style={[styles.modeBtn, stylistOn && styles.modeBtnOn]}
          onPress={openPicker}
          accessibilityRole="button"
          accessibilityState={{ selected: stylistOn }}
          accessibilityLabel={
            stylistOn
              ? `스타일리스트 모드 켜짐, ${selectedNames.join(', ')}. 선택 바꾸기`
              : '스타일리스트 모드 켜기'
          }>
          <Icon name="person.2" tintColor={stylistOn ? '#fff' : ink(0.55)} size={14} />
          <Text style={[styles.modeBtnText, stylistOn && styles.modeBtnTextOn]} numberOfLines={1}>
            {stylistOn && selectedNames.length > 0 ? selectedNames.join(' · ') : '스타일리스트'}
          </Text>
        </Pressable>

        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          style={styles.quickScroll}
          contentContainerStyle={styles.quickRow}
          keyboardShouldPersistTaps="handled">
          {QUICK.map((q) => (
            <Pressable key={q} style={styles.quickChip} onPress={() => setText(q)}>
              <Text style={styles.quickText}>{q}</Text>
            </Pressable>
          ))}
        </ScrollView>
      </View>

      <StylistPicker
        visible={pickerOpen}
        active={stylistOn}
        selectedIds={selectedIds}
        onClose={() => setPickerOpen(false)}
        onConfirm={enableStylists}
        onTurnOff={disableStylists}
      />

      {/* 입력 바 */}
      <SafeAreaView edges={isPanel ? [] : ['bottom']} style={styles.inputSafe}>
        {/* 고른 참고 옷. 보내기 전에 무엇을 참고하는지 보이고, 여기서 바로 바꾸거나 뺄 수 있다. */}
        {reference ? (
          <View style={[styles.refPreview, widthStyle]}>
            <SmartImage uri={reference.imageUrl} width={34} height={34} radius={8} />
            <View style={styles.refPreviewText}>
              <Text style={styles.refPreviewLabel}>
                {reference.referenceType === 'WARDROBE_ITEM' ? '내 옷 참고' : '공유 옷 참고'}
              </Text>
              <Text style={styles.refPreviewName} numberOfLines={1}>
                {reference.referenceType === 'WARDROBE_ITEM'
                  ? reference.itemName
                  : `${reference.ownerName}님의 ${reference.itemName}`}
              </Text>
            </View>
            <Pressable
              hitSlop={10}
              disabled={typing}
              onPress={() => {
                if (reference.referenceType === 'WARDROBE_ITEM') setClosetSelectOpen(true);
                else setSharedPickerOpen(true);
              }}
              accessibilityLabel="참고할 옷 바꾸기">
              <Text style={styles.refPreviewAction}>바꾸기</Text>
            </Pressable>
            <Pressable
              hitSlop={10}
              disabled={typing}
              onPress={() => setReference(null)}
              accessibilityLabel="참고 취소">
              <Icon name="xmark" tintColor={ink(0.45)} size={13} />
            </Pressable>
          </View>
        ) : null}
        <View style={[styles.inputBar, widthStyle]}>
          <Pressable style={styles.photoBtn} onPress={attachPhoto} disabled={typing} hitSlop={8}>
            <Icon name="photo" tintColor={ink(typing ? 0.25 : 0.55)} size={22} />
          </Pressable>
          <Pressable
            style={[styles.photoBtn, { marginLeft: -2 }]}
            onPress={() => setClosetSelectOpen(true)}
            disabled={typing}
            accessibilityLabel="내 옷이나 해시태그로 추천받기"
            hitSlop={8}>
            <Icon
              name="slider.horizontal.3"
              tintColor={ink(typing ? 0.25 : 0.55)}
              size={21}
            />
          </Pressable>
          <Pressable
            style={[styles.photoBtn, { marginLeft: -2 }]}
            onPress={() => setSharedPickerOpen(true)}
            disabled={typing}
            accessibilityLabel="공유 옷 참고하기"
            hitSlop={8}>
            <Icon name="tshirt" tintColor={ink(typing ? 0.25 : 0.55)} size={22} />
          </Pressable>
          {/* 웹에서 multiline 은 textarea 로 렌더되어 기본 2줄 높이를 갖는다.
              numberOfLines={1} 로 한 줄에서 시작하게 하고, 길어지면 maxHeight 까지 늘어난다. */}
          <TextInput
            style={styles.input}
            value={text}
            onChangeText={setText}
            placeholder="메시지를 입력하세요"
            placeholderTextColor={ink(0.35)}
            multiline
            numberOfLines={1}
            onKeyPress={Platform.OS === 'web' ? handleInputKeyPress : undefined}
          />
          <Pressable
            style={[styles.sendBtn, text.trim().length > 0 && styles.sendBtnOn]}
            accessibilityLabel="보내기"
            onPress={send}>
            <Icon
              name="arrow.up"
              tintColor={text.trim().length > 0 ? '#fff' : ink(0.35)}
              size={18}
            />
          </Pressable>
        </View>
      </SafeAreaView>

      <ClosetItemSelectSheet
        visible={closetSelectOpen}
        onClose={() => setClosetSelectOpen(false)}
        onSelect={handleSelectClosetItems}
      />

      <SharedItemPicker
        visible={sharedPickerOpen}
        onClose={() => setSharedPickerOpen(false)}
        onPick={setReference}
      />
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },

  messages: { padding: 16, gap: 16 },
  older: { alignSelf: 'center', paddingVertical: 8, paddingHorizontal: 16 },
  olderText: { fontSize: 12.5, fontWeight: '500', color: Editorial.textCaption },
  aiRow: { flexDirection: 'row', gap: 8, alignItems: 'flex-start', maxWidth: '90%' },
  aiCol: { flex: 1, gap: 10 },

  /* 아바타 자리(30) + 간격(8)만큼 들여 코지 말풍선과 왼쪽 선을 맞춘다. */
  errorBlock: {
    gap: 9,
    paddingLeft: 38,
    maxWidth: '90%',
    alignItems: 'flex-start',
  },
  errorRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 6,
  },
  errorText: { flex: 1, fontSize: Type.caption, color: Editorial.wine, lineHeight: 18 },
  errorCta: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    minHeight: 36,
    paddingHorizontal: 14,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
  },
  errorCtaText: { fontSize: Type.caption, color: '#fff', fontWeight: '600' },
  avatar: {
    width: 30,
    height: 30,
    borderRadius: 15,
    backgroundColor: INK,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 2,
  },
  avatarMark: { fontFamily: Fonts.serif, fontSize: 15, color: '#fff' },
  aiBubble: {
    flexShrink: 1,
    alignSelf: 'flex-start',
    backgroundColor: Editorial.surface,
    borderWidth: 1, borderColor: Editorial.line,
    borderRadius: 18,
    borderTopLeftRadius: 6,
    paddingHorizontal: 14,
    paddingVertical: 11,
  },
  aiText: { fontSize: 14, color: Editorial.ink, lineHeight: 21 },
  typingBubble: { paddingVertical: 15 },
  typing: { flexDirection: 'row', gap: 5, alignItems: 'center' },
  typingDot: { width: 7, height: 7, borderRadius: 3.5, backgroundColor: ink(0.45) },

  userRow: { alignSelf: 'flex-end', maxWidth: '80%' },
  userBubble: {
    backgroundColor: INK,
    borderRadius: 18,
    borderTopRightRadius: 6,
    paddingHorizontal: 14,
    paddingVertical: 11,
  },
  userText: { fontSize: 14, color: '#fff', lineHeight: 21 },
  userImage: {
    width: 150,
    height: 190,
    borderRadius: 18,
    borderTopRightRadius: 6,
    backgroundColor: BONE,
    alignItems: 'center',
    justifyContent: 'center',
    /* 사진이 모서리 밖으로 넘치지 않게 — 말풍선 모양이 사진에도 그대로 적용돼야 한다. */
    overflow: 'hidden',
  },

  // 추천 카드
  recGroup: { alignSelf: 'stretch', gap: 8 },
  recCard: {
    alignSelf: 'stretch',
    borderWidth: 1,
    borderColor: ink(0.1),
    borderRadius: 18,
    overflow: 'hidden',
    backgroundColor: Editorial.surface,
  },
  recBody: { padding: 14, gap: 10 },
  recTitle: { fontSize: 14, fontWeight: '600', color: INK },
  refMatch: { gap: 6 },
  /* 배지는 면이 아니라 테두리로 — 이 앱에서 채워지는 자리는 CTA 하나뿐이다. */
  refBadge: {
    alignSelf: 'flex-start',
    paddingHorizontal: 9,
    paddingVertical: 4,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  refBadgeText: { fontSize: Type.micro, color: Editorial.textCaption, fontWeight: '500' },
  refFallback: { fontSize: Type.caption, color: Editorial.textSoft, lineHeight: 19 },

  /* 아이템을 가로로 늘어놓는다. 개수가 적어(보통 3~5) 가로 스크롤 없이 줄바꿈으로 받는다. */
  recItems: { flexDirection: 'row', flexWrap: 'wrap', gap: 12 },
  recItem: { width: 64, gap: 4 },
  recItemImage: { backgroundColor: BONE },
  recItemName: { fontSize: Type.micro, color: INK, lineHeight: 15 },
  recItemMeta: { fontSize: Type.micro, color: Editorial.textCaption },

  recTotal: { fontSize: Type.caption, fontWeight: '600', color: INK },
  /* 카드 아래는 왜 이 룩이 사용자에게 맞는지만 설명한다. 아이템별 선택 이유는 상세로 분리한다. */
  recRationale: { fontSize: Type.footnote, color: Editorial.textSoft, lineHeight: 20 },
  /* 무드 태그는 최대 5개라 한 줄을 넘길 수 있다 — 넘치면 잘리지 않고 줄바꿈으로 받는다. */
  recTags: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  recTag: {
    backgroundColor: Editorial.control,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  recTagText: { fontSize: 11, color: Editorial.textCaption, fontWeight: '500' },
  recCta: { flexDirection: 'row', alignItems: 'center', gap: 5, marginTop: 2 },
  moodCard: {
    borderWidth: 1,
    borderColor: ink(0.12),
    borderRadius: 16,
    padding: 14,
    gap: 10,
    backgroundColor: Editorial.surface,
  },
  moodLead: { fontSize: 13, color: Editorial.textSoft },
  /** 서버가 읽어낸 한 줄 요약. 태그만으로는 왜 그렇게 읽었는지가 안 보인다. */
  moodSummary: { fontSize: Type.footnote, color: INK, lineHeight: 20 },
  /** 결정한 뒤 버튼 자리를 대신한다. */
  moodDecided: { fontSize: Type.caption, color: Editorial.textSoft, lineHeight: 18 },
  moodBtns: { flexDirection: 'row', gap: 8, marginTop: 2 },
  moodPrimary: {
    height: 36,
    paddingHorizontal: 14,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  moodPrimaryText: { fontSize: 12.5, fontWeight: '600', color: '#fff' },
  moodGhost: {
    height: 36,
    paddingHorizontal: 14,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: ink(0.14),
    alignItems: 'center',
    justifyContent: 'center',
  },
  moodGhostText: { fontSize: 12.5, color: Editorial.textCaption },
  moodBusy: { opacity: 0.5 },
  /* 고른 뒤 남는 한 줄. 버튼이 사라진 자리에 결과가 보여야 무엇을 골랐는지 알 수 있다. */
  moodDone: { fontSize: Type.caption, color: Editorial.textCaption, marginTop: 2 },
  recCtaText: { fontSize: 13, fontWeight: '600', color: INK },

  /* 모드 구분선 — 실패 줄과 같은 결(작고 조용하게). 가로선을 양옆에 둬서
     '여기가 경계'라는 것이 글자 없이도 읽히게 한다. */
  modeMarkRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 2 },
  modeMarkLine: { flex: 1, height: 1, backgroundColor: Editorial.lineSoft },
  modeMarkText: { fontSize: Type.micro, color: Editorial.textMuted },

  // 모드 버튼 + 빠른 프롬프트
  toolRow: { flexDirection: 'row', alignItems: 'center', paddingLeft: 16, gap: 8 },
  modeBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    height: 34,
    maxWidth: 190,
    paddingHorizontal: 12,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: ink(0.14),
  },
  /* 켜진 모드는 화면에서 유일하게 '지금 이게 켜져 있다'를 말해야 하는 자리라 면을 채운다. */
  modeBtnOn: { backgroundColor: Editorial.selected, borderColor: Editorial.selected },
  modeBtnText: { flexShrink: 1, fontSize: Type.caption, color: Editorial.textCaption, fontWeight: '500' },
  modeBtnTextOn: { color: '#fff' },

  quickScroll: { flexGrow: 0, maxHeight: 52 },
  quickRow: { paddingRight: 16, gap: 8, paddingVertical: 8, alignItems: 'center' },
  quickChip: {
    height: 34,
    justifyContent: 'center',
    paddingHorizontal: 14,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: ink(0.14),
  },
  quickText: { fontSize: 13, lineHeight: 16, color: Editorial.textCaption, fontWeight: '500' },

  // 입력 바
  inputSafe: { backgroundColor: Editorial.surface, borderTopWidth: 1, borderTopColor: ink(0.08) },
  inputBar: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 8,
    paddingHorizontal: 12,
    paddingTop: 10,
    paddingBottom: 6,
  },
  photoBtn: {
    width: 42,
    height: 42,
    borderRadius: 21,
    alignItems: 'center',
    justifyContent: 'center',
  },
  input: {
    flex: 1,
    minHeight: 42,
    maxHeight: 120,
    backgroundColor: Editorial.surface,
    borderWidth: 1, borderColor: Editorial.line,
    borderRadius: 21,
    paddingHorizontal: 16,
    paddingTop: 11,
    paddingBottom: 11,
    fontSize: 14,
    color: Editorial.ink,
  },
  sendBtn: {
    width: 42,
    height: 42,
    borderRadius: 21,
    // 보낼 내용이 없을 땐 면이 아니라 테두리로만 존재한다. 채워지는 건 활성일 때뿐.
    backgroundColor: Editorial.surface,
    borderWidth: 1,
    borderColor: Editorial.line,
    alignItems: 'center',
    justifyContent: 'center',
  },
  sendBtnOn: { backgroundColor: Editorial.cta },
  /* 참고 말풍선 — 사용자 말풍선이지만 검은 면을 쓰지 않는다. 사진과 이름을 읽어야 하고,
     '내가 한 말'보다 '무엇을 참고했는지'가 먼저 눈에 들어와야 한다. */
  refBubble: {
    alignSelf: 'flex-end',
    maxWidth: '84%',
    gap: 8,
    padding: 10,
    borderRadius: 16,
    borderTopRightRadius: 6,
    borderWidth: 1,
    borderColor: Editorial.line,
    backgroundColor: Editorial.surface,
  },
  refHead: { flexDirection: 'row', alignItems: 'center', gap: 9 },
  refHeadText: { flex: 1, gap: 1 },
  refLabel: { fontSize: Type.micro, color: Editorial.textMuted, fontWeight: '600' },
  refName: { fontSize: Type.caption, color: INK, fontWeight: '500' },
  refRoom: { fontSize: Type.micro, color: Editorial.textCaption },
  refText: { fontSize: Type.footnote, color: INK, lineHeight: 21 },

  /* 입력창 위 미리보기 — 보내기 전 마지막 확인 자리라 조용하게 둔다. */
  refPreview: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 9,
    paddingHorizontal: 14,
    paddingTop: 10,
  },
  refPreviewText: { flex: 1, gap: 1 },
  refPreviewLabel: { fontSize: Type.micro, color: Editorial.textMuted, fontWeight: '600' },
  refPreviewName: { fontSize: Type.caption, color: INK },
  refPreviewAction: { fontSize: Type.caption, color: Editorial.textCaption, fontWeight: '500' },
});
