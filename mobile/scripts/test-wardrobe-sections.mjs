import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { performance } from 'node:perf_hooks';
import { fileURLToPath } from 'node:url';

const mobileRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const buildDirectory = mkdtempSync(join(tmpdir(), 'cozy-wardrobe-sections-'));
const require = createRequire(import.meta.url);

function compileWardrobeSections() {
  const compiler = join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc');
  const result = spawnSync(
    process.execPath,
    [
      compiler,
      'src/lib/wardrobeSections.ts',
      '--ignoreConfig',
      '--target',
      'ES2022',
      '--module',
      'node16',
      '--moduleResolution',
      'node16',
      '--outDir',
      buildDirectory,
      '--skipLibCheck',
      '--declaration',
      'false',
    ],
    {
      cwd: mobileRoot,
      encoding: 'utf8',
    },
  );

  if (result.status !== 0) {
    throw new Error(`wardrobeSections.ts 컴파일 실패\n${result.stdout}${result.stderr}`);
  }

  return require(join(buildDirectory, 'lib', 'wardrobeSections.js'));
}

function hashtag(id, name, position) {
  return { id, name, position };
}

function item(id, overrides = {}) {
  return {
    id,
    item_name: `아이템 ${id}`,
    category_large: '상의',
    category_small: '티셔츠',
    color: '블랙',
    added_to_closet_at: '2026-08-01T00:00:00.000Z',
    created_at: '2026-08-01T00:00:00.000Z',
    wardrobe_hashtags: [],
    ...overrides,
  };
}

function sectionIds(sections) {
  return sections.map((section) => section.items.map((entry) => entry.id));
}

function percentile(samples, ratio) {
  const sorted = [...samples].sort((left, right) => left - right);
  return sorted[Math.ceil(sorted.length * ratio) - 1];
}

