import { Image } from 'expo-image';
import { router, type Href } from 'expo-router';
import { useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Icon } from '@/components/icon';
import { ModalShell, SmartImage } from '@/components/ui';
import { ContentMax, Editorial, Fonts, ink } from '@/constants/theme';
import { useOutfitAnalysisDetail } from '@/hooks/use-outfit-analysis-detail';
import type { WardrobeLink } from '@/lib/outfitHistoryApi';
import { pickOutfitPhoto, takeOutfitPhoto } from '@/lib/pickItemPhoto';
import { useAuth } from '@/state/auth';
import { outfitAnalysisStore, useOutfitAnalysis } from '@/state/outfit-analysis';

const INK = Editorial.ink;

export default function OutfitReviewScreen() {
  const { isLoggedIn, isDemo } = useAuth();
  const { job } = useOutfitAnalysis();
  const [photo, setPhoto] = useState<string | null>(null);
  /* 옷장은 사용자 소유 데이터라 백엔드가 비로그인 요청에서는 무시한다 — 선택지도 로그인 사용자에게만 준다.
     데모 세션은 토큰이 없어 옷장 API 가 전부 401 이므로 켜봐야 등록되지 않는다. */
  const canSaveToWardrobe = isLoggedIn && !isDemo;
  const [saveToWardrobe, setSaveToWardrobe] = useState(true);

  /* 옷장 등록은 평가와 다른 파이프라인이라 평가가 끝난 뒤에도 진행 중이다.
     상세 화면과 같은 훅으로 이 화면에서도 끝날 때까지 지켜본다 — 완료되면 실제 아이템이 채워진다. */
  const {
    analysis,
    loading: wardrobeLoading,
    error: wardrobeError,
    stalled: wardrobeStalled,
    reload: reloadWardrobe,
  } = useOutfitAnalysisDetail(job?.wardrobeJobId ? (job.analysisId ?? undefined) : undefined);

  const pending = outfitAnalysisStore.isPending(job);
  const result = job?.phase === 'SUCCEEDED' ? job.evaluation : null;
  const shownPhoto = job?.photoUri ?? photo;

  const choosePhoto = async (source: 'album' | 'camera' = 'album') => {
    const uri = source === 'album' ? await pickOutfitPhoto() : await takeOutfitPhoto();
    if (!uri) return;
    if (job && !pending) await outfitAnalysisStore.clear();
    setPhoto(uri);
  };

  const analyze = async () => {
    const targetPhoto = photo ?? job?.photoUri;
    if (!targetPhoto) return;
    try {
      /* 접수 뒤 이 화면에 머문다 — 방금 시작한 일의 진행 상태는 시작한 자리에서 보이는 게 자연스럽다.
         홈으로 보내면 사용자가 뭘 눌렀는지 잃어버린다. 다른 화면을 보고 싶으면 PendingView 의
         '홈 둘러보기' 로 나가면 되고, 분석은 전역 스토어라 어디서든 계속된다. */
      await outfitAnalysisStore.start(targetPhoto, canSaveToWardrobe && saveToWardrobe);
    } catch {
      // 실패 메시지는 전역 작업 상태에 저장되어 같은 화면에서 보여준다.
    }
  };

  const startNewAnalysis = async () => {
    await outfitAnalysisStore.clear();
    setPhoto(null);
  };

  return (
    /* 같은 착장 분석 흐름의 기록 상세·목록이 ContentMax.card 를 쓴다. 여기만 narrow(720)
       였어서 데스크톱에서 글줄이 160px 더 길게 흘렀다 — 같은 사진·같은 평가문인데 화면마다
       다르게 읽힌다. 사진이 주인공인 카드라는 점에서도 card(560)가 맞다. */
    <ModalShell maxWidth={ContentMax.card}>
      <View style={styles.container}>
        <SafeAreaView edges={['top']}>
          <View style={styles.header}>
            <Text style={styles.headerTitle}>내 착장 분석</Text>
            <Pressable hitSlop={12} onPress={() => router.back()}>
              <Text style={styles.close}>×</Text>
            </Pressable>
          </View>
        </SafeAreaView>
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          {pending ? (
            <PendingView photo={shownPhoto} phase={job?.phase} />
          ) : result ? (
            <>
              {shownPhoto ? <Image source={{ uri: shownPhoto }} style={styles.resultImage} contentFit="cover" /> : null}
              <Text style={styles.resultEyebrow}>COZY&apos;S REVIEW</Text>
              <Text style={styles.title}>{result.summary}</Text>
              <View style={styles.feedbackCard}>
                <Text style={styles.feedbackTitle}>잘 어울리는 포인트</Text>
                <Text style={styles.feedbackText}>{result.strengths.map((item) => `• ${item}`).join('\n')}</Text>
              </View>
              <View style={styles.tipCard}>
                <Text style={styles.tipTitle}>더 좋아질 수 있는 제안</Text>
                <Text style={styles.tipText}>{result.styling_tips.map((item) => `• ${item}`).join('\n')}</Text>
              </View>

              {job?.wardrobeJobId ? (
                <FoundItems
                  link={analysis?.wardrobe ?? null}
                  loaded={Boolean(analysis)}
                  loading={wardrobeLoading}
                  error={wardrobeError}
                  stalled={wardrobeStalled}
                  onRefresh={reloadWardrobe}
                />
              ) : null}

              <Pressable style={styles.secondaryButton} onPress={startNewAnalysis}>
                <Text style={styles.secondaryButtonText}>새 사진 분석하기</Text>
              </Pressable>
            </>
          ) : (
            <>
              <Text style={styles.title}>평소에 입는 옷들을 보여주세요</Text>
              <Text style={styles.body}>잘 어울리는 포인트를 짚어드릴게요.{`\n`}분석 중에는 다른 화면을 둘러봐도 괜찮아요.</Text>
              <Pressable style={styles.photoBox} onPress={() => choosePhoto()}>
                {shownPhoto ? (
                  <Image source={{ uri: shownPhoto }} style={StyleSheet.absoluteFill} contentFit="cover" />
                ) : (
                  <View style={styles.photoEmpty}>
                    <Text style={styles.photoIcon}>＋</Text>
                    <Text style={styles.photoLabel}>착장 사진 선택하기</Text>
                    <Text style={styles.photoHint}>전신이 보이는 사진이면 더 좋아요</Text>
                  </View>
                )}
              </Pressable>
              <View style={styles.photoActions}>
                <Pressable style={styles.photoAction} onPress={() => choosePhoto()}>
                  <Icon name="photo.on.rectangle" tintColor={INK} size={22} />
                  <Text style={styles.photoActionText}>{shownPhoto ? '다른 사진 선택' : '앨범에서 선택'}</Text>
                </Pressable>
                <Pressable style={styles.photoAction} onPress={() => choosePhoto('camera')}>
                  <Icon name="camera" tintColor={INK} size={22} />
                  <Text style={styles.photoActionText}>카메라로 촬영</Text>
                </Pressable>
              </View>
              {canSaveToWardrobe ? (
                <Pressable
                  style={styles.optionRow}
                  onPress={() => setSaveToWardrobe((on) => !on)}
                  accessibilityRole="checkbox"
                  accessibilityState={{ checked: saveToWardrobe }}>
                  <View style={[styles.checkbox, saveToWardrobe && styles.checkboxOn]}>
                    <Text style={styles.check}>{saveToWardrobe ? '✓' : ''}</Text>
                  </View>
                  <View style={styles.optionText}>
                    <Text style={styles.optionLabel}>이 사진 속 옷도 옷장에 등록하기</Text>
                    <Text style={styles.optionHint}>옷을 하나씩 분리해 담아요. 몇 분 걸려요.</Text>
                  </View>
                </Pressable>
              ) : null}
              {job?.phase === 'FAILED' && job.detail ? <Text style={styles.errorText}>{job.detail}</Text> : null}
              <Pressable
                style={[styles.primary, !shownPhoto && styles.primaryDisabled]}
                disabled={!shownPhoto}
                onPress={analyze}>
                <Text style={styles.primaryText}>{job?.phase === 'FAILED' ? '다시 분석하기' : '착장 분석하기'}</Text>
              </Pressable>
              <Text style={styles.privacy}>사진은 착장 분석을 위해서만 사용돼요.</Text>
            </>
          )}
        </ScrollView>
      </View>
    </ModalShell>
  );
}

