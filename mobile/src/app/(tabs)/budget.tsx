import { Icon } from '@/components/icon';
import { useToast } from '@/components/ui';
import { ContentMax, Editorial, Fonts, ink } from '@/constants/theme';
import { goBack } from '@/lib/goBack';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import {
  BUDGET_CATEGORIES,
  DEFAULT_CATEGORY_BUDGETS,
  type BudgetCategory,
  type CategoryBudgets,
  prefsStore,
  usePrefs,
} from '@/state/prefs';
import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

const INK = Editorial.ink;
const SLIDER_MAX = 50;
const SLIDER_TICKS = [10, 20, 30, 40, 50];

function BudgetSlider({
  value,
  onChange,
  label,
}: {
  value: number;
  onChange: (value: number) => void;
  label: string;
}) {
  const [width, setWidth] = useState(0);
  const progress = (Math.min(SLIDER_MAX, Math.max(0, value)) / SLIDER_MAX) * 100;
  const update = (x: number) => {
    if (!width) return;
    const raw = (Math.min(width, Math.max(0, x)) / width) * SLIDER_MAX;
    onChange(Math.max(1, Math.round(raw)));
  };

  return (
    <View style={styles.sliderArea}>
      <View
        style={styles.slider}
        onLayout={(event) => setWidth(event.nativeEvent.layout.width)}
        onStartShouldSetResponder={() => true}
        onMoveShouldSetResponder={() => true}
        onResponderGrant={(event) => update(event.nativeEvent.locationX)}
        onResponderMove={(event) => update(event.nativeEvent.locationX)}
        accessible
        accessibilityRole="adjustable"
        accessibilityLabel={label}
        accessibilityValue={{ min: 1, max: SLIDER_MAX, now: value, text: `${value}만원` }}
        accessibilityActions={[{ name: 'increment' }, { name: 'decrement' }]}
        onAccessibilityAction={(event) =>
          onChange(
            Math.min(
              SLIDER_MAX,
              Math.max(1, value + (event.nativeEvent.actionName === 'increment' ? 1 : -1)),
            ),
          )
        }>
        <View style={styles.sliderTrack} />
        <View style={[styles.sliderFill, { width: `${progress}%` }]} />
        {SLIDER_TICKS.map((tick) => (
          <View key={tick} style={[styles.sliderTick, { left: `${(tick / SLIDER_MAX) * 100}%` }]} />
        ))}
        <View style={[styles.sliderThumb, { left: `${progress}%` }]} />
      </View>
      <View style={styles.tickLabels}>
        {SLIDER_TICKS.map((tick) => (
          <Pressable
            key={tick}
            hitSlop={6}
            onPress={() => onChange(tick)}
            accessibilityRole="button"
            accessibilityLabel={`${tick}만원으로 선택`}
            style={[styles.tickButton, { left: `${(tick / SLIDER_MAX) * 100}%` }]}>
            <Text style={styles.tickLabel}>{tick}</Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

function toInputs(values: CategoryBudgets): Record<BudgetCategory, string> {
  return Object.fromEntries(
    BUDGET_CATEGORIES.map((category) => [
      category,
      values[category] == null ? '' : String(values[category]! / 10_000),
    ]),
  ) as Record<BudgetCategory, string>;
}

export default function Budget() {
  const { contentStyle } = useBreakpoint();
  const prefs = usePrefs();
  const [inputs, setInputs] = useState(() => toInputs(prefs.categoryBudgets));
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  const change = (category: BudgetCategory, text: string) => {
    setInputs((current) => ({ ...current, [category]: text.replace(/[^0-9]/g, '') }));
  };

  const save = async () => {
    if (saving) return;
    const values = Object.fromEntries(
      BUDGET_CATEGORIES.flatMap((category) => {
        const manwon = Number(inputs[category]);
        return manwon > 0 ? [[category, manwon * 10_000]] : [];
      }),
    ) as CategoryBudgets;

    setSaving(true);
    try {
      await prefsStore.saveBudget(values);
    } catch (error) {
      toast(error instanceof Error ? error.message : '예산을 저장하지 못했어요', {
        variant: 'error',
      });
      return;
    } finally {
      setSaving(false);
    }
    toast('카테고리별 예산을 저장했어요', { variant: 'success' });
    goBack('/(tabs)/my');
  };

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top']} style={styles.headerSafe}>
        <View style={[styles.header, contentStyle(ContentMax.narrow)]}>
          <Pressable hitSlop={12} onPress={() => goBack('/(tabs)/my')}>
            <Icon name="chevron.left" tintColor={INK} size={20} />
          </Pressable>
          <Text style={styles.headerTitle}>예산 설정</Text>
          <View style={{ width: 20 }} />
        </View>
      </SafeAreaView>

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={[styles.content, contentStyle(ContentMax.narrow)]}>
        <Text style={styles.title}>상품 한 개에 얼마까지 사용할 수 있나요?</Text>
        <Text style={styles.lead}>
          슬라이더를 움직여 만원 단위로 자유롭게 조절하세요.{`\n`}
          아래 금액은 빠른 선택이며, 입력을 비우면 기본값으로 돌아가요.{`\n`}
          50만원을 넘는 예산은 직접 입력할 수 있어요.
        </Text>

        <View style={styles.list}>
          {BUDGET_CATEGORIES.map((category) => (
            <View key={category} style={styles.row}>
              <View style={styles.rowHeader}>
                <Text style={styles.category}>{category}</Text>
                <Text style={styles.defaultValue}>
                  기본 {DEFAULT_CATEGORY_BUDGETS[category]! / 10_000}만원
                </Text>
              </View>
              <View style={styles.controls}>
                <BudgetSlider
                  value={Number(inputs[category]) || DEFAULT_CATEGORY_BUDGETS[category]! / 10_000}
                  onChange={(value) => change(category, String(value))}
                  label={`${category} 상품 1개 최대 예산`}
                />
                <View style={[styles.inputRow, inputs[category] && styles.inputRowActive]}>
                  <TextInput
                    style={styles.input}
                    value={inputs[category]}
                    onChangeText={(text) => change(category, text)}
                    placeholder={String(DEFAULT_CATEGORY_BUDGETS[category]! / 10_000)}
                    placeholderTextColor={ink(0.3)}
                    keyboardType="number-pad"
                    maxLength={6}
                    accessibilityLabel={`${category} 상품 1개 최대 예산`}
                  />
                  <Text style={styles.unit}>만원</Text>
                </View>
              </View>
            </View>
          ))}
        </View>
      </ScrollView>

      <View style={styles.bottomDivider} />
      <View style={[styles.bottomBar, contentStyle(ContentMax.narrow)]}>
        <Pressable style={[styles.cta, saving && styles.ctaDisabled]} onPress={save} disabled={saving}>
          <Text style={styles.ctaText}>{saving ? '저장 중…' : '저장'}</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  headerSafe: { backgroundColor: Editorial.page },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingTop: 8, paddingBottom: 10,
  },
  headerTitle: { fontSize: 15, fontWeight: '600', color: INK },
  content: { paddingHorizontal: 24, paddingTop: 12, paddingBottom: 28 },
  title: {
    fontFamily: Fonts.serif, fontSize: 24, fontWeight: '600',
    letterSpacing: -0.4, color: INK, lineHeight: 30,
  },
  lead: { maxWidth: 560, fontSize: 14, color: Editorial.textCaption, lineHeight: 22, marginTop: 14 },
  list: { marginTop: 28, gap: 24 },
  row: { gap: 8 },
  rowHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  category: { fontSize: 15, fontWeight: '600', color: INK },
  defaultValue: { fontSize: 13, color: Editorial.textCaption },
  controls: { flexDirection: 'row', alignItems: 'center', gap: 18, minWidth: 0 },
  sliderArea: { flex: 1, minWidth: 0 },
  slider: { height: 30, justifyContent: 'center' },
  sliderTrack: { height: 4, borderRadius: 2, backgroundColor: ink(0.12) },
  sliderFill: { position: 'absolute', height: 4, borderRadius: 2, backgroundColor: Editorial.selected },
  sliderTick: {
    position: 'absolute', width: 2, height: 8, marginLeft: -1,
    borderRadius: 1, backgroundColor: ink(0.24),
  },
  sliderThumb: {
    position: 'absolute', width: 20, height: 20, marginLeft: -10,
    borderRadius: 10, backgroundColor: Editorial.selected,
    borderWidth: 3, borderColor: Editorial.page,
  },
  tickLabels: { position: 'relative', height: 12, marginTop: -2 },
  tickButton: { position: 'absolute', width: 24, marginLeft: -12, alignItems: 'center' },
  tickLabel: { fontSize: 10, textAlign: 'center', color: Editorial.textCaption },
  inputRow: {
    width: 112, height: 46, flexDirection: 'row', alignItems: 'center', gap: 6, overflow: 'hidden',
    borderWidth: 1, borderColor: ink(0.12), borderRadius: 12,
    paddingHorizontal: 12, backgroundColor: '#fafaf9',
  },
  inputRowActive: { borderColor: Editorial.selected, backgroundColor: Editorial.surface },
  input: { flex: 1, minWidth: 0, fontSize: 16, textAlign: 'right', color: INK, padding: 0 },
  unit: { fontSize: 13, color: Editorial.textCaption, fontWeight: '600' },
  bottomDivider: { height: 1, backgroundColor: ink(0.08) },
  bottomBar: { backgroundColor: Editorial.page, paddingHorizontal: 24, paddingTop: 12, paddingBottom: 12 },
  cta: { height: 52, borderRadius: 999, backgroundColor: Editorial.cta, alignItems: 'center', justifyContent: 'center' },
  ctaDisabled: { opacity: 0.6 },
  ctaText: { color: '#fff', fontSize: 15, fontWeight: '500' },
});
