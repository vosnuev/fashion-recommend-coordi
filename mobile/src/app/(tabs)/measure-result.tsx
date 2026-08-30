import { Icon } from '@/components/icon';
import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useMemo, useState } from 'react';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { MeasureGuideSheet } from '@/components/measure/measure-guide-sheet';
import { ErrorState, LoadingState, useToast } from '@/components/ui';
import {
  BODY_MEASURES,
  EDITABLE_MEASURES,
  PREVIEW_COUNT,
  measureLabel,
  type BodyMeasureKey,
  type BodyMeasureSpec,
} from '@/constants/body-measures';
import { ContentMax, Editorial, Fonts, ink, Type } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { ApiError } from '@/lib/apiClient';
import { measurementResultDescription } from '@/lib/bodyMeasurementResult';
import { measureStore, useMeasure, type Measurement } from '@/state/measure';

const INK = Editorial.ink;

function Steps({ active }: { active: number }) {
  return (
    <View style={styles.steps}>
      {[0, 1, 2].map((i) => (
        <View key={i} style={[styles.step, i <= active && styles.stepOn]} />
      ))}
    </View>
  );
}

/** 입력칸 문자열이 백엔드 허용 범위 안의 수인지 (벗어나면 PATCH detail 이 400 이 된다) */
function isValid(spec: BodyMeasureSpec, raw: string | undefined): boolean {
  const n = parseFloat(raw ?? '');
  return Number.isFinite(n) && n >= spec.min && n <= spec.max;
}

