import { Icon } from '@/components/icon';
import { SegmentedToggle } from '@/components/ui';
import { router, useLocalSearchParams } from 'expo-router';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ContentMax, Editorial, Type } from '@/constants/theme';
import { PRIVACY_SECTIONS, TERMS_SECTIONS } from '@/constants/support';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { goBack } from '@/lib/goBack';

const INK = Editorial.ink;

type Doc = 'terms' | 'privacy';
const DOCS: { value: Doc; label: string }[] = [
  { value: 'terms', label: '이용약관' },
  { value: 'privacy', label: '개인정보' },
];

/**
 * 이용약관 · 개인정보 처리방침 전문.
 *
 * 두 문서를 한 화면에 세그먼트로 둔 이유: 사용자는 보통 "내 사진 어떻게 되나"를 찾아 들어오는데,
 * 그 답이 어느 문서에 있는지 모른다. 오가는 비용을 없앤다.
 * 어느 쪽을 열지는 URL 파라미터로 받아 링크가 바로 그 문서를 가리킬 수 있게 한다.
 */
export default function TermsScreen() {
  const { contentStyle } = useBreakpoint();
  const { doc } = useLocalSearchParams<{ doc?: string }>();
  const current: Doc = doc === 'privacy' ? 'privacy' : 'terms';
  const sections = current === 'privacy' ? PRIVACY_SECTIONS : TERMS_SECTIONS;

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top']} style={styles.headerSafe}>
        <View style={[styles.header, contentStyle(ContentMax.narrow)]}>
          <Pressable hitSlop={12} onPress={() => goBack('/(tabs)/support')}>
            <Icon name="chevron.left" tintColor={INK} size={20} />
          </Pressable>
          <Text style={styles.headerTitle}>약관·정책</Text>
        </View>
        <View style={[styles.toggleWrap, contentStyle(ContentMax.narrow)]}>
          <SegmentedToggle
            value={current}
            options={DOCS}
            onChange={(v) => router.setParams({ doc: v })}
          />
        </View>
      </SafeAreaView>

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={[
          styles.content,
          { paddingBottom: 24 },
          contentStyle(ContentMax.narrow),
        ]}>
        <Text style={styles.title}>
          {current === 'privacy' ? '개인정보 처리방침' : '이용약관'}
        </Text>

        {sections.map((s) => (
          <View key={s.title} style={styles.section}>
            <Text style={styles.sectionTitle}>{s.title}</Text>
            <Text style={styles.body}>{s.body}</Text>
          </View>
        ))}

        {/* 검토 전 초안임을 숨기지 않는다 — 다 된 문서인 척하면 아무도 확인하지 않는다. */}
        <View style={styles.draftNote}>
          <Icon name="exclamationmark.triangle" tintColor={Editorial.wine} size={14} />
          <Text style={styles.draftText}>
            법적 검토 전 초안이에요. 서비스를 정식으로 열기 전에 내용을 다시 확인할 거예요.
          </Text>
        </View>
      </ScrollView>
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
  headerTitle: { fontSize: Type.label, fontWeight: '600', color: INK },
  toggleWrap: { paddingHorizontal: 20, paddingBottom: 8, alignItems: 'flex-start' },

  content: { paddingHorizontal: 20, paddingTop: 12 },
  title: { fontSize: Type.lead, fontWeight: '700', color: INK, marginBottom: 6 },

  section: { marginTop: 22 },
  sectionTitle: { fontSize: Type.footnote, fontWeight: '600', color: INK, marginBottom: 7 },
  body: { fontSize: Type.caption, color: Editorial.textSoft, lineHeight: 22 },

  draftNote: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 9,
    marginTop: 32,
    padding: 14,
    borderRadius: 12,
    backgroundColor: Editorial.surfaceSoft,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  draftText: { flex: 1, fontSize: Type.micro, color: Editorial.textCaption, lineHeight: 18 },
});
