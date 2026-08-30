import { Icon } from '@/components/icon';
import { router, useFocusEffect } from 'expo-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { HomeStatusSlot } from '@/components/home/home-status-slot';
import { Avatar, ErrorState, LoadingState, Skeleton, SmartImage, useToast } from '@/components/ui';
import { DEMO_HOME, DEMO_LOOKS } from '@/constants/demo';
import { Editorial, ink, Fonts , ContentMax} from '@/constants/theme';
import { displayName, profilePhoto } from '@/lib/userProfile';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { useDailyLook } from '@/hooks/use-daily-look';
import { useHome, type HomeData, type HomeWeather } from '@/hooks/use-home';
import { useRefresh } from '@/hooks/use-refresh';
import { useWardrobeItems } from '@/hooks/use-wardrobe';
import {
  DAILY_LOOK_EMPTY_RETRY,
  DAILY_LOOK_ONCE_A_DAY,
  dailyLookPhase,
  type DailyLook,
  type DailyLookPhase,
  type DailyLookResult,
} from '@/lib/dailyLookApi';
import { useAuth } from '@/state/auth';
import { savedLookStore } from '@/state/saved';

// ── 에디토리얼 본 팔레트 (라이트 고정) ──
const INK = Editorial.ink;
const CHIP = Editorial.surface;

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];

/** 홈 오늘의 룩 placeholder — URL만 바꿔서 미리보기 */
/* 오늘의 룩 사진 비율(가로:세로). 고정 높이로 두면 카드가 넓어지는 데스크톱에서
   가로로 납작한 틀이 되어 세로 사진이 가운데만 잘린다. */
const LOOK_IMAGE_RATIO = 1 / 1.05;

/** "7월 15일 화요일" — 오늘 날짜 (기기 로컬 기준) */
function todayLabel(): string {
  const d = new Date();
  return `${d.getMonth() + 1}월 ${d.getDate()}일 ${WEEKDAYS[d.getDay()]}요일`;
}

/** "서울 24° · 맑음" — 값이 없으면 우아하게 생략 */
function weatherLabel(w: HomeWeather): string {
  const region = w.region ?? '서울';
  const temp = w.temperature != null ? `${w.temperature}°` : '—';
  return w.sky_state ? `${region} ${temp} · ${w.sky_state}` : `${region} ${temp}`;
}

