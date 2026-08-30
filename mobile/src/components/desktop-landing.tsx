import { router } from 'expo-router';
import { useEffect, useState } from 'react';
import { Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { SmartImage } from '@/components/ui';
import { Editorial, Fonts, ink } from '@/constants/theme';

const CONTENT_MAX = 1120;

/** 좌우 여백(40*2)까지 감안해 콘텐츠가 눌리기 시작하는 폭. 이하에선 히어로를 세로로 쌓는다. */
const NARROW = CONTENT_MAX + 80;

/** 기능 카드 3열이 읽기 어려워지는 폭. 이하에선 카드도 세로로 쌓는다(폰으로 링크를 열었을 때). */
const STACKED = 760;

/** 제목 굵기. 한 곳에서 조절한다 — '400'(기본) / '600'(semibold) / '700'(bold). */
const HEADING_WEIGHT = '600' as const;

/* 발표 중 외부 프록시(images.weserv.nl) 장애로 히어로가 비는 걸 막기 위해 번들 에셋 사용. */
const HERO_IMAGE = require('../../assets/images/mock/closet.jpg');

/** 온보딩과 동일한 3가지 가치 제안 — 카피/이미지 재사용으로 톤 일관성 유지. */
const FEATURES = [
  {
    kicker: 'AI STYLIST',
    image: require('../../assets/images/mock/stylist.jpg'),
    title: '매일 아침, 오늘의 룩',
    body: '날씨·일정·취향을 반영한 오늘의 코디.',
  },
  {
    kicker: 'YOUR CLOSET',
    image: require('../../assets/images/mock/closet.jpg'),
    title: '내 옷장을 그대로',
    body: '사진 한 장으로 끝내는 옷장 정리.',
  },
  {
    kicker: 'FITTING',
    image: require('../../assets/images/mock/fitting.jpg'),
    title: '입기 전에 먼저 확인',
    body: '체형 측정과 가상 착장으로 미리 맞추는 핏.',
  },
] as const;

/**
 * 데스크톱('진짜 웹') 랜딩 페이지. "/landing" 라우트에서만 렌더된다.
 * 마운트 동안 body 에 풀와이드 클래스를 붙여 폰 프레임(global.css 440 캡)을 해제한다.
 */
export function DesktopLanding() {
  /* 정적 프리렌더 시점엔 폭을 알 수 없으므로 0(=데스크톱 기본)으로 두고, 마운트 직후 실제 폭으로
     보정한다. useWindowDimensions 는 하이드레이션 후 재측정을 보장하지 않아 프리렌더 배치가
     그대로 굳는 경우가 있어 직접 읽는다. */
  const [width, setWidth] = useState(0);

  useEffect(() => {
    if (Platform.OS !== 'web' || typeof window === 'undefined') return;
    const measure = () => setWidth(window.innerWidth);
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, []);

  const measured = width > 0;
  const isNarrow = measured && width < NARROW;
  const isStacked = measured && width < STACKED;

  useEffect(() => {
    if (Platform.OS !== 'web' || typeof document === 'undefined') return;
    document.body.classList.add('cozy-landing-fullwidth');
    return () => document.body.classList.remove('cozy-landing-fullwidth');
  }, []);

  /* 랜딩에서 앱으로 들어가는 통로. push 로 두면 랜딩이 스택에 남아 unmount 되지 않고,
     위 effect 의 cleanup 이 실행되지 않아 풀와이드 클래스가 걸린 채 앱이 뜬다 → replace 를 쓸 것. */
  const enterApp = () => router.replace('/onboarding');

  return (
    <ScrollView style={styles.page} contentContainerStyle={styles.pageContent}>
      {/* 상단 바 */}
      <View style={styles.nav}>
        <View style={styles.navInner}>
          <Text style={styles.brand}>cozy</Text>
        </View>
      </View>

      {/* 히어로 */}
      <View style={styles.section}>
        <View style={[styles.inner, styles.hero, isNarrow && styles.heroNarrow]}>
          <View style={[styles.heroText, isNarrow && styles.heroTextNarrow]}>
            <Text style={styles.heroKicker}>AI 패션 클로젯</Text>
            <Text style={[styles.heroTitle, isNarrow && styles.heroTitleNarrow]}>
              옷장에서 시작하는{'\n'}오늘의 스타일
            </Text>
            <Text style={styles.heroBody}>
              날씨와 취향을 읽어 완성하는 오늘의 코디
            </Text>
            <View style={styles.heroActions}>
              <Pressable style={styles.primaryCta} onPress={enterApp}>
                <Text style={styles.primaryCtaText}>앱 둘러보기</Text>
              </Pressable>
            </View>
          </View>
          <View style={[styles.heroVisual, isNarrow && styles.heroVisualNarrow]}>
            <SmartImage asset={HERO_IMAGE} height={440} radius={28} contentFit="cover" />
          </View>
        </View>
      </View>

      {/* 기능 3종 */}
      <View style={[styles.section, styles.featuresSection]}>
        <View style={styles.inner}>
          <Text style={styles.sectionKicker}>WHAT COZY DOES</Text>
          <View style={[styles.featureGrid, isStacked && styles.featureGridStacked]}>
            {FEATURES.map((f) => (
              <View key={f.kicker} style={styles.featureCard}>
                {/* 카드 폭이 화면에 따라 달라지므로 고정 높이 대신 비율로 정사각형을 유지한다. */}
                <SmartImage asset={f.image} aspectRatio={1} radius={20} contentFit="cover" />
                <Text style={styles.featureKicker}>{f.kicker}</Text>
                <Text style={styles.featureTitle}>{f.title}</Text>
                <Text style={styles.featureBody}>{f.body}</Text>
              </View>
            ))}
          </View>
        </View>
      </View>

      {/* CTA 배너 */}
      <View style={styles.section}>
        <View style={[styles.inner, styles.ctaBand]}>
          <Pressable style={styles.ctaBandButton} onPress={enterApp}>
            <Text style={styles.ctaBandButtonText}>앱 둘러보기</Text>
          </Pressable>
        </View>
      </View>

      {/* 푸터 */}
      <View style={styles.footer}>
        <View style={[styles.inner, styles.footerInner]}>
          <Text style={styles.footerBrand}>cozy</Text>
          <Text style={styles.footerText}>SKN28-FINAL-1Team · AI 개인화 패션 추천 서비스</Text>
        </View>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: Editorial.surface },
  pageContent: { paddingBottom: 0 },
  inner: { width: '100%', maxWidth: CONTENT_MAX, marginHorizontal: 'auto' },

  nav: { borderBottomWidth: 1, borderBottomColor: ink(0.06), backgroundColor: Editorial.surface },
  navInner: {
    width: '100%',
    maxWidth: CONTENT_MAX,
    marginHorizontal: 'auto',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 40,
    paddingVertical: 20,
  },
  brand: { fontFamily: Fonts.serif, fontSize: 26, color: Editorial.ink },

  section: { paddingHorizontal: 40 },

  hero: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 56,
    paddingVertical: 88,
  },
  /* 1200 미만에선 2단 배치가 눌려 제목이 깨진다 → 세로로 쌓는다. */
  heroNarrow: { flexDirection: 'column', alignItems: 'stretch', gap: 44, paddingVertical: 64 },
  heroText: { flex: 1, gap: 22 },
  /* 세로 배치에선 flex:1이 남는 높이를 전부 먹어 이미지를 찌그러뜨린다 → 콘텐츠 높이로 되돌린다.
     flex:0 은 flexBasis:0% 까지 걸려 블록이 높이 0으로 붕괴하므로 basis 를 auto 로 명시한다. */
  heroTextNarrow: { flexGrow: 0, flexShrink: 0, flexBasis: 'auto' },
  heroKicker: { fontSize: 13, letterSpacing: 3, color: Editorial.textCaption, fontWeight: '600' },
  heroTitle: {
    fontFamily: Fonts.serif,
    fontSize: 52,
    /* 두 줄 사이 간격. 폰트 크기의 1.3배 정도가 제목이 붙어 보이지 않는 하한. */
    lineHeight: 68,
    color: Editorial.ink,
    fontWeight: HEADING_WEIGHT,
  },
  heroTitleNarrow: { fontSize: 42, lineHeight: 56 },
  heroBody: { fontSize: 17, lineHeight: 27, color: Editorial.textCaption, maxWidth: 460 },
  heroActions: { gap: 12, marginTop: 28 },
  primaryCta: {
    alignSelf: 'flex-start',
    height: 54,
    paddingHorizontal: 34,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  primaryCtaText: { color: Editorial.white, fontSize: 16, fontWeight: '600' },
  heroVisual: { flex: 1, maxWidth: 520 },
  heroVisualNarrow: { flexGrow: 0, flexShrink: 0, flexBasis: 'auto', maxWidth: '100%' },

  /* surfaceSoft 가 흰 배경과 거의 구분되지 않아, 히어로와의 경계를 얇은 선으로 한 번 끊어준다. */
  featuresSection: {
    backgroundColor: Editorial.surfaceSoft,
    paddingVertical: 88,
    borderTopWidth: 1,
    borderTopColor: ink(0.07),
  },
  sectionKicker: {
    fontSize: 12,
    letterSpacing: 2.5,
    color: Editorial.textCaption,
    fontWeight: '600',
    textAlign: 'center',
  },
  sectionTitle: {
    fontFamily: Fonts.serif,
    fontSize: 34,
    color: Editorial.ink,
    textAlign: 'center',
    marginTop: 12,
    fontWeight: HEADING_WEIGHT,
  },
  featureGrid: { flexDirection: 'row', gap: 28, marginTop: 48 },
  featureGridStacked: { flexDirection: 'column', gap: 40 },
  featureCard: { flex: 1, gap: 10 },
  featureKicker: { fontSize: 11, letterSpacing: 2, color: Editorial.textCaption, fontWeight: '600', marginTop: 16 },
  featureTitle: {
    fontFamily: Fonts.serif,
    fontSize: 22,
    color: Editorial.ink,
    marginTop: 2,
    fontWeight: HEADING_WEIGHT,
  },
  featureBody: { fontSize: 15, lineHeight: 23, color: Editorial.textCaption },

  ctaBand: {
    alignItems: 'center',
    gap: 14,
    paddingVertical: 96,
  },
  ctaTitle: {
    fontFamily: Fonts.serif,
    fontSize: 36,
    color: Editorial.ink,
    textAlign: 'center',
    fontWeight: HEADING_WEIGHT,
  },
  ctaBody: { fontSize: 16, color: Editorial.textCaption, textAlign: 'center' },
  ctaBandButton: {
    marginTop: 12,
    height: 54,
    paddingHorizontal: 40,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  ctaBandButtonText: { color: Editorial.white, fontSize: 16, fontWeight: '600' },

  footer: { borderTopWidth: 1, borderTopColor: ink(0.06), backgroundColor: Editorial.surface },
  footerInner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 40,
    paddingVertical: 36,
  },
  footerBrand: { fontFamily: Fonts.serif, fontSize: 20, color: Editorial.ink },
  footerText: { fontSize: 13, color: Editorial.textCaption },
});
