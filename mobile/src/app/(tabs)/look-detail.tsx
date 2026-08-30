import { Icon } from '@/components/icon';
import { ErrorState, LoadingState, SmartImage, useToast } from '@/components/ui';
import { categoryBudget, formatBudget, parsePrice, usePrefs } from '@/state/prefs';
import { router, useLocalSearchParams } from 'expo-router';
import { useMemo, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Editorial, ink, Fonts } from '@/constants/theme';
import { TODAY_LOOK_IMAGE } from '@/constants/look-images';
import {
  dailyLookResults,
  dailyLookToVariant,
  LOOK_VARIANTS,
  pickDailyLookResult,
  resolveLookVariant,
} from '@/constants/today-look';
import { savedLookStore } from '@/state/saved';
import { useAuth } from '@/state/auth';
import { draftItem } from '@/state/draft-item';
import { backTo, goBack } from '@/lib/goBack';
import { mallLabel, openExternal, productUrl } from '@/lib/mall';
import type { LookRelated } from '@/constants/today-look';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { useDailyLook } from '@/hooks/use-daily-look';
import { useHome } from '@/hooks/use-home';
import { DAILY_LOOK_EMPTY_RETRY, DAILY_LOOK_ONCE_A_DAY, dailyLookPhase } from '@/lib/dailyLookApi';
import { DetailTwoPane } from '@/components/detail-two-pane';
import { isDiscoveryLookId, useDiscoveryLook } from '@/hooks/use-discovery-look';
import { lookVoteStore, useLookVotes } from '@/state/look-votes';
import type { LookVariant } from '@/constants/today-look';
import { sameSlotSimilarProducts } from '@/lib/discoveryLookPresentation';

const INK = Editorial.ink;
const WINE = Editorial.wine;
const BONE = Editorial.bone;

/** 저장 시 태그 — 무드·상황 서브텍스트에서 뽑는다('미니멀 · 데일리' → ['미니멀','데일리']) */
/**
 * 하늘 상태 → 이모지. 서버가 주는 값은 다섯 가지뿐이다
 * (weather/services.py: 맑음·구름많음·흐림 + 강수형태를 합친 비·눈).
 * 모르는 값이면 아무것도 붙이지 않는다 — 틀린 그림보다 없는 편이 낫다.
 */
const SKY_EMOJI: Record<string, string> = {
  맑음: '☀️',
  구름많음: '⛅',
  흐림: '☁️',
  비: '🌧️',
  눈: '❄️',
};

function tagsOf(subtitle: string): string[] {
  return subtitle.split('·').map((s) => s.trim()).filter(Boolean);
}

