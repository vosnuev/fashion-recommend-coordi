/**
 * 공유 옷장 초대 — 카카오톡 공유.
 *
 * 모바일 앱은 카카오톡을 열고, PC 웹은 초대 문구만 복사한다.
 *
 * 플랫폼별로 SDK 가 갈린다 — 쓰는 키가 서로 다르기 때문이다.
 *   네이티브 : @react-native-kakao/share, **네이티브 앱 키**로 초기화(_layout 에서 1회)
 *   웹       : Kakao JS SDK, **JavaScript 키**로 Kakao.init
 * 웹에서 네이티브 앱 키로 init 하면 예외도 없이 조용히 실패한다. 이 파일이 존재하는
 * 이유의 절반이 그 실수를 다시 못 하게 하는 것이다.
 *
 * 어떤 경로로 가든 **먼저 클립보드에 초대 문구를 복사**한다. 카카오 공유가 막히거나
 * (도메인 미등록·카톡 미설치) 사용자가 다른 채팅방에 직접 붙여넣고 싶을 때
 * 손으로 코드를 옮겨 적지 않아도 되게 하기 위해서다.
 */
import type { KakaoFeedTemplate } from '@react-native-kakao/share';
import * as Clipboard from 'expo-clipboard';
import { Platform, Share } from 'react-native';

import { KAKAO_JAVASCRIPT_KEY } from '@/constants/config';
import { buildKakaoExecutionParams } from '@/lib/kakaoInviteLink';

/** 카카오 JS SDK. 버전을 올릴 때 CDN 경로 형식도 함께 확인한다. */
const KAKAO_JS_SDK_SRC = 'https://t1.kakaocdn.net/kakao_js_sdk/2.8.0/kakao.min.js';

/** 공유 카드 대표 이미지. 카카오는 절대 URL 만 받는다(상대경로·data URI 불가). */
const INVITE_THUMBNAIL =
  'https://images.unsplash.com/photo-1540221652346-e5dd6b50f3e7?w=800&auto=format&fit=crop&q=60';

export type KakaoInvite = {
  /** 공유 옷장 이름 */
  roomName: string;
  /** 6자리 참여 코드 */
  code: string;
  /** 초대 수락 링크 (/invite?code=...) */
  link: string;
};

/**
 * 공유가 실제로 어떤 경로로 나갔는지 — 호출부가 토스트 문구를 고르는 데 쓴다.
 * `no-key`는 설정 누락이라 사용자가 아니라 **개발자가 고쳐야 하는** 실패다.
 * 이걸 'clipboard'와 뭉뚱그리면 "왜 카톡이 안 열리지"를 영원히 못 찾는다.
 */
export type KakaoShareResult =
  | 'kakao'
  | 'share-sheet'
  | 'clipboard'
  | 'no-key'
  | 'cancelled';

/** 웹에서 카카오 SDK를 쓸 수 있는 상태인지 (키가 번들에 실렸는지) */
export function isKakaoWebConfigured(): boolean {
  return Boolean(KAKAO_JAVASCRIPT_KEY);
}

/**
 * 카카오톡·다른 앱에 실려 나갈 본문.
 *
 * 본문에는 참여 코드만 넣는다. 초대 링크는 카카오 카드 버튼 목적지로만 사용한다.
 * 코드를 눈에 띄게 해 앱의 참여코드 입력 흐름으로 통일한다.
 */
export function inviteMessage({ roomName, code, link }: KakaoInvite): string {
  return [
    `[cozy] '${roomName}' 공유 옷장에 초대합니다!`,
    `참여코드: ${code}`,
    `초대장 열기: ${link}`,
    `링크가 열리지 않으면 앱에서 '참여코드'에 위 코드를 입력하세요`,
  ].join('\n');
}

/**
 * 구식 동기 복사 (execCommand). 비보안 컨텍스트의 유일한 수단이다.
 *
 * 세 가지를 안 지키면 조용히 false 가 난다:
 *  1. `display:none`·`visibility:hidden` 요소는 브라우저가 복사를 거부한다 → 화면 밖으로 민다
 *  2. iOS Safari 는 `select()` 로 선택되지 않는다 → Range + `setSelectionRange` 를 같이 쓴다
 *  3. iOS 는 `readonly` 인 채로도 선택이 안 잡히는 경우가 있다 → `contentEditable` 을 켠다
 */
