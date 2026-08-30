import { useCallback, useEffect, useRef, useState } from 'react';
import { Modal, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Icon } from '@/components/icon';
import { ErrorState, LoadingState, SmartImage } from '@/components/ui';
import { ChatPanelWidth, Editorial, ink, SidebarWidth, Type } from '@/constants/theme';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import {
  SHARED_REFERENCE_VECTOR_POLL_MS,
  sharedReferenceUnavailableLabel,
  shouldPollSharedReferenceVector,
} from '@/lib/sharedReferencePresentation';
import {
  getMySharedRooms,
  listSharedRoomItems,
  sharedUserDisplayName,
  type SharedReferenceUnavailableReason,
  type SharedRoomItem,
} from '@/lib/wardrobeApi';
import type { ChatReferencePick } from '@/state/chat';

const INK = Editorial.ink;

/**
 * 참고할 공유 옷을 고르는 시트.
 *
 * **추천에 넣을 옷을 고르는 자리가 아니다.** 친구 옷은 참고 이미지로만 쓰이고, 최종 코디에는
 * 그 옷과 비슷한 내 옷(옷장 기반)이나 비슷한 상품(추구미 반영)이 들어간다. 그래서 문구에
 * '포함'을 쓰지 않는다.
 *
 * ⚠️ 서버 계약이 **공유 아이템 한 벌만** 받는다(shared_item_id 하나). 그래서 다중 선택도,
 *    내 옷장 탭도 두지 않는다 — 내 옷 id 를 shared_item_id 자리에 넣으면 서버가 못 찾는다.
 * ⚠️ 카드가 들고 있는 id 는 `SharedWardrobeItem.id`(= SharedRoomItem.id)다.
 *    안에 든 `wardrobe_item.id` 가 아니다. 둘을 섞으면 조용히 404 가 난다.
 */

/** 방 하나가 실패해도 나머지는 보여준다 — 전부 못 보느니 일부라도 고를 수 있어야 한다. */
type LoadState = {
  picks: SharedReferencePickerItem[];
  failedRooms: number;
  error: string | null;
};

type SharedReferencePickerItem = ChatReferencePick & {
  referenceEligible: boolean;
  referenceUnavailableReason: SharedReferenceUnavailableReason | null;
};

function unavailableLabel(item: SharedReferencePickerItem): string | null {
  return sharedReferenceUnavailableLabel(item);
}

function toPick(
  item: SharedRoomItem,
  roomId: string,
  roomName: string,
): SharedReferencePickerItem {
  const w = item.wardrobe_item;
  return {
    referenceType: 'SHARED_WARDROBE_ITEM',
    referenceItemId: item.id,
    imageUrl: w.image_url ?? null,
    // 이름이 비어 있는 옷이 있어 대분류로 대신한다 (식별에는 쓰지 않는다).
    itemName: w.item_name?.trim() || w.category_large?.trim() || '옷',
    ownerName: sharedUserDisplayName(item.registered_by),
    roomId,
    roomName,
    referenceEligible: item.reference_eligible,
    referenceUnavailableReason: item.reference_unavailable_reason,
  };
}

