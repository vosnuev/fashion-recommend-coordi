import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const buildDirectory = mkdtempSync(join(tmpdir(), 'cozy-kakao-invite-'));
const require = createRequire(import.meta.url);

function compileInviteLinkModule() {
  const compiler = join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc');
  const result = spawnSync(
    process.execPath,
    [
      compiler,
      'src/lib/kakaoInviteLink.ts',
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
    throw new Error(`kakaoInviteLink.ts 컴파일 실패\n${result.stdout}${result.stderr}`);
  }
  return require(join(buildDirectory, 'kakaoInviteLink.js'));
}

try {
  const { buildKakaoExecutionParams, parseKakaoInviteCode, redirectKakaoInvitePath } =
    compileInviteLinkModule();

  const kakaoUrl = 'kakao1366adcd2e8c643a4b5471fabd32b6ea://kakaolink?code=ab12cd';
  assert.equal(parseKakaoInviteCode(kakaoUrl), 'AB12CD');
  assert.equal(redirectKakaoInvitePath(kakaoUrl), '/invite?code=AB12CD');
  assert.equal(
    redirectKakaoInvitePath(
      'kakao1366adcd2e8c643a4b5471fabd32b6ea://kakaolink?code=%20xy9z01%20',
    ),
    '/invite?code=XY9Z01',
  );
  assert.equal(redirectKakaoInvitePath('/home'), '/home');
  assert.equal(redirectKakaoInvitePath('mobile://invite?code=ABC123'), 'mobile://invite?code=ABC123');
  assert.equal(redirectKakaoInvitePath('not a valid url'), 'not a valid url');
  assert.equal(buildKakaoExecutionParams(' ab12cd '), 'code=AB12CD');
  assert.equal(buildKakaoExecutionParams('가 나'), 'code=%EA%B0%80+%EB%82%98');

  console.log('카카오 초대 딥링크 회귀 테스트: 8개 시나리오 통과');
} finally {
  const expectedPrefix = join(tmpdir(), 'cozy-kakao-invite-');
  if (buildDirectory.startsWith(expectedPrefix)) {
    rmSync(buildDirectory, { recursive: true, force: true });
  }
}
