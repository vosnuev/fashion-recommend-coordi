import { router } from 'expo-router';
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Icon } from '@/components/icon';
import { EmptyState, ErrorState, LoadingState, LoginGate } from '@/components/ui';
import { ContentMax, Editorial, ink } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { useOutfitHistory } from '@/hooks/use-outfit-history';
import { useRefresh } from '@/hooks/use-refresh';
import { goBack, withReturn } from '@/lib/goBack';
import type { AnalysisWeather, OutfitAnalysisListItem } from '@/lib/outfitHistoryApi';
import { useAuth } from '@/state/auth';

const INK = Editorial.ink;

/** '8월 6일 (수)' — 목록에서 날짜를 세로로 훑을 수 있게 짧게 쓴다. */
function dateLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString('ko-KR', { month: 'long', day: 'numeric', weekday: 'short' });
}

/** '서울 36° · 구름많음' — 분석 시점의 날씨라 지금과 다를 수 있다. 값이 없으면 생략. */
function weatherLabel(w: AnalysisWeather | null | undefined): string | null {
  if (!w?.region && w?.temperature == null) return null;
  const region = w?.region ?? '';
  const temp = w?.temperature != null ? `${w.temperature}°` : '';
  const head = [region, temp].filter(Boolean).join(' ');
  return w?.sky_state ? `${head} · ${w.sky_state}` : head || null;
}

/**
 * 착장 분석 기록 — 지난 분석을 서랍처럼 열어보는 화면. (홈 헤더의 보관함 아이콘으로 진입)
 *
 * 비회원에게는 목록을 주지 않는다 — 백엔드 GET /api/v1/outfits/analyses/ 가 IsAuthenticated 이고
 * 익명 기록(user=NULL)은 조회 대상에서 빠진다. 비회원 분석은 로그인 시점에 claim 으로 계정에
 * 옮겨온 뒤에야 여기에 나타난다.
 *
 * 목록에는 사진이 없어 썸네일을 못 붙인다. 사진은 상세(본인 기록)에만 image_url 로 온다 —
 * 목록에서도 보여주려면 백엔드가 목록 응답에 실어줘야 한다.
 */
export default function OutfitHistoryScreen() {
  const { isLoggedIn, isDemo } = useAuth();
  const { contentStyle } = useBreakpoint();

  /* 데모 세션은 status='authed' 지만 토큰이 없어 목록 API 가 401 이다 — 로그인으로 보지 않는다. */
  const canRead = isLoggedIn && !isDemo;

  /* 훅 순서를 지키려고 비회원이어도 훅은 전부 호출한 뒤 분기한다(호출만 막는다). */
  const { items, total, hasMore, loading, loadingMore, error, reload, loadMore } =
    useOutfitHistory(canRead);
  const { refreshing, onRefresh } = useRefresh(reload);

  if (!canRead) {
    return (
      <LoginGate
        title="분석 기록은 로그인하고 볼 수 있어요"
        body="로그인하면 그동안 분석한 착장을 모아서 다시 볼 수 있어요."
      />
    );
  }

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top']} style={styles.headerSafe}>
        <View style={[styles.header, contentStyle(ContentMax.card)]}>
          <Pressable hitSlop={12} onPress={() => goBack('/(tabs)/home')}>
            <Icon name="chevron.left" tintColor={INK} size={20} />
          </Pressable>
          <Text style={styles.headerTitle}>분석 기록</Text>
          {/* 좌우 균형용 빈 자리 — 제목이 가운데 오게 한다 */}
          <View style={styles.headerSpacer} />
        </View>
      </SafeAreaView>

      <ScrollView
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={INK} />}
        contentContainerStyle={[
          styles.content,
          { paddingBottom: 24 },
          contentStyle(ContentMax.card),
        ]}>
        {loading ? (
          <LoadingState message="분석 기록을 불러오는 중…" />
        ) : error ? (
          <ErrorState onRetry={reload} />
        ) : items.length === 0 ? (
          <EmptyState
            icon="archivebox"
            title="첫 착장을 분석해 보세요"
            description="사진 한 장이면 잘 어울리는 포인트를 짚어드려요."
            actionLabel="착장 분석하러 가기"
            onAction={() => router.push('/outfit-review')}
          />
        ) : (
          <>
            <Text style={styles.count}>전체 {total}건</Text>
            {items.map((item) => (
              <HistoryRow
                key={item.id}
                item={item}
                onPress={() =>
                  router.push(withReturn(`/analysis-detail?id=${item.id}`, '/outfit-history'))
                }
              />
            ))}
            {hasMore ? (
              <Pressable style={styles.more} onPress={loadMore} disabled={loadingMore}>
                {loadingMore ? (
                  <ActivityIndicator color={Editorial.textSoft} />
                ) : (
                  <Text style={styles.moreText}>더 보기</Text>
                )}
              </Pressable>
            ) : null}
          </>
        )}
      </ScrollView>
    </View>
  );
}

