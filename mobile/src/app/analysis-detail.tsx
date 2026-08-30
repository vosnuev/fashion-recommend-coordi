import { router, useLocalSearchParams } from 'expo-router';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Icon } from '@/components/icon';
import { ErrorState, LoadingState, LoginGate, SmartImage } from '@/components/ui';
import { ContentMax, Editorial, ink } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { useOutfitAnalysisDetail } from '@/hooks/use-outfit-analysis-detail';
import { backTo, goBack } from '@/lib/goBack';
import type { WardrobeLink, WardrobeLinkedItem } from '@/lib/outfitHistoryApi';
import { useAuth } from '@/state/auth';

const INK = Editorial.ink;
const PHOTO_RATIO = 1 / 1.15;

/** '2026년 8월 6일 (수) 16:25' — 상세에서는 시각까지 보여준다. */
function stampLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const date = d.toLocaleDateString('ko-KR', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'short',
  });
  const time = d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
  return `${date} ${time}`;
}

/**
 * 분석 기록 상세 — 서랍(/outfit-history)에서 한 건을 눌러 들어온다.
 *
 * 본인 기록이라 목록에 없는 것들이 여기서 열린다: 원본 사진, 평가 전문(날씨·취향 코멘트 포함),
 * 그리고 옷장 등록 진행 상황과 뽑아낸 아이템.
 *
 * 옷장 등록은 평가와 별도 파이프라인이라 평가가 끝난 뒤에도 진행 중일 수 있다.
 * 훅이 그동안 이 화면에서만 다시 조회한다(화면을 나가면 멈춘다 — 서버 작업은 계속된다).
 */
export default function AnalysisDetailScreen() {
  const { isLoggedIn, isDemo } = useAuth();
  const { contentStyle } = useBreakpoint();
  const { id, from } = useLocalSearchParams<{ id?: string; from?: string }>();

  /* 데모 세션은 토큰이 없어 본인 기록 조회가 401 이다 — 로그인으로 보지 않는다. */
  const canRead = isLoggedIn && !isDemo;

  /* 훅 순서를 지키려고 비회원이어도 전부 호출한 뒤 분기한다. */
  const { analysis, loading, error, stalled, reload } = useOutfitAnalysisDetail(canRead ? id : undefined);

  if (!canRead) {
    return (
      <LoginGate
        title="분석 기록은 로그인하고 볼 수 있어요"
        body="로그인하면 그동안 분석한 착장을 모아서 다시 볼 수 있어요."
      />
    );
  }

  const evaluation = analysis?.evaluation ?? null;

  return (
    <View style={styles.container}>
      <SafeAreaView edges={['top']} style={styles.headerSafe}>
        <View style={[styles.header, contentStyle(ContentMax.card)]}>
          <Pressable hitSlop={12} onPress={() => goBack(backTo(from, '/outfit-history'))}>
            <Icon name="chevron.left" tintColor={INK} size={20} />
          </Pressable>
          <Text style={styles.headerTitle}>분석 상세</Text>
          <View style={styles.headerSpacer} />
        </View>
      </SafeAreaView>

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={[
          styles.content,
          { paddingBottom: 24 },
          contentStyle(ContentMax.card),
        ]}>
        {loading && !analysis ? (
          <LoadingState message="분석 기록을 불러오는 중…" />
        ) : error || !analysis ? (
          <ErrorState onRetry={reload} />
        ) : (
          <>
            {analysis.image_url ? (
              /* presigned URL 은 1시간 만료라 저장하지 않고 매번 받은 값을 그대로 쓴다. */
              <SmartImage
                uri={analysis.image_url}
                width="100%"
                aspectRatio={PHOTO_RATIO}
                radius={20}
                contentFit="cover"
              />
            ) : null}

            <View style={styles.stampRow}>
              <Text style={styles.stamp}>{stampLabel(analysis.created_at)}</Text>
              {evaluation ? <Text style={styles.score}>{evaluation.overall_score}점</Text> : null}
            </View>

            {analysis.status === 'FAILED' ? (
              <Text style={styles.failed}>
                {analysis.error_message || '이 착장은 평가를 끝내지 못했어요.'}
              </Text>
            ) : !evaluation ? (
              <Text style={styles.body}>아직 평가를 만들고 있어요.</Text>
            ) : (
              <>
                <Text style={styles.summary}>{evaluation.summary}</Text>

                <Section title="잘 어울리는 포인트" lines={evaluation.strengths} />
                <Section title="더 좋아질 수 있는 제안" lines={evaluation.styling_tips} />

                {evaluation.weather_comment ? (
                  <Note label="날씨" text={evaluation.weather_comment} />
                ) : null}
                {evaluation.personalization_comment ? (
                  <Note
                    label={analysis.personalized ? '취향 반영' : '취향'}
                    text={evaluation.personalization_comment}
                  />
                ) : null}
              </>
            )}

            {analysis.wardrobe ? (
              <WardrobeSection link={analysis.wardrobe} stalled={stalled} onRefresh={reload} />
            ) : null}
          </>
        )}
      </ScrollView>
    </View>
  );
}

function Section({ title, lines }: { title: string; lines: string[] }) {
  if (!lines.length) return null;
  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>{title}</Text>
      {lines.map((line) => (
        <Text key={line} style={styles.cardText}>
          • {line}
        </Text>
      ))}
    </View>
  );
}

function Note({ label, text }: { label: string; text: string }) {
  return (
    <View style={styles.note}>
      <Text style={styles.noteLabel}>{label}</Text>
      <Text style={styles.noteText}>{text}</Text>
    </View>
  );
}

