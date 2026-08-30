import { Icon } from '@/components/icon';
import { router, useLocalSearchParams } from 'expo-router';
import { backTo, goBack, withReturn, goTo } from '@/lib/goBack';
import { useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { EmptyState, SmartImage, useConfirm, useToast } from '@/components/ui';
import { Editorial, ink, Fonts , ContentMax} from '@/constants/theme';
import { TODAY_LOOK } from '@/constants/today-look';
import type { WardrobeSource } from '@/constants/wardrobe';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { useAuth } from '@/state/auth';
import { draftItem } from '@/state/draft-item';
import { formatDateLabel, type EntryItem } from '@/state/calendar';
import { ALLOWED_HASHTAGS, type AllowedHashtag } from '@/state/lookbook';
import { savedLookStore, useSavedLooks } from '@/state/saved';

const INK = Editorial.ink;
const BONE = Editorial.bone;

/* 구성 아이템은 룩 단일 출처를 그대로 쓴다 — 여기만 목업을 따로 두면 룩상세와 다른 옷이 나온다.
   내가 직접 기록한 룩은 실제로 담은 옷(look.items)이 있어 이 목업 대신 그걸 그린다. */
const PIECES = TODAY_LOOK.pieces;

const SOURCE_LABEL: Record<WardrobeSource, string> = {
  closet: '내 옷장',
  library: '앱 추천',
  shared: '친구 옷장',
};

/** '2026. 7. 6. 저장' — 목록에서 온 룩의 저장 시각. 시드(0·1·2)는 날짜가 아니라 순번이라 건너뛴다. */
function savedAtLabel(savedAt: number): string | null {
  if (savedAt < 1_000_000_000) return null;
  return `${new Date(savedAt).toLocaleDateString('ko-KR')} 저장`;
}

// E2 저장 룩 상세 — 구성·추천이유 재확인·메모/해시태그
export default function SavedLook() {
  const { contentStyle } = useBreakpoint();
  const toast = useToast();
  const confirm = useConfirm();
  const { isLoggedIn } = useAuth();

  /* 어떤 룩인지는 목록에서 id 로 받는다. id 없이 들어오면(아직 id 를 안 넘기는 경로가 있다)
     첫 저장 룩을 보여준다 — 고정 목업을 그리던 자리다. */
  const { id, from } = useLocalSearchParams<{ id?: string; from?: string }>();
  const looks = useSavedLooks();
  const look = (id ? looks.find((l) => l.id === id) : looks[0]) ?? null;
  /** 이 룩이 내 룩북의 어느 갈래에 서 있는지 — 추천에서 담은 룩(✨)은 위시, 나머지는 올린 룩. */
  const inWish = look?.origin === 'ai';
  /* 룩북에서도 캘린더에서도 들어온다 — 들어온 자리로 돌려보낸다.
     from 이 없으면 이 룩이 실제로 서 있는 갈래로 보낸다(없는 목록에 떨어뜨리지 않는다). */
  const back = () =>
    goBack(backTo(from, `/(tabs)/lookbook?tab=${inWish ? 'wish' : 'mine'}`));

  const [editing, setEditing] = useState(false);
  const [memo, setMemo] = useState('');
  const [tags, setTags] = useState<AllowedHashtag[]>([]);

  const startEdit = () => {
    if (!look) return;
    setMemo(look.memo ?? '');
    setTags(look.tags.filter((t): t is AllowedHashtag =>
      (ALLOWED_HASHTAGS as readonly string[]).includes(t),
    ));
    setEditing(true);
  };

  const save = async () => {
    if (!look) return;
    try {
      await savedLookStore.updateLook(look.id, { memo, tags });
    } catch (error) {
      toast(error instanceof Error ? error.message : '저장하지 못했어요', { variant: 'error' });
      return;
    }
    setEditing(false);
    toast('저장했어요', { variant: 'success' });
  };

  const toggleTag = (tag: AllowedHashtag) => {
    setTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]));
  };

  const remove = async () => {
    if (!look) return;
    const ok = await confirm({
      title: inWish ? '이 룩을 위시에서 뺄까요?' : '이 룩을 내 룩북에서 뺄까요?',
      message: '메모와 태그도 함께 사라져요.',
      confirmLabel: '삭제',
      destructive: true,
    });
    if (!ok) return;
    try {
      await savedLookStore.removeLook(look.id);
    } catch (error) {
      /* 처리 중인 룩은 서버가 409 로 막는다 — 왜 안 지워지는지 알려야 다시 누르지 않는다. */
      toast(error instanceof Error ? error.message : '빼지 못했어요', { variant: 'error' });
      return;
    }
    toast(inWish ? '위시에서 뺐어요' : '내 룩북에서 뺐어요');
    /* 뺀 룩의 상세는 돌아갈 자리가 아니다 — 이력과 무관하게 목록으로 보낸다. */
    goTo(backTo(from, `/(tabs)/lookbook?tab=${inWish ? 'wish' : 'mine'}`));
  };

  const subtitle = look
    ? [savedAtLabel(look.savedAt), look.tags.join(' · ')].filter(Boolean).join(' · ')
    : '';

  /* 내가 직접 기록한 룩이면 담은 옷이 실제로 있다 — 그때만 목업 구성 대신 그 옷을 그린다. */
  const isMine = look?.origin === 'closet';
  const myItems = look?.items?.length ? look.items : null;

  /**
   * 룩에 걸린 옷을 내 옷장에 들인다.
   *
   * 이 옷은 사진에서 이미 잘려 태깅까지 끝나 있다 — 등록 화면을 다시 거칠 이유가 없다.
   * 서버에 '옷장에 넣겠다'고 표시만 하면 그 순간 옷장 목록에 나타난다.
   */
  const [adding, setAdding] = useState<string | null>(null);
  const addItemToCloset = async (item: EntryItem) => {
    if (!isLoggedIn) {
      toast('옷장은 로그인하고 쓸 수 있어요');
      router.push('/login');
      return;
    }
    if (!look || adding) return;
    setAdding(item.id);
    try {
      await savedLookStore.addItemToCloset(look.id, item.id);
      toast(`${item.name}을(를) 옷장에 담았어요`, { variant: 'success' });
    } catch {
      toast('옷장에 담지 못했어요. 잠시 후 다시 시도해 주세요', { variant: 'error' });
    } finally {
      setAdding(null);
    }
  };

  /**
   * 추천 룩(✨)의 구성 아이템은 아직 옷장 아이템이 아니다 — 사진만 있다.
   * 그래서 이 갈래는 종전대로 등록 화면을 거친다.
   */
  const addPhotoToCloset = (photo: string) => {
    if (!isLoggedIn) {
      toast('옷장은 로그인하고 쓸 수 있어요');
      router.push('/login');
      return;
    }
    draftItem.setPhoto(photo);
    router.push('/item-add');
  };

  if (!look) {
    return (
      <View style={styles.container}>
        <SafeAreaView edges={['top']} style={styles.headerSafe}>
          <View style={[styles.header, contentStyle(ContentMax.card)]}>
            <Pressable hitSlop={12} onPress={back}>
              <Icon name="chevron.left" tintColor={INK} size={20} />
            </Pressable>
          </View>
        </SafeAreaView>
        <EmptyState
          icon="book"
          title="마음에 드는 룩을 저장해 보세요"
          description="추천 룩에서 저장하면 여기에 모여요."
          actionLabel="추천 룩 보러 가기"
          onAction={() => router.push('/look-detail')}
        />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top']} style={styles.headerSafe}>
        <View style={[styles.header, contentStyle(ContentMax.card)]}>
          <Pressable hitSlop={12} onPress={back}>
            <Icon name="chevron.left" tintColor={INK} size={20} />
          </Pressable>
          <View style={styles.headerActions}>
            <Pressable hitSlop={10} onPress={editing ? save : startEdit}>
              {editing ? (
                <Text style={styles.doneText}>완료</Text>
              ) : (
                <Icon name="square.and.pencil" tintColor={ink(0.6)} size={19} />
              )}
            </Pressable>
            <Pressable hitSlop={10} onPress={remove} accessibilityLabel="이 룩 삭제">
              <Icon name="trash" tintColor={ink(0.6)} size={18} />
            </Pressable>
          </View>
        </View>
      </SafeAreaView>

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={[styles.content, contentStyle(ContentMax.card)]}>
        {/* 룩 이미지 */}
        <View style={styles.image}>
          {/* 바깥 View 가 이미 비율(1.176)을 잡고 있어, 사진은 그 안을 채우기만 하면 된다. */}
          <SmartImage
            uri={look.image}
            asset={look.image ? undefined : look.asset}
            width="100%"
            radius={20}
            contentFit="cover"
            style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }}
          />
          <View style={styles.savedBadge}>
            <Icon name={isMine ? 'tshirt' : 'sparkles'} tintColor="#fff" size={11} />
            <Text style={styles.savedText}>{isMine ? '내가 기록한 룩' : '앱이 추천한 룩'}</Text>
          </View>
        </View>

        <View style={styles.body}>
          <Text style={styles.title}>{look.comment ?? (isMine ? '내가 기록한 룩' : '저장한 룩')}</Text>
          <Text style={styles.subtitle}>{subtitle}</Text>

          {/* 이어진 착장 기록 — 룩북과 캘린더 중 한쪽에서 넘어갈 수 있게 한다 */}
          {look.entryDate ? (
            <Pressable
              style={styles.entryLink}
              onPress={() => router.push(`/calendar-entry?date=${look.entryDate}`)}>
              <Icon name="calendar" tintColor={INK} size={15} />
              <Text style={styles.entryLinkText}>
                {formatDateLabel(look.entryDate)} 착장으로 기록됨
              </Text>
              <Icon name="chevron.right" tintColor={ink(0.3)} size={14} />
            </Pressable>
          ) : null}

          {/* 구성 (칩 나열) */}
          <Text style={styles.sectionTitle}>{isMine ? '입은 옷' : '구성 아이템'}</Text>
          <View style={styles.pieces}>
            {myItems
              ? myItems.map((item) => (
                  <View key={`${item.source}:${item.id}`} style={styles.piece}>
                    <View style={styles.pieceThumb}>
                      <SmartImage uri={item.image} width="100%" aspectRatio={1} radius={10} contentFit="cover" />
                    </View>
                    <View style={styles.pieceBody}>
                      <Text style={styles.pieceSlot}>
                        {item.owner ? `${item.owner} 옷` : SOURCE_LABEL[item.source]}
                      </Text>
                      <Text style={styles.pieceName} numberOfLines={1}>
                        {item.name}
                      </Text>
                    </View>
                    {/* 사진에서 뽑힌 옷은 사용자가 눌러야 옷장에 든다. 이미 든 옷은 그렇다고만 알린다. */}
                    {item.inCloset === false ? (
                      <Pressable
                        style={styles.addBtn}
                        onPress={() => addItemToCloset(item)}
                        disabled={adding === item.id}
                        accessibilityLabel={`${item.name} 옷장에 추가`}>
                        {adding === item.id ? (
                          <ActivityIndicator size="small" color={INK} />
                        ) : (
                          <>
                            <Icon name="plus" tintColor={INK} size={13} />
                            <Text style={styles.addBtnText}>옷장에 추가</Text>
                          </>
                        )}
                      </Pressable>
                    ) : (
                      <View style={styles.inClosetTag}>
                        <Icon name="checkmark" tintColor={ink(0.4)} size={12} />
                        <Text style={styles.inClosetText}>옷장에 있음</Text>
                      </View>
                    )}
                  </View>
                ))
              : isMine ? (
                /* 사진·일정만 남긴 내 기록 — 목업 구성을 끼워 넣으면 입지도 않은 옷을 입었다고 하는 셈이다 */
                <Text style={styles.piecesEmpty}>담아 둔 옷이 없어요</Text>
              ) : PIECES.map((p) => (
                  <View key={p.slot} style={styles.piece}>
                    <View style={styles.pieceThumb}>
                      <SmartImage uri={p.image} width="100%" aspectRatio={1} radius={10} contentFit="cover" />
                    </View>
                    <View style={styles.pieceBody}>
                      <Text style={styles.pieceSlot}>{p.slot}</Text>
                      <Text style={styles.pieceName} numberOfLines={1}>
                        {p.name}
                      </Text>
                    </View>
                    {/* 사진이 없는 아이템은 등록할 것이 없다 — 옷장 등록은 사진 한 장에서 시작한다. */}
                    {p.image ? (
                      <Pressable
                        style={styles.addBtn}
                        onPress={() => addPhotoToCloset(p.image!)}
                        accessibilityLabel={`${p.name} 옷장에 추가`}>
                        <Icon name="plus" tintColor={INK} size={13} />
                        <Text style={styles.addBtnText}>옷장에 추가</Text>
                      </Pressable>
                    ) : null}
                  </View>
                ))}
          </View>

          {/* 추천 이유 — 저장할 때 받아둔 것이 있을 때만. 없는 룩에 남의 이유를 붙이지 않는다. */}
          {look.reason ? (
            <>
              <Text style={styles.sectionTitle}>추천받은 이유</Text>
              <View style={styles.reasonCard}>
                <Text style={styles.reasonText}>{look.reason}</Text>
              </View>
            </>
          ) : null}

          {/* 메모 */}
          <Text style={styles.sectionTitle}>메모</Text>
          {editing ? (
            <TextInput
              style={styles.memoInput}
              value={memo}
              onChangeText={setMemo}
              placeholder="이 룩에 대해 남겨둘 것이 있나요?"
              placeholderTextColor={Editorial.textMuted}
              multiline
              maxLength={200}
            />
          ) : (
            <Pressable style={styles.memoCard} onPress={startEdit}>
              <Text style={[styles.memoText, !look.memo && styles.memoEmpty]}>
                {look.memo ?? '메모를 남겨보세요'}
              </Text>
              <View style={styles.memoEdit}>
                <Icon name="pencil" tintColor={ink(0.4)} size={13} />
              </View>
            </Pressable>
          )}

          {/* 해시태그 — 수정 중에는 전체 목록에서 고르고, 평소엔 고른 것만 보여준다 */}
          <View style={styles.tags}>
            {editing
              ? ALLOWED_HASHTAGS.map((t) => {
                  const on = tags.includes(t);
                  return (
                    <Pressable
                      key={t}
                      onPress={() => toggleTag(t)}
                      style={[styles.tag, on && styles.tagOn]}>
                      <Text style={[styles.tagText, on && styles.tagTextOn]}>#{t}</Text>
                    </Pressable>
                  );
                })
              : look.tags.map((t) => (
                  <View key={t} style={styles.tag}>
                    <Text style={styles.tagText}>#{t}</Text>
                  </View>
                ))}
          </View>
        </View>
      </ScrollView>

      <View style={styles.bottomDivider} />
      <View style={[styles.bottomBar, { paddingBottom: 12 }, contentStyle(ContentMax.card)]}>
        {/* 채팅에서 뒤로 나오면 보던 룩으로 돌아오게 자리를 알려 준다 (룩북·캘린더와 같은 방식). */}
        <Pressable
          style={styles.cta}
          onPress={() =>
            router.push(
              withReturn('/chat-room', look ? `/saved-look?id=${look.id}` : '/saved-look'),
            )
          }>
          <Icon name="sparkles" tintColor="#fff" size={15} />
          <Text style={styles.ctaText}>비슷하게 추천받기</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  headerSafe: { backgroundColor: Editorial.page },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 8,
  },
  headerActions: { flexDirection: 'row', gap: 18 },

  content: { paddingBottom: 24 },
  image: {
    /* 고정 높이로 두면 폭이 넓어지는 데스크톱에서 가로로 납작해져 세로 사진이 잘린다.
       폰 폭(400) 기준 비율을 유지한다. */
    aspectRatio: 1.176,
    backgroundColor: BONE,
    marginHorizontal: 20,
    borderRadius: 20,
    overflow: 'hidden',
  },
  savedBadge: {
    position: 'absolute',
    top: 14,
    left: 14,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    backgroundColor: INK,
    paddingHorizontal: 11,
    paddingVertical: 6,
    borderRadius: 999,
  },
  savedText: { fontSize: 10.5, color: '#fff', fontWeight: '500' },

  body: { paddingHorizontal: 20, paddingTop: 22 },
  title: { fontFamily: Fonts.serif, fontSize: 24, color: INK },
  subtitle: { fontSize: 13, color: Editorial.textCaption, marginTop: 6 },

  sectionTitle: { fontSize: 13, fontWeight: '600', color: INK, marginTop: 26, marginBottom: 12 },

  entryLink: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginTop: 16,
    paddingHorizontal: 14,
    height: 46,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  entryLinkText: { flex: 1, fontSize: 13, fontWeight: '600', color: INK },

  /* 아이템마다 '옷장에' 버튼이 붙어 2단으로 두면 이름이 잘린다 → 한 줄에 하나씩. */
  pieces: { gap: 10 },
  piece: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    borderWidth: 1,
    borderColor: ink(0.09),
    borderRadius: 14,
    padding: 10,
  },
  piecesEmpty: { fontSize: 13, color: Editorial.textCaption },
  pieceThumb: { width: 44, height: 44, borderRadius: 10, backgroundColor: BONE, overflow: 'hidden' },
  pieceBody: { flex: 1, gap: 3 },
  pieceSlot: { fontSize: 10.5, color: Editorial.textCaption },
  pieceName: { fontSize: 13.5, fontWeight: '500', color: Editorial.ink },
  addBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
    height: 30,
    paddingHorizontal: 10,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: ink(0.14),
  },
  addBtnText: { fontSize: 11.5, fontWeight: '600', color: INK },
  /* 이미 옷장에 있는 옷 — 누를 것이 없으니 버튼이 아니라 표시로 둔다 */
  inClosetTag: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 6 },
  inClosetText: { fontSize: 11.5, color: Editorial.textMuted },

  reasonCard: { backgroundColor: Editorial.surfaceSoft, borderWidth: 1, borderColor: Editorial.line, borderRadius: 16, padding: 16 },
  reasonText: { fontSize: 13.5, color: Editorial.textSoft, lineHeight: 21 },

  memoCard: {
    borderWidth: 1,
    borderColor: ink(0.1),
    borderRadius: 14,
    padding: 15,
    paddingRight: 40,
  },
  memoText: { fontSize: 13.5, color: Editorial.textSoft, lineHeight: 20 },
  memoEmpty: { color: Editorial.textMuted },
  memoEdit: { position: 'absolute', top: 12, right: 12 },
  memoInput: {
    borderWidth: 1,
    borderColor: ink(0.16),
    borderRadius: 14,
    padding: 15,
    minHeight: 90,
    fontSize: 13.5,
    color: Editorial.textSoft,
    lineHeight: 20,
    textAlignVertical: 'top',
  },

  tags: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 18 },
  tag: {
    backgroundColor: Editorial.control,
    borderRadius: 999,
    paddingHorizontal: 13,
    paddingVertical: 7,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  tagOn: { backgroundColor: Editorial.selected, borderColor: Editorial.selected },
  tagText: { fontSize: 12.5, color: Editorial.textCaption, fontWeight: '500' },
  tagTextOn: { color: '#fff' },

  doneText: { fontSize: 14, fontWeight: '600', color: INK },

  bottomDivider: { height: 1, backgroundColor: ink(0.08) },
  bottomBar: { backgroundColor: Editorial.page, paddingHorizontal: 20, paddingTop: 12 },
  cta: {
    flexDirection: 'row',
    gap: 8,
    height: 52,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  ctaText: { fontSize: 14.5, color: '#fff', fontWeight: '500' },
});
