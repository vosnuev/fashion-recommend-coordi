import { router, useLocalSearchParams } from 'expo-router';
import { goBack } from '@/lib/goBack';
import { useEffect, useState } from 'react';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useToast } from '@/components/ui';
import { Editorial, ink, Fonts , ContentMax} from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { ApiError } from '@/lib/apiClient';
import { fetchBodyBasic, measureStore } from '@/state/measure';

const INK = Editorial.ink;
const WINE = Editorial.wine;

function Steps({ active }: { active: number }) {
  return (
    <View style={styles.steps}>
      {[0, 1, 2].map((i) => (
        <View key={i} style={[styles.step, i <= active && styles.stepOn]} />
      ))}
    </View>
  );
}

// G1 체형 정보 입력 — 키/몸무게 + BMI 자동계산
export default function MeasureInput() {
  const { contentStyle } = useBreakpoint();
  const { returnTo } = useLocalSearchParams<{ returnTo?: string }>();
  const toast = useToast();
  const [height, setHeight] = useState('');
  const [weight, setWeight] = useState('');
  const [sex, setSex] = useState<'female' | 'male' | null>(null);
  const [saving, setSaving] = useState(false);
  /** 저장된 치수를 못 불러왔을 때 — 칸이 빈 이유를 알린다(입력은 그대로 가능) */
  const [prefillFailed, setPrefillFailed] = useState(false);

  // 새 측정 플로우 진입 → 이전 데이터 초기화 후, 저장된 기본 정보가 있으면 프리필
  useEffect(() => {
    measureStore.reset();
    let alive = true;
    fetchBodyBasic()
      .then((b) => {
        if (!alive) return;
        if (b.sex != null) setSex((current) => current ?? b.sex);
        if (b.height != null) setHeight(String(b.height));
        if (b.weight != null) setWeight(String(b.weight));
      })
      /* catch 가 없으면 서버가 막혔을 때 처리되지 않은 거부가 된다.
         못 불러와도 직접 입력하면 되는 화면이라 막지 않고, 빈칸인 이유만 알린다. */
      .catch(() => {
        if (alive) setPrefillFailed(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  const h = parseFloat(height);
  const w = parseFloat(weight);
  const bmi = h > 0 && w > 0 ? w / (h / 100) ** 2 : null;
  const bmiLabel = bmi
    ? bmi < 18.5
      ? '저체중'
      : bmi < 23
        ? '정상'
        : bmi < 25
          ? '과체중'
          : '비만'
    : '';
  /* 성별도 있어야 다음으로 간다 — 서버가 gender 없이는 키·몸무게를 아예 저장하지 않는다.
     빼먹으면 측정을 끝내도 아무것도 안 남아 '된 것처럼 보이는' 화면이 된다. */
  const canNext = h > 0 && w > 0 && sex !== null;

  const goCapture = () => {
    router.push({
      pathname: '/measure-capture',
      params: returnTo ? { returnTo } : undefined,
    });
  };

  /* 건너뛰기의 뜻이 진입 경로마다 다르다.
     가입 직후 온보딩에서는 체형을 통째로 넘기는 것이므로 다음 단계(추구미)로 보내고,
     그 밖(마이 등)에서는 예전처럼 입력만 생략하고 사진 촬영으로 이어 간다. */
  const skip = () => {
    if (returnTo === 'onboarding') {
      router.navigate({ pathname: '/style-onboarding', params: { returnTo: 'onboarding' } });
      return;
    }
    goCapture();
  };

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top']} style={styles.safe}>
        <View style={styles.top}>
          <Pressable hitSlop={12} onPress={() => goBack('/(tabs)/my')}>
            <Text style={styles.close}>✕</Text>
          </Pressable>
        </View>

        <ScrollView
          contentContainerStyle={[styles.content, contentStyle(ContentMax.narrow)]}
          keyboardShouldPersistTaps="handled">
          <Steps active={0} />
          <Text style={styles.eyebrow}>STEP 1 / 3</Text>
          <Text style={styles.title}>체형 정보를 알려주세요</Text>
          <Text style={styles.lead}>키와 몸무게로 치수를 더 정확히 추정해요.</Text>
          {prefillFailed ? (
            <Text style={styles.prefillWarn}>
              저장해 둔 값을 불러오지 못했어요. 직접 입력하면 그대로 진행돼요.
            </Text>
          ) : null}

          {/* 키 */}
          <View style={styles.field}>
            <Text style={styles.label}>키</Text>
            <View style={styles.inputRow}>
              <TextInput
                style={styles.input}
                value={height}
                onChangeText={setHeight}
                placeholder="0"
                placeholderTextColor={ink(0.25)}
                keyboardType="number-pad"
              />
              <Text style={styles.unit}>cm</Text>
            </View>
            <View style={styles.underline} />
          </View>

          {/* 몸무게 */}
          <View style={styles.field}>
            <Text style={styles.label}>몸무게</Text>
            <View style={styles.inputRow}>
              <TextInput
                style={styles.input}
                value={weight}
                onChangeText={setWeight}
                placeholder="0"
                placeholderTextColor={ink(0.25)}
                keyboardType="number-pad"
              />
              <Text style={styles.unit}>kg</Text>
            </View>
            <View style={styles.underline} />
          </View>

          {/* BMI */}
          <View style={styles.field}>
            <Text style={styles.label}>BMI (자동 계산)</Text>
            <View style={styles.inputRow}>
              <Text style={styles.bmiValue}>{bmi ? bmi.toFixed(1) : '—'}</Text>
              {bmiLabel ? (
                <View style={styles.bmiTag}>
                  <Text style={styles.bmiTagText}>{bmiLabel}</Text>
                </View>
              ) : null}
            </View>
            <View style={styles.underline} />
          </View>

          {/* 성별 */}
          <View style={styles.field}>
            <Text style={styles.label}>성별</Text>
            <View style={styles.sexRow}>
              {([
                ['female', '여성'],
                ['male', '남성'],
              ] as const).map(([key, lbl]) => {
                const on = sex === key;
                return (
                  <Pressable
                    key={key}
                    style={[styles.sexChip, on && styles.sexChipOn]}
                    onPress={() => setSex(key)}>
                    <Text style={[styles.sexText, on && styles.sexTextOn]}>{lbl}</Text>
                  </Pressable>
                );
              })}
            </View>
          </View>

          {h > 0 && w > 0 && sex === null ? (
            <Text style={styles.sexHint}>성별까지 골라야 저장돼요</Text>
          ) : null}

          <Pressable style={styles.skipWrap} hitSlop={8} onPress={skip}>
            <Text style={styles.skipText}>
              {returnTo === 'onboarding' ? '나중에 할게요' : '입력 없이 건너뛰기'}
            </Text>
          </Pressable>
        </ScrollView>

        <View style={[styles.bottomBar, { paddingBottom: 12 }, contentStyle(ContentMax.narrow)]}>
          <Pressable
            style={[styles.cta, (!canNext || saving) && styles.ctaDisabled]}
            disabled={!canNext || saving}
            onPress={async () => {
              setSaving(true);
              try {
                await measureStore.saveBasic({ height: h, weight: w, sex: sex ?? 'none' });
                goCapture();
              } catch (e) {
                toast(
                  e instanceof ApiError ? e.message : '키·몸무게 저장에 실패했어요. 다시 시도해 주세요.',
                  { variant: 'error' },
                );
                if (returnTo !== 'onboarding') goCapture();
              } finally {
                setSaving(false);
              }
            }}>
            <Text style={styles.ctaText}>다음</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  safe: { flex: 1 },
  top: { paddingHorizontal: 24, paddingTop: 8, alignItems: 'flex-end' },
  close: { fontSize: 20, color: Editorial.textCaption },
  content: { paddingHorizontal: 24, paddingTop: 8, paddingBottom: 24 },

  steps: { flexDirection: 'row', gap: 6, marginBottom: 24 },
  step: { flex: 1, height: 3, borderRadius: 2, backgroundColor: ink(0.1) },
  stepOn: { backgroundColor: Editorial.selected },

  eyebrow: { fontSize: 11, letterSpacing: 1.5, color: Editorial.textCaption, fontWeight: '600' },
  title: { fontFamily: Fonts.serif, fontSize: 24, color: INK, marginTop: 10, lineHeight: 30 },
  lead: { fontSize: 14, color: Editorial.textCaption, marginTop: 12 },
  prefillWarn: { fontSize: 12.5, color: WINE, marginTop: 8, lineHeight: 18 },
  sexHint: { fontSize: 12.5, color: Editorial.textCaption, marginTop: 10 },

  field: { marginTop: 28 },
  label: { fontSize: 13, fontWeight: '600', color: Editorial.textCaption, letterSpacing: 0.2 },
  inputRow: { flexDirection: 'row', alignItems: 'flex-end', gap: 8, marginTop: 8 },
  input: { flex: 1, fontFamily: Fonts.serif, fontSize: 26, color: Editorial.ink, padding: 0 },
  unit: { fontSize: 14, color: Editorial.textCaption, marginBottom: 4 },
  underline: { marginTop: 8, height: 1, backgroundColor: ink(0.15) },

  bmiValue: { flex: 1, fontFamily: Fonts.serif, fontSize: 26, color: Editorial.ink },
  bmiTag: {
    backgroundColor: Editorial.accent,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 999,
    marginBottom: 4,
  },
  bmiTagText: { fontSize: 11, color: WINE, fontWeight: '600' },

  sexRow: { flexDirection: 'row', gap: 8, marginTop: 12 },
  sexChip: {
    flex: 1,
    height: 44,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: ink(0.14),
    alignItems: 'center',
    justifyContent: 'center',
  },
  sexChipOn: { backgroundColor: Editorial.selected, borderColor: Editorial.selected },
  sexText: { fontSize: 13, color: Editorial.textCaption, fontWeight: '500' },
  sexTextOn: { color: '#fff' },

  bottomBar: {
    paddingHorizontal: 24,
    paddingTop: 8,
    paddingBottom: 4,
    borderTopWidth: 1,
    borderTopColor: ink(0.08),
  },
  skipWrap: { alignItems: 'center', marginTop: 40, paddingVertical: 4 },
  skipText: { fontSize: 14, color: Editorial.textCaption, fontWeight: '500', textDecorationLine: 'underline' },
  cta: {
    height: 52,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  ctaDisabled: { backgroundColor: ink(0.22) },
  ctaText: { color: '#fff', fontSize: 15, fontWeight: '500' },
});
