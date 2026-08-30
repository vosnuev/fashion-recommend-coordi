/** 공유 옷 레퍼런스의 선택 상태와 결과 표시를 화면용 값으로 변환한다. */

export type SharedReferenceUnavailableReason =
  | 'NOT_CONFIRMED'
  | 'VECTOR_NOT_READY';

export type ReferenceMatchLike = {
  match_type?: string;
  source_type?: string;
  reasons?: string[];
};

export type ReferenceSummaryLike = {
  item_name: string;
  category_large: string;
  owner_name: string;
  room_name: string;
  image_url: string | null;
} & (
  | { type: 'SHARED_WARDROBE_ITEM'; shared_item_id: string }
  | { type: 'WARDROBE_ITEM'; wardrobe_item_id: string }
);

export type ReferenceBadgePresentation = {
  label: string;
  isStyleFallback: boolean;
  reasons: string[];
};

export const SHARED_REFERENCE_VECTOR_POLL_MS = 15_000;
export const SHARED_REFERENCE_VECTOR_MAX_POLLS = 8;

export function shouldPollSharedReferenceVector(input: {
  visible: boolean;
  loading: boolean;
  hasVectorPending: boolean;
  pollCount: number;
}): boolean {
  return (
    input.visible &&
    !input.loading &&
    input.hasVectorPending &&
    input.pollCount < SHARED_REFERENCE_VECTOR_MAX_POLLS
  );
}

const UNAVAILABLE_LABELS: Record<SharedReferenceUnavailableReason, string> = {
  VECTOR_NOT_READY: '이미지 분석 중',
  NOT_CONFIRMED: '옷 정보 확인 필요',
};

const REFERENCE_LABELS: Record<string, string> = {
  'WARDROBE:VISUAL_SIMILAR': '친구 옷과 비슷한 내 옷',
  'WARDROBE:STYLE_SIMILAR': '친구 옷과 스타일이 비슷한 내 옷',
  'PRODUCT:VISUAL_SIMILAR': '친구 옷과 비슷한 새 상품',
  'PRODUCT:STYLE_SIMILAR': '친구 옷과 스타일이 비슷한 새 상품',
};

export function sharedReferenceUnavailableLabel(input: {
  referenceEligible: boolean;
  referenceUnavailableReason: SharedReferenceUnavailableReason | null;
}): string | null {
  if (input.referenceEligible) return null;
  return input.referenceUnavailableReason
    ? UNAVAILABLE_LABELS[input.referenceUnavailableReason]
    : '지금은 참고할 수 없어요';
}

export function buildReferenceBadge(
  match: ReferenceMatchLike | undefined,
): ReferenceBadgePresentation | null {
  const label = REFERENCE_LABELS[`${match?.source_type}:${match?.match_type}`];
  if (!label) return null;
  return {
    label,
    isStyleFallback: match?.match_type === 'STYLE_SIMILAR',
    reasons: match?.reasons ?? [],
  };
}

export function buildReferenceBubble(summary: ReferenceSummaryLike, text: string) {
  const referenceItemId =
    summary.type === 'SHARED_WARDROBE_ITEM'
      ? summary.shared_item_id
      : summary.wardrobe_item_id;
  if (!referenceItemId) {
    throw new Error('참고 옷 식별자가 없습니다.');
  }
  return {
    kind: 'reference' as const,
    text,
    referenceType: summary.type,
    referenceItemId,
    imageUrl: summary.image_url,
    itemName: summary.item_name || summary.category_large || '옷',
    ownerName: summary.owner_name,
    roomName: summary.room_name || undefined,
  };
}