/**
 * 사진에서 뽑아낸 실제 옷. 예전엔 여기가 고정 문구 3개였다 — 어떤 사진을 올려도 같은 옷이 나왔다.
 *
 * 옷장 파이프라인은 평가보다 오래 걸려서, 이 화면이 처음 뜰 때는 아직 처리 중일 수 있다.
 * 그래서 상태를 그대로 보여주고 끝나면 아이템으로 바뀐다(훅이 완료까지 다시 조회한다).
 */
function FoundItems({
  link,
  loaded,
  loading,
  error,
  stalled,
  onRefresh,
}: {
  link: WardrobeLink | null;
  /** 상세 조회가 한 번이라도 성공했는지. 이걸 안 보면 "못 불러옴"과 "처리 중"이 구분되지 않는다. */
  loaded: boolean;
  loading: boolean;
  error: string | null;
  /** 폴링 상한에 걸려 더 이상 지켜보지 않는 상태 */
  stalled: boolean;
  onRefresh: () => void;
}) {
  const processing = link?.status === 'PENDING' || link?.status === 'PROCESSING';
  /* 조회를 아직 못 했거나(loaded=false) 실패했으면 폴링도 안 걸린다 —
     스피너를 돌려두면 영영 처리 중인 것처럼 보이므로 다시 시도할 길을 준다. */
  const unreachable = !loading && (!loaded || Boolean(error));

  return (
    <View style={styles.wardrobeNote}>
      <View style={styles.wardrobeHead}>
        <Text style={styles.wardrobeNoteTitle}>이 사진에서 찾은 옷</Text>
        {(loading || (processing && !stalled)) && !unreachable ? (
          <ActivityIndicator size="small" color={Editorial.textCaption} />
        ) : null}
      </View>

      {unreachable ? (
        <>
          <Text style={styles.wardrobeNoteBody}>옷장 등록 상태를 불러오지 못했어요.</Text>
          <Pressable style={styles.secondaryButton} onPress={onRefresh}>
            <Text style={styles.secondaryButtonText}>다시 확인하기</Text>
          </Pressable>
        </>
      ) : !link ? (
        <Text style={styles.wardrobeNoteBody}>옷장 등록 상태를 확인하고 있어요.</Text>
      ) : processing && stalled ? (
        /* 지켜보기를 멈춘 상태. 스피너를 계속 돌리면 영영 처리 중인 것처럼 보인다. */
        <>
          <Text style={styles.wardrobeNoteBody}>
            생각보다 오래 걸리고 있어요. 다 됐는지 눌러서 확인해 보세요.
          </Text>
          <Pressable style={styles.secondaryButton} onPress={onRefresh}>
            <Text style={styles.secondaryButtonText}>다시 확인하기</Text>
          </Pressable>
        </>
      ) : processing ? (
        <Text style={styles.wardrobeNoteBody}>옷을 하나씩 분리하고 있어요. 몇 분 걸릴 수 있어요.</Text>
      ) : link.status === 'FAILED' ? (
        <Text style={styles.errorText}>{link.error_message || '옷을 옷장에 등록하지 못했어요.'}</Text>
      ) : link.items.length === 0 ? (
        <Text style={styles.wardrobeNoteBody}>이 사진에서는 옷을 찾지 못했어요.</Text>
      ) : (
        link.items.map((item) => (
          <View key={item.id} style={styles.foundRow}>
            <SmartImage uri={item.image_url} width={48} height={48} radius={10} contentFit="contain" />
            <View style={styles.foundText}>
              <Text style={styles.foundName} numberOfLines={1}>
                {item.item_name || '이름 없는 아이템'}
              </Text>
              <Text style={styles.foundMeta} numberOfLines={1}>
                {[item.category_large, item.category_small, item.color].filter(Boolean).join(' · ')}
              </Text>
            </View>
          </View>
        ))
      )}

      <Pressable style={styles.secondaryButton} onPress={() => router.push('/(tabs)/closet' as Href)}>
        <Text style={styles.secondaryButtonText}>옷장에서 확인하기</Text>
      </Pressable>
    </View>
  );
}

