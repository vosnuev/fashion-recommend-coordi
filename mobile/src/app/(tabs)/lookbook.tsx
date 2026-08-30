import {
  ErrorState,
  LoadingState,
  LookbookFilterSheet,
  SearchFilterBar,
  SegmentedToggle,
  SmartImage,
} from '@/components/ui';
import { Icon, type IconName } from '@/components/icon';
import { useMultiSelectFilter } from '@/hooks/useMultiSelectFilter';
import { useRefresh } from '@/hooks/use-refresh';
import {
  LOOKBOOK_FILTER_OPTIONS,
  lookbookStore,
  useLookbook,
  useLookbookLoadState,
} from '@/state/lookbook';
import { useLookVotes } from '@/state/look-votes';
import type { LookGenderFilter } from '@/lib/discoveryLookApi';
import { likesStore, useLikedLooks } from '@/state/likes';
import { savedLookStore, useSavedLooks, useSavedLooksState, type LookOrigin } from '@/state/saved';
import { router, useLocalSearchParams } from 'expo-router';
import { withReturn } from '@/lib/goBack';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  type NativeScrollEvent,
  type NativeSyntheticEvent,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Editorial, ink, GridCard, gridCardImageHeight, gridCardWidth , ContentMax} from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';

const INK = Editorial.ink;

/* 카드 크기는 창 폭에서 파생 → 컴포넌트 안에서 useBreakpoint() 로 구한다. */
const PAD = GridCard.pad;

/**
 * 상단 세그먼트: 둘러보기 / 내 룩북.
 *
 * - 둘러보기 = **모두가 보는 룩**. 앱이 기본으로 주는 룩과, 사용자가 전체공개한 룩이 모인다.
 *   여기 칩은 전부 해시태그다 — 남의 룩을 태그로 좁혀 보는 자리.
 * - 내 룩북 = **내 것으로 삼은 룩**. 내가 올린 룩과 담아둔 룩(위시)이 여기 함께 있다.
 *   둘 다 '내 목록'이라 한 세그먼트에 두고, 안에서 칩으로 가른다.
 */
type Mode = 'browse' | 'mine';
const MODE_OPTIONS: { value: Mode; label: string }[] = [
  { value: 'browse', label: '둘러보기' },
  { value: 'mine', label: '내 룩북' },
];

/**
 * 내 룩북 안의 두 갈래 — 해시태그가 아니라 '어디서 온 룩인지'라는 갈래다.
 * 둘러보기 칩과 달리 **한 번에 하나만** 켜진다(같은 룩이 양쪽에 서지 않으므로 겹쳐 볼 이유가 없다).
 */
type MineTab = 'uploaded' | 'wish';
/**
 * 그리드를 한 번에 다 그리지 않고 이만큼씩 늘려 간다.
 *
 * 커버 한 장이 1080x1350 PNG 로 평균 2MB 라, 138건을 한 화면에 다 걸면 100MB 를
 * 내려받기 시작한다. 폰에서는 수십 초 동안 빈 칸으로 남아 '사진이 안 뜬다'로 보인다.
 * 목록 JSON 자체는 0.3MB 로 가벼우니 **받는 건 그대로 두고 그리는 것만** 창으로 자른다
 * (필터·검색은 전체 목록 위에서 걸리므로 결과가 달라지지 않는다).
 *
 * 근본 해결은 서버가 목록용 썸네일을 주는 것이다. 그때 이 창을 넓히거나 걷어내면 된다.
 */
const GRID_PAGE = 8;
/** 바닥에서 이만큼 남았을 때 다음 묶음을 그린다 — 스크롤이 멈추기 전에 채워지도록. */
const GRID_PREFETCH_PX = 600;

const UPLOADED = '올린 룩';
const WISH = '위시';
const MINE_CHIPS = [UPLOADED, WISH];
/* 위시 칩에만 하트를 단다 — 카드 위 하트와 같은 표식이라
   "하트 누른 것들"이라는 뜻이 글자 없이도 읽힌다. */
const CHIP_ICONS = { [WISH]: 'heart.fill' as const };

