import { useLocalSearchParams } from 'expo-router';
import { useCallback, useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Icon } from '@/components/icon';
import { ErrorState, LoadingState, SmartImage, useToast } from '@/components/ui';
import { ContentMax, Editorial, Fonts, ink, Type } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { STYLE_FALLBACK_NOTE, toReferenceBadge } from '@/state/chat';
import { backTo, goBack } from '@/lib/goBack';
import { mallLabel, openExternal } from '@/lib/mall';
import { recommendationItemMeta } from '@/lib/recommendationPresentation';
import {
  deleteCardFeedback,
  FEEDBACK_REASONS,
  getCardRender,
  getRecommendationCard,
  itemImageUrl,
  isRenderTerminal,
  putCardFeedback,
  requestCardRender,
  type ApiCardFeedback,
  type ApiFeedbackReaction,
  type ApiRecommendationCard,
  type ApiRenderJob,
} from '@/lib/recommendApi';

const INK = Editorial.ink;

/** 이미지 생성 상태를 다시 물어보는 간격. 완성까지 보통 수십 초 걸린다. */
const RENDER_POLL_MS = 3000;


/**
 * 추천 코디 상세.
 *
 * 채팅 답변에 붙은 카드를 눌러 들어온다. 채팅 카드에는 이름·가격·썸네일만 있고,
 * **구매 링크 · 코디 이미지 · 피드백**은 전부 여기에 있다.
 *
 * ⚠️ /look-detail 과 다른 화면이다. 그쪽은 번들 목업(오늘의 룩) 기준이라 방금 받은
 *    추천을 그릴 수 없다. 이 화면은 추천 결과 API만 본다.
 */