// 홈 탭 (Figma B1) — GET /api/v1/home/ 연동
export default function HomeScreen() {
  const { contentStyle } = useBreakpoint();
  const { status, isDemo, user } = useAuth();
  /* 비회원은 부를 것이 없어(토큰도, 옷장도 없다) 온보딩 전용 홈을 즉시 보여준다.
     데모 세션도 부른다 — 토큰이 없을 뿐 요청은 통과한다(dev 서버가 무토큰 요청을 허용).
     그래야 발표에서 진짜 날씨가 뜬다. 예전엔 여기서 막아 두어 고정 목업만 보였다. */
  const { data: apiData, loading, reload } = useHome(undefined, status === 'authed');
  /* 오늘의 룩(추천 API)의 상태는 **홈 응답이 이미 싣고 온다** — 홈 진입이 곧 생성
     트리거라 백엔드가 걸어두는 김에 같은 시리얼라이저로 넣어준다. 그걸 시드로 주면
     첫 프레임부터 올바른 분기를 그릴 수 있고(왕복 0회), 아직 만드는 중일 때만 훅이
     폴링을 이어받는다. 홈 응답 전에는 `undefined` 를 넘겨 훅이 기다리게 한다 —
     여기서 따로 조회하면 같은 것을 두 번 묻고, 그 사이 카드가 빈 채로 깜빡인다.
     데모 세션은 토큰이 없어 추천 API 가 401 이므로 아예 부르지 않는다(아래 목업 카드). */
  const {
    look: dailyLook,
    stalled: dailyStalled,
    reload: reloadDailyLook,
  } = useDailyLook(status === 'authed' && !isDemo, apiData ? apiData.daily_look : undefined);
  /* 당겨서 새로고침은 홈만 다시 부르면 된다 — 홈 응답에 룩 상태가 실려 오므로
     시드가 갱신되고 카드도 같이 바뀐다. 홈이 룩 상태를 못 실어 온(선반영 실패)
     경우에만 훅이 직접 조회하고 있으므로, 그때를 위해 룩 쪽도 함께 부른다. */
  const reloadAll = useCallback(
    () => Promise.all([reload(), reloadDailyLook()]),
    [reload, reloadDailyLook],
  );
  const { refreshing, onRefresh } = useRefresh(reloadAll);
  /* 실패하면 데모 세션만 목업으로 물러난다 — 인증이 켜지면 401 이 나는데,
     체험용 링크에서 홈이 통째로 에러 화면이 되는 것보다 낫다. */
  const data = apiData ?? (isDemo ? DEMO_HOME : null);

  /* 추천 카드가 그려야 할 단계. 이 세 갈래를 구분하지 않으면 "아직 없음"이
     "완성됨"과 같은 모양으로 나가고, 그게 목업이 진짜 추천처럼 보이던 원인이었다. */
  const lookPhase: DailyLookPhase = dailyLookPhase(dailyLook, dailyStalled);

  /* 프로필을 채우고 돌아온 사용자를 위해, 추천이 '준비 안 됨'일 때만 복귀 시 다시 묻는다.

     서버는 EMPTY 로 끝난 오늘의 룩을 체형·추구미가 바뀌었으면 그 자리에서 다시 만든다
     (ensure_today_look). 그런데 홈은 탭 스택에 얹혀 한 번 뜨면 언마운트되지 않아,
     '프로필 채우기'로 나갔다 돌아와도 홈 API 를 다시 부르지 않는다 — 그러면 서버에게
     다시 만들 기회 자체가 없고, 안내대로 한 사용자는 종일 같은 화면을 본다.

     완성·생성중일 때는 부르지 않는다. 생성은 하루 한 번이라 결과가 같고, 탭을 오갈
     때마다 왕복만 는다. 판단값을 ref 로 읽는 이유는 useFocusEffect 의 콜백 신원이
     바뀌면 (포커스를 잃지 않았어도) 정리 후 다시 실행되기 때문이다 — 의존성에 상태를
     넣으면 화면에 머무는 동안에도 재조회가 돈다. */
  const recheckLookRef = useRef(false);
  useEffect(() => {
    recheckLookRef.current = status === 'authed' && !isDemo && lookPhase === 'unavailable';
  }, [status, isDemo, lookPhase]);
  const focusedOnceRef = useRef(false);
  useFocusEffect(
    useCallback(() => {
      // 첫 포커스는 마운트 직후라 이미 불러오고 있다.
      if (focusedOnceRef.current && recheckLookRef.current) void reloadAll();
      focusedOnceRef.current = true;
    }, [reloadAll]),
  );

  /* 옷장이 비었는지는 **실제 옷장**에 물어본다.
     홈 API 의 closet_count 는 백엔드가 아직 고정값(MOCK_CLOSET_COUNT)을 주기 때문에,
     그대로 믿으면 옷장이 텅 비어도 "42벌 있다"고 보고 추천 카드를 띄운다.
     옷장·채팅 모드 선택과 같은 출처(필터 없음)를 써서 세 화면이 늘 같은 수를 본다.
     **이 값은 이제 홈의 분기를 정하지 않는다** — 오늘의 룩은 골든셋에서 나오므로
     옷장이 비어도 성립한다(아이템이 전부 '추천 구매'인 이유). 옷장 상태는 카드 아래의
     안내 한 줄만 정한다. 데모는 토큰이 없어 401 이 나므로 아예 묻지 않는다. */
  const {
    items: closetItems,
    loading: closetLoading,
    error: closetError,
    reload: reloadCloset,
  } = useWardrobeItems({}, status === 'authed' && !isDemo);

  /* 소셜로 들어왔으면 provider 닉네임. 아직 이름이 없으면 빈 문자열이라 이름 없이 인사한다
     (lib/userProfile.ts 가 유일한 판정 자리다 — 화면마다 다르게 정하지 않는다). */
  const nickname = displayName(user);
  const photo = profilePhoto(user);

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top']} style={styles.safe}>
        <ScrollView
          showsVerticalScrollIndicator={false}
          /* 비회원·데모는 불러올 것이 없어 당겨도 반응하지 않는다. */
          refreshControl={
            status === 'authed' ? (
              <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={INK} />
            ) : undefined
          }
          contentContainerStyle={[styles.content, { paddingBottom: 24 }, contentStyle(ContentMax.card)]}>
          {/* 헤더: 인사말 + 기록/캘린더/프로필 (한 줄) */}
          <View style={styles.header}>
            <Text style={styles.greeting} numberOfLines={1}>
              {nickname ? `안녕하세요 ${nickname}님` : '안녕하세요'}
            </Text>
            <View style={styles.headerRight}>
              {/* 분석 기록은 늘 열 수 있어야 하는 진입점이라 본문이 아니라 헤더에 둔다.
                  본문에 두면 상시로 세로 공간을 먹어 오늘의 룩 카드가 밀린다. */}
              <Pressable hitSlop={10} onPress={() => router.push('/outfit-history')}>
                <Icon name="archivebox" tintColor={INK} size={24} />
              </Pressable>
              <Pressable hitSlop={10} onPress={() => router.push('/calendar')}>
                <Icon name="calendar" tintColor={INK} size={24} />
              </Pressable>
              {/* 옆의 캘린더 아이콘은 눌리는데 아바타만 안 눌리면 어긋난다 → 마이로 보낸다 */}
              <Pressable hitSlop={10} onPress={() => router.push('/my')}>
                <Avatar name={nickname} {...photo} size={40} />
              </Pressable>
            </View>
          </View>

          {/* 상태 카드는 홈이 어느 분기를 그리든 보여야 한다 — 분기 안에 넣으면
              옷장에 옷이 있는 회원은 진행 중인 분석을 볼 데가 없어진다. */}
          <HomeStatusSlot />

          {status === 'loading' ? (
            <LoadingState message="홈을 준비하는 중…" />
          ) : status === 'guest' ? (
            <EmptyClosetStart />
          ) : loading ? (
            /* 옷장 로딩은 더 이상 기다리지 않는다 — 홈의 주인공(오늘의 룩)과 무관한
               부가 정보라, 여기서 같이 묶으면 룩 카드가 옷장 응답만큼 늦게 뜬다. */
            <LoadingState message="오늘의 추천을 불러오는 중…" />
          ) : !data ? (
            /* 에러가 나도 데모 세션은 위에서 DEMO_HOME 으로 물러나 있다 —
               error 를 함께 보면 그 폴백이 무효가 되어 체험용 링크가 통째로 에러 화면이 된다. */
            <ErrorState onRetry={reload} />
          ) : (
            <HomeBody
              data={data}
              daily={dailyLook}
              phase={isDemo ? 'ready' : lookPhase}
              isDemo={isDemo}
              onRetry={reloadAll}
              closet={{
                /* 빈 옷장과 조회 실패를 구분한다. 예전에는 실패해도 items 가 []
                   라서(use-wardrobe.ts) "옷장이 비었네"로 둔갑해 온보딩 화면이 떴다. */
                empty: !closetError && !closetLoading && closetItems.length === 0,
                error: closetError,
                reload: reloadCloset,
              }}
            />
          )}
        </ScrollView>
      </SafeAreaView>
    </View>
  );
}