function copyTextSync(text: string): boolean {
  if (typeof document === 'undefined') return false;

  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.contentEditable = 'true';
  ta.style.cssText =
    'position:fixed;top:0;left:-9999px;width:1px;height:1px;padding:0;border:none;font-size:16px;';
  document.body.appendChild(ta);

  try {
    const range = document.createRange();
    range.selectNodeContents(ta);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
    ta.setSelectionRange(0, text.length);
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    document.body.removeChild(ta);
  }
}

/**
 * 텍스트 복사.
 *
 * 웹에서 **비동기 API를 먼저 await 하면 안 된다.** `navigator.clipboard`(expo-clipboard가
 * 웹에서 쓰는 것)는 보안 컨텍스트에서만 존재하는데, 실기기 테스트는 보통
 * `http://<PC-IP>:8081` 이라 없다. 그런데 없는 API를 await 로 한 번 태우고 나면
 * 사용자 제스처(transient activation)가 끊겨 뒤따르는 execCommand 폴백까지 같이 죽는다.
 * → 그래서 쓸 수 있는지를 **동기로 판별**해 폴백을 먼저 태운다.
 *
 * 이걸 안 지키면 사용자 눈에는 "코드복사를 눌러도 아무 일도 안 일어남"으로 보인다.
 */
export async function copyText(text: string): Promise<boolean> {
  if (Platform.OS === 'web') {
    const asyncApi =
      typeof navigator !== 'undefined' &&
      typeof navigator.clipboard?.writeText === 'function' &&
      typeof window !== 'undefined' &&
      window.isSecureContext;

    let ok: boolean;
    if (!asyncApi) {
      ok = copyTextSync(text);
    } else {
      try {
        await navigator.clipboard.writeText(text);
        ok = true;
      } catch {
        // 권한 거부·포커스 상실 — 구식 경로가 통하는 경우가 있어 한 번 더 시도한다.
        ok = copyTextSync(text);
      }
    }

    // 실패하면 어느 조건에서 막혔는지 남긴다 — 기기별로만 재현돼 원격 진단이 어렵다.
    if (!ok && __DEV__) {
      console.warn('[copyText] 복사 실패', {
        secureContext: typeof window !== 'undefined' ? window.isSecureContext : 'n/a',
        hasAsyncClipboard: asyncApi,
        origin: typeof window !== 'undefined' ? window.location.origin : 'n/a',
      });
    }
    return ok;
  }

  try {
    await Clipboard.setStringAsync(text);
    return true;
  } catch {
    return false;
  }
}

export function copyInviteMessage(invite: KakaoInvite): Promise<boolean> {
  return copyText(inviteMessage(invite));
}

/**
 * OS 공유 시트. 웹과 네이티브가 완전히 다른 API다.
 *
 * react-native 의 `Share.share` 는 **웹에서 동작하지 않는다**(react-native-web 미구현).
 * 그래서 웹은 Web Share API 를 직접 쓴다 — 다만 이것도 보안 컨텍스트 + 사용자 제스처가
 * 필요해서, 없으면 조용히 false 를 돌려주고 호출부가 클립보드로 내려가게 한다.
 */
export async function openShareSheet(message: string, title: string): Promise<boolean> {
  if (Platform.OS === 'web') {
    const nav = typeof navigator !== 'undefined' ? (navigator as Navigator & { share?: (d: ShareData) => Promise<void> }) : undefined;
    if (!nav?.share) return false;
    try {
      await nav.share({ title, text: message });
      return true;
    } catch {
      return false; // 사용자가 취소했거나 브라우저가 거부
    }
  }

  try {
    await Share.share({ message, title });
    return true;
  } catch {
    return false;
  }
}

/* ── 웹: Kakao JS SDK ─────────────────────────────────────────────── */

declare global {
  // eslint-disable-next-line no-var
  var Kakao: any;
}

/** 스크립트 로드는 한 번만 — 시트를 여러 번 열어도 <script> 가 쌓이지 않게 캐시한다. */
let webSdkReady: Promise<void> | null = null;

