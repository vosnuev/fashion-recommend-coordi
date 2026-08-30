import { Icon, type IconName } from '@/components/icon';
import { Image } from 'expo-image';
import { router, useLocalSearchParams } from 'expo-router';
import { goBack } from '@/lib/goBack';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Editorial, ink, Fonts , ContentMax} from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { pickBodyPhoto } from '@/lib/pickItemPhoto';
import { measureStore, useMeasure } from '@/state/measure';

const INK = Editorial.ink;
const BONE = Editorial.bone;

function Steps({ active }: { active: number }) {
  return (
    <View style={styles.steps}>
      {[0, 1, 2].map((i) => (
        <View key={i} style={[styles.step, i <= active && styles.stepOn]} />
      ))}
    </View>
  );
}

const GUIDE: { icon: IconName; text: string }[] = [
  { icon: 'ruler', text: '카메라와 2m 거리에서 전신이 나오게' },
  { icon: 'figure.stand', text: '팔을 살짝 벌리고 정면·측면으로 서기' },
  { icon: 'sun.max', text: '밝고 단색인 배경에서 촬영' },
  { icon: 'tshirt', text: '몸매가 드러나는 옷을 입어주세요' },
];

// G2 정면·측면 촬영 — 촬영 가이드 + 2컷 업로드
export default function MeasureCapture() {
  const { contentStyle } = useBreakpoint();
  const { returnTo } = useLocalSearchParams<{ returnTo?: string }>();
  /* 첨부 여부만 로컬 boolean 으로 들고 있으면 "무엇을 붙였는지"를 화면이 알 수 없다.
     스토어의 photos(uri) 를 그대로 구독해 슬롯에 사진을 띄운다 — 재첨부하면 uri 가 바뀌므로
     바뀐 사진이 눈에 바로 보이고, 화면을 나갔다 돌아와도 첨부한 사진이 남는다. */
  const { photos } = useMeasure();
  const both = Boolean(photos.front && photos.side);

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top']} style={styles.safe}>
        <View style={styles.top}>
          <Pressable hitSlop={12} onPress={() => goBack('/(tabs)/my')}>
            <Icon name="chevron.left" tintColor={INK} size={20} />
          </Pressable>
        </View>

        <ScrollView contentContainerStyle={[styles.content, contentStyle(ContentMax.narrow)]} showsVerticalScrollIndicator={false}>
          <Steps active={1} />
          <Text style={styles.eyebrow}>STEP 2 / 3</Text>
          <Text style={styles.title}>정면·측면 사진을 촬영해요</Text>
          <Text style={styles.lead}>2장의 사진으로 어깨·가슴·허리 둘레를 추정해요.</Text>

          {/* 촬영 슬롯 2컷 */}
          <View style={styles.slots}>
            {(['front', 'side'] as const).map((k) => {
              const uri = photos[k];
              return (
                <Pressable
                  key={k}
                  style={[styles.slot, Boolean(uri) && styles.slotFilled]}
                  onPress={async () => {
                    // 앨범에서 전신 사진 1장 선택 (웹은 파일 선택 창). 취소하면 무시.
                    const picked = await pickBodyPhoto();
                    if (!picked) return;
                    measureStore.setPhoto(k, picked);
                  }}>
                  <View style={styles.silhouette}>
                    {uri ? (
                      <>
                        {/* key 에 uri 를 넣어 재첨부 때 이미지를 새로 마운트한다 —
                            이전 사진이 잠깐 남아 보이면 바뀐 건지 확인이 안 된다. */}
                        <Image
                          key={uri}
                          source={{ uri }}
                          style={StyleSheet.absoluteFill}
                          contentFit="cover"
                        />
                        <View style={styles.doneBadge}>
                          <Icon name="checkmark.circle.fill" tintColor={INK} size={18} />
                        </View>
                      </>
                    ) : (
                      <Icon name="camera" tintColor={ink(0.35)} size={26} />
                    )}
                  </View>
                  <Text style={styles.slotLabel}>{k === 'front' ? '정면' : '측면'}</Text>
                  <Text style={[styles.slotState, Boolean(uri) && styles.slotStateDone]}>
                    {uri ? '다른 사진으로 바꾸기' : '탭하여 첨부'}
                  </Text>
                </Pressable>
              );
            })}
          </View>

          {/* 가이드 */}
          <Text style={styles.sectionTitle}>촬영 가이드</Text>
          <View style={styles.guideCard}>
            {GUIDE.map((g, i) => (
              <View key={i} style={styles.guideRow}>
                <View style={styles.guideIcon}>
                  <Icon name={g.icon} tintColor={INK} size={15} />
                </View>
                <Text style={styles.guideText}>{g.text}</Text>
              </View>
            ))}
          </View>

          {/* 프라이버시 */}
          <View style={styles.privacy}>
            <Icon name="lock.shield" tintColor={ink(0.5)} size={15} />
            <Text style={styles.privacyText}>
              사진은 서버에 저장하지 않고 치수 추정 후 바로 폐기해요.
            </Text>
          </View>

          <Pressable
            style={styles.skipWrap}
            hitSlop={8}
            onPress={() => {
              measureStore.estimate();
              router.push({
                pathname: '/measure-result',
                params: returnTo ? { returnTo } : undefined,
              });
            }}>
            <Text style={styles.skipText}>사진 없이 진행할게요</Text>
          </Pressable>
        </ScrollView>

        <View style={[styles.bottomBar, { paddingBottom: 12 }, contentStyle(ContentMax.narrow)]}>
          <Pressable
            style={[styles.cta, !both && styles.ctaDisabled]}
            disabled={!both}
            onPress={() => {
              measureStore.startPhotoMeasurement(); // 사진 업로드→폴링 시작 — STEP3 가 결과를 구독
              router.push({
                pathname: '/measure-result',
                params: returnTo ? { returnTo } : undefined,
              });
            }}>
            <Text style={styles.ctaText}>
              {both ? '측정 시작하기' : '두 사진을 촬영해주세요'}
            </Text>
          </Pressable>
        </View>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  safe: { flex: 1 },
  top: { paddingHorizontal: 20, paddingTop: 8 },
  content: { paddingHorizontal: 24, paddingTop: 8, paddingBottom: 24 },

  steps: { flexDirection: 'row', gap: 6, marginBottom: 24 },
  step: { flex: 1, height: 3, borderRadius: 2, backgroundColor: ink(0.1) },
  stepOn: { backgroundColor: Editorial.selected },

  eyebrow: { fontSize: 11, letterSpacing: 1.5, color: Editorial.textCaption, fontWeight: '600' },
  title: { fontFamily: Fonts.serif, fontSize: 28, color: INK, marginTop: 10, lineHeight: 34 },
  lead: { fontSize: 14, color: Editorial.textCaption, marginTop: 12 },

  slots: { flexDirection: 'row', gap: 12, marginTop: 26 },
  slot: {
    flex: 1,
    alignItems: 'center',
    gap: 8,
    borderWidth: 1,
    borderColor: ink(0.1),
    borderRadius: 18,
    paddingVertical: 20,
  },
  // 사진이 들어오면 테두리를 진하게 — 어느 쪽을 채웠는지 카드 단위로도 구분된다.
  slotFilled: { borderColor: ink(0.28) },
  silhouette: {
    width: 90,
    height: 120,
    borderRadius: 14,
    backgroundColor: BONE,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 4,
    // 사진이 absoluteFill 로 깔리므로 모서리를 잘라 줘야 둥근 박스가 유지된다.
    overflow: 'hidden',
  },
  doneBadge: {
    position: 'absolute',
    top: 5,
    right: 5,
    width: 22,
    height: 22,
    borderRadius: 11,
    backgroundColor: Editorial.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
  slotLabel: { fontSize: 15, fontWeight: '600', color: INK },
  slotState: { fontSize: 12, color: Editorial.textCaption },
  slotStateDone: { color: INK, fontWeight: '500' },

  sectionTitle: { fontSize: 13, fontWeight: '600', color: INK, marginTop: 30, marginBottom: 12 },
  guideCard: { backgroundColor: Editorial.surfaceSoft, borderWidth: 1, borderColor: Editorial.line, borderRadius: 16, padding: 8 },
  guideRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 8, paddingVertical: 10 },
  guideIcon: {
    width: 32,
    height: 32,
    borderRadius: 10,
    backgroundColor: Editorial.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
  guideText: { flex: 1, fontSize: 13.5, color: Editorial.textSoft },

  privacy: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginTop: 20,
    paddingHorizontal: 4,
  },
  privacyText: { flex: 1, fontSize: 12, color: Editorial.textCaption, lineHeight: 18 },

  bottomBar: {
    paddingHorizontal: 24,
    paddingTop: 8,
    paddingBottom: 4,
    borderTopWidth: 1,
    borderTopColor: ink(0.08),
  },
  skipWrap: { alignItems: 'center', marginTop: 36, paddingVertical: 4 },
  cta: {
    height: 52,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  ctaDisabled: { backgroundColor: ink(0.22) },
  ctaText: { color: '#fff', fontSize: 15, fontWeight: '500' },

  skipText: { fontSize: 14, color: Editorial.textCaption, fontWeight: '500', textDecorationLine: 'underline' },
});