/**
 * **비회원 전용** 온보딩 홈. 부를 토큰도 옷장도 없으니 추천 대신 체험 경로를 준다.
 *
 * 예전에는 로그인 사용자도 옷장이 비면 여기로 왔는데, 오늘의 룩은 골든셋에서 나오므로
 * 옷장 없이도 만들어진다 — 멀쩡히 준비된 추천을 온보딩 화면이 덮고 있었다.
 * 로그인 사용자의 옷장 유도는 이제 룩 카드 아래 한 줄(HomeBody)로 내려갔다.
 *
 * 착장 분석 진행 상태는 여기 있었지만 components/home/analysis-status-card.tsx 로 옮겼다.
 * 이 분기 안에 두면 옷장에 옷이 생기는 순간 진행 중인 분석이 화면에서 사라진다.
 */
function EmptyClosetStart() {
  return (
    <View style={styles.emptyStart}>
      <View style={styles.emptyEyebrow}>
        <Text style={styles.emptyEyebrowText}>MY FIRST LOOK</Text>
      </View>
      <Text style={styles.emptyTitle}>옷장이 비어 있어도 괜찮아요</Text>
      <Text style={styles.emptyBody}>사진 한 장으로 내 스타일을 시작해 볼까요?</Text>
      <View style={styles.emptyActions}>
        <Pressable style={styles.emptyPrimary} onPress={() => router.push('/outfit-review')}>
          <Text style={styles.emptyPrimaryText} numberOfLines={1}>
            내 착장 분석하기
          </Text>
        </Pressable>
        <Pressable style={styles.emptySecondary} onPress={() => router.push('/(tabs)/lookbook')}>
          <Text style={styles.emptySecondaryText} numberOfLines={1}>
            스타일 둘러보기
          </Text>
        </Pressable>
      </View>
    </View>
  );
}