function PendingView({ photo, phase }: { photo: string | null; phase?: string }) {
  return (
    <View>
      {photo ? <Image source={{ uri: photo }} style={styles.resultImage} contentFit="cover" /> : null}
      <View style={styles.pendingCard}>
        <ActivityIndicator color={Editorial.selected} />
        <Text style={styles.pendingTitle}>{phase === 'SUBMITTING' ? '사진을 접수하고 있어요' : '착장을 분석하고 있어요'}</Text>
        <Text style={styles.pendingBody}>완료까지 잠시 걸릴 수 있어요. 홈이나 다른 탭을 둘러봐도 분석은 계속됩니다.</Text>
      </View>
      <Pressable style={styles.primary} onPress={() => router.replace('/(tabs)/home')}>
        <Text style={styles.primaryText}>홈 둘러보기</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  header: { height: 58, paddingHorizontal: 20, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: 1, borderBottomColor: ink(0.1) },
  headerTitle: { fontSize: 16, fontWeight: '600', color: INK },
  close: { fontSize: 24, color: Editorial.textCaption },
  content: { padding: 24, paddingBottom: 40 },
  title: { marginTop: 10, fontFamily: Fonts.serif, fontSize: 24, lineHeight: 32, color: INK },
  body: { marginTop: 13, fontSize: 14, lineHeight: 21, color: Editorial.textCaption },
  photoBox: { height: 300, marginTop: 28, borderRadius: 20, overflow: 'hidden', backgroundColor: Editorial.surface, borderWidth: 1, borderColor: Editorial.line },
  photoEmpty: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 7 },
  photoIcon: { fontSize: 32, color: Editorial.textCaption },
  photoLabel: { fontSize: 14, fontWeight: '600', color: INK },
  photoHint: { fontSize: 12, color: Editorial.textCaption },
  photoActions: { flexDirection: 'row', gap: 12, marginTop: 14 },
  photoAction: { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, height: 52, borderRadius: 14, backgroundColor: Editorial.surface, borderWidth: 1, borderColor: Editorial.line },
  photoActionText: { fontSize: 14, fontWeight: '600', color: INK },
  primary: { height: 52, marginTop: 26, borderRadius: 999, alignItems: 'center', justifyContent: 'center', backgroundColor: Editorial.cta },
  primaryDisabled: { backgroundColor: ink(0.22) },
  primaryText: { color: '#ffffff', fontSize: 14, fontWeight: '600' },
  secondaryButton: { height: 52, marginTop: 14, borderRadius: 999, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: Editorial.line, backgroundColor: Editorial.surface },
  secondaryButtonText: { color: Editorial.textSoft, fontSize: 14, fontWeight: '600' },
  errorText: { marginTop: 18, textAlign: 'center', fontSize: 13, lineHeight: 20, color: Editorial.danger },
  privacy: { marginTop: 13, textAlign: 'center', fontSize: 11, lineHeight: 16, color: Editorial.textCaption },
  resultImage: { height: 250, borderRadius: 20 },
  resultEyebrow: { marginTop: 22, fontSize: 10, letterSpacing: 1.6, fontWeight: '600', color: Editorial.textCaption },
  feedbackCard: { marginTop: 22, borderRadius: 16, padding: 18, backgroundColor: Editorial.surface, borderWidth: 1, borderColor: Editorial.line },
  feedbackTitle: { fontSize: 14, fontWeight: '700', color: INK },
  feedbackText: { marginTop: 10, fontSize: 15, lineHeight: 24, color: Editorial.textSoft },
  tipCard: { marginTop: 10, borderRadius: 16, borderWidth: 1, borderColor: ink(0.1), padding: 18 },
  tipTitle: { fontSize: 14, fontWeight: '700', color: INK },
  tipText: { marginTop: 10, fontSize: 15, lineHeight: 24, color: Editorial.textSoft },
  checkbox: { width: 19, height: 19, borderRadius: 6, borderWidth: 1, borderColor: ink(0.25), alignItems: 'center', justifyContent: 'center' },
  checkboxOn: { borderColor: Editorial.selected, backgroundColor: Editorial.selected },
  check: { fontSize: 12, fontWeight: '700', color: '#ffffff' },

  // 시작 화면의 "옷장에도 등록" 선택
  optionRow: { marginTop: 20, flexDirection: 'row', alignItems: 'flex-start', gap: 11 },
  optionText: { flex: 1, gap: 3 },
  optionLabel: { fontSize: 14, fontWeight: '600', color: INK },
  optionHint: { fontSize: 12, lineHeight: 18, color: Editorial.textCaption },

  // 결과 화면의 옷장 등록 안내
  wardrobeNote: {
    marginTop: 24,
    borderRadius: 18,
    backgroundColor: Editorial.control,
    paddingHorizontal: 18,
    paddingTop: 16,
    paddingBottom: 6,
    gap: 6,
  },
  wardrobeHead: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  wardrobeNoteTitle: { flex: 1, fontSize: 14, fontWeight: '700', color: INK },
  wardrobeNoteBody: { fontSize: 13, lineHeight: 20, color: Editorial.textCaption },
  foundRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 4 },
  foundText: { flex: 1, gap: 2 },
  foundName: { fontSize: 14, fontWeight: '600', color: INK },
  foundMeta: { fontSize: 12, color: Editorial.textCaption },
  pendingCard: { marginTop: 24, padding: 24, alignItems: 'center', borderRadius: 18, backgroundColor: Editorial.surface, borderWidth: 1, borderColor: Editorial.line },
  pendingTitle: { marginTop: 14, fontSize: 17, fontWeight: '700', color: INK },
  pendingBody: { marginTop: 8, textAlign: 'center', fontSize: 13, lineHeight: 20, color: Editorial.textCaption },
});
