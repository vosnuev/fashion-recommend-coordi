type RecommendationItemMeta = {
  category: string | null;
  color: string | null;
};

/** 내부 슬롯 식별자는 숨기고 사용자가 이해할 수 있는 상품 정보만 표시한다. */
export function recommendationItemMeta(item: RecommendationItemMeta): string {
  return [item.category, item.color]
    .map((value) => value?.trim() ?? '')
    .filter(Boolean)
    .join(' · ');
}

/** 추천 카드 태그에도 내부 슬롯 식별자를 대체 표시값으로 사용하지 않는다. */
export function recommendationCategoryTags(
  items: readonly RecommendationItemMeta[],
): string[] {
  return items.flatMap((item) => {
    const category = item.category?.trim();
    return category ? [category] : [];
  });
}