type DisplayLook = {
  image?: string | null;
  comment: string;
  tags: string[];
  /** 눌렀을 때 열 룩 상세 (constants/today-look.ts LOOK_VARIANTS) */
  variantId: string;
  /** 이 카드가 어느 골든 코디인지 — 저장·상세로 이 룩을 그대로 이어 준다(데모는 없음) */
  goldenId?: string;
};

/** 추천 결과 한 벌 → 홈 카드. 대표 룩과 '다른 룩' 후보가 같은 스키마라 함께 쓴다. */
function resultToDisplayLook(r: DailyLookResult): DisplayLook {
  /* 대표 이미지 우선순위: 정면 착용 이미지 → 원본 코디 사진(exposable 일 때만 있음)
     → 아이템 카드 첫 장. 전부 presigned URL 이라 캐시하지 않고 받은 그대로 쓴다. */
  const image =
    r.render_image_url ??
    r.outfit_image_url ??
    r.items?.find((i) => i.image_url)?.image_url ??
    null;
  /* 태그는 백엔드가 **룩북과 같은 어휘**로 만들어 내려준다(result.tags).
     예전에는 여기서 아이템 이름에 `#`만 붙였는데, 그러면 `#블랙스트레이트데님팬츠`
     같은 덩어리가 나오고 룩북 필터 칩과 어휘가 갈렸다. 비어 있으면 지어내지 않고
     태그 줄을 통째로 숨긴다(ReadyLook) — 어색한 태그보다 없는 편이 낫다. */
  const tags = (r.tags ?? []).map((t) => `#${t}`);
  return {
    image,
    /* 카드 문구는 headline — 룩 상세의 제목과 같은 값이라, 카드에서 본 문장이
       눌러서 들어간 화면의 제목으로 그대로 이어진다. 비어 있으면 근거 문장으로. */
    comment: r.headline || r.rationale_ko,
    tags,
    variantId: 'daily',
    goldenId: r.golden_id,
  };
}

/**
 * 오늘의 룩 API 응답 → 홈 카드 목록. 대표 룩이 먼저, 그 뒤가 '다른 룩' 후보다.
 *
 * 완성(SUCCEEDED) 전에는 빈 배열 — 카드는 스켈레톤으로 간다. 후보 이미지는
 * 서버가 나중에 채우므로, 처음 몇 초는 후보 카드가 아이템 사진으로 그려질 수 있다.
 * 그래도 목록에서 빼지는 않는다: 눌렀을 때 룩이 나오는 편이 버튼이 사라졌다
 * 나타나는 것보다 낫다.
 */
function toDisplayLooks(look: DailyLook | null): DisplayLook[] {
  if (look?.status !== 'SUCCEEDED' || !look.result) return [];
  return [look.result, ...(look.alternatives ?? [])].map(resultToDisplayLook);
}

/** 홈 본문 — 오늘의 룩.
 *
 * 카드의 껍데기(제목·날짜·날씨)는 어느 단계에서나 같고 속만 바뀐다. 그래야
 * 완성되는 순간 화면이 튀지 않는다. 중요한 건 **아직 없는 추천을 완성된 것처럼
 * 그리지 않는 것** — 예전에는 생성 중(수 초~수십 초)에도 기온 템플릿 문구와
 * 번들 목업 사진으로 카드를 채워, 잠시 뒤 통째로 다른 룩으로 바뀌었다.
 */
