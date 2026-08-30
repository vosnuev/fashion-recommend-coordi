import { router } from 'expo-router';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { Editorial } from '@/constants/theme';
import { outfitAnalysisStore, useOutfitAnalysis } from '@/state/outfit-analysis';

/**
 * 착장 분석 진행/완료 상태 카드.
 *
 * 원래 home.tsx 의 EmptyClosetStart 안에 박혀 있었다. 그래서 옷장이 빈 사용자만
 * 진행 상황을 볼 수 있었고, 홈이 다른 분기를 그리는 순간(옷장에 옷이 생기거나
 * 오늘의 룩을 보여주게 되면) 분석이 돌고 있어도 화면에서 사라졌다.
 * 슬롯(HomeStatusSlot)이 정확히 이런 **일시적인 상태 카드**를 위한 자리라 이리로 옮겼다.
 *
 * 보여줄 게 없으면 반드시 null — 그래야 평소에 홈이 길어지지 않는다(슬롯 계약).
 */
export function AnalysisStatusCard() {
  const { job } = useOutfitAnalysis();
  if (!job) return null;

  const pending = outfitAnalysisStore.isPending(job);
  return (
    <Pressable style={styles.card} onPress={() => router.push('/outfit-review')}>
      <View style={styles.icon}>
        {pending ? (
          <ActivityIndicator size="small" color={Editorial.selected} />
        ) : (
          <Text style={styles.mark}>{job.phase === 'SUCCEEDED' ? '✓' : '!'}</Text>
        )}
      </View>
      <View style={styles.text}>
        <Text style={styles.title}>
          {pending
            ? '착장 분석이 진행 중이에요'
            : job.phase === 'SUCCEEDED'
              ? '착장 분석이 완료됐어요'
              : '착장 분석을 완료하지 못했어요'}
        </Text>
        <Text style={styles.body} numberOfLines={1}>
          {pending
            ? '다른 화면을 둘러봐도 분석은 계속됩니다.'
            : job.phase === 'SUCCEEDED'
              ? '눌러서 분석 결과를 확인해 보세요.'
              : job.detail}
        </Text>
      </View>
      <Text style={styles.arrow}>›</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    alignSelf: 'stretch',
    minHeight: 72,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 16,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    backgroundColor: Editorial.page,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  icon: { width: 28, height: 28, alignItems: 'center', justifyContent: 'center' },
  mark: { fontSize: 17, fontWeight: '700', color: Editorial.selected },
  text: { flex: 1 },
  title: { fontSize: 14, fontWeight: '700', color: Editorial.ink },
  body: { marginTop: 4, fontSize: 12, color: Editorial.textCaption },
  arrow: { fontSize: 24, color: Editorial.textCaption },
});