export default function RecCard() {
  const { contentStyle } = useBreakpoint();
  const { resultId, cardId, from } = useLocalSearchParams<{
    resultId?: string;
    cardId?: string;
    from?: string;
  }>();
  const toast = useToast();
  const [card, setCard] = useState<ApiRecommendationCard | null>(null);
  /** 공유 옷 참고 배지. 참고 안 한 추천이면 null 이라 블록 자체를 그리지 않는다. */
  const referenceBadge = toReferenceBadge(card?.reference_match);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [render, setRender] = useState<ApiRenderJob | null>(null);
  const [rendering, setRendering] = useState(false);
  /* 피드백은 서버 응답이 정답이지만, 누른 즉시 반응이 보여야 해서 화면 상태를 먼저 바꾼다. */
  const [feedback, setFeedback] = useState<ApiCardFeedback | null>(null);
  const [savingFeedback, setSavingFeedback] = useState(false);

  const load = useCallback(async () => {
    if (!resultId || !cardId) return;
    setLoadError(null);
    try {
      const loaded = await getRecommendationCard(resultId, cardId);
      setCard(loaded);
      setFeedback(loaded.feedback);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : '추천 코디를 불러오지 못했어요');
    }
  }, [resultId, cardId]);

  useEffect(() => {
    load();
  }, [load]);

  /* 이미지 생성 작업은 추천이 저장될 때 서버가 미리 걸어둔다. 화면에 들어오면 한 번 물어본다
     (작업이 아직 없으면 null 이고, 그때는 아래 버튼으로 직접 건다). */
  useEffect(() => {
    if (!resultId || !cardId) return;
    let alive = true;
    getCardRender(resultId, cardId)
      .then((job) => {
        if (alive && job) setRender(job);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [resultId, cardId]);

  /* 아직 만드는 중이면 끝날 때까지 다시 묻는다. render 가 바뀔 때마다 이 효과가 다시 걸리고,
     끝난(또는 없는) 상태에서는 아예 걸리지 않아 폴링이 저절로 멈춘다. */
  useEffect(() => {
    if (!resultId || !cardId || !render || isRenderTerminal(render.status)) return;
    const timer = setTimeout(async () => {
      const job = await getCardRender(resultId, cardId).catch(() => null);
      if (job) setRender(job);
    }, RENDER_POLL_MS);
    return () => clearTimeout(timer);
  }, [resultId, cardId, render]);

  const startRender = async () => {
    if (!resultId || !cardId || rendering) return;
    setRendering(true);
    try {
      // 접수 응답을 그대로 넣으면 위 효과가 이어서 완료를 지켜본다.
      setRender(await requestCardRender(resultId, cardId));
    } catch (e) {
      toast(e instanceof Error ? e.message : '코디 이미지를 만들지 못했어요', {
        variant: 'error',
      });
    } finally {
      setRendering(false);
    }
  };

  /**
   * 반응 남기기. 같은 것을 다시 누르면 취소(삭제)다.
   * 서버가 카드당 최신 하나만 두므로 PUT 을 여러 번 보내도 마지막 것만 남는다.
   */
  const react = async (reaction: ApiFeedbackReaction) => {
    if (!resultId || !cardId || savingFeedback) return;
    const previous = feedback;
    const cancelling = previous?.reaction === reaction;
    setSavingFeedback(true);
    try {
      if (cancelling) {
        setFeedback(null);
        await deleteCardFeedback(resultId, cardId);
      } else {
        /* 반응을 바꾸면 사유는 지운다 — '비싸요'라고 골라둔 채 좋아요로 바뀌면
           서버에 앞뒤가 안 맞는 기록이 남는다. */
        const saved = await putCardFeedback(resultId, cardId, { reaction });
        setFeedback(saved);
      }
    } catch (e) {
      setFeedback(previous);
      toast(e instanceof Error ? e.message : '반응을 남기지 못했어요', { variant: 'error' });
    } finally {
      setSavingFeedback(false);
    }
  };

  /** 사유 토글 — 서버가 전체 교체(PUT)라 지금 목록을 통째로 다시 보낸다. */
  const toggleReason = async (code: string) => {
    if (!resultId || !cardId || savingFeedback || feedback?.reaction !== 'DISLIKE') return;
    const previous = feedback;
    const has = previous.reason_codes.includes(code);
    const next = has
      ? previous.reason_codes.filter((c) => c !== code)
      : [...previous.reason_codes, code];
    if (next.length > 5) {
      toast('사유는 5개까지 고를 수 있어요');
      return;
    }
    setSavingFeedback(true);
    setFeedback({ ...previous, reason_codes: next });
    try {
      const saved = await putCardFeedback(resultId, cardId, {
        reaction: 'DISLIKE',
        reasonCodes: next,
        comment: previous.comment,
      });
      setFeedback(saved);
    } catch (e) {
      setFeedback(previous);
      toast(e instanceof Error ? e.message : '사유를 저장하지 못했어요', { variant: 'error' });
    } finally {
      setSavingFeedback(false);
    }
  };

  const back = () => goBack(backTo(from, '/chat'));

  if (!resultId || !cardId) {
    return (
      <View style={styles.page}>
        <SafeAreaView edges={['top']} style={styles.flex}>
          <Header onBack={back} />
          <ErrorState
            title="어떤 추천인지 알 수 없어요"
            description="채팅의 추천 카드에서 다시 열어 주세요."
            style={styles.state}
          />
        </SafeAreaView>
      </View>
    );
  }

  return (
    <View style={styles.page}>
      <SafeAreaView edges={['top']} style={styles.flex}>
        <Header onBack={back} />

        {loadError ? (
          <ErrorState
            title="추천 코디를 불러오지 못했어요"
            description={loadError}
            onRetry={load}
            style={styles.state}
          />
        ) : !card ? (
          <LoadingState message="추천 코디를 불러오는 중…" style={styles.state} />
        ) : (
          <ScrollView
            style={styles.flex}
            contentContainerStyle={[styles.content, contentStyle(ContentMax.narrow)]}>
            <Text style={styles.title}>추천 코디 {card.rank}</Text>

            {/* 코디 이미지 — 아이템 사진을 합쳐 한 장으로 만든 것 */}
            <RenderBlock job={render} busy={rendering} onStart={startRender} />

            {/* 공유 옷을 참고한 추천이면 무엇과 비슷한 것인지 구성 아이템 앞에 밝힌다.
                근거(reasons)는 상세에서만 문장으로 보여준다 — 카드에서는 소음이다.
                점수는 어디에도 노출하지 않는다(사용자에게 뜻이 없는 숫자다). */}
            {referenceBadge ? (
              <View style={styles.refBlock}>
                <View style={styles.refBadge}>
                  <Text style={styles.refBadgeText}>{referenceBadge.label}</Text>
                </View>
                {referenceBadge.isStyleFallback ? (
                  <Text style={styles.refFallback}>{STYLE_FALLBACK_NOTE}</Text>
                ) : null}
                {referenceBadge.reasons.map((r) => (
                  <Text key={r} style={styles.refReason}>
                    · {r}
                  </Text>
                ))}
              </View>
            ) : null}

            <Text style={styles.section}>구성 아이템</Text>
            {card.items.map((item) => {
              const image = itemImageUrl(item);
              const fromWardrobe = item.source_type !== 'PRODUCT';
              const buyUrl = item.purchase_url;
              const meta = recommendationItemMeta(item);
              return (
                <View key={item.item_id} style={styles.item}>
                  <SmartImage uri={image} width={72} height={72} radius={12} />
                  <View style={styles.itemBody}>
                    <Text style={styles.itemName} numberOfLines={2}>
                      {item.display_name}
                    </Text>
                    {meta ? <Text style={styles.itemMeta}>{meta}</Text> : null}
                    {/* 옷장 옷은 살 필요가 없다는 것이 가격보다 중요한 정보다 */}
                    <Text style={styles.itemPrice}>
                      {fromWardrobe
                        ? '내 옷장'
                        : item.price_snapshot != null
                          ? `${item.price_snapshot.toLocaleString()}원`
                          : '새 상품'}
                    </Text>
                    {item.note ? <Text style={styles.itemNote}>💬 {item.note}</Text> : null}

                    {buyUrl ? (
                      <Pressable style={styles.buy} onPress={() => openExternal(buyUrl)}>
                        <Text style={styles.buyText}>{mallLabel(buyUrl)}에서 보기</Text>
                        <Icon name="arrow.up.right.square" tintColor={INK} size={13} />
                      </Pressable>
                    ) : null}
                  </View>
                </View>
              );
            })}

            {card.total_product_price ? (
              <Text style={styles.total}>
                새로 사면 {card.total_product_price.toLocaleString()}원
              </Text>
            ) : null}

            <Text style={styles.section}>이 추천 어땠나요?</Text>
            <View style={styles.reactions}>
              <ReactionButton
                icon="hand.thumbsup"
                label="좋아요"
                on={feedback?.reaction === 'LIKE'}
                disabled={savingFeedback}
                onPress={() => react('LIKE')}
              />
              <ReactionButton
                icon="hand.thumbsdown"
                label="별로예요"
                on={feedback?.reaction === 'DISLIKE'}
                disabled={savingFeedback}
                onPress={() => react('DISLIKE')}
              />
            </View>

            {/* 어디가 별로였는지 — 다음 추천을 고치는 데 쓰는 값이라 별로일 때만 묻는다. */}
            {feedback?.reaction === 'DISLIKE' ? (
              <View style={styles.reasons}>
                {FEEDBACK_REASONS.map((r) => {
                  const on = feedback.reason_codes.includes(r.code);
                  return (
                    <Pressable
                      key={r.code}
                      style={[styles.chip, on && styles.chipOn]}
                      disabled={savingFeedback}
                      onPress={() => toggleReason(r.code)}>
                      <Text style={[styles.chipText, on && styles.chipTextOn]}>{r.label}</Text>
                    </Pressable>
                  );
                })}
              </View>
            ) : null}
          </ScrollView>
        )}
      </SafeAreaView>
    </View>
  );
}

function Header({ onBack }: { onBack: () => void }) {
  return (
    <View style={styles.header}>
      <Pressable onPress={onBack} hitSlop={10} accessibilityLabel="뒤로">
        <Icon name="chevron.left" tintColor={INK} size={20} />
      </Pressable>
      <Text style={styles.headerTitle}>추천 코디</Text>
      {/* 좌우 균형용 빈 자리 — 제목이 가운데 오게 한다 */}
      <View style={styles.headerSpacer} />
    </View>
  );
}

/**
 * 코디 이미지 자리.
 * 생성은 비동기라 상태마다 다른 것을 보여준다. 실패했거나 작업이 아예 없으면 눌러서 건다.
 */
function RenderBlock({
  job,
  busy,
  onStart,
}: {
  job: ApiRenderJob | null;
  busy: boolean;
  onStart: () => void;
}) {
  if (job?.status === 'SUCCEEDED' && job.image_url) {
    /* 폭이 화면에 따라 달라지는 자리라 높이를 고정하지 않고 비율로 준다.
       착장 이미지는 세로가 길어 3:4 로 잡는다 (SmartImage 는 height 없이 두면 0이 된다). */
    return <SmartImage uri={job.image_url} width="100%" aspectRatio={3 / 4} radius={16} />;
  }
  const waiting = job?.status === 'QUEUED' || job?.status === 'PROCESSING';
  return (
    <View style={styles.renderEmpty}>
      {waiting ? (
        <Text style={styles.renderText}>코디 이미지를 만드는 중이에요…</Text>
      ) : (
        <>
          <Text style={styles.renderText}>
            {job?.status === 'FAILED'
              ? (job.error?.message ?? '이미지를 만들지 못했어요.')
              : '아이템을 합친 코디 이미지를 만들어 볼 수 있어요.'}
          </Text>
          <Pressable style={styles.renderBtn} onPress={onStart} disabled={busy}>
            <Text style={styles.renderBtnText}>
              {busy ? '접수하는 중…' : job?.status === 'FAILED' ? '다시 만들기' : '코디 이미지 만들기'}
            </Text>
          </Pressable>
        </>
      )}
    </View>
  );
}

function ReactionButton({
  icon,
  label,
  on,
  disabled,
  onPress,
}: {
  icon: 'hand.thumbsup' | 'hand.thumbsdown';
  label: string;
  on: boolean;
  disabled: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable style={[styles.reaction, on && styles.reactionOn]} disabled={disabled} onPress={onPress}>
      <Icon name={icon} tintColor={on ? '#fff' : ink(0.5)} size={16} />
      <Text style={[styles.reactionText, on && styles.reactionTextOn]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: Editorial.page },
  flex: { flex: 1 },
  state: { paddingTop: 60 },

  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  headerTitle: { flex: 1, textAlign: 'center', fontFamily: Fonts.serif, fontSize: 17, color: INK },
  headerSpacer: { width: 20 },

  content: { padding: 16, paddingBottom: 40, gap: 12 },
  title: { fontFamily: Fonts.serif, fontSize: 22, color: INK },

  renderEmpty: {
    alignItems: 'center',
    gap: 10,
    paddingVertical: 28,
    paddingHorizontal: 16,
    borderRadius: 16,
    backgroundColor: Editorial.surface,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  renderText: { fontSize: 13, color: Editorial.textCaption, textAlign: 'center', lineHeight: 19 },
  renderBtn: {
    paddingHorizontal: 16,
    height: 38,
    borderRadius: 999,
    justifyContent: 'center',
    backgroundColor: Editorial.cta,
  },
  renderBtnText: { fontSize: 13, fontWeight: '600', color: '#fff' },

  refBlock: { gap: 7, marginTop: 4 },
  refBadge: {
    alignSelf: 'flex-start',
    paddingHorizontal: 9,
    paddingVertical: 4,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  refBadgeText: { fontSize: Type.micro, color: Editorial.textCaption, fontWeight: '500' },
  refFallback: { fontSize: Type.caption, color: Editorial.textSoft, lineHeight: 19 },
  refReason: { fontSize: Type.caption, color: Editorial.textSoft, lineHeight: 19 },
  section: { marginTop: 12, fontSize: 14, fontWeight: '600', color: INK },

  item: {
    flexDirection: 'row',
    gap: 12,
    padding: 12,
    borderRadius: 14,
    backgroundColor: Editorial.surface,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  itemBody: { flex: 1, gap: 3 },
  itemName: { fontSize: 14, fontWeight: '500', color: INK },
  itemMeta: { fontSize: Type.caption, color: Editorial.textCaption },
  itemPrice: { fontSize: Type.caption, fontWeight: '600', color: INK },
  itemNote: {
    marginTop: 4,
    fontSize: Type.caption,
    color: Editorial.textSoft,
    lineHeight: 18,
  },
  buy: { flexDirection: 'row', alignItems: 'center', gap: 5, marginTop: 6 },
  buyText: { fontSize: 13, fontWeight: '600', color: INK },

  total: { marginTop: 4, fontSize: 14, fontWeight: '600', color: INK },

  reactions: { flexDirection: 'row', gap: 8 },
  reaction: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    height: 40,
    paddingHorizontal: 16,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: Editorial.line,
    backgroundColor: Editorial.surface,
  },
  reactionOn: { backgroundColor: Editorial.cta, borderColor: Editorial.cta },
  reactionText: { fontSize: 13, fontWeight: '500', color: Editorial.textCaption },
  reactionTextOn: { color: '#fff' },

  reasons: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: {
    paddingHorizontal: 12,
    height: 34,
    justifyContent: 'center',
    borderRadius: 999,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  chipOn: { borderColor: INK, backgroundColor: Editorial.control },
  chipText: { fontSize: 12.5, color: Editorial.textCaption },
  chipTextOn: { color: INK, fontWeight: '600' },
});