// C4 추천 룩 상세 — 2D 가상착장 + 구성 + 추천 이유 + 피드백
export default function LookDetail() {
  const { contentStyle, width } = useBreakpoint();
  // 2단(≥1280)일 땐 본문을 넓게, 세로로 쌓일 땐 좁게 잡아 사진·카드가 과하게 커지지 않게 한다.
  const maxW = width >= 1280 ? 960 : 720;
  /* 어떤 룩을 볼지는 주소가 정한다. 없으면 오늘의 룩. */
  /* golden = 홈 카드가 보여 주던 그 룩('다른 룩'으로 돌려본 후보일 수 있다).
     없으면 대표 룩이다. */
  const { id, from, golden } = useLocalSearchParams<{
    id?: string;
    from?: string;
    golden?: string;
  }>();
  /* authLoading = 저장된 토큰으로 세션을 복원하는 중(status 'loading').
     **비회원과 갈라야 한다** — 아래 목업 분기가 이 구간을 게스트로 보면
     로그인 사용자가 부팅 몇 초 동안 목업을 본다. */
  const { isLoggedIn, isLoading: authLoading } = useAuth();
  /* 서브텍스트의 날씨는 홈과 같은 출처(useHome)에서 가져와 통일한다. 홈 응답에는
     오늘의 룩 상태도 실려 있어 아래 훅의 시드로 그대로 넘긴다(왕복 0회). */
  const { data: home } = useHome();
  /* 오늘의 룩(id 없음/'daily')은 추천 API 실데이터로 그린다. 홈이 이미 만들어 둔
     것을 다시 조회하는 것뿐이라(하루 1건 멱등) 재생성은 없고, 생성 중이면 훅이
     폴링해 보는 사이 완성되면 화면이 실제 추천으로 바뀐다.
     **완성 전에는 목업으로 물러나지 않는다** — 로그인 사용자에게 남의 룩 목업을
     보여주면 몇 초 뒤 통째로 바뀌어 방금 본 것이 가짜였다는 인상만 남는다.
     번들 목업은 비회원 둘러보기(LOOK_VARIANTS) 몫으로만 남긴다. */
  const {
    look: dailyLook,
    stalled: dailyStalled,
    reload: reloadDailyLook,
  } = useDailyLook(isLoggedIn, home ? home.daily_look : undefined);
  const {
    look: discoveryLook,
    failed: discoveryFailed,
    reload: reloadDiscovery,
  } = useDiscoveryLook(id);
  const apiVariant = useMemo(
    () => dailyLookToVariant(dailyLook, golden),
    [dailyLook, golden],
  );
  const discoveryVariant = useMemo<LookVariant | null>(() => discoveryLook ? ({
    id: discoveryLook.id,
    title: discoveryLook.title,
    subtitle: discoveryLook.subtitle,
    image: discoveryLook.image,
    reasons: discoveryLook.reasons,
    pieces: discoveryLook.items.map((item) => ({
      slot: item.slot,
      image: item.image,
      name: item.name,
      brand: item.brand,
      price: item.price == null ? undefined : String(item.price),
      link: item.link,
      tone: 0.08,
      mine: false,
      related: sameSlotSimilarProducts(item).map((product) => ({
        name: product.name,
        brand: product.brand,
        price: String(product.price),
        tone: 0.08,
        image: product.image,
        link: product.link,
      })),
    })),
  }) : null, [discoveryLook]);
  const look = discoveryVariant ?? ((!id || id === 'daily') && apiVariant ? apiVariant : resolveLookVariant(id));
  const PIECES = look.pieces;
  const lookTags = tagsOf(look.subtitle);
  /* 사진은 원격 URL 이 있으면 그것, 없으면 번들 목업(오늘의 룩) */
  const lookKey = look.image ? { image: look.image } : { asset: TODAY_LOOK_IMAGE };
  /* 지금 그리는 것이 서버가 만든 오늘의 룩인가. 저장이 골든 코디 경로를 타야 하는
     조건이자, '이미 담았는지'를 사진이 아니라 골든 id 로 봐야 하는 조건이다 —
     이 룩의 사진은 presigned URL 이라 조회마다 달라진다. */
  const serverGoldenId =
    !discoveryVariant && (!id || id === 'daily') && apiVariant
      ? (pickDailyLookResult(dailyLook, golden)?.golden_id ?? '')
      : '';

  /* 북마크는 **보고 있는 룩마다** 따로다. '다른 룩'으로 돌리면 같은 화면에서 대상이
     바뀌므로, 하나의 boolean 으로 들고 있으면 앞 룩의 상태가 뒷 룩에 그대로 남는다.
     낙관적 갱신(누르는 즉시 켜기)은 유지하되 룩 단위로 덮어쓴다. */
  /* 착용 이미지(render_frontal_*)가 아직 없는 실제 추천인가.
     골든 코디당 한 장을 만들어 재사용하는 구조라, 처음 나간 코디는 추천이 끝난 뒤
     몇 초~몇 분 동안 이미지가 비어 있다. 그때 번들 목업으로 메우면 안 된다 —
     내 추천을 보러 온 사람에게 남의 룩 사진을 보여주는 셈이고, 진짜 이미지가
     들어오는 순간 방금 본 것이 가짜였다는 인상만 남는다. */
  const renderPending = Boolean(serverGoldenId) && !look.image;
  /* 둘러보기 룩의 원본 id. 저장하면 서버가 사진을 자기 것으로 복사해 주소가 달라지므로,
     '같은 룩인가'는 사진이 아니라 이 값으로 본다(오늘의 룩의 serverGoldenId 와 같은 역할). */
  const sourceId = isDiscoveryLookId(id) ? id! : '';
  const savedKey = serverGoldenId || sourceId || (look.image ?? `asset:${TODAY_LOOK_IMAGE}`);
  const [savedOverrides, setSavedOverrides] = useState<Record<string, boolean>>({});
  const saved =
    savedOverrides[savedKey] ??
    (serverGoldenId
      ? savedLookStore.getByGoldenId(serverGoldenId) != null
      : sourceId
        ? savedLookStore.getBySourceId(sourceId) != null
        : savedLookStore.isSaved(lookKey));
  const setSaved = (next: boolean) =>
    setSavedOverrides((prev) => ({ ...prev, [savedKey]: next }));
  /* 평가는 화면 밖(기기)에 남긴다 — 뒤로 나갔다 들어와도 유지되고, 룩북 목록이
     '별로예요' 한 룩을 뒤로 미는 데 쓴다(state/look-votes.ts). */
  const votes = useLookVotes();
  const vote = votes[look.id] ?? null;
  const [openSlot, setOpenSlot] = useState<string | null>(null);
  const toast = useToast();
  const { effectiveCategoryBudgets } = usePrefs();

  /* 가상 피팅은 **서버가 만든 오늘의 룩**에서만 연다. 목업·둘러보기 룩에는 넘길
     look_id 가 없어, 그대로 보내면 피팅 화면이 내 룩이 아닌 것을 그린다.

     golden 도 함께 넘긴다 — '다른 룩'으로 돌려보던 후보를 입어보려는 것인데
     lookId 만 주면 서버가 대표 룩을 입힌다. 화면에서 고른 룩과 마네킹이 입은 룩이
     달라지는 것은 사용자에게 그냥 오작동이다(저장 버튼에서 같은 문제를 고쳤다). */
  const openVirtualTryOn = () => {
    if (!dailyLook?.look_id || !serverGoldenId) {
      toast('서버에서 생성된 추천 룩만 가상으로 입어볼 수 있어요.');
      return;
    }
    router.push({
      pathname: '/fitting',
      params: { lookId: dailyLook.look_id, golden: serverGoldenId },
    });
  };

  /** 관련 상품은 실제 적용 예산 안에서만 보여준다. */
  const filterRelated = (items: LookRelated[], category: string) =>
    items.filter((item) => {
      const budget = categoryBudget(effectiveCategoryBudgets, category);
      return budget == null || parsePrice(item.price) <= budget;
    });

  const relatedHead = (category: string) => {
    const parts: string[] = [];
    const budget = categoryBudget(effectiveCategoryBudgets, category);
    if (budget != null) parts.push(`${formatBudget(budget)} 예산 내 우선`);
    return parts.length ? `비슷한 상품 · ${parts.join(' · ')}` : '비슷한 상품';
  };

  /* [다른 룩] = 다음 변형으로. 이름 그대로 다른 룩을 보여준다 —
     예전엔 룩북으로 나가버려서 버튼 이름과 하는 일이 어긋나 있었다.

     오늘의 룩이면 리트리버가 뽑은 **진짜 차순위 후보**를 돌린다. 목업 변형으로
     넘어가면 로그인 사용자가 자기 추천을 보다가 남의 룩을 보게 된다. */
  const showAnotherLook = () => {
    if (serverGoldenId) {
      const results = dailyLookResults(dailyLook);
      const at = results.findIndex((r) => r.golden_id === serverGoldenId);
      const next = results[(at + 1) % results.length];
      router.setParams({ id: 'daily', golden: next.golden_id });
    } else {
      const at = LOOK_VARIANTS.findIndex((l) => l.id === look.id);
      const next = LOOK_VARIANTS[(at + 1) % LOOK_VARIANTS.length];
      router.setParams({ id: next.id });
    }
    setOpenSlot(null);
  };

  /**
   * 이 옷을 내 옷장에 등록한다 — 등록 화면에서 사진을 확인하고 시작한다.
   * (같은 흐름을 저장 룩 상세에서도 쓴다)
   */
  const addToCloset = (photo: string) => {
    if (!isLoggedIn) {
      toast('옷장은 로그인하고 쓸 수 있어요');
      router.push('/login');
      return;
    }
    draftItem.setPhoto(photo);
    router.push('/item-add');
  };

  /* 북마크 = 저장 토글. 켜면 내 룩북의 '위시'에 담고, 끄면 뺀다.
     하트를 안 쓰는 이유 — 하트는 룩북 피드의 '좋아요'가 가져갔다. 한 아이콘이 화면마다
     다른 뜻이면 누르기 전에 무슨 일이 생길지 알 수 없다. */
  /* 서버 왕복이라 먼저 켜 두고 실패하면 되돌린다 — 저장은 한 번 누르면 끝나야 하는 동작이라
     응답을 기다리는 동안 아이콘이 꺼져 있으면 눌리지 않은 것처럼 보인다. */
  const saveLook = async (): Promise<boolean> => {
    setSaved(true);
    try {
      if (serverGoldenId) {
        /* 골든 코디는 서버가 이미 가진 자산이다. 표지 사진을 다시 올리면(addLook)
           같은 사진이 사용자 수만큼 복제되고 이미 끝난 옷 추출을 다시 돈다. */
        await savedLookStore.saveDailyLook(serverGoldenId);
        return true;
      }
      await savedLookStore.addLook({
        ...lookKey,
        sourceId: sourceId || undefined,
        comment: look.title,
        tags: lookTags,
        reason: look.reasons[0],
      });
      return true;
    } catch (error) {
      setSaved(false);
      toast(error instanceof Error ? error.message : '저장하지 못했어요', { variant: 'error' });
      return false;
    }
  };

  const toggleSave = async () => {
    if (saved) {
      /* 사진으로 찾는 건 마지막 수단이다 — 저장하면 서버가 사진을 자기 것으로 복사해
         주소가 달라진다. 안정된 id(골든/둘러보기 원본)를 먼저 보고, 그것도 없는
         예전 저장분만 사진으로 훑는다. */
      const byPhoto = () =>
        savedLookStore
          .getLooks()
          .find((l) => (look.image ? l.image === look.image : l.asset === TODAY_LOOK_IMAGE));
      const found = serverGoldenId
        ? savedLookStore.getByGoldenId(serverGoldenId)
        : (sourceId ? savedLookStore.getBySourceId(sourceId) : undefined) ?? byPhoto();

      /* 못 찾았는데 화면만 끄면 안 된다 — 서버엔 그대로 남아 있어서, 한 번 더 누르면
         같은 룩이 또 담긴다(실제로 그렇게 두 번 저장됐다). 상태를 지키고 사실대로 알린다. */
      if (!found) {
        toast('담아 둔 룩을 찾지 못했어요. 룩북에서 빼 주세요', { variant: 'error' });
        return;
      }

      setSaved(false);
      try {
        await savedLookStore.removeLook(found.id);
      } catch (error) {
        setSaved(true);
        toast(error instanceof Error ? error.message : '빼지 못했어요', { variant: 'error' });
      }
      return;
    }
    if (await saveLook()) toast(serverGoldenId ? '내 룩북에 담았어요' : '위시에 담았어요');
  };

  /* 하단 '룩북에 저장' = 담고 그 룩이 선 갈래로 이동 — 담았다고 해 놓고 그 룩이 없는
     목록을 열면 실패로 읽힌다. 오늘의 룩은 서버에 진짜 룩북 글로 남아 내 룩북에,
     목업·피드 룩은 예전처럼 위시에 선다. */
  const saveAndGoLookbook = async () => {
    if (await saveLook()) {
      router.push(serverGoldenId ? '/(tabs)/lookbook?tab=mine' : '/(tabs)/lookbook?tab=wish');
    }
  };

  /* 아직 안 불러왔거나 API 실패 시엔 날씨를 생략하고 무드·상황만 보여준다. */
  const w = home?.weather;
  const weatherPart = w && w.temperature != null ? `${w.region ?? '서울'} ${w.temperature}°` : null;
  const subtitle = [weatherPart, look.subtitle].filter(Boolean).join(' · ');
  /* 둘러보기 룩의 머리글은 날씨다. 룩 이름('남성 나들이 룩 01')은 운영자가 붙인 일련번호라
     사용자에게 알려주는 것이 없다 — 그 자리를 지금 날씨로 바꾸고 해시태그를 아래에 둔다.
     날씨를 못 받아왔으면 머리글을 비우고 해시태그만 남긴다(빈 줄을 만들지 않는다). */
  const skyEmoji = w?.sky_state ? (SKY_EMOJI[w.sky_state] ?? '') : '';
  const discoveryHead = [weatherPart, skyEmoji].filter(Boolean).join(' ');
  /* 해시태그는 서버가 준 tags 를 그대로 쓴다 — subtitle 을 '·' 로 쪼개 만든 lookTags 는
     오늘의 룩 표기를 위한 것이라 둘러보기 룩에서는 한 개로 뭉뚱그려질 때가 있다. */
  const discoveryTags = discoveryLook?.tags?.length ? discoveryLook.tags : lookTags;

  /* 로그인 사용자가 '오늘의 룩'을 열었는데 아직 완성 전이면, 목업 룩을 그리는 대신
     지금 무슨 일이 벌어지는지 보여준다. 홈 카드와 같은 판정(dailyLookPhase)을 써서
     두 화면이 같은 순간에 같은 말을 하게 한다. */
  const isDailyRoute = !discoveryVariant && (!id || id === 'daily');
  /* 룩북(둘러보기) 룩인가. 오늘의 룩과 성격이 다르다 — 개인 추천이 아니라 운영자가
     미리 만들어 둔 룩이라, 가상 피팅도 '왜 이 룩일까요'도 여기에는 해당하지 않는다.
     한 화면이 두 성격을 겸하고 있어서 판정을 여기 한 번만 두고 아래에서 갈라 쓴다. */
  const isDiscovery = Boolean(discoveryVariant);
  const dailyPhase = dailyLookPhase(dailyLook, dailyStalled);
  /* 오늘의 룩 경로에서 실데이터를 그릴 수 없는 동안은 목업으로 물러나지 않는다.
     두 경우다.

     1. 인증 복원 중(authLoading) — 아직 회원인지 모른다. bootstrap 은 secure store
        읽기에 `GET /users/me` 왕복까지라 수백 ms~수 초다. 그동안 useDailyLook 도
        꺼져 있어(enabled=isLoggedIn) 조회가 시작조차 안 된다.
     2. 로그인 사용자인데 추천이 아직 안 됐다(생성 중·후보 없음·실패).

     비회원은 여기 걸리지 않는다 — 둘러보기로 들어온 사람에게는 샘플 룩이 빈 화면보다 낫다. */
  /* 둘러보기 룩도 조회가 끝나기 전에는 목업으로 물러나지 않는다.
     예전에는 useDiscoveryLook 이 로딩 중에도 null 을 줘서 아래 resolveLookVariant 가
     번들 목업 룩을 그렸다 — 목록에서 고른 것과 다른 사진이 잠깐 떴다가 실제 룩으로
     바뀌어, 방금 본 것이 가짜였다는 인상만 남았다. 오늘의 룩 경로와 같은 규칙이다. */
  if (isDiscoveryLookId(id) && !discoveryVariant) {
    return (
      <View style={styles.container}>
        <SafeAreaView edges={['top']} style={styles.headerSafe}>
          <View style={[styles.header, contentStyle(maxW)]}>
            <Pressable hitSlop={12} onPress={() => goBack(backTo(from, '/(tabs)/lookbook'))}>
              <Icon name="chevron.left" tintColor={INK} size={20} />
            </Pressable>
            {/* 홈의 오늘의 룩과 같은 화면을 쓰지만 성격이 다르다 — 이름으로 갈라 준다 */}
          <Text style={styles.headerTitle}>{isDiscovery ? '둘러보기' : '추천 룩'}</Text>
            <View style={styles.headerRight} />
          </View>
        </SafeAreaView>
        {discoveryFailed ? (
          <ErrorState
            title="룩을 불러오지 못했어요"
            description="잠시 뒤 다시 시도해 주세요."
            onRetry={reloadDiscovery}
          />
        ) : (
          <LoadingState message="룩을 불러오는 중이에요…" />
        )}
      </View>
    );
  }

  if (isDailyRoute && (authLoading || (isLoggedIn && dailyPhase !== 'ready'))) {
    return (
      <View style={styles.container}>
        <SafeAreaView edges={['top']} style={styles.headerSafe}>
          <View style={[styles.header, contentStyle(maxW)]}>
            <Pressable hitSlop={12} onPress={() => goBack(backTo(from, '/(tabs)/home'))}>
              <Icon name="chevron.left" tintColor={INK} size={20} />
            </Pressable>
            <Text style={styles.headerTitle}>추천 룩</Text>
            <View style={styles.headerRight} />
          </View>
        </SafeAreaView>
        {authLoading || dailyPhase === 'pending' ? (
          /* 인증 복원 중에는 '만들고 있어요'가 사실이 아니다 — 아직 아무것도
             부르지 않았다. 문구를 갈라 두면 사용자가 기다리는 대상이 정확해진다. */
          <LoadingState
            message={
              authLoading
                ? '오늘의 룩을 불러오는 중이에요…'
                : (dailyLook?.detail ?? '오늘의 룩을 만들고 있어요…')
            }
          />
        ) : dailyLook?.status === 'EMPTY' ? (
          /* 후보가 없는 것은 오류가 아니다 — 다시 시도해도 같으니 프로필로 보낸다.
             (홈의 같은 버튼과 마찬가지로, 이름만 바꾸는 edit-profile 이 아니라
             체형·추구미 입력이 모여 있는 마이 탭이 목적지다.) */
          <ErrorState
            title="오늘 추천할 룩을 찾지 못했어요"
            description={DAILY_LOOK_EMPTY_RETRY}
            onRetry={() => router.push('/(tabs)/my')}
            retryLabel="프로필 채우기"
            retryIcon="person"
          />
        ) : (
          <ErrorState
            title="오늘의 룩을 준비하지 못했어요"
            description={dailyLook?.detail ?? '잠시 뒤 다시 시도해 주세요.'}
            onRetry={reloadDailyLook}
          />
        )}
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* 헤더 */}
      <SafeAreaView edges={['top']} style={styles.headerSafe}>
        <View style={[styles.header, contentStyle(maxW)]}>
          {/* 들어온 자리(from)로 돌아간다. 없으면 홈.
              ⚠️ router.replace 를 직접 쓰지 말 것 — 웹에서 조용히 무시돼 버튼이 먹통이 된다(lib/goBack.ts). */}
          {/* from 이 없을 때 돌아갈 자리도 갈라 준다 — 둘러보기 룩을 홈으로 돌려보내면
              방금 보던 목록이 아니라 남의 화면이 열린다. */}
          <Pressable
            hitSlop={12}
            onPress={() => goBack(backTo(from, isDiscovery ? '/(tabs)/lookbook' : '/(tabs)/home'))}>
            <Icon name="chevron.left" tintColor={INK} size={20} />
          </Pressable>
          <Text style={styles.headerTitle}>{isDiscovery ? '둘러보기' : '추천 룩'}</Text>
          <Pressable
            style={styles.headerRight}
            hitSlop={12}
            onPress={toggleSave}
            accessibilityLabel={saved ? '저장 취소' : '저장'}>
            <Icon
              name={saved ? 'bookmark.fill' : 'bookmark'}
              tintColor={saved ? WINE : ink(0.5)}
              size={20}
            />
          </Pressable>
        </View>
      </SafeAreaView>

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={[styles.content, contentStyle(maxW)]}>
        {/* 데스크톱: [사진 | 상세·아이템] 2단 / 태블릿·모바일: 세로 */}
        <DetailTwoPane
          image={
            /* 2D 가상착장 — 탭하면 가상 피팅 화면으로 */
            <Pressable
              style={styles.fitting}
              /* 준비 중에는 눌리지 않는다 — 없는 착용 이미지를 두고 '가상으로
                 입어보기'로 보내면 버튼이 약속한 것과 다른 화면이 열린다.
                 둘러보기 룩은 넘길 look_id 가 없어 애초에 입어볼 수 없다. */
              disabled={renderPending || isDiscovery}
              onPress={openVirtualTryOn}>
          {renderPending ? (
            <>
              <ActivityIndicator color={Editorial.selected} />
              <Text style={styles.renderPendingTitle}>착용 이미지를 준비하고 있어요</Text>
              {/* "잠시 뒤 자동으로 보여요"라고 쓰지 않는다 — 추천이 이미 SUCCEEDED 라
                  이 화면은 더 이상 폴링하지 않는다(서버 계약: 다음 조회에서 채워진다).
                  대신 지금 확인할 수단을 옆에 둔다. */}
              <Text style={styles.renderPendingBody}>
                코디 구성은 아래에서 먼저 볼 수 있어요. 이미지는 다 만들어진 뒤 다시 열면 보여요.
              </Text>
              <Pressable
                style={styles.renderPendingBtn}
                hitSlop={8}
                onPress={() => {
                  void reloadDailyLook();
                }}>
                <Icon name="arrow.clockwise" tintColor={INK} size={13} />
                <Text style={styles.renderPendingBtnText}>지금 확인</Text>
              </Pressable>
            </>
          ) : (
            <>
          <SmartImage
            uri={look.image}
            asset={look.image ? undefined : TODAY_LOOK_IMAGE}
            width="100%"
            radius={0}
            contentFit="cover"
            style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }}
          />
          {/* 가상 피팅은 오늘의 룩 전용이다. 둘러보기 룩에 이 배지·버튼을 두면
              눌러도 아무 일이 안 일어나거나 남의 룩을 입어보게 된다. */}
          {isDiscovery ? null : (
            <>
              <View style={styles.fittingBadge}>
                <Icon name="figure.stand" tintColor="#fff" size={12} />
                <Text style={styles.fittingBadgeText}>내 체형 반영</Text>
              </View>
              <View style={styles.fittingCta}>
                <Icon name="sparkles" tintColor={INK} size={13} />
                <Text style={styles.fittingCtaText}>가상으로 입어보기</Text>
              </View>
            </>
          )}
            </>
          )}
            </Pressable>
          }
          details={
            <View style={styles.body}>
          {isDiscovery ? (
            <>
              {discoveryHead ? <Text style={styles.title}>{discoveryHead}</Text> : null}
              {discoveryTags.length > 0 ? (
                <Text style={[styles.subtitle, discoveryHead ? null : styles.subtitleLead]}>
                  {discoveryTags.map((tag) => `#${tag}`).join(' ')}
                </Text>
              ) : null}
            </>
          ) : (
            <>
              <Text style={styles.title}>{look.title}</Text>
              <Text style={styles.subtitle}>{subtitle}</Text>
            </>
          )}

          {/* 구성 아이템 — 탭하면 비슷한/대체 상품 아코디언 */}
          <Text style={styles.sectionTitle}>구성 아이템</Text>
          <View style={styles.pieces}>
            {PIECES.map((p) => {
              /* 운영자 룩은 원본 판매처만 있어도 열 수 있다. 유사 후보가 없으면 원본과
                 빈 상태를 함께 보여 주고, 다른 상품으로 억지로 채우지 않는다. */
              const expandable = Boolean(p.link) || p.related.length > 0;
              const open = expandable && openSlot === p.slot;
              const related = filterRelated(p.related, p.slot);
              return (
                <View key={p.slot} style={[styles.pieceWrap, open && styles.pieceWrapOpen]}>
                  <Pressable
                    style={styles.piece}
                    disabled={!expandable}
                    onPress={() => setOpenSlot(open ? null : p.slot)}>
                    <View style={styles.pieceThumb}>
                      <SmartImage uri={p.image} width="100%" aspectRatio={1} radius={12} contentFit="cover" />
                    </View>
                    <View style={styles.pieceBody}>
                      <View style={styles.pieceTop}>
                        <Text style={styles.pieceSlot}>{p.slot}</Text>
                        <View style={[styles.ownTag, !p.mine && styles.newTag]}>
                          <Text style={[styles.ownTagText, !p.mine && styles.newTagText]}>
                            {p.mine ? '내 옷장' : '추천 구매'}
                          </Text>
                        </View>
                      </View>
                      <Text style={styles.pieceName}>{p.name}</Text>
                      <Text style={styles.pieceBrand}>{p.brand}</Text>
                    </View>
                    {/* 내 옷장에 없는 옷만 담을 수 있다. 이미 가진 옷을 또 넣으면 옷장이 겹친다.
                        사진이 없는 아이템도 뺀다 — 옷장 등록은 사진 한 장에서 시작한다. */}
                    {!p.mine && p.image ? (
                      <Pressable
                        style={styles.pieceAdd}
                        hitSlop={6}
                        onPress={() => addToCloset(p.image!)}
                        accessibilityLabel={`${p.name} 옷장에 추가`}>
                        <Icon name="plus" tintColor={INK} size={13} />
                        <Text style={styles.pieceAddText}>옷장에</Text>
                      </Pressable>
                    ) : null}
                    {expandable ? (
                      <Icon
                        name={open ? 'chevron.down' : 'chevron.right'}
                        tintColor={ink(0.3)}
                        size={16}
                      />
                    ) : null}
                  </Pressable>

                  {open ? (
                    <View style={styles.related}>
                      {p.link ? (
                        <>
                          <Text style={styles.relatedHead}>원본 상품</Text>
                          <View style={styles.relatedItem}>
                            <Pressable
                              style={styles.relatedMain}
                              onPress={() => openExternal(p.link!)}
                              accessibilityLabel={`${p.brand} ${p.name} 원본 상품 보기`}>
                              <View style={styles.relatedThumb}>
                                <SmartImage
                                  uri={p.image}
                                  width={44}
                                  height={44}
                                  radius={10}
                                  contentFit="cover"
                                />
                              </View>
                              <View style={styles.relatedBody}>
                                <Text style={styles.relatedName} numberOfLines={1}>
                                  {p.name}
                                </Text>
                                <View style={styles.relatedMeta}>
                                  <Text style={styles.relatedBrand}>{p.brand}</Text>
                                  <Icon
                                    name="arrow.up.right.square"
                                    tintColor={ink(0.32)}
                                    size={11}
                                  />
                                  <Text style={styles.relatedMall}>원본 판매처</Text>
                                </View>
                              </View>
                              {p.price ? (
                                <Text style={styles.relatedPrice}>{p.price}원</Text>
                              ) : null}
                            </Pressable>
                          </View>
                        </>
                      ) : null}
                      <Text style={styles.relatedHead}>{relatedHead(p.slot)}</Text>
                      {related.map((r) => {
                        const budget = categoryBudget(effectiveCategoryBudgets, p.slot);
                        const inBudget = budget != null && parsePrice(r.price) <= budget;
                        const url = productUrl(r, r.mall);
                        return (
                          <View key={r.name} style={styles.relatedItem}>
                            {/* 상품 본문을 누르면 판매처로 나간다 — 우리는 결제를 받지 않는다. */}
                            <Pressable
                              style={styles.relatedMain}
                              onPress={() => openExternal(url)}
                              accessibilityLabel={`${r.brand} ${r.name} — ${mallLabel(url)}에서 보기`}>
                              <View style={styles.relatedThumb}>
                                <SmartImage
                                  uri={r.image}
                                  width={44}
                                  height={44}
                                  radius={10}
                                  contentFit="cover"
                                />
                              </View>
                              <View style={styles.relatedBody}>
                                <Text style={styles.relatedName} numberOfLines={1}>
                                  {r.name}
                                </Text>
                                <View style={styles.relatedMeta}>
                                  <Text style={styles.relatedBrand}>{r.brand}</Text>
                                  <Icon name="arrow.up.right.square" tintColor={ink(0.32)} size={11} />
                                  <Text style={styles.relatedMall}>{mallLabel(url)}</Text>
                                </View>
                              </View>
                              <View style={styles.relatedRight}>
                                <Text style={styles.relatedPrice}>{r.price}원</Text>
                                {inBudget ? (
                                  <View style={styles.budgetTag}>
                                    <Text style={styles.budgetTagText}>예산 내</Text>
                                  </View>
                                ) : null}
                              </View>
                            </Pressable>
                          </View>
                        );
                      })}
                      {related.length === 0 ? (
                        <Text style={styles.relatedEmpty}>
                          {p.related.length === 0
                            ? '조건에 맞는 비슷한 상품을 찾지 못했어요.'
                            : '현재 예산 안의 비슷한 상품을 찾지 못했어요.'}
                        </Text>
                      ) : null}
                      {p.related.length > 0 &&
                      categoryBudget(effectiveCategoryBudgets, p.slot) == null ? (
                        <Pressable
                          style={styles.budgetPrompt}
                          onPress={() => router.push('/budget')}>
                          <Icon name="wallet" tintColor={ink(0.5)} size={14} />
                          <Text style={styles.budgetPromptText}>{p.slot} 예산을 설정하면 예산 내 상품을 먼저 보여드려요</Text>
                        </Pressable>
                      ) : null}
                    </View>
                  ) : null}
                </View>
              );
            })}
          </View>

          {/* 추천 이유 — 오늘의 룩에만. 둘러보기 룩의 reasons 는 모든 룩에 똑같이 붙는
              안내문("운영자가 선별한 …")이라, '왜 이 룩일까요?' 아래 두면 개인에게 맞춘
              추천처럼 읽힌다. 개수 안내(하루 한 번)도 오늘의 룩 이야기다. */}
          {isDiscovery ? null : (
            <>
              <Text style={styles.sectionTitle}>왜 이 룩일까요?</Text>
              <View style={styles.reasonCard}>
                {look.reasons.map((r, i) => (
                  <View key={i} style={styles.reasonRow}>
                    <View style={styles.pin}>
                      <Text style={styles.pinNum}>{i + 1}</Text>
                    </View>
                    <Text style={styles.reasonText}>{r}</Text>
                  </View>
                ))}
              </View>

              {/* 하단 [다른 룩]은 오늘 함께 뽑아 둔 후보를 돌려보는 버튼이다 — 새로 만드는 게
                  아니라는 걸 여기서 밝힌다. 홈 카드와 같은 문구를 쓴다. */}
              <Text style={styles.onceADay}>{DAILY_LOOK_ONCE_A_DAY}</Text>
            </>
          )}

          {/* 피드백 */}
          <View style={styles.feedback}>
            <Text style={styles.feedbackLabel}>이 추천 어떠세요?</Text>
            <View style={styles.voteRow}>
              <Pressable
                style={[styles.voteBtn, vote === 'up' && styles.voteUpOn]}
                onPress={() => lookVoteStore.toggle(look.id, 'up')}>
                <Icon
                  name="hand.thumbsup"
                  tintColor={vote === 'up' ? '#fff' : ink(0.6)}
                  size={16}
                />
                <Text style={[styles.voteText, vote === 'up' && styles.voteTextOn]}>좋아요</Text>
              </Pressable>
              <Pressable
                style={[styles.voteBtn, vote === 'down' && styles.voteDownOn]}
                onPress={() => lookVoteStore.toggle(look.id, 'down')}>
                <Icon
                  name="hand.thumbsdown"
                  tintColor={vote === 'down' ? '#fff' : ink(0.6)}
                  size={16}
                />
                <Text style={[styles.voteText, vote === 'down' && styles.voteTextOn]}>별로예요</Text>
              </Pressable>
            </View>
          </View>
            </View>
          }
        />
      </ScrollView>

      {/* 하단 바 */}
      <View style={styles.bottomDivider} />
      <View style={[styles.bottomBar, { paddingBottom: 12 }, contentStyle(maxW)]}>
        {/* '다른 룩'은 오늘 함께 뽑아 둔 **후보를 돌려보는** 버튼이다. 둘러보기 룩에는
            돌려볼 후보가 없어 번들 목업(LOOK_VARIANTS)으로 튄다 — 목록에서 고른 룩과
            상관없는 사진이 열리므로 아예 두지 않는다. */}
        {isDiscovery ? null : (
          <Pressable style={styles.altBtn} onPress={showAnotherLook}>
            <Text style={styles.altText}>다른 룩</Text>
          </Pressable>
        )}
        <Pressable style={styles.saveBtn} onPress={saveAndGoLookbook}>
          <Icon name="bookmark.fill" tintColor="#fff" size={15} />
          <Text style={styles.saveText}>룩북에 저장</Text>
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
    gap: 10,
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 10,
  },
  // 제목을 뒤로가기 버튼 바로 옆에 붙이고, 우측 아이콘은 끝으로 민다.
  headerRight: { marginLeft: 'auto' },
  headerTitle: { fontSize: 15, fontWeight: '600', color: INK },

  content: { paddingBottom: 24 },

  fitting: {
    /* 고정 높이로 두면 폭이 넓어지는 데스크톱에서 가로로 납작해져 세로 사진이 잘린다.
       폰 폭(400) 기준 비율을 유지한다. */
    aspectRatio: 1.111,
    backgroundColor: BONE,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    /* 절대배치된 미리보기 사진이 밖으로 넘쳐 헤더·하단 버튼을 덮지 않도록 잘라낸다. */
    overflow: 'hidden',
  },
  fittingMark: { fontFamily: Fonts.serif, fontSize: 54, color: Editorial.textMuted },
  fittingLabel: { fontSize: 13, color: Editorial.textCaption, letterSpacing: 0.5 },
  fittingBadge: {
    position: 'absolute',
    bottom: 16,
    right: 16,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    backgroundColor: INK,
    paddingHorizontal: 11,
    paddingVertical: 6,
    borderRadius: 999,
  },
  fittingBadgeText: { fontSize: 10.5, color: '#fff', fontWeight: '500' },
  fittingCta: {
    position: 'absolute',
    bottom: 16,
    left: 16,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: 'rgba(255,255,255,0.92)',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 999,
  },
  fittingCtaText: { fontSize: 12.5, fontWeight: '600', color: INK },
  /* 착용 이미지 준비 중 — 사진 자리를 그대로 쓰므로(styles.fitting) 이미지가
     도착해도 레이아웃이 튀지 않는다. */
  renderPendingTitle: { fontSize: 15, fontWeight: '600', color: INK, marginTop: 4 },
  renderPendingBody: {
    fontSize: 12.5,
    lineHeight: 19,
    color: Editorial.textCaption,
    textAlign: 'center',
    paddingHorizontal: 32,
  },
  renderPendingBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginTop: 4,
    height: 34,
    paddingHorizontal: 14,
    borderRadius: 17,
    borderWidth: 1,
    borderColor: Editorial.line,
    backgroundColor: Editorial.page,
  },
  renderPendingBtnText: { fontSize: 12.5, fontWeight: '600', color: INK },

  body: { paddingHorizontal: 20, paddingTop: 22 },
  title: { fontFamily: Fonts.serif, fontSize: 24, color: INK },
  subtitle: { fontSize: 13, color: Editorial.textCaption, marginTop: 6 },
  /** 머리글(날씨) 없이 해시태그만 남을 때 — 위 여백을 지운다 */
  subtitleLead: { marginTop: 0 },

  sectionTitle: { fontSize: 13, fontWeight: '600', color: INK, marginTop: 28, marginBottom: 12 },

  pieces: { gap: 10 },
  pieceWrap: {
    borderWidth: 1,
    borderColor: ink(0.09),
    borderRadius: 16,
    overflow: 'hidden',
  },
  pieceWrapOpen: { borderColor: ink(0.16) },
  piece: {
    flexDirection: 'row',
    gap: 12,
    padding: 10,
    alignItems: 'center',
  },
  pieceThumb: { width: 56, height: 56, borderRadius: 12, backgroundColor: BONE, overflow: 'hidden' },
  pieceBody: { flex: 1, gap: 3 },
  pieceTop: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  pieceSlot: { fontSize: 11, color: Editorial.textCaption, fontWeight: '500' },
  ownTag: { backgroundColor: Editorial.surfaceTag, paddingHorizontal: 7, paddingVertical: 2, borderRadius: 999 },
  ownTagText: { fontSize: 9.5, color: Editorial.textCaption, fontWeight: '600' },
  newTag: { backgroundColor: Editorial.accent },
  newTagText: { color: WINE },
  pieceName: { fontSize: 14, fontWeight: '500', color: Editorial.ink },
  pieceAdd: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
    height: 28,
    paddingHorizontal: 9,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: ink(0.14),
  },
  pieceAddText: { fontSize: 11, fontWeight: '600', color: INK },
  pieceBrand: { fontSize: 12, color: Editorial.textCaption },

  // 관련/대체 상품 아코디언
  related: {
    borderTopWidth: 1,
    borderTopColor: ink(0.08),
    backgroundColor: '#faf9f7',
    paddingHorizontal: 10,
    paddingTop: 12,
    paddingBottom: 8,
    gap: 10,
  },
  relatedHead: { fontSize: 11, color: Editorial.textCaption, fontWeight: '600' },
  relatedEmpty: { fontSize: 12, color: Editorial.textCaption, paddingVertical: 10 },
  relatedItem: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  relatedMain: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 10 },
  relatedThumb: { width: 44, height: 44, borderRadius: 10, backgroundColor: BONE },
  relatedBody: { flex: 1, gap: 2 },
  relatedName: { fontSize: 13, fontWeight: '500', color: Editorial.ink },
  relatedMeta: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  relatedBrand: { fontSize: 11.5, color: Editorial.textCaption },
  relatedMall: { fontSize: 11, color: Editorial.textMuted },
  relatedRight: { alignItems: 'flex-end', gap: 4 },
  relatedPrice: { fontSize: 13, fontWeight: '600', color: INK },
  budgetTag: {
    backgroundColor: '#e6efe6',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 6,
  },
  budgetTagText: { fontSize: 9.5, color: '#3f6b3f', fontWeight: '700' },
  budgetPrompt: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    marginTop: 2,
    paddingVertical: 8,
    paddingHorizontal: 10,
    borderRadius: 10,
    backgroundColor: Editorial.surface,
    borderWidth: 1, borderColor: Editorial.line,
  },
  budgetPromptText: { flex: 1, fontSize: 11.5, color: Editorial.textCaption },

  reasonCard: {
    backgroundColor: Editorial.surfaceSoft,
    borderWidth: 1, borderColor: Editorial.line,
    borderRadius: 16,
    padding: 16,
    gap: 14,
  },
  reasonRow: { flexDirection: 'row', gap: 11, alignItems: 'flex-start' },
  pin: {
    width: 22,
    height: 22,
    borderRadius: 11,
    backgroundColor: WINE,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 1,
  },
  pinNum: { fontSize: 11, color: '#fff', fontWeight: '700' },
  reasonText: { flex: 1, fontSize: 13.5, color: Editorial.textSoft, lineHeight: 20 },

  /* 추천 이유 아래 한 줄 — 본문보다 낮은 톤으로, 룩을 가리지 않는다. */
  onceADay: { marginTop: 14, fontSize: 12, lineHeight: 18, color: Editorial.textSoft },
  feedback: { marginTop: 28, alignItems: 'center', gap: 12 },
  feedbackLabel: { fontSize: 13, color: Editorial.textCaption },
  voteRow: { flexDirection: 'row', gap: 10 },
  voteBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    paddingHorizontal: 22,
    height: 44,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: ink(0.14),
  },
  voteUpOn: { backgroundColor: Editorial.selected, borderColor: Editorial.selected },
  voteDownOn: { backgroundColor: ink(0.55), borderColor: ink(0.55) },
  voteText: { fontSize: 13.5, color: Editorial.textCaption, fontWeight: '500' },
  voteTextOn: { color: '#fff' },

  bottomDivider: { height: 1, backgroundColor: ink(0.08) },
  bottomBar: {
    flexDirection: 'row',
    gap: 10,
    backgroundColor: Editorial.page,
    paddingHorizontal: 20,
    paddingTop: 12,
  },
  altBtn: {
    height: 50,
    paddingHorizontal: 22,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: ink(0.14),
    alignItems: 'center',
    justifyContent: 'center',
  },
  altText: { fontSize: 14, color: Editorial.textCaption, fontWeight: '500' },
  saveBtn: {
    flex: 1,
    flexDirection: 'row',
    gap: 8,
    height: 50,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  saveText: { fontSize: 14, color: '#fff', fontWeight: '500' },
});