// G3 치수 결과·사이즈 매칭 — measureStore 결과를 구독. 완료 시 측정 플로우 닫기
export default function MeasureResult() {
  const { contentStyle } = useBreakpoint();
  /* guide 파라미터로 '재는 법'을 바로 열 수 있다 (mobile:///measure-result?guide=shoulder).
     화면을 거치지 않고 특정 항목 안내로 보낼 때 쓴다 — 도움말 링크·QA 확인용. */
  const { returnTo, guide } = useLocalSearchParams<{ returnTo?: string; guide?: string }>();
  const { status, result, photos, error, needsInput, photoQualityFailed } = useMeasure();
  const toast = useToast();
  const [savingDone, setSavingDone] = useState(false);
  /** '재는 법' 시트에서 처음 보여줄 항목. null 이면 닫힌 상태 */
  const [guideKey, setGuideKey] = useState<BodyMeasureKey | null>(null);
  /** 접힘 상태 — 처음엔 어깨·가슴·허리·엉덩이 4개만 */
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? BODY_MEASURES : BODY_MEASURES.slice(0, PREVIEW_COUNT);

  // 플로우를 거치지 않고 직접 진입했으면(status idle) 추정을 시작한다.
  useEffect(() => {
    if (status === 'idle') measureStore.estimate();
  }, [status]);

  /* 초기값이 아니라 effect 로 여는 이유: 이미 이 화면이 떠 있는 상태에서 guide 만 다른
     링크가 오면 컴포넌트가 다시 마운트되지 않아 useState 초기화가 실행되지 않는다. */
  useEffect(() => {
    const key = BODY_MEASURES.find((m) => m.key === guide)?.key;
    if (key) setGuideKey(key);
  }, [guide]);

  const usingPhotos = Boolean(photos.front && photos.side);
  /* 실패한 것을 그대로 다시 한다 — 사진 측정이 실패했는데 estimate() 를 부르면
     사진과 무관한 키·몸무게 추정값이 조용히 결과로 앉는다. */
  const retry = () =>
    usingPhotos ? measureStore.startPhotoMeasurement() : measureStore.estimate();

  // 사용자가 직접 수정하는 편집값(문자열) — 결과가 도착하면 초기화
  const [values, setValues] = useState<Partial<Record<BodyMeasureKey, string>>>({});
  useEffect(() => {
    if (!result) return;
    // cm 는 소수 1자리, 비율은 3자리 — 백엔드 Decimal 자릿수와 같게 표기한다.
    setValues(
      Object.fromEntries(
        BODY_MEASURES.map((spec) => [spec.key, result.measures[spec.key].toFixed(spec.decimals)]),
      ),
    );
  }, [result]);

  /* 범위를 벗어난 값은 저장이 400 으로 튕긴다. 눌러 보고 실패를 알려 주는 대신
     어느 칸이 문제인지 먼저 짚고 완료를 막는다. */
  const invalid = useMemo(
    () => EDITABLE_MEASURES.filter((spec) => !isValid(spec, values[spec.key])),
    [values],
  );

  // 로딩 / 에러 — 결과가 아직 없을 때
  if (status !== 'success' || !result) {
    return (
      <View style={styles.container}>
        <SafeAreaView edges={['top']} style={styles.safe}>
          <View style={styles.stateWrap}>
            <Steps active={2} />
            {/* 입력이 없어서 못 한 것과 그 밖의 실패(로그인 만료·서버 장애)는 갈 곳이 다르다 —
                전자만 STEP1 로 돌려보내고, 나머지는 그 자리에서 다시 시도하게 한다. */}
            {status === 'error' && photoQualityFailed ? (
              <View style={[styles.photoFailure, styles.stateFill]}>
                <View style={styles.failureIcon}>
                  <Icon name="exclamationmark.triangle" tintColor={Editorial.wine} size={24} />
                </View>
                <Text style={styles.failureTitle}>사진 인식 실패</Text>
                <Text style={styles.failureDescription}>
                  {error ?? '얼굴부터 발끝까지 보이도록 정면·측면 사진을 다시 촬영해 주세요.'}
                </Text>
                <Pressable
                  style={styles.failurePrimary}
                  onPress={() =>
                    router.replace({
                      pathname: '/measure-capture',
                      params: returnTo ? { returnTo } : undefined,
                    })
                  }>
                  <Icon name="camera" tintColor="#fff" size={16} />
                  <Text style={styles.failurePrimaryText}>다시 촬영</Text>
                </Pressable>
                <Pressable style={styles.failureSecondary} onPress={() => measureStore.estimate(true)}>
                  <Icon name="figure.stand" tintColor={INK} size={16} />
                  <Text style={styles.failureSecondaryText}>키·몸무게·성별로만 측정하기</Text>
                </Pressable>
              </View>
            ) : status === 'error' && needsInput ? (
              <ErrorState
                title="추정할 정보가 없어요"
                description={error ?? '키·몸무게를 입력하거나 사진을 등록해 주세요.'}
                onRetry={() =>
                  router.replace({ pathname: '/measure-input', params: returnTo ? { returnTo } : {} })
                }
                retryLabel="정보 입력하러 가기"
                retryIcon="chevron.left"
                style={styles.stateFill}
              />
            ) : status === 'error' ? (
              <ErrorState
                title="치수 추정에 실패했어요"
                description={error ?? undefined}
                onRetry={retry}
                style={styles.stateFill}
              />
            ) : (
              <LoadingState
                message={
                  usingPhotos
                    ? '사진으로 치수를 측정하고 있어요… (몇 분 걸릴 수 있어요)'
                    : '입력 정보로 치수를 추정하고 있어요…'
                }
                style={styles.stateFill}
              />
            )}
          </View>
        </SafeAreaView>
      </View>
    );
  }

  // 완료 — 수정한 값을 서버에 저장(PATCH detail)하고 플로우 닫기
  const onDone = async () => {
    if (invalid.length > 0) return;
    /* 고칠 수 있는 값만 입력칸에서 읽고, 읽기 전용(서버 계산값)은 받은 그대로 둔다.
       로컬 결과는 10개가 온전해야 화면이 그대로 그려진다 — 전송에서 빼는 일은 saveDetail 이 한다. */
    const measures: Measurement = { ...result.measures };
    for (const spec of EDITABLE_MEASURES) {
      measures[spec.key] = parseFloat(values[spec.key] as string);
    }

    setSavingDone(true);
    try {
      await measureStore.saveDetail(measures);
      if (returnTo === 'onboarding') {
        router.navigate({ pathname: '/style-onboarding', params: { returnTo: 'onboarding' } });
      } else if (returnTo === 'my') {
        router.navigate('/my');
      } else {
        router.navigate('/home');
      }
    } catch (e) {
      toast(
        e instanceof ApiError ? e.message : '치수 저장에 실패했어요. 다시 시도해 주세요.',
        { variant: 'error' },
      );
      if (returnTo !== 'onboarding') {
        router.navigate(returnTo === 'my' ? '/my' : '/home');
      }
    } finally {
      setSavingDone(false);
    }
  };

  const guideButton = (spec: BodyMeasureSpec) => (
    <Pressable
      hitSlop={10}
      accessibilityLabel={`${spec.label} 재는 법`}
      onPress={() => setGuideKey(spec.key)}>
      <Icon name="questionmark.circle" tintColor={ink(0.45)} size={15} />
    </Pressable>
  );

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top']} style={styles.safe}>
        <ScrollView contentContainerStyle={[styles.content, contentStyle(ContentMax.narrow)]} showsVerticalScrollIndicator={false}>
          <Steps active={2} />

          <View style={styles.hero}>
            <View style={styles.mark}>
              <Icon name="checkmark" tintColor="#fff" size={22} />
            </View>
            <Text style={styles.title}>치수 측정 완료</Text>
            <Text style={styles.lead}>
              {measurementResultDescription(result)}
            </Text>
            {result.bodyTypeLabel ? (
              <Text style={styles.bodyType}>{result.bodyTypeLabel}입니다</Text>
            ) : null}
          </View>

          {/* 추정 치수 — 값 탭하여 직접 수정. 처음엔 4개만 보이고 나머지는 '더보기' */}
          <View style={styles.sectionHead}>
            <Text style={styles.sectionTitlePlain}>추정 치수</Text>
            <Text style={styles.editHint}>탭하여 수정</Text>
          </View>
          <View style={styles.measureGrid}>
            {shown.map((spec) => (
              <View key={spec.key} style={styles.measureTile}>
                <View style={styles.measureLabelRow}>
                  <Text style={styles.measureLabel} numberOfLines={1}>
                    {measureLabel(spec)}
                  </Text>
                  {guideButton(spec)}
                </View>
                <View style={styles.measureValueRow}>
                  {spec.editable ? (
                    <TextInput
                      style={[
                        styles.measureInput,
                        !isValid(spec, values[spec.key]) && styles.measureInputBad,
                      ]}
                      value={values[spec.key] ?? ''}
                      onChangeText={(t) => setValues((prev) => ({ ...prev, [spec.key]: t }))}
                      keyboardType="decimal-pad"
                      selectTextOnFocus
                      maxLength={6}
                      returnKeyType="done"
                    />
                  ) : (
                    /* 서버가 계산해 주는 값 — 밑줄(수정 가능 신호)을 빼서 입력칸과 구분한다 */
                    <Text style={styles.measureReadonly}>{values[spec.key] ?? ''}</Text>
                  )}
                  {spec.unit ? <Text style={styles.measureUnit}>{spec.unit}</Text> : null}
                </View>
              </View>
            ))}

            {/* 접기/펴기 — 그리드 안에 둬서 카드 하나로 읽히게 한다 */}
            <Pressable style={styles.moreRow} onPress={() => setExpanded((v) => !v)}>
              <Text style={styles.moreText}>
                {expanded ? '접기' : `더보기 (${BODY_MEASURES.length - PREVIEW_COUNT}개)`}
              </Text>
              <Icon
                name={expanded ? 'chevron.up' : 'chevron.down'}
                tintColor={ink(0.5)}
                size={14}
              />
            </Pressable>
          </View>

          {/* 사진 없이 추정하면 상하체 비율만 개인차가 없다 — 숫자를 그대로 믿게 두지 않는다 */}
          {expanded && !result.usedPhotos ? (
            <Text style={styles.ratioNote}>
              * 상하체 비율은 사진이 있어야 실제로 잴 수 있어요. 지금은 기준값이라 사진으로 다시
              측정하거나 직접 고쳐 주세요.
            </Text>
          ) : null}

          {invalid.length > 0 ? (
            <Text style={styles.invalidText}>
              {invalid.map((s) => s.label).join(' · ')} 값을 확인해 주세요
              {invalid.length === 1 ? ` (${invalid[0].min} ~ ${invalid[0].max})` : ''}.
            </Text>
          ) : null}

          {/* 재는 법 — 치수를 보고 '이상한데' 싶은 자리, 사이즈 매칭으로 넘어가기 직전에 둔다.
              가장 흔한 오차인 어깨너비부터 연다. */}
          <Pressable style={styles.guideBanner} onPress={() => setGuideKey('shoulder')}>
            <View style={styles.guideBannerIcon}>
              <Icon name="questionmark.circle" tintColor={INK} size={16} />
            </View>
            <View style={styles.guideBannerTexts}>
              <Text style={styles.guideBannerTitle}>값이 실제와 다른가요?</Text>
              <Text style={styles.guideBannerBody}>
                어디서 어디까지 재는지 그림으로 확인하고 직접 고칠 수 있어요.
              </Text>
            </View>
            <Icon name="chevron.right" tintColor={ink(0.35)} size={16} />
          </Pressable>

          {/* 사이즈 매칭 */}
          <Text style={styles.sectionTitle}>브랜드 사이즈 매칭</Text>
          <View style={styles.sizeCard}>
            {result.sizes.map((s, i) => (
              <View key={s.brand}>
                <View style={styles.sizeRow}>
                  <Text style={styles.sizeBrand}>{s.brand}</Text>
                  <View style={styles.sizeRight}>
                    <View style={styles.sizeBadge}>
                      <Text style={styles.sizeBadgeText}>{s.size}</Text>
                    </View>
                    <Text style={styles.sizeFit}>{s.fit}</Text>
                  </View>
                </View>
                {i < result.sizes.length - 1 ? <View style={styles.sizeLine} /> : null}
              </View>
            ))}
          </View>

          <Text style={styles.note}>
            * 실제와 오차가 있을 수 있어요. 결과는 2D 가상착장·사이즈 추천에 활용돼요.
          </Text>

          <Pressable
            style={styles.remeasure}
            onPress={() =>
              router.replace({
                pathname: '/measure-input',
                params: returnTo ? { returnTo } : undefined,
              })
            }>
            <Icon name="arrow.clockwise" tintColor={ink(0.5)} size={14} />
            <Text style={styles.remeasureText}>다시 측정하기</Text>
          </Pressable>
        </ScrollView>

        <View style={[styles.bottomBar, { paddingBottom: 12 }, contentStyle(ContentMax.narrow)]}>
          <Pressable
            style={[styles.cta, (savingDone || invalid.length > 0) && styles.ctaOff]}
            onPress={onDone}
            disabled={savingDone || invalid.length > 0}>
            <Text style={styles.ctaText}>{savingDone ? '저장 중…' : '완료'}</Text>
          </Pressable>
        </View>
      </SafeAreaView>

      <MeasureGuideSheet
        visible={guideKey !== null}
        measureKey={guideKey}
        onClose={() => setGuideKey(null)}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  safe: { flex: 1 },
  content: { paddingHorizontal: 24, paddingTop: 12, paddingBottom: 24 },
  stateWrap: { flex: 1, paddingHorizontal: 24, paddingTop: 12 },
  stateFill: { flex: 1 },
  photoFailure: { alignItems: 'center', justifyContent: 'center', paddingHorizontal: 32 },
  failureIcon: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: Editorial.accent,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 16,
  },
  failureTitle: { fontSize: Type.label, fontWeight: '600', color: INK },
  failureDescription: {
    marginTop: 7,
    marginBottom: 24,
    fontSize: Type.footnote,
    lineHeight: 20,
    color: Editorial.textCaption,
    textAlign: 'center',
  },
  failurePrimary: {
    width: '100%',
    height: 48,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    borderRadius: 8,
    backgroundColor: INK,
  },
  failurePrimaryText: { fontSize: Type.footnote, fontWeight: '600', color: '#fff' },
  failureSecondary: {
    width: '100%',
    height: 48,
    marginTop: 10,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: ink(0.15),
  },
  failureSecondaryText: { fontSize: Type.footnote, fontWeight: '600', color: INK },

  steps: { flexDirection: 'row', gap: 6, marginBottom: 28 },
  step: { flex: 1, height: 3, borderRadius: 2, backgroundColor: ink(0.1) },
  stepOn: { backgroundColor: Editorial.selected },

  hero: { alignItems: 'center', gap: 8 },
  mark: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: INK,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 6,
  },
  title: { fontFamily: Fonts.serif, fontSize: 26, color: INK },
  lead: {
    fontSize: Type.footnote,
    lineHeight: 20,
    color: Editorial.textCaption,
    textAlign: 'center',
  },
  bodyType: { marginTop: 8, fontSize: Type.body, fontWeight: '600', color: INK },

  sectionTitle: { fontSize: Type.label, fontWeight: '600', color: INK, marginTop: 30, marginBottom: 12 },
  sectionHead: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 30,
    marginBottom: 12,
  },
  sectionTitlePlain: { fontSize: Type.label, fontWeight: '600', color: INK },
  editHint: { fontSize: Type.micro, color: Editorial.textCaption },

  guideBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    marginTop: 18,
    paddingVertical: 14,
    paddingHorizontal: 16,
    borderWidth: 1,
    borderColor: Editorial.line,
    borderRadius: 14,
  },
  guideBannerIcon: {
    width: 32,
    height: 32,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: Editorial.line,
    alignItems: 'center',
    justifyContent: 'center',
  },
  guideBannerTexts: { flex: 1, gap: 3 },
  guideBannerTitle: { fontSize: Type.footnote, fontWeight: '600', color: INK },
  guideBannerBody: { fontSize: Type.micro, color: Editorial.textCaption, lineHeight: 17 },

  measureGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    borderWidth: 1,
    borderColor: ink(0.09),
    borderRadius: 16,
    overflow: 'hidden',
  },
  measureTile: { width: '50%', paddingHorizontal: 18, paddingVertical: 16, gap: 6 },
  measureLabelRow: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  measureLabel: { fontSize: Type.micro, color: Editorial.textCaption },
  measureValueRow: { flexDirection: 'row', alignItems: 'flex-end', gap: 4 },
  measureInput: {
    fontFamily: Fonts.serif,
    fontSize: 20,
    fontWeight: '600',
    color: INK,
    padding: 0,
    minWidth: 48,
    borderBottomWidth: 1,
    borderBottomColor: ink(0.18),
    paddingBottom: 2,
  },
  /* 입력칸과 같은 글자 크기·무게, 밑줄만 없다 — '고칠 수 있는 것'은 밑줄로만 구분한다 */
  measureReadonly: {
    fontFamily: Fonts.serif,
    fontSize: 20,
    fontWeight: '600',
    color: INK,
    paddingBottom: 3,
  },
  measureInputBad: { color: Editorial.danger, borderBottomColor: Editorial.danger },
  measureUnit: { fontSize: Type.micro, color: Editorial.textCaption, marginBottom: 3 },

  /* 그리드 마지막 줄 전체를 차지한다 — 타일이 홀수 개여도 버튼이 한가운데 온다 */
  moreRow: {
    width: '100%',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 5,
    paddingVertical: 14,
    borderTopWidth: 1,
    borderTopColor: ink(0.07),
  },
  moreText: { fontSize: Type.caption, color: Editorial.textCaption },
  ratioNote: { fontSize: Type.micro, color: Editorial.textCaption, lineHeight: 18, marginTop: 10 },

  invalidText: { fontSize: Type.caption, color: Editorial.danger, marginTop: 10 },

  sizeCard: { borderWidth: 1, borderColor: ink(0.09), borderRadius: 16, paddingHorizontal: 16 },
  sizeRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 16,
  },
  sizeBrand: { fontSize: 14.5, color: Editorial.ink, fontWeight: '500' },
  sizeRight: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  sizeBadge: {
    minWidth: 34,
    height: 30,
    paddingHorizontal: 10,
    borderRadius: 8,
    backgroundColor: INK,
    alignItems: 'center',
    justifyContent: 'center',
  },
  sizeBadgeText: { fontSize: Type.caption, color: '#fff', fontWeight: '700' },
  sizeFit: { fontSize: Type.micro, color: Editorial.textCaption, width: 58, textAlign: 'right' },
  sizeLine: { height: 1, backgroundColor: ink(0.07) },

  note: { fontSize: Type.micro, color: Editorial.textCaption, lineHeight: 18, marginTop: 16 },
  remeasure: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    alignSelf: 'center',
    marginTop: 22,
    paddingVertical: 6,
  },
  remeasureText: { fontSize: Type.caption, color: Editorial.textCaption },

  bottomBar: {
    paddingHorizontal: 24,
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: ink(0.08),
  },
  cta: {
    height: 52,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
  },
  ctaOff: { opacity: 0.45 },
  ctaText: { color: '#fff', fontSize: Type.body, fontWeight: '500' },
});