export function SharedItemPicker({
  visible,
  onClose,
  onPick,
}: {
  visible: boolean;
  onClose: () => void;
  onPick: (pick: ChatReferencePick) => void;
}) {
  const { isDesktop, isWide } = useBreakpoint();
  const [state, setState] = useState<LoadState>({ picks: [], failedRooms: 0, error: null });
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const vectorPollCount = useRef(0);

  /* 데스크톱 웹에선 대화 열 안에 뜨는 다이얼로그 (session-sheet·stylist-picker 와 같은 규칙). */
  const asDialog = Platform.OS === 'web' && isDesktop;
  const columnInset = asDialog
    ? { marginLeft: SidebarWidth, marginRight: isWide ? ChatPanelWidth : 0 }
    : null;

  /**
   * 방마다 따로 받아 온다. 한 방이 실패해도 나머지는 그대로 보여준다 —
   * 전부 못 보느니 일부라도 고를 수 있어야 한다(요구사항 9장).
   *
   * setState 를 전부 then/catch 안에서 한다. effect 안에서 곧바로 부르면 렌더가 연쇄로 돌고,
   * 시트를 닫은 뒤에 응답이 와서 사라진 화면을 건드리는 일도 막아야 해서 alive 로 잠근다.
   */
  const run = useCallback((alive: () => boolean) => {
    getMySharedRooms()
      .then((rooms) =>
        Promise.all(
          rooms.map((room) =>
            listSharedRoomItems(room.id)
              .then((items) => items.map((it) => toPick(it, room.id, room.title)))
              .catch(() => null),
          ),
        ),
      )
      .then((results) => {
        if (!alive()) return;
        setState({
          picks: results.flatMap((r) => r ?? []),
          failedRooms: results.filter((r) => r === null).length,
          error: null,
        });
      })
      .catch((e: unknown) => {
        if (!alive()) return;
        setState({
          picks: [],
          failedRooms: 0,
          error: e instanceof Error && e.message ? e.message : '공유 옷장을 불러오지 못했어요',
        });
      })
      .finally(() => {
        if (alive()) setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (!visible) return;
    let on = true;
    run(() => on);
    return () => {
      on = false;
    };
  }, [visible, run]);

  const hasVectorPending = state.picks.some(
    (pick) => pick.referenceUnavailableReason === 'VECTOR_NOT_READY',
  );

  /* 재인덱싱은 GPU 모델 초기화까지 포함해 수십 초 걸릴 수 있다. 15초 간격으로
     최대 2분만 조용히 갱신하고, 그 뒤에는 사용자가 수동으로 확인하게 한다. */
  useEffect(() => {
    if (!shouldPollSharedReferenceVector({
      visible,
      loading,
      hasVectorPending,
      pollCount: vectorPollCount.current,
    })) {
      return;
    }
    let alive = true;
    const timer = setTimeout(() => {
      vectorPollCount.current += 1;
      run(() => alive);
    }, SHARED_REFERENCE_VECTOR_POLL_MS);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [hasVectorPending, loading, run, visible, state.picks]);

  /* 열 때마다 처음 상태로 되돌린다. effect 가 아니라 렌더 중에 맞추므로 옛 선택이
     한 프레임 스치지 않는다 (stylist-picker 와 같은 방식). */
  const openKey = visible ? 'open' : null;
  const [shownFor, setShownFor] = useState<string | null>(null);
  if (openKey !== shownFor) {
    setShownFor(openKey);
    if (visible) {
      setSelectedId(null);
      setLoading(true);
      vectorPollCount.current = 0;
    }
  }

  const selected =
    state.picks.find((p) => p.referenceItemId === selectedId && p.referenceEligible) ?? null;
  /* 방이 여럿일 때만 방 이름을 붙인다 — 하나뿐이면 모든 카드에 같은 말이 붙어 소음이 된다. */
  const showRoom = new Set(state.picks.map((p) => p.roomId)).size > 1;

  return (
    <Modal
      visible={visible}
      transparent
      animationType={asDialog ? 'fade' : 'slide'}
      onRequestClose={onClose}>
      <Pressable style={[styles.backdrop, asDialog && styles.backdropDialog, columnInset]} onPress={onClose}>
        <Pressable style={[styles.sheet, asDialog && styles.dialog]} onPress={(e) => e.stopPropagation()}>
          {asDialog ? null : <View style={styles.handle} />}

          <View style={styles.head}>
            <View style={styles.headText}>
              <Text style={styles.title}>공유 옷 참고하기</Text>
              <Text style={styles.lead}>
                친구 옷과 비슷한 옷을 찾아 드려요. 친구 옷 자체가 코디에 들어가지는 않아요.
              </Text>
            </View>
            <Pressable hitSlop={12} onPress={onClose} accessibilityLabel="닫기">
              <Icon name="xmark" tintColor={ink(0.5)} size={16} />
            </Pressable>
          </View>

          {loading ? (
            <LoadingState message="공유 옷을 불러오는 중…" style={styles.state} />
          ) : state.error ? (
            <ErrorState
              title="공유 옷장을 불러오지 못했어요"
              description={state.error}
              onRetry={() => {
                setLoading(true);
                run(() => true);
              }}
              style={styles.state}
            />
          ) : state.picks.length === 0 ? (
            <View style={styles.state}>
              <Text style={styles.emptyText}>
                {state.failedRooms > 0
                  ? '참고할 수 있는 공유 옷을 불러오지 못했어요.'
                  : '공유 옷장에 옷이 올라오면 참고할 수 있어요.'}
              </Text>
            </View>
          ) : (
            <>
              {/* 일부 방만 실패했으면 성공한 목록은 그대로 두고 사실만 알린다. */}
              {state.failedRooms > 0 ? (
                <Pressable
                  style={styles.partialRow}
                  onPress={() => {
                    setLoading(true);
                    run(() => true);
                  }}>
                  <Icon name="exclamationmark.triangle" tintColor={Editorial.textMuted} size={11} />
                  <Text style={styles.partialText}>
                    공유 옷장 {state.failedRooms}곳을 불러오지 못했어요. 눌러서 다시 시도
                  </Text>
                </Pressable>
              ) : null}

              {hasVectorPending ? (
                <Pressable
                  style={styles.partialRow}
                  onPress={() => {
                    vectorPollCount.current = 0;
                    setLoading(true);
                    run(() => true);
                  }}>
                  <Icon name="arrow.clockwise" tintColor={Editorial.textMuted} size={11} />
                  <Text style={styles.partialText}>
                    이미지 분석 상태 새로고침
                  </Text>
                </Pressable>
              ) : null}

              <ScrollView contentContainerStyle={styles.grid} showsVerticalScrollIndicator={false}>
                {state.picks.map((p) => {
                  const disabled = !p.referenceEligible;
                  const reason = unavailableLabel(p);
                  const on = !disabled && p.referenceItemId === selectedId;
                  return (
                    <Pressable
                      key={p.referenceItemId}
                      style={[styles.card, disabled && styles.cardDisabled, on && styles.cardOn]}
                      disabled={disabled}
                      onPress={() => {
                        if (disabled) return;
                        setSelectedId(on ? null : p.referenceItemId);
                      }}
                      accessibilityRole="radio"
                      accessibilityState={{ selected: on, disabled }}
                      accessibilityLabel={`${p.ownerName}님의 ${p.itemName}${reason ? `, ${reason}` : ''}`}>
                      <View style={styles.imageFrame}>
                        <SmartImage uri={p.imageUrl} width="100%" height={92} radius={10} />
                        {disabled ? <View pointerEvents="none" style={styles.imageVeil} /> : null}
                      </View>
                      {on ? (
                        <View style={styles.check}>
                          <Icon name="checkmark" tintColor="#fff" size={11} />
                        </View>
                      ) : null}
                      <View style={styles.cardMeta}>
                        <Text style={styles.cardName} numberOfLines={1}>
                          {p.itemName}
                        </Text>
                        <Text style={styles.cardOwner} numberOfLines={1}>
                          {p.ownerName}
                          {showRoom && p.roomName ? ` · ${p.roomName}` : ''}
                        </Text>
                        {reason ? (
                          <View style={styles.unavailableRow}>
                            <Icon
                              name={
                                p.referenceUnavailableReason === 'VECTOR_NOT_READY'
                                  ? 'sparkles'
                                  : 'questionmark.circle'
                              }
                              tintColor={Editorial.textMuted}
                              size={10}
                            />
                            <Text style={styles.unavailableText} numberOfLines={1}>
                              {reason}
                            </Text>
                          </View>
                        ) : null}
                      </View>
                    </Pressable>
                  );
                })}
              </ScrollView>
            </>
          )}

          <Pressable
            style={[styles.confirm, !selected && styles.confirmOff]}
            disabled={!selected}
            onPress={() => {
              if (!selected) return;
              onPick(selected);
              onClose();
            }}>
            <Text style={styles.confirmText}>이 옷 참고하기</Text>
          </Pressable>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(28,25,23,0.35)' },
  backdropDialog: {
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 24,
    backgroundColor: 'rgba(28,25,23,0.22)',
  },
  sheet: {
    backgroundColor: Editorial.surface,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 20,
    paddingBottom: 28,
    maxHeight: '82%',
  },
  dialog: {
    width: '100%',
    maxWidth: 460,
    borderRadius: 20,
    paddingTop: 24,
    maxHeight: '80%',
    shadowColor: Editorial.ink,
    shadowOffset: { width: 0, height: 12 },
    shadowOpacity: 0.14,
    shadowRadius: 32,
    elevation: 12,
  },
  handle: {
    alignSelf: 'center',
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: ink(0.12),
    marginTop: 10,
    marginBottom: 14,
  },

  head: { flexDirection: 'row', alignItems: 'flex-start', gap: 12, marginTop: 4 },
  headText: { flex: 1, gap: 5 },
  title: { fontSize: Type.lead, fontWeight: '600', color: INK },
  lead: { fontSize: Type.caption, color: Editorial.textCaption, lineHeight: 19 },
  state: { paddingVertical: 36 },
  emptyText: { textAlign: 'center', fontSize: Type.footnote, color: Editorial.textCaption },

  partialRow: { flexDirection: 'row', alignItems: 'center', gap: 5, marginTop: 12 },
  partialText: { flex: 1, fontSize: Type.micro, color: Editorial.textMuted, lineHeight: 16 },

  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, paddingVertical: 16 },
  card: {
    width: 104,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: Editorial.line,
    padding: 6,
    gap: 6,
  },
  /* 고른 것은 면이 아니라 테두리로 말한다 — 채워지는 자리는 CTA 하나뿐이다. */
  cardOn: { borderColor: Editorial.lineStrong },
  cardDisabled: { borderColor: Editorial.lineSoft },
  imageFrame: { height: 92, position: 'relative' },
  imageVeil: {
    ...StyleSheet.absoluteFill,
    borderRadius: 10,
    backgroundColor: 'rgba(255,255,255,0.48)',
  },
  check: {
    position: 'absolute',
    top: 12,
    right: 12,
    width: 18,
    height: 18,
    borderRadius: 9,
    backgroundColor: Editorial.selected,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cardMeta: { gap: 2 },
  cardName: { fontSize: Type.micro, color: INK, fontWeight: '500' },
  cardOwner: { fontSize: Type.micro, color: Editorial.textCaption },
  unavailableRow: { flexDirection: 'row', alignItems: 'center', gap: 3, marginTop: 2 },
  unavailableText: { flex: 1, fontSize: 9.5, color: Editorial.textMuted, lineHeight: 13 },

  confirm: {
    height: 48,
    borderRadius: 999,
    backgroundColor: Editorial.cta,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 6,
  },
  confirmOff: { opacity: 0.35 },
  confirmText: { fontSize: Type.label, fontWeight: '600', color: '#fff' },
});