function HomeBody({
  data,
  daily,
  phase,
  isDemo,
  onRetry,
  closet,
}: {
  data: HomeData;
  daily: DailyLook | null;
  phase: DailyLookPhase;
  isDemo: boolean;
  onRetry: () => void;
  /** 옷장 상태. 홈의 분기가 아니라 카드 아래 안내 한 줄만 정한다. */
  closet: { empty: boolean; error: string | null; reload: () => void };
}) {
  const toast = useToast();
  const [idx, setIdx] = useState(0);

  const apiLooks = useMemo(() => toDisplayLooks(daily), [daily]);
  /* 데모는 진짜 추천이 없어 목업으로 그린다 — 목업이 인증 사용자 경로로 새지 않게
     데모 상수(constants/demo.ts)에만 둔다. 인증 사용자는 대표 룩 + 리트리버가 뽑은
     차순위 후보를 '다른 룩'으로 돌려본다. */
  const looks = useMemo<DisplayLook[]>(
    () => (isDemo ? DEMO_LOOKS : apiLooks),
    [isDemo, apiLooks],
  );
  const look = looks.length ? looks[idx % looks.length] : null;

  /**
   * 지금 보고 있는 룩을 내 룩북에 담고 그 목록으로 이동한다.
   *
   * 진짜 추천(daily)과 데모 목업은 담는 길이 다르다.
   * - 추천: 서버가 골든 코디를 가리키기만 한다(왕복 한 번). 표지 사진을 다시
   *   올리면 이미 가진 자산이 사용자 수만큼 복제되고 옷 추출까지 다시 돈다.
   * - 데모: 번들 목업이라 서버에 올릴 실체가 없다 — 예전처럼 이 기기에만 담는다.
   */
  const saveCurrentLook = async () => {
    if (!look) return;
    const isRealLook = !isDemo && phase === 'ready';
    /* 서버 왕복이라 끝난 뒤에 알린다 — 먼저 토스트를 띄우면 실패해도 담긴 것처럼 보인다. */
    try {
      if (isRealLook) {
        /* 지금 보고 있는 룩을 담는다 — '다른 룩'으로 돌려본 뒤 저장했는데 대표 룩이
           담기면 화면과 결과가 어긋난다. 서버가 이 id 를 오늘 후보 안에서 확인한다. */
        const { created } = await savedLookStore.saveDailyLook(look.goldenId);
        toast(created ? '내 룩북에 담았어요' : '이미 담아둔 룩이에요');
      } else {
        await savedLookStore.addLook({
          image: look.image ?? undefined,
          comment: look.comment,
          tags: look.tags,
        });
        toast('위시에 담았어요');
      }
    } catch (error) {
      toast(error instanceof Error ? error.message : '저장하지 못했어요', { variant: 'error' });
      return;
    }
    /* 담긴 갈래로 보낸다 — 담았다고 해 놓고 그 룩이 없는 목록을 열면 실패로 읽힌다.
       데모 목업은 예전처럼 위시(origin 'ai')에, 진짜 추천은 내 룩북에 선다. */
    router.push(isRealLook ? '/(tabs)/lookbook?tab=mine' : '/(tabs)/lookbook?tab=wish');
  };

  return (
    <View style={styles.lookSection}>
      <View style={styles.lookCard}>
        <View style={styles.lookMetaRow}>
          <Text style={styles.sectionTitle} numberOfLines={1}>
            오늘의 룩
          </Text>
          <Text style={styles.metaText} numberOfLines={1}>
            {todayLabel()} | {weatherLabel(data.weather)}
          </Text>
        </View>

        {phase === 'ready' && look ? (
          <ReadyLook look={look} showAnother={looks.length > 1} onAnother={() => setIdx((i) => i + 1)} onSave={saveCurrentLook} />
        ) : phase === 'pending' ? (
          <PendingLook hint={data.today_look} detail={daily?.detail} />
        ) : (
          <UnavailableLook status={daily?.status} detail={daily?.detail} onRetry={onRetry} />
        )}
      </View>

      {/* 옷장이 비었으면 채우도록 권한다 — 다만 **오늘의 룩을 가리지 않는다.**
          추천은 골든셋에서 나오므로 옷장 없이도 성립하고, 첫 화면을 온보딩으로 덮으면
          이 서비스가 무엇인지 보여줄 기회를 잃는다. 조회에 실패한 경우는 "비었다"고
          말하지 않고 다시 시도할 길을 준다. */}
      {closet.error ? (
        <Pressable style={styles.analyzeLink} onPress={closet.reload}>
          <Text style={styles.closetErrorText}>옷장을 불러오지 못했어요 · 다시 시도</Text>
          <Text style={styles.analyzeLinkArrow}>↻</Text>
        </Pressable>
      ) : closet.empty ? (
        <Pressable style={styles.analyzeLink} onPress={() => router.push('/item-add')}>
          <Text style={styles.analyzeLinkText}>옷장 채우고 내 옷으로 추천받기</Text>
          <Text style={styles.analyzeLinkArrow}>›</Text>
        </Pressable>
      ) : null}

      {/* 착장 분석 진입점은 옷장이 빈 사용자용 EmptyClosetStart 에만 있었다 — 옷을 등록하고 나면
          홈에서 들어갈 길이 사라진다. 오늘의 룩 아래 한 줄로 두어 세로 공간을 거의 안 쓰면서
          "오늘 뭐 입지" 다음에 "내가 입은 건 어때" 가 이어지게 한다. */}
      <Pressable style={styles.analyzeLink} onPress={() => router.push('/outfit-review')}>
        <Text style={styles.analyzeLinkText}>내 착장 분석하기</Text>
        <Text style={styles.analyzeLinkArrow}>›</Text>
      </Pressable>
    </View>
  );
}