/**
 * 옷장 등록 섹션. 평가와 다른 파이프라인이라 상태를 그대로 보여준다 —
 * 처리 중인데 아무 말도 없으면 사용자는 등록이 안 된 것으로 읽는다.
 */
function WardrobeSection({
  link,
  stalled,
  onRefresh,
}: {
  link: WardrobeLink;
  /** 폴링 상한에 걸려 더 이상 지켜보지 않는 상태 */
  stalled: boolean;
  onRefresh: () => void;
}) {
  const pending = link.status === 'PENDING' || link.status === 'PROCESSING';

  return (
    <View style={styles.wardrobe}>
      <View style={styles.wardrobeHead}>
        <Text style={styles.wardrobeTitle}>이 사진에서 찾은 옷</Text>
        {pending && !stalled ? <ActivityIndicator size="small" color={Editorial.textCaption} /> : null}
      </View>

      {pending && stalled ? (
        /* 지켜보기를 멈춘 상태 — 스피너를 계속 돌리면 영영 처리 중인 것처럼 보인다. */
        <>
          <Text style={styles.body}>생각보다 오래 걸리고 있어요. 다 됐는지 눌러서 확인해 보세요.</Text>
          <Pressable style={styles.wardrobeCta} onPress={onRefresh}>
            <Text style={styles.wardrobeCtaText}>다시 확인하기</Text>
          </Pressable>
        </>
      ) : pending ? (
        <Text style={styles.body}>옷을 하나씩 분리하고 있어요. 몇 분 걸릴 수 있어요.</Text>
      ) : link.status === 'FAILED' ? (
        <Text style={styles.failed}>
          {link.error_message || '옷을 옷장에 등록하지 못했어요.'}
        </Text>
      ) : link.items.length === 0 ? (
        <Text style={styles.body}>이 사진에서는 옷을 찾지 못했어요.</Text>
      ) : (
        <>
          {link.items.map((item) => (
            <WardrobeItemRow key={item.id} item={item} />
          ))}
          {/* 확정 전 아이템은 추천 검색에서 빠진다 — 옷장에서 태그를 확인해야 한다. */}
          {link.items.some((item) => !item.confirmed) ? (
            <Pressable style={styles.wardrobeCta} onPress={() => router.push('/(tabs)/closet')}>
              <Text style={styles.wardrobeCtaText}>옷장에서 확인하기</Text>
            </Pressable>
          ) : null}
        </>
      )}
    </View>
  );
}

function WardrobeItemRow({ item }: { item: WardrobeLinkedItem }) {
  const meta = [item.category_large, item.category_small, item.color].filter(Boolean).join(' · ');
  return (
    <View style={styles.itemRow}>
      <SmartImage uri={item.image_url} width={56} height={56} radius={12} contentFit="contain" />
      <View style={styles.itemText}>
        <Text style={styles.itemName} numberOfLines={1}>
          {item.item_name || '이름 없는 아이템'}
        </Text>
        {meta ? (
          <Text style={styles.itemMeta} numberOfLines={1}>
            {meta}
          </Text>
        ) : null}
      </View>
      {!item.confirmed ? <Text style={styles.pendingTag}>확인 필요</Text> : null}
    </View>
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

  content: { paddingHorizontal: 20, paddingTop: 12, gap: 14 },
  stampRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  stamp: { flexShrink: 1, fontSize: 13, color: Editorial.textCaption },
  /* 점수는 실측상 대부분 85 근처라 변별력이 크지 않다 — 평가 문구가 주인공이다. */
  score: { fontSize: 14, fontWeight: '600', color: Editorial.textSoft },
  summary: { fontSize: 17, lineHeight: 26, color: INK },
  body: { fontSize: 14, lineHeight: 21, color: Editorial.textCaption },
  failed: { fontSize: 14, lineHeight: 21, color: Editorial.danger },

  card: {
    borderRadius: 18,
    backgroundColor: Editorial.surface,
    borderWidth: 1,
    borderColor: ink(0.1),
    paddingHorizontal: 18,
    paddingVertical: 16,
    gap: 8,
  },
  cardTitle: { fontSize: 14, fontWeight: '700', color: INK },
  cardText: { fontSize: 14, lineHeight: 22, color: Editorial.textSoft },

  note: { gap: 4 },
  noteLabel: { fontSize: 11, letterSpacing: 1.2, fontWeight: '600', color: Editorial.textCaption },
  noteText: { fontSize: 14, lineHeight: 22, color: Editorial.textSoft },

  wardrobe: {
    marginTop: 6,
    borderRadius: 20,
    backgroundColor: Editorial.control,
    paddingHorizontal: 18,
    paddingVertical: 16,
    gap: 12,
  },
  wardrobeHead: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  wardrobeTitle: { flex: 1, fontSize: 14, fontWeight: '700', color: INK },
  itemRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  itemText: { flex: 1, gap: 3 },
  itemName: { fontSize: 14, fontWeight: '600', color: INK },
  itemMeta: { fontSize: 12, color: Editorial.textCaption },
  pendingTag: { fontSize: 11, fontWeight: '600', color: Editorial.textCaption },
  wardrobeCta: {
    height: 44,
    marginTop: 2,
    borderRadius: 999,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Editorial.surface,
    borderWidth: 1,
    borderColor: ink(0.14),
  },
  wardrobeCtaText: { fontSize: 14, fontWeight: '600', color: Editorial.textSoft },
});
