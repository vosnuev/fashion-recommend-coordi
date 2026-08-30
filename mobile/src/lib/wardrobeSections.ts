import { COLORS } from '../constants/wardrobe-taxonomy';

export type WardrobeSectionHashtag = {
  id: string;
  name: string;
  position: number;
};

export type WardrobeSectionItem = {
  id: string;
  item_name: string;
  category_large: string;
  category_small: string;
  color: string;
  added_to_closet_at: string | null;
  created_at: string;
  wardrobe_hashtags: WardrobeSectionHashtag[];
};

export type WardrobeGroupMode = 'SYSTEM_CATEGORY' | 'HASHTAG';
export type WardrobeItemSort = 'ADDED_DESC' | 'COLOR_NAME_ASC';

export type WardrobeSectionFilters = {
  selectedSystemCategories: string[];
  selectedHashtagIds: string[];
  query: string;
  systemCategoryOrder: string[];
  hashtagOrder: WardrobeSectionHashtag[];
};

export type WardrobeSection<T extends WardrobeSectionItem = WardrobeSectionItem> = {
  id: string;
  title: string;
  items: T[];
};

export const UNCATEGORIZED_SECTION_ID = 'virtual:uncategorized';

export function wardrobeSectionCountLabel(
  section: Pick<WardrobeSection, 'id' | 'items'>,
): string {
  const unit = section.id === 'system:가방' ? '개' : '벌';
  return `${section.items.length}${unit}`;
}

const colorRank = new Map<string, number>(COLORS.map((color, index) => [color, index]));

function displayName(item: WardrobeSectionItem): string {
  return item.item_name || item.category_small || item.category_large;
}

function compareAddedDesc(left: WardrobeSectionItem, right: WardrobeSectionItem): number {
  const leftAdded = Date.parse(left.added_to_closet_at ?? left.created_at) || 0;
  const rightAdded = Date.parse(right.added_to_closet_at ?? right.created_at) || 0;
  if (leftAdded !== rightAdded) return rightAdded - leftAdded;

  const leftCreated = Date.parse(left.created_at) || 0;
  const rightCreated = Date.parse(right.created_at) || 0;
  if (leftCreated !== rightCreated) return rightCreated - leftCreated;
  return left.id.localeCompare(right.id);
}

function compareColorName(left: WardrobeSectionItem, right: WardrobeSectionItem): number {
  const unknownRank = COLORS.length;
  const leftColor = colorRank.get(left.color) ?? unknownRank;
  const rightColor = colorRank.get(right.color) ?? unknownRank;
  if (leftColor !== rightColor) return leftColor - rightColor;

  const byName = displayName(left).localeCompare(displayName(right), 'ko-KR');
  if (byName !== 0) return byName;
  return compareAddedDesc(left, right);
}

function sortItems<T extends WardrobeSectionItem>(
  items: T[],
  itemSort: WardrobeItemSort,
): T[] {
  return [...items].sort(itemSort === 'COLOR_NAME_ASC' ? compareColorName : compareAddedDesc);
}

function matchesFilters(item: WardrobeSectionItem, filters: WardrobeSectionFilters): boolean {
  const systemMatched =
    filters.selectedSystemCategories.length === 0 ||
    filters.selectedSystemCategories.includes(item.category_large);
  const hashtagMatched =
    filters.selectedHashtagIds.length === 0 ||
    item.wardrobe_hashtags.some((hashtag) => filters.selectedHashtagIds.includes(hashtag.id));
  if (!systemMatched || !hashtagMatched) return false;

  const query = filters.query.trim();
  if (!query) return true;
  return displayName(item).includes(query) || item.category_large.includes(query);
}

/**
 * 서버 목록 순서와 무관하게 개인 옷장의 섹션과 섹션 내부 순서를 결정한다.
 * 해시태그 그룹은 다대다 소속을 그대로 보여주므로 같은 옷이 여러 섹션에 나올 수 있다.
 */
export function buildWardrobeSections<T extends WardrobeSectionItem>(
  items: T[],
  filters: WardrobeSectionFilters,
  groupMode: WardrobeGroupMode,
  itemSort: WardrobeItemSort,
): WardrobeSection<T>[] {
  const filtered = items.filter((item) => matchesFilters(item, filters));

  if (groupMode === 'SYSTEM_CATEGORY') {
    return filters.systemCategoryOrder.flatMap((category) => {
      const sectionItems = filtered.filter((item) => item.category_large === category);
      return sectionItems.length > 0
        ? [{ id: `system:${category}`, title: category, items: sortItems(sectionItems, itemSort) }]
        : [];
    });
  }

  const hashtagSections = filters.hashtagOrder.flatMap((hashtag) => {
    const sectionItems = filtered.filter((item) =>
      item.wardrobe_hashtags.some((entry) => entry.id === hashtag.id),
    );
    return sectionItems.length > 0
      ? [{ id: hashtag.id, title: `#${hashtag.name}`, items: sortItems(sectionItems, itemSort) }]
      : [];
  });
  const uncategorized = filtered.filter((item) => item.wardrobe_hashtags.length === 0);

  return uncategorized.length > 0
    ? [
        ...hashtagSections,
        {
          id: UNCATEGORIZED_SECTION_ID,
          title: '미분류',
          items: sortItems(uncategorized, itemSort),
        },
      ]
    : hashtagSections;
}

/** 해시태그 그룹의 중복 카드와 무관한 실제 옷 개수. */
export function uniqueWardrobeItemCount<T extends WardrobeSectionItem>(
  sections: WardrobeSection<T>[],
): number {
  return new Set(sections.flatMap((section) => section.items.map((item) => item.id))).size;
}
