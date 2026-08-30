import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { Icon, type IconName } from '@/components/icon';
import { Skeleton, SmartImage } from '@/components/ui';
import { Editorial, ink, Type } from '@/constants/theme';
import { isStylistMocked, type StylistId } from '@/lib/stylistApi';
import {
  STYLE_FALLBACK_NOTE,
  WARDROBE_UNAVAILABLE_CODE,
  WARDROBE_UNAVAILABLE_MESSAGE,
  type StylistCard,
} from '@/state/chat';

const INK = Editorial.ink;

/**
 * 스타일리스트별 추천 카드 (설계서 11장).
 *
 * 카드 한 장이 스타일리스트 한 명이고, 한 명당 코디는 하나다. 본문에는 **핵심 문장 하나만**
 * 두고 세부 근거는 접어 둔다 — 셋을 나란히 놓으면 카드마다 문단이 붙어 읽을 수 없게 된다.
 *
 * 카드는 인원수만큼 **먼저 깔린다.** 아직 안 끝난 자리는 뼈대만 있고, 끝난 것부터 내용이
 * 채워진다. 그래서 몇 장이 올지 처음부터 보이고, 먼저 끝난 추천을 남을 기다리며 못 보는 일이 없다.
 */

/**
 * 카드 머리의 글자 옆 표시. **이름이 정체를 말하고 이 아이콘은 거들기만 한다** —
 * 서버가 페르소나를 늘리면 모르는 id 가 오므로 아이콘만으로는 누구인지 알 수 없어야 정상이다.
 */
const PERSONA_ICON: Record<StylistId, IconName> = {
  minimal: 'checkmark',
  experimental: 'sparkles',
  practical: 'sun.max',
};

function iconOf(personaId: StylistId): IconName {
  return PERSONA_ICON[personaId] ?? 'person';
}

export function StylistCardGroup({
  cards,
  onSelect,
  onAlternative,
  onRetry,
  onRenderRetry,
  wardrobeBased,
  onOpenWardrobe,
}: {
  cards: StylistCard[];
  onSelect: (personaId: StylistId) => void;
  onAlternative: (personaId: StylistId) => void;
  onRetry: (personaId: StylistId) => void;
  onRenderRetry: (personaId: StylistId) => void;
  wardrobeBased: boolean;
  onOpenWardrobe: () => void;
}) {
  /* 목업으로 그리는 중이면 숨기지 않고 말한다. 지어낸 코디를 진짜 추천으로 읽고
     스크린샷이 돌아다니면 그게 더 큰 문제가 된다. (판정은 첫 목록 조회에서 이미 끝나 있어
     카드가 그려질 시점엔 값이 바뀌지 않는다 — 구독하지 않아도 된다.) */
  const mocked = isStylistMocked();

  return (
    <View style={styles.group}>
      {mocked ? (
        <View style={styles.mockNote}>
          <Icon name="exclamationmark.triangle" tintColor={Editorial.textMuted} size={11} />
          <Text style={styles.mockNoteText}>
            서버에 스타일리스트 API가 아직 없어 예시로 그린 카드예요
          </Text>
        </View>
      ) : null}
      {cards.map((card) => (
        <StylistOutfitCard
          key={card.personaId}
          card={card}
          onSelect={() => onSelect(card.personaId)}
          onAlternative={() => onAlternative(card.personaId)}
          onRetry={() => onRetry(card.personaId)}
          onRenderRetry={() => onRenderRetry(card.personaId)}
          wardrobeBased={wardrobeBased}
          onOpenWardrobe={onOpenWardrobe}
        />
      ))}
    </View>
  );
}