function loadKakaoJsSdk(): Promise<void> {
  if (webSdkReady) return webSdkReady;

  webSdkReady = new Promise<void>((resolve, reject) => {
    if (typeof window === 'undefined' || typeof document === 'undefined') {
      return reject(new Error('브라우저 환경이 아닙니다'));
    }
    if (!KAKAO_JAVASCRIPT_KEY) {
      return reject(new Error('EXPO_PUBLIC_KAKAO_JAVASCRIPT_KEY 가 비어 있습니다'));
    }

    const init = () => {
      try {
        if (!window.Kakao.isInitialized()) window.Kakao.init(KAKAO_JAVASCRIPT_KEY);
        resolve();
      } catch (e) {
        reject(e);
      }
    };

    if (window.Kakao) return init();

    // 같은 스크립트가 이미 붙어 있으면(핫리로드 등) 재사용한다.
    const existing = document.querySelector<HTMLScriptElement>(
      `script[src="${KAKAO_JS_SDK_SRC}"]`,
    );
    const script = existing ?? document.createElement('script');
    script.addEventListener('load', init, { once: true });
    script.addEventListener('error', () => reject(new Error('Kakao SDK 로드 실패')), {
      once: true,
    });
    if (!existing) {
      script.src = KAKAO_JS_SDK_SRC;
      script.async = true;
      document.head.appendChild(script);
    }
  }).catch((e) => {
    webSdkReady = null; // 실패는 캐시하지 않는다 — 다음 시도에서 다시 받아본다
    throw e;
  });

  return webSdkReady;
}

/**
 * SDK를 미리 받아 둔다. **초대 시트가 열릴 때 호출한다.**
 *
 * 이게 없으면 첫 클릭에서 `await loadKakaoJsSdk()`(CDN 네트워크 왕복)를 태우게 되는데,
 * 그동안 사용자 제스처(transient activation)가 끝나서 뒤이은 `sendDefault`의
 * **팝업이 브라우저에 차단된다.** 예외도 안 나서 "눌렀는데 아무 일도 안 일어남"이 된다.
 */
export function preloadKakaoWebSdk(): void {
  if (Platform.OS !== 'web' || !KAKAO_JAVASCRIPT_KEY) return;
  void loadKakaoJsSdk().catch(() => {
    /* 미리 받기 실패는 조용히 넘긴다 — 실제 공유 시도에서 다시 처리한다 */
  });
}

/** SDK가 지금 당장 동기로 쓸 수 있는 상태인가 (= 팝업을 제스처 안에서 열 수 있는가) */
function isKakaoWebReady(): boolean {
  try {
    return Boolean(window.Kakao?.Share && window.Kakao.isInitialized());
  } catch {
    return false;
  }
}

/** 공유 카드 전송. **반드시 동기로** 호출한다 — 팝업이 제스처에 묶여야 한다. */
function sendKakaoWebFeed(invite: KakaoInvite): void {
  const webTarget = { mobileWebUrl: invite.link, webUrl: invite.link };
  const executionParams = buildKakaoExecutionParams(invite.code);
  const appTarget = {
    ...webTarget,
    androidExecutionParams: executionParams,
    iosExecutionParams: executionParams,
  };

  window.Kakao.Share.sendDefault({
    objectType: 'feed',
    content: {
      title: `${invite.roomName} 공유 옷장 초대`,
      description: `참여코드 ${invite.code}\n눌러서 바로 참여하세요.`,
      imageUrl: INVITE_THUMBNAIL,
      // 카드 본문은 어느 기기에서 눌러도 웹 초대장을 연다.
      link: webTarget,
    },
    buttons: [
      {
        title: '앱에서 초대 수락',
        // 모바일은 설치된 앱을 우선하고, PC에서는 webUrl로 자연스럽게 내려간다.
        link: appTarget,
      },
      {
        title: '웹 초대장 열기',
        // 앱 설치 여부와 관계없이 반드시 열 수 있는 명시적 대체 경로다.
        link: webTarget,
      },
    ],
  });
}

/* ── 공통 진입점 ──────────────────────────────────────────────────── */

/**
 * 카카오톡 공유 창을 연다. 성공하면 친구·채팅방을 고르는 화면이 뜬다.
 *
 * 실패하면 조용히 죽지 않고 단계적으로 물러난다:
 *   카카오 SDK → OS 공유 시트 → 클립보드 복사
 * 어느 단계든 초대 문구는 이미 클립보드에 있으므로 사용자가 직접 붙여넣을 수 있다.
 */
