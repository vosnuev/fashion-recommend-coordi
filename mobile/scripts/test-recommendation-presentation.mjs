import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const buildDirectory = mkdtempSync(join(tmpdir(), 'cozy-recommendation-presentation-'));
const require = createRequire(import.meta.url);

function compilePresentationModule() {
  const compiler = join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc');
  const result = spawnSync(
    process.execPath,
    [
      compiler,
      'src/lib/recommendationPresentation.ts',
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
      `recommendationPresentation.ts 컴파일 실패\n${result.stdout}${result.stderr}`,
    );
  }
  return require(join(buildDirectory, 'recommendationPresentation.js'));
}

try {
  const {
    recommendationCategoryTags,
    recommendationItemMeta,
  } = compilePresentationModule();

  assert.equal(
    recommendationItemMeta({
      slot: '기본 상의:b9a4e6cb-92de-4e43-8f11-b12ef1f22222',
      category: '티셔츠',
      color: '검정',
    }),
    '티셔츠 · 검정',
    '내부 슬롯 식별자는 표시 메타에 포함하면 안 된다.',
  );
  assert.equal(
    recommendationItemMeta({ category: '티셔츠', color: null }),
    '티셔츠',
  );
  assert.equal(recommendationItemMeta({ category: null, color: null }), '');
  assert.deepEqual(
    recommendationCategoryTags([
      {
        slot: '기본 상의:b9a4e6cb-92de-4e43-8f11-b12ef1f22222',
        category: null,
        color: null,
      },
      { category: ' 티셔츠 ', color: null },
    ]),
    ['티셔츠'],
    '카테고리가 없더라도 내부 슬롯 식별자를 태그로 노출하면 안 된다.',
  );

  console.log('추천 카드 표시 회귀 테스트: 4개 시나리오 통과');
} finally {
  const expectedPrefix = join(tmpdir(), 'cozy-recommendation-presentation-');
  if (buildDirectory.startsWith(expectedPrefix)) {
    rmSync(buildDirectory, { recursive: true, force: true });
  }
}
