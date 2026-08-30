import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const buildDirectory = mkdtempSync(join(tmpdir(), 'cozy-shared-reference-'));
const require = createRequire(import.meta.url);

function compilePresentationModule() {
  const compiler = join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc');
  const result = spawnSync(
    process.execPath,
    [
      compiler,
      'src/lib/sharedReferencePresentation.ts',
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
    throw new Error(`sharedReferencePresentation.ts 컴파일 실패\n${result.stdout}${result.stderr}`);
  }
  return require(join(buildDirectory, 'sharedReferencePresentation.js'));
}

try {
  const {
    SHARED_REFERENCE_VECTOR_MAX_POLLS,
    SHARED_REFERENCE_VECTOR_POLL_MS,
    buildReferenceBadge,
    buildReferenceBubble,
    sharedReferenceUnavailableLabel,
    shouldPollSharedReferenceVector,
  } = compilePresentationModule();

  assert.equal(SHARED_REFERENCE_VECTOR_POLL_MS, 15_000);
  assert.equal(SHARED_REFERENCE_VECTOR_MAX_POLLS, 8);
  assert.equal(
    shouldPollSharedReferenceVector({
      visible: true,
      loading: false,
      hasVectorPending: true,
      pollCount: 0,
    }),
    true,
  );
  assert.equal(
    shouldPollSharedReferenceVector({
      visible: true,
      loading: false,
      hasVectorPending: true,
      pollCount: SHARED_REFERENCE_VECTOR_MAX_POLLS,
    }),
    false,
    '자동 갱신은 2분 뒤 멈춰야 한다.',
  );
  assert.equal(
    shouldPollSharedReferenceVector({
      visible: false,
      loading: false,
      hasVectorPending: true,
      pollCount: 0,
    }),
    false,
    '선택창을 닫으면 폴링하지 않아야 한다.',
  );

  assert.equal(
    sharedReferenceUnavailableLabel({
      referenceEligible: false,
      referenceUnavailableReason: 'VECTOR_NOT_READY',
    }),
    '이미지 분석 중',
  );
  assert.equal(
    sharedReferenceUnavailableLabel({
      referenceEligible: true,
      referenceUnavailableReason: null,
    }),
    null,
    'BORROWED도 서버가 eligible이면 선택 가능해야 한다.',
  );

  assert.deepEqual(
    buildReferenceBadge({
      source_type: 'WARDROBE',
      match_type: 'STYLE_SIMILAR',
      reasons: ['색상과 핏이 비슷해요'],
    }),
    {
      label: '친구 옷과 스타일이 비슷한 내 옷',
      isStyleFallback: true,
      reasons: ['색상과 핏이 비슷해요'],
    },
  );
  assert.deepEqual(
    buildReferenceBadge({
      source_type: 'PRODUCT',
      match_type: 'VISUAL_SIMILAR',
      reasons: ['실루엣이 비슷해요'],
    }),
    {
      label: '친구 옷과 비슷한 새 상품',
      isStyleFallback: false,
      reasons: ['실루엣이 비슷해요'],
    },
  );
  assert.equal(buildReferenceBadge({ match_type: 'UNKNOWN' }), null);

  assert.deepEqual(
    buildReferenceBubble(
      {
        type: 'SHARED_WARDROBE_ITEM',
        shared_item_id: 'shared-item-1',
        item_name: '',
        category_large: '아우터',
        owner_name: '하영',
        room_name: '친구 옷장',
        image_url: null,
      },
      '이 옷처럼 추천해줘',
    ),
    {
      kind: 'reference',
      text: '이 옷처럼 추천해줘',
      referenceType: 'SHARED_WARDROBE_ITEM',
      referenceItemId: 'shared-item-1',
      imageUrl: null,
      itemName: '아우터',
      ownerName: '하영',
      roomName: '친구 옷장',
    },
    '대화를 다시 열어도 reference_summary로 말풍선을 복원해야 한다.',
  );

  assert.deepEqual(
    buildReferenceBubble(
      {
        type: 'WARDROBE_ITEM',
        wardrobe_item_id: 'wardrobe-item-1',
        item_name: '파란 셔츠',
        category_large: '상의',
        owner_name: '내 옷',
        room_name: '',
        image_url: null,
      },
      '이 옷처럼 추천해줘',
    ),
    {
      kind: 'reference',
      text: '이 옷처럼 추천해줘',
      referenceType: 'WARDROBE_ITEM',
      referenceItemId: 'wardrobe-item-1',
      imageUrl: null,
      itemName: '파란 셔츠',
      ownerName: '내 옷',
      roomName: undefined,
    },
  );

  console.log('옷장 레퍼런스 모바일 회귀 테스트: 13개 시나리오 통과');
} finally {
  const expectedPrefix = join(tmpdir(), 'cozy-shared-reference-');
  if (buildDirectory.startsWith(expectedPrefix)) {
    rmSync(buildDirectory, { recursive: true, force: true });
  }
}