/** 그리드 카드 공통 형태 — 피드 룩(price 有)·저장 룩(asset 有) 모두 이 형태로 정규화 */
type CardData = {
  id: string;
  uri?: string;
  asset?: number;
  price?: string;
  /** 하트를 달 수 있는 룩(=피드 룩)에만 있다. 태그 검색의 대상이기도 하다. */
  tags?: string[];
  /** 피드 룩이 가리키는 룩 상세 */
  variantId?: string;
  /** 어느 상세로 보낼지 — 피드 룩은 추천 상세, 담아둔 룩은 저장 룩 상세 */
  kind: 'feed' | 'saved';
  /** 위시 목록에서만 쓰는 출처 표시. 추천에서 저장한 룩(✨)을 하트로 담은 룩과 가른다. */
  origin?: LookOrigin;
};

/** 출처 배지 — 아이콘만 둔다. 라벨을 붙이면 사진 위 면적을 그만큼 더 가린다. */
const ORIGIN_BADGE: Record<LookOrigin, { icon: IconName; label: string }> = {
  ai: { icon: 'sparkles', label: '앱이 추천한 룩' },
  closet: { icon: 'tshirt', label: '내가 기록한 룩' },
  daily: { icon: 'sparkles', label: '오늘의 룩에서 담은 룩' },
};

/** 취향 추천 가로 카드 크기 — 그리드보다 작게 잡아 본 목록을 밀어내지 않는다. */

function matchesQuery(look: { tags: string[] }, query: string): boolean {
  const q = query.trim().toLocaleLowerCase();
  if (!q) return true;
  return look.tags.some((tag) => tag.toLocaleLowerCase().includes(q));
}

function matchesTags(look: { tags: string[] }, selected: string[]): boolean {
  if (selected.length === 0) return true;
  return look.tags.some((tag) => selected.includes(tag));
}

