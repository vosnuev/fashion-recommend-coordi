import { router } from 'expo-router';
import { useRef, useState } from 'react';
import {
  NativeScrollEvent,
  NativeSyntheticEvent,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { SmartImage } from '@/components/ui';
import { Editorial, ink, Fonts } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';

const INK = Editorial.ink;

const VISUAL_H = 320;
const DESK_VISUAL_H = 340;

/** 온보딩 슬라이드 이미지 — 랜딩(/landing)과 같은 3장을 공유한다.
 *  교체하려면 assets/images/mock/ 의 파일을 바꾸면 된다. */
const ONBOARDING_IMAGES = {
  stylist: require('../../assets/images/mock/stylist.jpg'),
  closet: require('../../assets/images/mock/closet.jpg'),
  fitting: require('../../assets/images/mock/fitting.jpg'),
} as const;

const SLIDES = [
  {
    kicker: 'AI STYLIST',
    image: ONBOARDING_IMAGES.stylist,
    title: '매일 아침, 오늘의 룩을 골라드려요',
    body: '날씨·일정·취향을 반영해 AI가 코디를 제안해요.',
  },
  {
    kicker: 'YOUR CLOSET',
    image: ONBOARDING_IMAGES.closet,
    title: '내 옷장을 그대로 옮겨보세요',
    body: '사진 한 장이면 카테고리·색상·소재를 자동으로 정리해요.',
  },
  {
    kicker: 'FITTING',
    image: ONBOARDING_IMAGES.fitting,
    title: '입어보기 전에 먼저 확인하세요',
    body: '체형 측정으로 사이즈를 맞추고 가상 착장으로 미리 볼 수 있어요.',
  },
];

// A2 온보딩 — 3장 소개. 모바일은 스와이프 캐러셀, 데스크톱은 3장을 한 화면에.
export default function Onboarding() {
  // 슬라이드 폭 = 창 폭(폰 프레임 상한 적용). 웹이 SPA 라 브라우저에서 항상 정확히 측정된다.
  const { frameWidth: width, isDesktop } = useBreakpoint();
  /* 가로 스와이프 캐러셀이라 폰/태블릿에선 폰 폭으로 묶어야 한 장씩 넘어간다.
     데스크톱에선 캐러셀 대신 3장을 나란히 펼치므로 이 상한을 풀고 넓게 쓴다. */
  const frame = { width: '100%' as const, maxWidth: width, alignSelf: 'center' as const };

  const [page, setPage] = useState(0);
  const scrollRef = useRef<ScrollView>(null);

  const onScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const p = Math.round(e.nativeEvent.contentOffset.x / width);
    if (p !== page) setPage(p);
  };

  const isLast = page === SLIDES.length - 1;
  const goLogin = () => router.push('/login');
  const next = () => {
    if (isLast) goLogin();
    else scrollRef.current?.scrollTo({ x: width * (page + 1), animated: true });
  };

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top', 'bottom']} style={styles.safe}>
        {/* 상단: 워드마크 + 건너뛰기 */}
        <View style={[styles.top, isDesktop ? styles.wide : frame]}>
          <Text style={styles.brand}>cozy</Text>
          <Pressable hitSlop={10} onPress={goLogin}>
            <Text style={styles.skip}>건너뛰기</Text>
          </Pressable>
        </View>

        {isDesktop ? (
          // 데스크톱: 3장을 한 화면에 나란히 (캐러셀·도트 없음)
          <View style={styles.deskMain}>
            <View style={styles.deskRow}>
              {SLIDES.map((s) => (
                <View key={s.kicker} style={styles.deskCard}>
                  <SmartImage asset={s.image} height={DESK_VISUAL_H} radius={20} contentFit="cover" />
                  <Text style={styles.kicker}>{s.kicker}</Text>
                  <Text style={styles.deskTitle}>{s.title}</Text>
                  <Text style={styles.deskBody}>{s.body}</Text>
                </View>
              ))}
            </View>
          </View>
        ) : (
          <ScrollView
            style={frame}
            ref={scrollRef}
            horizontal
            pagingEnabled
            showsHorizontalScrollIndicator={false}
            onScroll={onScroll}
            scrollEventThrottle={16}>
            {SLIDES.map((s) => (
              <View key={s.kicker} style={[styles.slide, { width }]}>
                <SmartImage asset={s.image} height={VISUAL_H} radius={24} contentFit="cover" />
                <Text style={styles.kicker}>{s.kicker}</Text>
                <Text style={styles.title} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.85}>
                  {s.title}
                </Text>
                <Text style={styles.body} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.85}>
                  {s.body}
                </Text>
              </View>
            ))}
          </ScrollView>
        )}

        {/* 하단: (모바일만) 도트 + CTA */}
        <View style={[styles.bottom, isDesktop ? styles.bottomWide : frame]}>
          {!isDesktop && (
            <View style={styles.dots}>
              {SLIDES.map((s, i) => (
                <View key={s.kicker} style={[styles.dot, i === page && styles.dotOn]} />
              ))}
            </View>
          )}
          <Pressable style={styles.cta} onPress={isDesktop ? goLogin : next}>
            <Text style={styles.ctaText}>{isDesktop || isLast ? '시작하기' : '다음'}</Text>
          </Pressable>
          <Pressable style={styles.secondary} onPress={goLogin}>
            <Text style={styles.secondaryText}>이미 계정이 있어요</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  safe: { flex: 1 },

  top: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 30,
    paddingTop: 8,
  },
  /** 데스크톱에서 상단·하단을 본문(3장)과 같은 폭으로 가운데 정렬 */
  wide: { width: '100%', maxWidth: 1080, alignSelf: 'center', paddingHorizontal: 40, paddingTop: 12 },
  brand: { fontFamily: Fonts.serif, fontSize: 22, color: INK },
  skip: { fontSize: 13, color: Editorial.textCaption },

  slide: { paddingHorizontal: 40, paddingTop: 24, alignItems: 'flex-start' },
  kicker: { fontSize: 11, letterSpacing: 2, color: Editorial.textCaption, fontWeight: '600', marginTop: 26 },
  title: { fontFamily: Fonts.serif, fontSize: 22, color: INK, lineHeight: 28, marginTop: 12 },
  body: { fontSize: 13, color: Editorial.textCaption, lineHeight: 18, marginTop: 10 },

  // 데스크톱 3열
  deskMain: { flex: 1, justifyContent: 'center', alignItems: 'center', paddingHorizontal: 40 },
  deskRow: { flexDirection: 'row', gap: 28, width: '100%', maxWidth: 1080, justifyContent: 'center' },
  deskCard: { flex: 1, maxWidth: 330, alignItems: 'flex-start' },
  deskTitle: { fontFamily: Fonts.serif, fontSize: 20, color: INK, lineHeight: 27, marginTop: 12 },
  deskBody: { fontSize: 14, color: Editorial.textCaption, lineHeight: 20, marginTop: 10 },

  bottom: { paddingHorizontal: 30, paddingBottom: 8 },
  bottomWide: { width: '100%', maxWidth: 420, alignSelf: 'center', paddingBottom: 16, marginTop: 36 },
  dots: { flexDirection: 'row', gap: 6, justifyContent: 'center', marginBottom: 22 },
  dot: { width: 6, height: 6, borderRadius: 3, backgroundColor: ink(0.15) },
  dotOn: { width: 20, backgroundColor: Editorial.selected },
  cta: {
    height: 52,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  ctaText: { color: '#ffffff', fontSize: 15, fontWeight: '500' },
  secondary: { alignSelf: 'center', marginTop: 16, paddingVertical: 4 },
  secondaryText: { fontSize: 13, color: Editorial.textCaption },
});