/** 완성된 추천. */
function ReadyLook({
  look,
  showAnother,
  onAnother,
  onSave,
}: {
  look: DisplayLook;
  showAnother: boolean;
  onAnother: () => void;
  onSave: () => void;
}) {
  return (
    <>
      {/* 상세도 카드가 보여 준 그 룩을 연다 — golden 이 없으면(데모) 대표 룩이다. */}
      <Pressable
        onPress={() =>
          router.push(
            `/look-detail?id=${look.variantId}${look.goldenId ? `&golden=${encodeURIComponent(look.goldenId)}` : ''}`,
          )
        }>
        {/* 사진이 없으면 SmartImage 가 자리만 잡는다 — 다른 룩의 목업 사진을 빌려
            쓰면 문구와 사진이 어긋나 placeholder 보다 나쁜 화면이 된다. */}
        <SmartImage uri={look.image} width="100%" aspectRatio={LOOK_IMAGE_RATIO} radius={0} contentFit="cover" />
      </Pressable>
      <View style={styles.lookBody}>
        <Text style={styles.lookText} numberOfLines={2}>
          {look.comment}
        </Text>
        {/* 태그가 없으면 줄 자체를 없앤다 — 빈 ScrollView 를 두면 gap 만큼 허공이 남는다. */}
        {look.tags.length ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.tagRow}>
            {look.tags.map((t) => (
              <View key={t} style={styles.tag}>
                <Text style={styles.tagText}>{t}</Text>
              </View>
            ))}
          </ScrollView>
        ) : null}
        <View style={styles.lookButtons}>
          <Pressable style={styles.saveBtn} onPress={onSave}>
            <Text style={styles.saveBtnText}>저장</Text>
          </Pressable>
          {/* 돌려볼 대상이 하나뿐이면 버튼을 숨긴다 — 눌러도 같은 룩이 나오면
              버튼 이름('다른 룩')이 거짓말이 된다. */}
          {showAnother ? (
            <Pressable style={styles.altBtn} onPress={onAnother}>
              <Text style={styles.altBtnText}>다른 룩</Text>
            </Pressable>
          ) : null}
        </View>
        {/* '다른 룩'은 오늘 함께 뽑아 둔 후보를 돌려보는 것이지 새로 만드는 게 아니다.
            그 차이를 적어 두지 않으면 눌러도 안 바뀌는 고장으로 읽힌다. */}
        <Text style={styles.onceADay}>{DAILY_LOOK_ONCE_A_DAY}</Text>
      </View>
    </>
  );
}

/**
 * 만드는 중. 사진 자리는 스켈레톤으로 비워 둔다.
 *
 * 홈 API 의 기온 템플릿(hint)은 여기서 **추천이 아니라 힌트로만** 쓴다 —
 * "지금 서울 25도, 이런 옷을 찾고 있어요" 정도. 이 문구를 추천 카드 본문 자리에
 * 그대로 올리면 사용자는 그게 오늘의 추천인 줄 알고, 몇 초 뒤 바뀌는 걸 본다.
 */