export default function LookbookScreen() {
  const { frameWidth, contentStyle } = useBreakpoint();
  const cardW = gridCardWidth(frameWidth);
  const cardH = gridCardImageHeight(cardW);

  const allLooks = useLookbook();
  const {
    loading: lookbookLoading,
    error: lookbookError,
    progress: lookbookProgress,
  } = useLookbookLoadState();
  const [displayedLookbookProgress, setDisplayedLookbookProgress] = useState(0);
  const [lookbookProgressCycleActive, setLookbookProgressCycleActive] = useState(false);
  const savedLooks = useSavedLooks();
  const likedLooks = useLikedLooks();
  const [query, setQuery] = useState('');
  const [gender, setGender] = useState<LookGenderFilter>('ALL');
  const [filterOpen, setFilterOpen] = useState(false);
  const { toggle, isActive, selected, label } = useMultiSelectFilter();
  const selectedKey = selected.join('|');

  /* 둘 다 서버에서 온다 — 내 룩북은 내 목록, 둘러보기는 공개 피드.
     세그먼트를 오갈 때마다 기다리게 하지 않으려고 화면에 들어올 때 함께 받는다. */
  const { loading, error, loaded } = useSavedLooksState();
  const loadAll = useCallback(
    () => Promise.all([
      savedLookStore.load(),
      lookbookStore.load(gender, selectedKey ? selectedKey.split('|') : []),
    ]).then(() => undefined),
    [gender, selectedKey],
  );
  const { refreshing, onRefresh } = useRefresh(loadAll);
  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  useEffect(() => {
    if (!lookbookLoading && lookbookProgress !== 100) {
      return;
    }

    if (lookbookLoading && lookbookProgress <= 8) {
      setDisplayedLookbookProgress(0);
      setLookbookProgressCycleActive(true);
    }

    const targetProgress = lookbookLoading ? lookbookProgress : 100;

    const timer = setInterval(() => {
      setDisplayedLookbookProgress((current) => {
        if (current >= targetProgress) return current;
        const remaining = targetProgress - current;
        return Math.min(targetProgress, current + Math.max(1, Math.ceil(remaining / 10)));
      });
    }, 45);

    return () => clearInterval(timer);
  }, [lookbookLoading, lookbookProgress]);

  useEffect(() => {
    if (lookbookLoading || !lookbookProgressCycleActive || displayedLookbookProgress < 100) {
      return;
    }

    const timer = setTimeout(() => setLookbookProgressCycleActive(false), 450);
    return () => clearTimeout(timer);
  }, [displayedLookbookProgress, lookbookLoading, lookbookProgressCycleActive]);

  /* 모드도 내 룩북 안 갈래도 URL 파라미터에서 파생한다(useState+useEffect 동기화는
     불필요한 리렌더를 만들어 지양). 갈래까지 URL 에 두는 이유는 상세에서 뒤로 왔을 때
     보던 자리로 정확히 돌아오게 하기 위해서다.
     ?tab=saved 는 예전 링크 — 'mine' 으로 함께 받는다. */
  const { tab } = useLocalSearchParams<{ tab?: string }>();
  const mode: Mode = tab === 'mine' || tab === 'saved' || tab === 'wish' ? 'mine' : 'browse';
  const mineTab: MineTab = tab === 'wish' ? 'wish' : 'uploaded';
  const setMode = (m: Mode) => router.setParams({ tab: m === 'mine' ? 'mine' : 'browse' });
  const setMineTab = (t: MineTab) => router.setParams({ tab: t === 'wish' ? 'wish' : 'mine' });

  /* 칩 줄은 모드마다 성격이 다르다 — 둘러보기는 해시태그(여러 개 켜짐),
     내 룩북은 갈래(하나만 켜짐). 같은 자리를 쓰되 배선만 갈아 끼운다.
     둘러보기 해시태그는 서버가 정한 고정 목록이다 — 사용자가 편집하지 않는다. */
  const isMine = mode === 'mine';
  /* string[] 로 못 박는다 — LOOKBOOK_FILTER_OPTIONS 는 as const 라 리터럴 유니온인데,
     칩 콜백(chipActive/chipToggle)은 평범한 string 을 받는다. 예전 tags state 가
     string[] 이었으므로 그 자리를 그대로 잇는다. */
  const chipOptions: string[] = isMine ? MINE_CHIPS : LOOKBOOK_FILTER_OPTIONS;
  const chipActive = (c: string) => (isMine ? c === (mineTab === 'wish' ? WISH : UPLOADED) : isActive(c));
  const chipToggle = (c: string) =>
    isMine ? setMineTab(c === WISH ? 'wish' : 'uploaded') : toggle(c);

  /** 담아둔 룩 = 하트 누른 피드 룩 + 추천에서 저장해 둔 룩(✨). 최근에 담은 것이 앞에 온다. */
  const wishCards: CardData[] = useMemo(() => {
    const liked: CardData[] = likedLooks.map((l) => ({
      id: l.id,
      uri: l.image,
      tags: l.tags,
      /* 상세로 보내려면 variantId 가 필요하다 → 피드에서 다시 찾고, 내려간 룩이면 기본 룩으로. */
      variantId: allLooks.find((f) => f.id === l.id)?.variantId,
      kind: 'feed' as const,
    }));
    const savedAi: CardData[] = savedLooks
      .filter((l) => l.origin === 'ai')
      .map((l) => ({
        id: l.id,
        uri: l.image,
        asset: l.asset,
        tags: l.tags,
        origin: l.origin,
        kind: 'saved' as const,
      }));
    /* 태그 칩은 둘러보기 쪽에만 있다 — 여기서 좁히는 건 검색어뿐이다. */
    return [...liked, ...savedAi].filter((c) => matchesQuery({ tags: c.tags ?? [] }, query));
  }, [likedLooks, savedLooks, allLooks, query]);

  /** 내 룩북 '올린 룩' = 내가 올린 룩만. 추천에서 저장한 룩(✨)은 옆 갈래인 위시에 선다. */
  const uploadedCards: CardData[] = useMemo(
    () =>
      savedLooks
        .filter((l) => l.origin !== 'ai' && matchesQuery(l, query))
        .map((l) => ({ id: l.id, uri: l.image, asset: l.asset, kind: 'saved' as const })),
    [savedLooks, query],
  );

  const selectGender = (next: LookGenderFilter) => {
    if (next === gender) return;
    setGender(next);
  };

  const feedCards: CardData[] = useMemo(
    () =>
      allLooks
        .filter(
          (l) =>
            (gender === 'ALL' || l.gender === gender) &&
            matchesTags(l, selected) &&
            matchesQuery(l, query),
        )
        .map((l) => ({
          id: l.id,
          uri: l.image,
          price: l.price,
          tags: l.tags,
          variantId: l.variantId,
          kind: 'feed' as const,
        })),
    [allLooks, gender, selected, query],
  );

  /* '별로예요' 한 룩은 목록 뒤로 민다 — **지우지는 않는다.** 마음이 바뀌거나 비슷한 룩을
     다시 찾아볼 수 있는데, 목록에서 사라지면 되돌릴 길이 없다.
     좋아요는 순서를 건드리지 않는다: 위로 끌어올리면 이미 본 룩으로 앞이 채워져
     '둘러보기'가 아니라 '다시 보기'가 된다. */
  const votes = useLookVotes();
  const cards: CardData[] = useMemo(() => {
    const base = !isMine ? feedCards : mineTab === 'wish' ? wishCards : uploadedCards;
    const disliked = (card: CardData) => votes[card.variantId ?? card.id] === 'down';
    // 하나도 없으면 원래 배열을 그대로 돌려준다(새 배열을 만들면 아래 memo 들이 헛돈다).
    if (!base.some(disliked)) return base;
    return [...base.filter((card) => !disliked(card)), ...base.filter(disliked)];
  }, [isMine, mineTab, feedCards, wishCards, uploadedCards, votes]);

  /* 지금 그리는 카드 수. 갈래·필터·검색이 바뀌면 목록이 통째로 달라지므로 처음으로 되돌린다
     (안 그러면 새 목록을 이전 스크롤만큼 펼친 채 시작해 이미지를 또 왕창 받는다). */
  const [shown, setShown] = useState(GRID_PAGE);
  /* 스크롤 영역의 높이. 아래 '화면을 다 못 채웠으면 더 그린다' 판정에 쓴다. */
  const [gridH, setGridH] = useState(0);
  useEffect(() => {
    setShown(GRID_PAGE);
  }, [mode, mineTab, gender, query, selectedKey]);

  const visibleCards = useMemo(() => cards.slice(0, shown), [cards, shown]);
  const hasMore = shown < cards.length;

  /* 바닥 근처에 닿으면 다음 묶음을 그린다. onEndReached 가 없는 ScrollView 라 직접 잰다. */
  /* 넓은 화면에서는 한 줄에 여러 장이 들어가 GRID_PAGE 장으로 화면이 안 찬다.
     그러면 스크롤이 생기지 않아 다음 묶음을 부를 계기도 없어진다 — 스피너만 남는다.
     내용이 화면보다 짧으면 찰 때까지 늘린다(hasMore 가 꺼지면 자연히 멈춘다). */
  const onGridContentSize = useCallback(
    (_w: number, h: number) => {
      /* 여기서는 프리페치 여유(GRID_PREFETCH_PX)를 더하지 않는다 — 그걸 더하면 폰에서도
         첫 화면이 한 묶음 더 늘어 받는 양이 두 배가 된다. 순수하게 '화면이 안 찼는가'만 본다. */
      if (hasMore && gridH > 0 && h <= gridH) setShown((n) => n + GRID_PAGE);
    },
    [hasMore, gridH],
  );

  const onGridScroll = useCallback(
    (e: NativeSyntheticEvent<NativeScrollEvent>) => {
      const { contentOffset, layoutMeasurement, contentSize } = e.nativeEvent;
      const remaining = contentSize.height - (contentOffset.y + layoutMeasurement.height);
      if (remaining <= GRID_PREFETCH_PX) setShown((n) => n + GRID_PAGE);
    },
    [],
  );

  /** 서버에서 오는 목록을 보고 있는가 — 로딩·에러·당겨서 새로고침은 그때만 뜬다. */
  const usesServer = isMine;

  /** 상세에서 뒤로 왔을 때 보던 갈래 그대로 돌아오게 한다. */
  const returnHere = isMine
    ? `/(tabs)/lookbook?tab=${mineTab === 'wish' ? 'wish' : 'mine'}`
    : '/(tabs)/lookbook';

  const likedIds = useMemo(() => new Set(likedLooks.map((l) => l.id)), [likedLooks]);
  /* 토스트를 띄우지 않는다 — 하트가 그 자리에서 바로 채워지고 비워져 결과가 이미 보인다. */
  const toggleLike = (look: { id: string; image?: string; tags?: string[] }) =>
    likesStore.toggleLook(look);

  const emptyText = useMemo(() => {
    if (query.trim()) return `'${query.trim()}' 검색 결과가 없어요`;
    if (isMine) return mineTab === 'wish' ? '마음에 드는 룩을 담아 보세요' : '첫 룩을 올려 보세요';
    if (selected.length) return `'${label}' 태그 룩이 없어요`;
    return '아직 올라온 룩이 없어요';
  }, [isMine, mineTab, selected, query, label]);

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top']} style={styles.safe}>
        <View style={styles.filterArea}>
          {/* 태그 편집은 둘러보기에만 — 내 룩북 칩(올린 룩/위시)은 사용자가 지울 수 없는 고정 갈래다. */}
          <SearchFilterBar
            query={query}
            onQueryChange={setQuery}
            searchPlaceholder="해시태그 검색"
            options={chipOptions}
            chipIcons={CHIP_ICONS}
            onToggle={chipToggle}
            isActive={chipActive}
            onEditCategories={isMine ? undefined : () => setFilterOpen(true)}
            trailing={
              <SegmentedToggle value={mode} options={MODE_OPTIONS} onChange={setMode} />
            }
          />
        </View>

        <ScrollView
          style={styles.gridScroll}
          showsVerticalScrollIndicator={false}
          onScroll={onGridScroll}
          /* 16 이면 매 프레임 — 스크롤 중 한 번은 재야 바닥 전에 채워진다. */
          scrollEventThrottle={16}
          onLayout={(e) => setGridH(e.nativeEvent.layout.height)}
          onContentSizeChange={onGridContentSize}
          refreshControl={
            usesServer ? (
              <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={INK} />
            ) : undefined
          }
          contentContainerStyle={[styles.grid, { paddingBottom: 24 }, contentStyle(ContentMax.wide)]}>
          {!isMine &&
          (lookbookLoading ||
            lookbookProgressCycleActive) ? (
            <View
              style={styles.empty}
              accessibilityRole="progressbar"
              accessibilityValue={{ min: 0, max: 100, now: displayedLookbookProgress }}>
              <Text style={styles.loadingPercent}>{displayedLookbookProgress}%</Text>
              <Text style={styles.loadingMessage}>룩북을 불러오는 중입니다.</Text>
            </View>
          ) : !isMine && lookbookError && cards.length === 0 ? (
            <ErrorState
              title="룩북을 불러오지 못했어요"
              description={lookbookError}
              onRetry={() => void loadAll()}
              style={styles.empty}
            />
          ) : usesServer && loading && !loaded ? (
            <LoadingState message="룩북을 불러오는 중…" style={styles.empty} />
          ) : usesServer && error && savedLooks.length === 0 ? (
            <ErrorState
              title="룩북을 불러오지 못했어요"
              description={error}
              onRetry={() => void loadAll()}
              style={styles.empty}
            />
          ) : cards.length === 0 ? (
            <View style={styles.empty}>
              <Text style={styles.emptyText}>{emptyText}</Text>
              {isMine && mineTab === 'wish' ? (
                /* 담을 것이 없으면 담으러 갈 곳으로 — 담을 룩은 둘러보기에 있다. */
                <Pressable style={styles.emptyBtn} onPress={() => setMode('browse')}>
                  <Text style={styles.emptyBtnText}>둘러보며 마음에 드는 룩 찾기</Text>
                </Pressable>
              ) : isMine ? (
                <Pressable style={styles.emptyBtn} onPress={() => router.push('/look-add')}>
                  <Text style={styles.emptyBtnText}>첫 룩 올리기</Text>
                </Pressable>
              ) : (
                <Pressable style={styles.emptyBtn} onPress={() => router.push('/look-add')}>
                  <Text style={styles.emptyBtnText}>내 룩 올리기</Text>
                </Pressable>
              )}
            </View>
          ) : (
            <>
              {visibleCards.map((c) => (
              <Pressable
                key={c.id}
                style={[styles.card, { width: cardW }]}
                /* 담아둔 룩은 저장 상세로, 피드 룩은 그 룩의 추천 상세로 보낸다.
                   돌아올 자리는 지금 보고 있던 갈래 그대로여야 한다. */
                onPress={() =>
                  router.push(
                    c.kind === 'saved'
                      ? withReturn(`/saved-look?id=${c.id}`, returnHere)
                      : withReturn(`/look-detail?id=${c.variantId ?? 'daily'}`, returnHere),
                  )
                }>
                <View style={[styles.cardImage, { height: cardH }]}>
                  <SmartImage
                    uri={c.uri}
                    asset={c.uri ? undefined : c.asset}
                    width="100%"
                    height={cardH}
                    radius={GridCard.radius}
                    contentFit="cover"
                  />
                  {c.price ? (
                    <View style={styles.priceBadge}>
                      <Text style={styles.priceText}>{c.price}</Text>
                    </View>
                  ) : null}
                  {/* 출처 — 하트가 오른쪽 위를 쓰고 있어 왼쪽 위에 둔다 */}
                  {c.origin ? (
                    <View
                      style={styles.originBadge}
                      accessibilityLabel={ORIGIN_BADGE[c.origin].label}>
                      <Icon name={ORIGIN_BADGE[c.origin].icon} tintColor={INK} size={15} />
                    </View>
                  ) : null}
                  {/* 하트는 피드 룩에만 단다. 저장 룩(✨)에 달면 저장 룩 id 로 좋아요가 따로 생겨
                      같은 룩이 위시 목록에 두 번 서게 된다 — 빼는 길은 저장 룩 상세에 있다. */}
                  {c.kind === 'feed' && c.tags ? (
                    <Pressable
                      style={styles.likeBtn}
                      hitSlop={8}
                      accessibilityLabel={likedIds.has(c.id) ? '좋아요 취소' : '좋아요'}
                      onPress={() => toggleLike({ id: c.id, image: c.uri, tags: c.tags })}>
                      <Icon
                        name={likedIds.has(c.id) ? 'heart.fill' : 'heart'}
                        tintColor={likedIds.has(c.id) ? Editorial.wine : INK}
                        size={17}
                      />
                    </Pressable>
                  ) : null}
                </View>
              </Pressable>
              ))}
              {/* 아래로 더 있다는 신호. 빈 칸으로 두면 '여기가 끝'으로 읽힌다. */}
              {hasMore ? (
                <View style={styles.gridMore}>
                  <ActivityIndicator size="small" color={ink(0.4)} />
                </View>
              ) : null}
            </>
          )}
        </ScrollView>

        <LookbookFilterSheet
          visible={filterOpen}
          gender={gender}
          onClose={() => setFilterOpen(false)}
          onApply={selectGender}
        />

        {/* 올리기는 어느 갈래에서든 같은 자리에 있다 — 결과는 늘 내 룩북에 쌓인다. */}
        <Pressable
          style={[styles.addFab, { bottom: 12 }]}
          onPress={() => router.push('/look-add')}
          accessibilityLabel="룩 올리기">
          <Icon name="plus" tintColor={INK} size={22} />
        </Pressable>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  safe: { flex: 1 },

  filterArea: { marginTop: 30 },

  gridScroll: { flex: 1, marginTop: 8 },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    /* space-between 으로 두면 마지막 줄의 카드가 양 끝으로 밀려 가운데가 빈다.
       왼쪽부터 차례로 채우고 간격은 columnGap 으로 준다. */
    justifyContent: 'flex-start',
    columnGap: GridCard.gap,
    paddingHorizontal: PAD,
  },
  // width/height 는 창 폭에서 파생되므로 컴포넌트에서 인라인으로 덧붙인다.
  card: { marginBottom: 12 },
  /* 그리드가 flexWrap 이라 가로 전체를 차지해야 자기 줄에 선다. */
  gridMore: { width: '100%', alignItems: 'center', paddingVertical: 18 },
  cardImage: {
    width: '100%',
    borderRadius: GridCard.radius,
    overflow: 'hidden',
    justifyContent: 'flex-end',
  },
  priceBadge: {
    position: 'absolute',
    left: 12,
    bottom: 12,
    backgroundColor: 'rgba(255,255,255,0.95)',
    paddingHorizontal: 11,
    paddingVertical: 5,
    borderRadius: 999,
  },
  priceText: { fontSize: 12, fontWeight: '700', color: INK },

  likeBtn: {
    position: 'absolute',
    top: 10,
    right: 10,
    width: 30,
    height: 30,
    borderRadius: 15,
    /* 사진 위에 얹히므로 밝은 사진에서도 하트가 보이게 흰 판을 깐다. */
    backgroundColor: 'rgba(255,255,255,0.92)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  originBadge: {
    position: 'absolute',
    top: 10,
    left: 10,
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: 'rgba(255,255,255,0.92)',
    alignItems: 'center',
    justifyContent: 'center',
  },

  // 취향 추천 — 그리드(row wrap) 안에 끼므로 한 줄을 통째로 차지하게 100% 로 둔다.

  empty: { width: '100%', alignItems: 'center', paddingTop: 60, gap: 16 },
  loadingPercent: { fontSize: 32, fontWeight: '700', color: Editorial.ink },
  loadingMessage: { fontSize: 13, color: Editorial.textCaption },
  emptyText: { fontSize: 13, color: Editorial.textCaption },
  emptyBtn: {
    paddingHorizontal: 18,
    paddingVertical: 10,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
  },
  emptyBtnText: { fontSize: 13, fontWeight: '600', color: '#fff' },

  addFab: {
    position: 'absolute',
    right: PAD,
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: Editorial.surface,
    borderWidth: 1.5,
    borderColor: ink(0.16),
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: INK,
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.12,
    shadowRadius: 14,
    elevation: 8,
  },
});