function StylistOutfitCard({
  card,
  onSelect,
  onAlternative,
  onRetry,
  onRenderRetry,
  wardrobeBased,
  onOpenWardrobe,
}: {
  card: StylistCard;
  onSelect: () => void;
  onAlternative: () => void;
  onRetry: () => void;
  onRenderRetry: () => void;
  wardrobeBased: boolean;
  onOpenWardrobe: () => void;
}) {
  const waiting = card.status === 'PENDING' || card.status === 'RUNNING';
  const wardrobeUnavailable =
    wardrobeBased && card.errorCode === WARDROBE_UNAVAILABLE_CODE;

  return (
    <View style={styles.card}>
      <View style={styles.head}>
        <View style={styles.mark}>
          <Icon name={iconOf(card.personaId)} tintColor="#fff" size={13} />
        </View>
        <Text style={styles.name}>{card.name}</Text>
        {/* 몇 번째로 받은 추천인지. 처음 것에는 붙이지 않는다 — 없는 정보를 만들지 않게. */}
        {card.alternativeCount > 0 && !card.alternating ? (
          <Text style={styles.generation}>{card.alternativeCount + 1}번째 추천</Text>
        ) : null}
      </View>

      {waiting ? (
        <View style={styles.waiting}>
          <Skeleton height={14} width="88%" />
          <Skeleton height={14} width="62%" />
          <View style={styles.waitingRow}>
            <ActivityIndicator size="small" color={ink(0.4)} />
            <Text style={styles.waitingText}>
              {card.status === 'RUNNING' ? '코디를 고르는 중…' : '차례를 기다리는 중…'}
            </Text>
          </View>
        </View>
      ) : card.status === 'FAILED' ? (
        /* 한 명이 실패해도 나머지 카드는 그대로다. 이 자리만 사유와 다시 시도를 보여준다. */
        <View style={styles.failed}>
          <View style={styles.failedRow}>
            <Icon name="exclamationmark.triangle" tintColor={Editorial.wine} size={13} />
            <Text style={styles.failedText}>
              {wardrobeUnavailable
                ? WARDROBE_UNAVAILABLE_MESSAGE
                : card.errorText ?? '이 관점에서는 코디를 만들지 못했어요.'}
            </Text>
          </View>
          {wardrobeUnavailable ? (
            <Pressable style={styles.wardrobeBtn} onPress={onOpenWardrobe}>
              <Icon name="plus" tintColor="#fff" size={13} />
              <Text style={styles.wardrobeBtnText}>옷장에 옷 추가하기</Text>
            </Pressable>
          ) : (
            <Pressable style={styles.ghostBtn} onPress={onRetry}>
              <Icon name="arrow.clockwise" tintColor={INK} size={13} />
              <Text style={styles.ghostText}>이 스타일리스트만 다시</Text>
            </Pressable>
          )}
        </View>
      ) : (
        <>
          {/* 공유 옷을 참고했으면 무엇과 비슷한지 먼저 — 기본 추천 카드와 같은 자리·같은 문구. */}
          {card.referenceBadge ? (
            <View style={styles.refMatch}>
              <View style={styles.refBadge}>
                <Text style={styles.refBadgeText}>{card.referenceBadge.label}</Text>
              </View>
              {/* fallback 은 실패가 아니라 정상 결과다 — 경고색·경고아이콘을 쓰지 않는다. */}
              {card.referenceBadge.isStyleFallback ? (
                <Text style={styles.refFallback}>{STYLE_FALLBACK_NOTE}</Text>
              ) : null}
            </View>
          ) : null}

          {/* 페르소나 관점의 핵심 문장 하나 */}
          {card.message ? <Text style={styles.message}>{card.message}</Text> : null}

          <View style={styles.items}>
            {card.items.map((item) => (
              <View key={item.id} style={styles.item}>
                <SmartImage
                  uri={item.imageUrl}
                  width={64}
                  height={64}
                  radius={10}
                  style={styles.itemImage}
                />
                <Text style={styles.itemName} numberOfLines={2}>
                  {item.name}
                </Text>
                {/* 옷장 옷은 살 필요가 없다는 것이 가격보다 중요한 정보다 */}
                <Text style={styles.itemMeta}>
                  {item.fromWardrobe
                    ? '내 옷장'
                    : item.price != null
                      ? `${item.price.toLocaleString()}원`
                      : '새 상품'}
                </Text>
              </View>
            ))}
          </View>

          {card.totalPrice ? (
            <Text style={styles.total}>새로 사면 {card.totalPrice.toLocaleString()}원</Text>
          ) : null}

          {card.warnings.map((w) => (
            <Text key={w} style={styles.warning}>
              {w}
            </Text>
          ))}

          {card.renderStatus === 'SUCCEEDED' && card.renderImageUrl ? (
            <SmartImage
              uri={card.renderImageUrl}
              width="100%"
              aspectRatio={3 / 4}
              radius={14}
            />
          ) : card.renderStatus === 'QUEUED' || card.renderStatus === 'PROCESSING' ? (
            <View style={styles.renderState}>
              <ActivityIndicator size="small" color={ink(0.4)} />
              <Text style={styles.renderText}>선택한 코디 이미지를 만드는 중이에요…</Text>
            </View>
          ) : card.renderStatus === 'FAILED' ? (
            <View style={styles.renderFailed}>
              <Text style={styles.renderError}>
                {card.renderErrorText ?? '코디 이미지를 만들지 못했어요.'}
              </Text>
              <Pressable style={styles.ghostBtn} onPress={onRenderRetry}>
                <Icon name="arrow.clockwise" tintColor={INK} size={13} />
                <Text style={styles.ghostText}>이미지만 다시 만들기</Text>
              </Pressable>
            </View>
          ) : card.saved ? (
            <View style={styles.renderFailed}>
              <Text style={styles.renderText}>저장된 코디의 이미지는 아직 만들지 않았어요.</Text>
              <Pressable style={styles.ghostBtn} onPress={onRenderRetry}>
                <Icon name="sparkles" tintColor={INK} size={13} />
                <Text style={styles.ghostText}>코디 이미지 만들기</Text>
              </Pressable>
            </View>
          ) : null}

          <View style={styles.actions}>
            <Pressable
              style={[styles.primaryBtn, card.saved && styles.primaryBtnDone]}
              disabled={card.saved}
              onPress={onSelect}>
              {card.saved ? (
                <Icon name="checkmark" tintColor={Editorial.textCaption} size={13} />
              ) : null}
              <Text style={[styles.primaryText, card.saved && styles.primaryTextDone]}>
                {card.saved ? '저장했어요' : '이 코디로 할래요'}
              </Text>
            </Pressable>
            <Pressable
              style={[styles.ghostBtn, card.alternating && styles.ghostBtnOff]}
              disabled={card.alternating}
              onPress={onAlternative}>
              {card.alternating ? (
                <ActivityIndicator size="small" color={ink(0.4)} />
              ) : (
                <Icon name="arrow.clockwise" tintColor={INK} size={13} />
              )}
              <Text style={styles.ghostText}>
                {card.alternating ? '받는 중…' : '다른 추천'}
              </Text>
            </Pressable>
          </View>
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  group: { alignSelf: 'stretch', gap: 10 },
  /* 목업 알림 — 카드보다 조용해야 한다. 경고가 아니라 사실 표시다. */
  mockNote: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  mockNoteText: { flex: 1, fontSize: Type.micro, color: Editorial.textMuted, lineHeight: 16 },
  card: {
    borderWidth: 1,
    borderColor: Editorial.line,
    borderRadius: 18,
    backgroundColor: Editorial.surface,
    padding: 14,
    gap: 10,
  },

  head: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  /* 코지 아바타와 같은 크기·모양. 답하는 주체가 바뀌었다는 것을 같은 자리에서 읽게 한다. */
  mark: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: INK,
    alignItems: 'center',
    justifyContent: 'center',
  },
  name: { flex: 1, fontSize: Type.footnote, fontWeight: '600', color: INK },
  generation: { fontSize: Type.micro, color: Editorial.textMuted },

  waiting: { gap: 8 },
  waitingRow: { flexDirection: 'row', alignItems: 'center', gap: 7, marginTop: 2 },
  waitingText: { fontSize: Type.caption, color: Editorial.textCaption },

  failed: { gap: 10 },
  failedRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 6 },
  failedText: { flex: 1, fontSize: Type.caption, color: Editorial.wine, lineHeight: 18 },
  wardrobeBtn: {
    alignSelf: 'flex-start',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 5,
    height: 38,
    paddingHorizontal: 14,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
  },
  wardrobeBtnText: { fontSize: Type.caption, color: '#fff', fontWeight: '600' },

  message: { fontSize: Type.footnote, color: INK, lineHeight: 21 },
  refMatch: { gap: 6 },
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

  items: { flexDirection: 'row', flexWrap: 'wrap', gap: 12 },
  item: { width: 64, gap: 4 },
  itemImage: { backgroundColor: Editorial.bone },
  itemName: { fontSize: Type.micro, color: INK, lineHeight: 15 },
  itemMeta: { fontSize: Type.micro, color: Editorial.textCaption },

  total: { fontSize: Type.caption, fontWeight: '600', color: INK },
  warning: { fontSize: Type.caption, color: Editorial.wine, lineHeight: 18 },
  renderState: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8 },
  renderText: { flex: 1, fontSize: Type.caption, color: Editorial.textCaption },
  renderFailed: { gap: 8, paddingVertical: 4 },
  renderError: { fontSize: Type.caption, color: Editorial.wine, lineHeight: 18 },

  actions: { flexDirection: 'row', gap: 8, marginTop: 2 },
  primaryBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 5,
    height: 38,
    paddingHorizontal: 14,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
  },
  /* 저장한 뒤에는 주 행동이 아니다 — 면을 비우고 테두리만 남긴다. */
  primaryBtnDone: {
    backgroundColor: Editorial.surface,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  primaryText: { fontSize: Type.caption, fontWeight: '600', color: '#fff' },
  primaryTextDone: { color: Editorial.textCaption },
  ghostBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 5,
    height: 38,
    paddingHorizontal: 14,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: Editorial.line,
  },
  ghostBtnOff: { opacity: 0.6 },
  ghostText: { fontSize: Type.caption, color: INK, fontWeight: '500' },
});
