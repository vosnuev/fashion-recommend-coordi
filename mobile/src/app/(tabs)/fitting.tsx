import { Icon } from '@/components/icon';
import { DetailTwoPane } from '@/components/detail-two-pane';
import { ErrorState, LoadingState, SmartImage, useToast } from '@/components/ui';
import { Editorial, ink, Fonts } from '@/constants/theme';
import { dailyLookToVariant } from '@/constants/today-look';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { useDailyLook } from '@/hooks/use-daily-look';
import { useVirtualTryOn } from '@/hooks/use-virtual-try-on';
import { goBack } from '@/lib/goBack';
import { pickBodyPhoto } from '@/lib/pickItemPhoto';
import { dailyLookPhase } from '@/lib/dailyLookApi';
import { isVirtualTryOnPending } from '@/lib/virtualTryOnApi';
import { router, useLocalSearchParams } from 'expo-router';
import { useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

const INK = Editorial.ink;
const CANVAS = '#f5f1ea';

export default function Fitting() {
  /* golden = 룩 상세에서 보고 있던 룩('다른 룩'으로 돌려본 후보일 수 있다).
     이 값이 없으면 서버는 대표 룩을 입힌다. */
  const { lookId, golden } = useLocalSearchParams<{ lookId?: string; golden?: string }>();
  const { contentStyle, width } = useBreakpoint();
  const { look: dailyLook, stalled: dailyStalled } = useDailyLook(Boolean(lookId));
  /* **목업으로 물러나지 않는다.** 예전에는 `?? TODAY_LOOK` 이 걸려 있어, 조회 전이나
     실패했을 때 '적용되는 추천 룩' 자리에 번들 목업의 옷이 섰다 — 내 룩을 입어보러
     온 화면에서 내 것이 아닌 구성을 보게 된다. 없으면 없다고 말한다(아래 early return). */
  const look = useMemo(() => dailyLookToVariant(dailyLook, golden), [dailyLook, golden]);
  const lookPhase = dailyLookPhase(dailyLook, dailyStalled);
  /* 접수·폴링·재진입 복원을 훅이 맡는다. 화면에 들어오면 먼저 조회하므로,
     나갔다 온 사용자는 사진을 다시 고르지 않아도 생성 중·완성 결과를 그대로 본다. */
  const {
    job,
    loading: restoring,
    submitting,
    stalled: tryOnStalled,
    submit,
  } = useVirtualTryOn(lookId, golden);
  const toast = useToast();
  const maxW = width >= 1280 ? 960 : 720;

  const resultUri = job?.status === 'SUCCEEDED' ? job.image_url : null;
  /* 만드는 중 — 사진을 올리는 동안과 워커가 만드는 동안을 한 상태로 묶는다.
     사용자에게는 "요청했고 기다린다" 하나의 일이다. */
  const generating = submitting || (isVirtualTryOnPending(job) && !tryOnStalled);

  const generate = async () => {
    if (!lookId) {
      toast('추천 룩 정보를 찾을 수 없어요.', { variant: 'error' });
      return;
    }
    const personUri = await pickBodyPhoto();
    if (!personUri) return;

    try {
      /* 보고 있던 그 룩을 입힌다 — golden 을 빼면 서버가 대표 룩을 입혀,
         화면의 구성과 결과가 어긋난다. */
      await submit(personUri);
    } catch (error) {
      toast(error instanceof Error ? error.message : '가상 착장을 요청하지 못했어요.', {
        variant: 'error',
      });
    }
  };

  const header = (
    <SafeAreaView edges={['top']} style={styles.headerSafe}>
      <View style={[styles.header, contentStyle(maxW)]}>
        <Pressable hitSlop={12} onPress={() => goBack('/look-detail')}>
          <Icon name="chevron.left" tintColor={INK} size={20} />
        </Pressable>
        <Text style={styles.headerTitle}>가상 피팅</Text>
      </View>
    </SafeAreaView>
  );

  /* 입힐 룩이 없으면 화면을 그리지 않는다. 예전에는 목업으로 메워, 눌러도 실패만
     하는 버튼과 남의 옷 목록이 함께 떠 있었다.

     - lookId 없음: 룩 상세를 거치지 않고 /fitting 을 직접 연 경우(웹 주소 입력 등).
       이 화면은 "어떤 룩을 입힐지"를 스스로 정하지 않는다 — 상세에서 고른 룩을 받는다.
     - 조회 전/생성 중: 로딩. 완성되면 그대로 이어진다.
     - 후보 없음·실패: 입힐 대상이 없다는 뜻이라 상세로 돌려보낸다. */
  if (!lookId || !look) {
    return (
      <View style={styles.container}>
        {header}
        {lookId && lookPhase === 'pending' ? (
          <LoadingState message={'추천 룩을 불러오는 중이에요…'} />
        ) : (
          <ErrorState
            title="입어볼 추천 룩이 없어요"
            description="오늘의 룩 상세에서 '가상으로 입어보기'를 눌러 주세요."
            onRetry={() => router.replace('/look-detail?id=daily')}
            retryLabel="오늘의 룩 보기"
            retryIcon="sparkles"
          />
        )}
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {header}

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={[styles.content, contentStyle(maxW)]}>
        <DetailTwoPane
          image={
            <View style={styles.canvas}>
              {restoring ? (
                /* 재진입 복원 — 이미 만들어 둔 것이 있는지 먼저 확인한다. */
                <LoadingState message={'가상 피팅을 불러오는 중이에요…'} />
              ) : generating ? (
                <>
                  <LoadingState
                    message={job?.detail ?? '가상 피팅 이미지를 생성 중입니다.'}
                  />
                  {/* 만드는 곳은 서버다. 이 화면을 벗어나도 계속 만들어지고,
                      다시 들어오면 이어서 보인다는 것을 알려 준다. */}
                  <Text style={styles.canvasGuide}>
                    다른 화면을 봐도 괜찮아요. 완성되면 여기서 다시 볼 수 있어요.
                  </Text>
                </>
              ) : resultUri ? (
                <>
                  <SmartImage
                    uri={resultUri}
                    width="100%"
                    radius={0}
                    contentFit="cover"
                    style={StyleSheet.absoluteFill}
                  />
                  <View style={styles.canvasBadge}>
                    <Icon name="figure.stand" tintColor="#fff" size={12} />
                    <Text style={styles.canvasBadgeText}>내 체형 반영</Text>
                  </View>
                </>
              ) : (
                <>
                  <Icon name="figure.stand" tintColor={ink(0.45)} size={42} />
                  <Text style={styles.canvasTitle}>
                    {tryOnStalled ? '아직 만들고 있어요' : '내 전신 사진으로 입어보기'}
                  </Text>
                  <Text style={styles.canvasGuide}>
                    {tryOnStalled
                      ? '생각보다 오래 걸리고 있어요. 잠시 뒤 다시 들어오면 보일 거예요.'
                      : job?.status === 'FAILED'
                        ? (job.detail ?? '만들지 못했어요. 다시 시도해 주세요.')
                        : /* 마네킹이 아니라 사진 속 본인에게 입힌다는 것을 먼저 말한다 —
                             무엇이 나올지 알고 사진을 고르게 된다. 사진은 워커가 읽어야 해서
                             잠시 저장되므로 "저장하지 않는다"고 적으면 실제와 다른 약속이 된다. */
                          '사진 속 내 모습 그대로 이 코디를 입혀 드려요. 사진은 만드는 데만 쓰고 자동으로 지워져요.'}
                  </Text>
                  <Pressable
                    style={[styles.photoBtn, submitting && styles.btnDisabled]}
                    disabled={submitting}
                    onPress={generate}>
                    <Text style={styles.photoBtnText}>
                      {job?.status === 'FAILED' ? '다시 시도' : '사진 선택하고 입어보기'}
                    </Text>
                  </Pressable>
                </>
              )}
            </View>
          }
          details={
            <View style={styles.body}>
              <Text style={styles.title}>{look.title}</Text>
              <Text style={styles.subtitle}>{look.subtitle}</Text>
              <Text style={styles.sectionTitle}>적용되는 추천 룩</Text>
              <View style={styles.thumbRow}>
                {look.pieces.map((piece) => (
                  <View key={piece.slot} style={styles.thumbCol}>
                    <SmartImage
                      uri={piece.image}
                      width="100%"
                      aspectRatio={1}
                      radius={12}
                      contentFit="cover"
                    />
                    <Text style={styles.thumbLabel}>{piece.slot}</Text>
                  </View>
                ))}
              </View>
            </View>
          }
        />
      </ScrollView>

      <View style={styles.bottomDivider} />
      <View style={[styles.bottomBar, { paddingBottom: 12 }, contentStyle(maxW)]}>
        <Pressable
          style={[styles.altBtn, generating && styles.btnDisabled]}
          disabled={generating}
          onPress={generate}>
          <Icon name="arrow.clockwise" tintColor={ink(0.6)} size={15} />
          <Text style={styles.altText}>{resultUri ? '다른 사진으로 생성' : '사진 선택'}</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  headerSafe: { backgroundColor: Editorial.page },
  header: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    paddingHorizontal: 16, paddingTop: 8, paddingBottom: 10,
  },
  headerTitle: { fontSize: 15, fontWeight: '600', color: INK },
  content: { paddingBottom: 24 },
  canvas: {
    aspectRatio: 0.8,
    marginHorizontal: 20,
    borderRadius: 20,
    backgroundColor: CANVAS,
    borderWidth: 1,
    borderColor: Editorial.line,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
    overflow: 'hidden',
    paddingHorizontal: 24,
  },
  canvasTitle: { fontSize: 16, fontWeight: '600', color: INK, marginTop: 4 },
  canvasGuide: { fontSize: 12, color: Editorial.textCaption, textAlign: 'center' },
  photoBtn: {
    marginTop: 8, backgroundColor: Editorial.cta, borderRadius: 999,
    paddingHorizontal: 18, paddingVertical: 11,
  },
  photoBtnText: { color: '#fff', fontSize: 13, fontWeight: '600' },
  canvasBadge: {
    position: 'absolute', bottom: 16, right: 16, flexDirection: 'row',
    alignItems: 'center', gap: 5, backgroundColor: INK,
    paddingHorizontal: 11, paddingVertical: 6, borderRadius: 999,
  },
  canvasBadgeText: { fontSize: 11, color: '#fff', fontWeight: '500' },
  body: { paddingHorizontal: 20, paddingTop: 22 },
  title: { fontFamily: Fonts.serif, fontSize: 24, color: INK },
  subtitle: { fontSize: 13, color: Editorial.textCaption, marginTop: 6 },
  sectionTitle: { fontSize: 13, fontWeight: '600', color: INK, marginTop: 26, marginBottom: 12 },
  thumbRow: { flexDirection: 'row', gap: 10, flexWrap: 'wrap' },
  thumbCol: { width: 72, alignItems: 'center', gap: 6 },
  thumbLabel: { fontSize: 12, color: Editorial.textCaption },
  bottomDivider: { height: 1, backgroundColor: ink(0.08) },
  bottomBar: {
    flexDirection: 'row', justifyContent: 'flex-end', backgroundColor: Editorial.page,
    paddingHorizontal: 20, paddingTop: 12,
  },
  btnDisabled: { opacity: 0.4 },
  altBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 7, height: 50,
    paddingHorizontal: 20, borderRadius: 999, borderWidth: 1,
    borderColor: ink(0.14), justifyContent: 'center',
  },
  altText: { fontSize: 14, color: Editorial.textCaption, fontWeight: '500' },
});