function PendingLook({ hint, detail }: { hint: HomeData['today_look']; detail?: string | null }) {
  return (
    <>
      <View style={styles.skeletonImage}>
        <Skeleton width="100%" height="100%" radius={0} />
      </View>
      <View style={styles.lookBody}>
        <View style={styles.pendingHead}>
          <ActivityIndicator size="small" color={Editorial.selected} />
          <Text style={styles.pendingTitle}>오늘의 룩을 만들고 있어요</Text>
        </View>
        <Text style={styles.pendingBody}>
          {detail ?? '체형·취향과 오늘 날씨를 맞춰보는 중이에요. 잠시만 기다려주세요.'}
        </Text>
        {hint.comment ? <Text style={styles.pendingHint}>{hint.comment}</Text> : null}
        {hint.tags.length ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.tagRow}>
            {hint.tags.map((t) => (
              <View key={t} style={[styles.tag, styles.hintTag]}>
                <Text style={styles.tagText}>{t}</Text>
              </View>
            ))}
          </ScrollView>
        ) : null}
      </View>
    </>
  );
}

/**
 * 준비하지 못함. EMPTY 와 FAILED 는 사용자가 할 일이 달라서 문구·버튼을 나눈다
 * (후보 없음 → 프로필을 채워야 하고, 실패 → 다시 시도하면 된다).
 */