/** 기록 한 줄. 완료 전이거나 실패한 건도 그대로 보여준다 — 사라지면 사용자가 더 혼란스럽다. */
function HistoryRow({ item, onPress }: { item: OutfitAnalysisListItem; onPress: () => void }) {
  const done = item.status === 'SUCCEEDED';
  const failed = item.status === 'FAILED';
  const weather = weatherLabel(item.weather);

  return (
    <Pressable style={styles.row} onPress={onPress}>
      <View style={styles.rowHead}>
        <Text style={styles.rowDate}>{dateLabel(item.created_at)}</Text>
        {done && item.overall_score != null ? (
          <Text style={styles.score}>{item.overall_score}점</Text>
        ) : (
          <Text style={[styles.badge, failed && styles.badgeFailed]}>
            {failed ? '분석 실패' : '분석 중'}
          </Text>
        )}
      </View>

      <Text style={styles.summary} numberOfLines={2}>
        {done ? item.summary : failed ? '이 착장은 평가를 끝내지 못했어요.' : '평가를 만들고 있어요.'}
      </Text>

      {weather || item.personalized ? (
        <View style={styles.metaRow}>
          {weather ? <Text style={styles.meta}>{weather}</Text> : null}
          {/* 비회원 때 분석한 건은 취향이 반영되지 않은 결과라, 왜 결이 다른지 알 수 있게 표시한다 */}
          {item.personalized ? <Text style={styles.metaTag}>취향 반영</Text> : null}
        </View>
      ) : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Editorial.page },
  headerSafe: { backgroundColor: Editorial.page },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 8,
  },
  headerTitle: { fontSize: 16, fontWeight: '600', color: INK },
  headerSpacer: { width: 20 },

  content: { paddingHorizontal: 20, paddingTop: 12, gap: 12 },
  count: { fontSize: 12, color: Editorial.textCaption },

  row: {
    borderRadius: 20,
    backgroundColor: Editorial.surface,
    borderWidth: 1,
    borderColor: ink(0.1),
    paddingHorizontal: 18,
    paddingVertical: 16,
    gap: 8,
  },
  rowHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  rowDate: { flexShrink: 1, fontSize: 13, color: Editorial.textCaption },
  /* 점수는 실측상 대부분 85 근처라 변별력이 크지 않다 — 요약보다 앞세우지 않는다. */
  score: { fontSize: 13, fontWeight: '600', color: Editorial.textCaption },
  badge: { fontSize: 12, fontWeight: '600', color: Editorial.textSoft },
  badgeFailed: { color: Editorial.danger },
  summary: { fontSize: 15, lineHeight: 22, color: INK },
  metaRow: { flexDirection: 'row', alignItems: 'center', gap: 10, flexWrap: 'wrap' },
  meta: { fontSize: 12, color: Editorial.textCaption },
  metaTag: {
    fontSize: 11,
    fontWeight: '600',
    color: Editorial.textCaption,
    backgroundColor: Editorial.control,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 4,
    overflow: 'hidden',
  },

  more: {
    height: 48,
    marginTop: 4,
    borderRadius: 999,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Editorial.surface,
    borderWidth: 1,
    borderColor: ink(0.14),
  },
  moreText: { fontSize: 14, fontWeight: '600', color: Editorial.textSoft },
});