try {
  const {
    buildWardrobeSections,
    UNCATEGORIZED_SECTION_ID,
    uniqueWardrobeItemCount,
    wardrobeSectionCountLabel,
  } = compileWardrobeSections();

  const work = hashtag('hashtag-work', '출근룩', 0);
  const weekend = hashtag('hashtag-weekend', '주말 산책', 1);
  const baseFilters = {
    selectedSystemCategories: [],
    selectedHashtagIds: [],
    query: '',
    systemCategoryOrder: ['상의', '하의', '아우터', '신발'],
    hashtagOrder: [work, weekend],
  };

  const systemSections = buildWardrobeSections(
    [
      item('bottom', { category_large: '하의' }),
      item('top-old', { added_to_closet_at: '2026-08-01T00:00:00.000Z' }),
      item('top-new', { added_to_closet_at: '2026-08-03T00:00:00.000Z' }),
      item('shoe', { category_large: '신발' }),
    ],
    baseFilters,
    'SYSTEM_CATEGORY',
    'ADDED_DESC',
  );
  assert.deepEqual(
    systemSections.map((section) => section.title),
    ['상의', '하의', '신발'],
    '기본 카테고리 순서대로 비어 있지 않은 섹션만 보여야 한다.',
  );
  assert.deepEqual(sectionIds(systemSections)[0], ['top-new', 'top-old']);
  assert.equal(
    wardrobeSectionCountLabel({ id: 'system:가방', items: [item('bag-1'), item('bag-2')] }),
    '2개',
  );
  assert.equal(
    wardrobeSectionCountLabel({ id: 'system:상의', items: [item('top-1'), item('top-2')] }),
    '2벌',
  );

  const hashtagSections = buildWardrobeSections(
    [
      item('both', { wardrobe_hashtags: [work, weekend] }),
      item('work-only', { wardrobe_hashtags: [work] }),
      item('uncategorized'),
    ],
    baseFilters,
    'HASHTAG',
    'ADDED_DESC',
  );
  assert.deepEqual(
    hashtagSections.map((section) => section.id),
    [work.id, weekend.id, UNCATEGORIZED_SECTION_ID],
    '해시태그 순서 뒤에 미분류가 와야 한다.',
  );
  assert.deepEqual(sectionIds(hashtagSections), [
    ['both', 'work-only'],
    ['both'],
    ['uncategorized'],
  ]);
  assert.equal(uniqueWardrobeItemCount(hashtagSections), 3);

  const filteredSections = buildWardrobeSections(
    [
      item('system-only', {
        item_name: '블루 셔츠',
        category_large: '상의',
      }),
      item('hashtag-only', {
        item_name: '화이트 셔츠',
        category_large: '하의',
        wardrobe_hashtags: [work],
      }),
      item('both-match', {
        item_name: '린넨 셔츠',
        category_large: '상의',
        wardrobe_hashtags: [work],
      }),
      item('query-miss', {
        item_name: '블랙 슬랙스',
        category_large: '상의',
      }),
      item('category-miss', {
        item_name: '그린 셔츠',
        category_large: '하의',
      }),
    ],
    {
      ...baseFilters,
      selectedSystemCategories: ['상의'],
      selectedHashtagIds: [work.id],
      query: '셔츠',
      systemCategoryOrder: ['상의', '하의'],
    },
    'SYSTEM_CATEGORY',
    'ADDED_DESC',
  );
  assert.deepEqual(sectionIds(filteredSections), [['both-match']]);

  const addedSections = buildWardrobeSections(
    [
      item('fallback-new', {
        added_to_closet_at: null,
        created_at: '2026-08-05T00:00:00.000Z',
      }),
      item('added-old', {
        added_to_closet_at: '2026-08-02T00:00:00.000Z',
        created_at: '2026-08-06T00:00:00.000Z',
      }),
      item('added-new', {
        added_to_closet_at: '2026-08-07T00:00:00.000Z',
        created_at: '2026-08-01T00:00:00.000Z',
      }),
    ],
    baseFilters,
    'SYSTEM_CATEGORY',
    'ADDED_DESC',
  );
  assert.deepEqual(sectionIds(addedSections)[0], ['added-new', 'fallback-new', 'added-old']);

  const colorSections = buildWardrobeSections(
    [
      item('unknown', { item_name: '가방', color: '' }),
      item('blue', { item_name: '블루 셔츠', color: '블루' }),
      item('white-z', { item_name: '하얀 셔츠', color: '화이트' }),
      item('white-a', { item_name: '면 셔츠', color: '화이트' }),
      item('multi', { item_name: '멀티 셔츠', color: '멀티' }),
    ],
    baseFilters,
    'SYSTEM_CATEGORY',
    'COLOR_NAME_ASC',
  );
  assert.deepEqual(sectionIds(colorSections)[0], [
    'white-a',
    'white-z',
    'blue',
    'multi',
    'unknown',
  ]);

  const performanceCategories = Array.from({ length: 20 }, (_, index) =>
    hashtag(`performance-${index}`, `성능 해시태그 ${index}`, index),
  );
  const performanceItems = Array.from({ length: 5_000 }, (_, index) => {
    const primary = performanceCategories[index % performanceCategories.length];
    const memberships = [primary];
    if (index % 3 === 0) {
      memberships.push(
        performanceCategories[(index + 7) % performanceCategories.length],
      );
    }
    return item(`performance-item-${index}`, {
      item_name: `성능 아이템 ${String(index).padStart(5, '0')}`,
      color: ['화이트', '블랙', '블루', '멀티'][index % 4],
      wardrobe_hashtags: memberships,
    });
  });
  const performanceFilters = {
    ...baseFilters,
    hashtagOrder: performanceCategories,
  };

  for (let index = 0; index < 3; index += 1) {
    buildWardrobeSections(
      performanceItems,
      performanceFilters,
      'HASHTAG',
      'COLOR_NAME_ASC',
    );
  }

  const durations = [];
  for (let index = 0; index < 12; index += 1) {
    const startedAt = performance.now();
    const sections = buildWardrobeSections(
      performanceItems,
      performanceFilters,
      'HASHTAG',
      'COLOR_NAME_ASC',
    );
    durations.push(performance.now() - startedAt);
    assert.equal(uniqueWardrobeItemCount(sections), 5_000);
  }

  const median = percentile(durations, 0.5);
  const p95 = percentile(durations, 0.95);
  assert.ok(
    p95 < 500,
    `5,000벌 그룹·정렬 p95가 500ms를 초과했습니다: ${p95.toFixed(2)}ms`,
  );

  console.log('옷장 섹션 회귀 테스트: 5개 시나리오 통과');
  console.log(
    `옷장 섹션 성능: 5,000벌 · 해시태그 20개 · ` +
      `median ${median.toFixed(2)}ms · p95 ${p95.toFixed(2)}ms`,
  );
} finally {
  const expectedPrefix = join(tmpdir(), 'cozy-wardrobe-sections-');
  if (buildDirectory.startsWith(expectedPrefix)) {
    rmSync(buildDirectory, { recursive: true, force: true });
  }
}