function UnavailableLook({
  status,
  detail,
  onRetry,
}: {
  status?: DailyLook['status'];
  detail?: string | null;
  onRetry: () => void;
}) {
  const empty = status === 'EMPTY';
  return (
    <View style={[styles.lookBody, styles.unavailable]}>
      <Text style={styles.pendingTitle}>
        {empty ? '오늘 추천할 룩을 찾지 못했어요' : '오늘의 룩을 준비하지 못했어요'}
      </Text>
      {/* EMPTY 는 서버 detail 을 쓰지 않는다 — 같은 말을 다르게 적은 문장이라 두 번 읽힌다. */}
      <Text style={styles.pendingBody}>
        {empty ? DAILY_LOOK_EMPTY_RETRY : (detail ?? '잠시 뒤 다시 시도해 주세요.')}
      </Text>
      <Pressable
        style={styles.unavailableBtn}
        /* edit-profile 은 표시 이름만 바꾸는 모달이라 여기서 열면 채울 것이 없다.
           체형·추구미 입력 진입점이 모인 마이 탭으로 보낸다. */
        onPress={() => (empty ? router.push('/(tabs)/my') : onRetry())}>
        <Text style={styles.unavailableBtnText}>{empty ? '프로필 채우기' : '다시 시도'}</Text>
      </Pressable>
      {/* EMPTY 안내는 버튼 위 한 줄로 끝난다 — '내일 도착'은 EMPTY 에는 해당하지 않는다. */}
      {empty ? null : <Text style={styles.onceADay}>{DAILY_LOOK_ONCE_A_DAY}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  safe: { flex: 1 },
  content: {
    paddingHorizontal: 20,
    paddingTop: 16,
    gap: 24,
  },
  lookSection: { gap: 14 },

  // 헤더
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: 12,
  },
  greeting: { flex: 1, fontFamily: Fonts.serif, fontSize: 18, fontWeight: '500', color: INK },
  headerRight: { flexDirection: 'row', alignItems: 'center', gap: 14, flexShrink: 0 },

  emptyStart: {
    borderRadius: 28,
    backgroundColor: CHIP,
    borderWidth: 1, borderColor: Editorial.line,
    paddingHorizontal: 28,
    paddingVertical: 34,
    alignItems: 'flex-start',
  },
  emptyEyebrow: { paddingBottom: 16 },
  emptyEyebrowText: { fontSize: 10, letterSpacing: 1.7, fontWeight: '600', color: Editorial.textCaption },
  emptyTitle: { fontFamily: Fonts.serif, fontSize: 28, lineHeight: 36, color: INK },
  emptyBody: { marginTop: 14, fontSize: 16, lineHeight: 24, color: Editorial.textCaption },
  // 두 버튼을 한 줄에 나란히. flex:1 로 폭을 반씩 나눠 가진다.
  emptyActions: { marginTop: 28, alignSelf: 'stretch', flexDirection: 'row', gap: 10 },
  emptyPrimary: {
    flex: 1,
    height: 50,
    borderRadius: 999,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Editorial.cta,
  },
  emptyPrimaryText: { color: '#ffffff', fontSize: 14, fontWeight: '600' },
  emptySecondary: {
    flex: 1,
    height: 50,
    borderRadius: 999,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Editorial.surface,
    borderWidth: 1,
    borderColor: ink(0.14),
  },
  emptySecondaryText: { fontSize: 14, fontWeight: '600', color: Editorial.textSoft },
  lookMetaRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 16,
    paddingHorizontal: 24,
    paddingTop: 14,
    paddingBottom: 10,
  },
  sectionTitle: { flexShrink: 0, fontSize: 15, fontWeight: '500', color: INK },
  metaText: {
    flexShrink: 1,
    fontSize: 13,
    color: Editorial.textCaption,
    textAlign: 'right',
  },

  // 오늘의 룩 카드
  lookCard: {
    flexShrink: 0,
    alignSelf: 'stretch',
    backgroundColor: Editorial.surface,
    borderWidth: 1,
    borderColor: ink(0.1),
    borderRadius: 28,
    overflow: 'hidden',
  },
  lookBody: { flexShrink: 0, padding: 24, gap: 16 },
  /* 사진 자리를 비율로 잡아둔다 — 완성됐을 때 들어올 SmartImage 와 같은 틀이라
     추천이 도착해도 카드 높이가 튀지 않는다. */
  skeletonImage: { width: '100%', aspectRatio: LOOK_IMAGE_RATIO },
  pendingHead: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  pendingTitle: { fontSize: 17, fontWeight: '600', color: Editorial.ink },
  pendingBody: { fontSize: 14, lineHeight: 21, color: Editorial.textCaption },
  /* 기온 템플릿은 추천이 아니라 힌트다 — 본문보다 한 단계 낮춰 그린다. */
  pendingHint: { fontSize: 13, lineHeight: 20, color: Editorial.textSoft, fontStyle: 'italic' },
  hintTag: { backgroundColor: Editorial.surface, borderWidth: 1, borderColor: Editorial.line },
  unavailable: { alignItems: 'flex-start' },
  unavailableBtn: {
    height: 44,
    paddingHorizontal: 22,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  unavailableBtnText: { color: '#ffffff', fontSize: 14, fontWeight: '600' },
  /* 규칙 안내는 추천 본문보다 한 단계 낮게 — 읽히되 룩을 가리지 않는다. */
  onceADay: { fontSize: 12, lineHeight: 18, color: Editorial.textSoft },
  lookText: { fontSize: 17, fontWeight: '500', color: Editorial.ink },
  tagRow: { flexDirection: 'row', gap: 8, alignItems: 'center' },
  tag: {
    backgroundColor: Editorial.control,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  tagText: { fontSize: 12, fontWeight: '500', color: Editorial.textCaption },
  lookButtons: { flexDirection: 'row', gap: 10, marginTop: 4 },
  saveBtn: {
    flex: 1,
    height: 44,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  saveBtnText: { color: '#ffffff', fontSize: 14, fontWeight: '500' },
  altBtn: {
    flex: 1,
    height: 44,
    borderRadius: 999,
    backgroundColor: Editorial.surface,
    borderWidth: 1,
    borderColor: ink(0.14),
    alignItems: 'center',
    justifyContent: 'center',
  },
  altBtnText: { color: Editorial.textSoft, fontSize: 14, fontWeight: '500' },

  // 카드가 아니라 한 줄 링크 — 오늘의 룩이 홈의 주인공 자리를 유지하게 한다
  analyzeLink: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 4,
    paddingVertical: 12,
  },
  analyzeLinkText: { fontSize: 14, fontWeight: '600', color: Editorial.textSoft },
  /* 실패는 권유가 아니다 — 같은 줄 형태를 쓰되 색으로 성격을 구분한다. */
  closetErrorText: { fontSize: 14, fontWeight: '600', color: Editorial.wine },
  analyzeLinkArrow: { fontSize: 20, color: Editorial.textCaption },
});
