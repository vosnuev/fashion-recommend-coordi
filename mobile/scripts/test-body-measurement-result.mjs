import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const buildDirectory = mkdtempSync(join(tmpdir(), 'cozy-body-measurement-'));
const require = createRequire(import.meta.url);

function compileResultModule() {
  const compiler = join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc');
  const result = spawnSync(
    process.execPath,
    [
      compiler,
      'src/lib/bodyMeasurementResult.ts',
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
    throw new Error(`bodyMeasurementResult.ts 컴파일 실패\n${result.stdout}${result.stderr}`);
  }
  return require(join(buildDirectory, 'bodyMeasurementResult.js'));
}

try {
  const {
    isPhotoQualityFailureCode,
    measurementRequestFailureState,
    measurementResultDescription,
    measurementResultSource,
    photoMeasurementFailureState,
  } = compileResultModule();

  assert.equal(isPhotoQualityFailureCode('photo_quality_failed'), true);
  assert.equal(isPhotoQualityFailureCode('PHOTO_QUALITY_FAILED'), false);
  assert.equal(isPhotoQualityFailureCode('vlm_timeout'), false);
  assert.equal(isPhotoQualityFailureCode(null), false);
  assert.deepEqual(photoMeasurementFailureState('photo_quality_failed'), {
    photoQualityFailed: true,
  });
  assert.deepEqual(measurementResultSource(false, true), {
    usedPhotos: false,
    photoFallback: true,
  });
  assert.deepEqual(measurementRequestFailureState(), { photoQualityFailed: false });

  assert.equal(
    measurementResultDescription({ usedPhotos: true, photoFallback: false }),
    '사진과 입력 정보로 추정한 결과예요.',
  );
  assert.equal(
    measurementResultDescription({ usedPhotos: false, photoFallback: true }),
    '사진을 인식하지 못해 키·몸무게·성별만으로 추정한 값입니다.',
  );
  assert.equal(
    measurementResultDescription({ usedPhotos: false, photoFallback: false }),
    '키·몸무게·성별로 추정한 결과예요.',
  );

  console.log('체형 측정 결과 출처 회귀 테스트: 10개 시나리오 통과');
} finally {
  const expectedPrefix = join(tmpdir(), 'cozy-body-measurement-');
  if (buildDirectory.startsWith(expectedPrefix)) {
    rmSync(buildDirectory, { recursive: true, force: true });
  }
}
