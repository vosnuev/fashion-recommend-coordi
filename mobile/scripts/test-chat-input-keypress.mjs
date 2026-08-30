import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const buildDirectory = mkdtempSync(join(tmpdir(), 'cozy-chat-input-'));
const require = createRequire(import.meta.url);

function compileChatInputModule() {
  const compiler = join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc');
  const result = spawnSync(
    process.execPath,
    [
      compiler,
      'src/lib/chatInput.ts',
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
    throw new Error(`chatInput.ts 컴파일 실패\n${result.stdout}${result.stderr}`);
  }
  return require(join(buildDirectory, 'chatInput.js'));
}

try {
  const { shouldSubmitChatInputOnKeyPress } = compileChatInputModule();

  assert.equal(shouldSubmitChatInputOnKeyPress('web', { key: 'Enter' }), true);
  assert.equal(
    shouldSubmitChatInputOnKeyPress('web', { key: 'Enter', shiftKey: true }),
    false,
    'Shift+Enter는 줄바꿈이어야 한다.',
  );
  assert.equal(
    shouldSubmitChatInputOnKeyPress('web', { key: 'Enter', isComposing: true }),
    false,
    '한글 IME 조합 중 Enter는 전송하면 안 된다.',
  );
  assert.equal(
    shouldSubmitChatInputOnKeyPress('web', { key: 'Enter', keyCode: 229 }),
    false,
    '브라우저가 조합 상태를 keyCode 229로만 알리는 경우도 전송하면 안 된다.',
  );
  assert.equal(shouldSubmitChatInputOnKeyPress('web', { key: 'a' }), false);
  assert.equal(
    shouldSubmitChatInputOnKeyPress('ios', { key: 'Enter' }),
    false,
    '네이티브에서는 Enter 전송을 적용하지 않는다.',
  );

  console.log('채팅 입력 키 처리 회귀 테스트: 6개 시나리오 통과');
} finally {
  const expectedPrefix = join(tmpdir(), 'cozy-chat-input-');
  if (buildDirectory.startsWith(expectedPrefix)) {
    rmSync(buildDirectory, { recursive: true, force: true });
  }
}
