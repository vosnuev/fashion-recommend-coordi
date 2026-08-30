type SlotCandidate = {
  category_large?: string;
};

type SimilarProductSource<TCandidate extends SlotCandidate> = {
  slot: string;
  similar_products: TCandidate[];
};

/** 잘못된 서버 응답이 다른 착장 슬롯에 노출되지 않도록 하는 마지막 화면 안전망. */
export function sameSlotSimilarProducts<TCandidate extends SlotCandidate>(
  item: SimilarProductSource<TCandidate>,
): TCandidate[] {
  const slot = item.slot.trim();
  return item.similar_products.filter(
    (product) => product.category_large?.trim() === slot,
  );
}