export async function shareInviteViaKakao(invite: KakaoInvite): Promise<KakaoShareResult> {
  const message = inviteMessage(invite);

  if (Platform.OS === 'web') {
    /* 설정 누락은 몇 번을 눌러도 안 되는 실패다. 다른 실패와 뭉뚱그리면
       "왜 카톡이 안 열리지"를 영원히 못 찾는다 — 제일 먼저 갈라낸다. */
    if (!isKakaoWebConfigured()) return 'no-key';

    // 카카오 JS SDK는 등록된 HTTPS 도메인에서만 공유창을 연다.
    // http 로 띄운 상태에서는 열리지 않을 링크를 보내지 않고 복사로 대신한다.
    if (!invite.link.startsWith('https://')) {
      return (await copyInviteMessage(invite)) ? 'clipboard' : 'cancelled';
    }

    /* ★ 이 아래에서 sendDefault 앞에 await 를 두면 안 된다.
       공유창은 팝업이라, 클릭 제스처가 살아 있는 동안 동기로 열어야 브라우저가 허용한다.
       예전엔 클립보드 복사(await) + SDK 로드(await) 두 개를 먼저 태워서
       팝업이 차단됐고, sendDefault 는 예외도 안 던져 "눌러도 무반응"으로 보였다.
       그래서 복사는 뒤로 미루고(void), SDK 는 시트가 열릴 때 미리 받아 둔다. */
    if (isKakaoWebReady()) {
      try {
        sendKakaoWebFeed(invite);
        void copyInviteMessage(invite);
        return 'kakao';
      } catch (e) {
        if (__DEV__) console.warn('[kakao] sendDefault 실패', e);
      }
    }

    /* 미리 받기가 아직 안 끝난 첫 클릭. 어쩔 수 없이 기다렸다 보내므로
       팝업이 막힐 수 있다 — 그 경우 다음 클릭이 위 동기 경로를 탄다. */
    try {
      await loadKakaoJsSdk();
      sendKakaoWebFeed(invite);
      void copyInviteMessage(invite);
      return 'kakao';
    } catch (e) {
      if (__DEV__) console.warn('[kakao] 웹 공유 실패 — 공유 시트로 대체합니다', e);
      if (await openShareSheet(message, `${invite.roomName} 초대`)) return 'share-sheet';
      return (await copyInviteMessage(invite)) ? 'clipboard' : 'cancelled';
    }
  }

  const copied = await copyInviteMessage(invite);

  /**
   * 링크 목적지.
   * - `mobileWebUrl`/`webUrl` : 앱이 없는 사람 → 웹 초대장(/invite?code=)
   * - `*ExecutionParams`      : 앱이 있는 사람 → 카카오톡이 앱을 직접 실행하고
   *   이 파라미터를 스킴 쿼리로 넘긴다. 받는 쪽은 app/+native-intent.tsx.
   * 둘을 같이 넣어야 "설치자는 앱, 미설치자는 웹"이 한 카드로 갈린다.
   */
  const target = {
    mobileWebUrl: invite.link,
    webUrl: invite.link,
    androidExecutionParams: { code: invite.code },
    iosExecutionParams: { code: invite.code },
  };
  /** 피드 템플릿 본문. 웹 SDK 만 여기에 objectType 을 얹어 달라고 요구한다. */
  const template: KakaoFeedTemplate = {
    content: {
      title: `${invite.roomName} 공유 옷장 초대`,
      // 카드를 눌러 들어오는 게 기본 경로라 링크가 버튼에 있고, 코드는 대비책으로 적는다.
      description: `참여코드 ${invite.code}\n눌러서 바로 참여하거나, 앱에 코드를 입력하세요.`,
      imageUrl: INVITE_THUMBNAIL,
      link: target,
    },
    buttons: [
      { title: '앱에서 초대 수락', link: target },
      {
        title: '웹 초대장 열기',
        link: { mobileWebUrl: invite.link, webUrl: invite.link },
      },
    ],
  };

  try {
    // 동적 import: 카카오 공유는 네이티브 모듈이라 웹 번들에 끌려 들어가면 안 된다.
    const { shareFeedTemplate } = await import('@react-native-kakao/share');
    await shareFeedTemplate({
      template,
      // 카카오톡이 없으면 카카오 웹 공유 페이지로 대신 연다.
      useWebBrowserIfKakaoTalkNotAvailable: true,
    });
    return 'kakao';
  } catch (e) {
    if (__DEV__) console.warn('카카오 공유 실패 — 공유 시트로 대체합니다', e);
  }

  if (await openShareSheet(message, `${invite.roomName} 초대`)) return 'share-sheet';

  return copied ? 'clipboard' : 'cancelled';
}
