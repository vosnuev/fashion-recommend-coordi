import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const buildDirectory = mkdtempSync(join(tmpdir(), 'cozy-discovery-look-'));
const require = createRequire(import.meta.url);

function compilePresentationModule() {
  const compiler = join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc');
  const result = spawnSync(
    process.execPath,
    [
      compiler,
      'src/lib/discoveryLookPresentation.ts',
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
    { cwd: mobileRoot, encoding: 'utf8' },
  );

  if (result.status !== 0) {
    throw new Error(
      `discoveryLookPresentation.ts 컴파일 실패\n${result.stdout}${result.stderr}`,
    );
  }
  return require(join(buildDirectory, 'discoveryLookPresentation.js'));
}

try {
  const { sameSlotSimilarProducts } = compilePresentationModule();
  const baseItem = {
    id: 'curated-item',
    slot: '액세서리',
    category_small: '블랙 퍼 머리띠',
    name: '블랙 퍼 머리띠',
    brand: '원본',
    image: 'original.jpg',
    price: 20_000,
    mall_name: '원본몰',
    link: 'https://example.com/original',
  };
  const product = (id, categoryLarge) => ({
    id,
    category_large: categoryLarge,
    name: id,
    brand: '테스트',
    image: `${id}.jpg`,
    price: 10_000,
    mall_name: '테스트몰',
    link: `https://example.com/${id}`,
  });

  assert.deepEqual(
    sameSlotSimilarProducts({
      ...baseItem,
      similar_products: [
        product('same-slot', ' 액세서리 '),
        product('bag', '가방'),
        product('underwear', '언더웨어/이너웨어'),
        product('missing-category', undefined),
      ],
    }).map((candidate) => candidate.id),
    ['same-slot'],
    '같은 슬롯만 남기고 다른 슬롯과 카테고리 누락 응답은 제거해야 한다.',
  );

  assert.deepEqual(
    sameSlotSimilarProducts({
      ...baseItem,
      similar_products: [],
    }),
    [],
    '후보가 없을 때 다른 상품으로 채우면 안 된다.',
  );

  console.log('룩북 유사 상품 표시 회귀 테스트: 2개 시나리오 통과');
} finally {
  const expectedPrefix = join(tmpdir(), 'cozy-discovery-look-');
  if (buildDirectory.startsWith(expectedPrefix)) {
    rmSync(buildDirectory, { recursive: true, force: true });
  }
}
