import { Image } from 'expo-image';
import { router, useLocalSearchParams } from 'expo-router';
import { goBack } from '@/lib/goBack';
import { useCallback, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import {
  WebView,
  type WebViewMessageEvent,
  type WebViewNavigation,
} from 'react-native-webview';

import { ThemedText } from '@/components/themed-text';
import { EmptyState, ModalShell, useToast } from '@/components/ui';
import { MOBILE_USER_AGENT, resolveImportSite, type ImportSite } from '@/constants/import-sites';
import { ContentMax, Editorial, ink, Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import {
  BOOTSTRAP_JS,
  buildOrderScraperJS,
  IMAGE_SCAN_JS,
  PROBE_JS,
  type ImageCandidate,
  type ImportMessage,
  type OrderItem,
  type ProbeHit,
} from '@/lib/import-inject';
import { classifyProduct } from '@/lib/classifyProduct';
import {
  WARDROBE_BATCH_MAX_ITEMS,
  WARDROBE_BATCH_MAX_LINK_LENGTH,
  WARDROBE_BATCH_MAX_NAME_LENGTH,
  type WardrobeBatchItemInput,
} from '@/lib/wardrobeApi';
import { uploadJobs } from '@/state/upload-jobs';

/**
 * 옷장 가져오기 — 인앱 브라우저.
 *
 * 흐름(WebView.md 4장 "방식① 구매내역 스캔"):
 *   ① 구매목록 URL 로 시작 → 미로그인이면 쇼핑몰이 로그인 페이지로 보낸다
 *   ② 사용자가 **직접** 로그인 (우리는 비밀번호를 보지도 저장하지도 않는다. 세션 쿠키만 WebView 에 남는다)
 *   ③ 로그인이 끝나면 구매목록으로 자동 복귀 — 엉뚱한 페이지에 떨어지면 한 번 더 밀어준다
 *   ④ "구매목록 불러오기" → 스크래퍼 주입 → postMessage 로 상품 목록 수신
 *   ⑤ 옷으로 보이는 것만 골라 **이미지 주소 + 상품명**을 일괄 등록 API 로 넘긴다.
 *      이미지 다운로드·누끼·태깅은 전부 서버 몫이고, 앱은 진행 화면에서 결과만 기다린다.
 *
 * 방식②(아무 상품 페이지에서 큰 이미지 후보 뽑기)는 "자동스캔" 버튼으로 그대로 유지한다.
 * 이쪽은 상품명이 없어 분류를 못 붙이지만, 서버 모델이 사진만으로 태그를 채운다.
 */

/** 스크래핑이 이 시간 안에 안 끝나면 실패로 본다(스크롤 루프 최악 ~14초 + 여유) */
const SCAN_TIMEOUT_MS = 25_000;

type Phase = 'login' | 'orders' | 'other';
type ResultMode = 'orders' | 'images' | null;

/**
 * 상품 1건 → 일괄 등록 payload.
 *
 * 서버가 받을 수 없는 주소(공개 http(s) 가 아니거나 너무 긴 것)는 여기서 걸러 null 로 만든다.
 * 한 건이라도 형식이 어긋나면 배치 요청 **전체**가 400 이라, 보내기 전에 떨어뜨리는 편이 낫다.
 */
function toBatchItem(
  site: ImportSite,
  imageLink: string,
  name: string,
): WardrobeBatchItemInput | null {
  // 썸네일 대신 원본을 보낸다 — 서버 모델이 읽을 사진이라 클수록 태그가 정확하다
  const link = site.upscaleImage?.(imageLink) ?? imageLink;
  if (!/^https?:\/\//i.test(link)) return null;
  if (link.length > WARDROBE_BATCH_MAX_LINK_LENGTH) return null;

  const guess = classifyProduct(name);
  return {
    image_link: link,
    item_name: name.trim().slice(0, WARDROBE_BATCH_MAX_NAME_LENGTH),
    /* 상품명으로 알아본 분류만 넣는다. 못 알아봤으면 비워 두고 서버 모델에 맡긴다 —
       추측으로 채우면 틀린 분류가 모델 결과를 덮어쓴다(앱이 보낸 값이 우선이다). */
    ...(guess ? { category_large: guess.large, category_small: guess.small } : {}),
    // 사람이 태그를 확인하기 전까지는 추천에서 빼둔다(사진 1장 업로드와 같은 규칙)
    confirmed: false,
  };
}

/**
 * 웹 안내 화면.
 * react-native-webview 는 웹을 지원하지 않아 브라우저에서는 빈 화면 + 에러가 뜬다.
 * 진입을 막는 대신, 기능이 존재한다는 것과 어디서 쓸 수 있는지를 알려준다.
 */
function ImportUnsupportedOnWeb() {
  return (
    <ModalShell maxWidth={ContentMax.card}>
      <View style={styles.webNotice}>
        <SafeAreaView edges={['top']} style={styles.webNoticeSafe}>
          <EmptyState
            icon="globe"
            title="가져오기는 앱에서 쓸 수 있어요"
            description={
              '쇼핑몰에 로그인해 구매목록을 불러오는 기능이라\n' +
              'iOS·Android 앱에서만 동작해요. 웹에서는 앨범·카메라로 추가해 주세요.'
            }
            actionLabel="옷장으로 돌아가기"
            onAction={() => router.replace('/(tabs)/closet')}
          />
        </SafeAreaView>
      </View>
    </ModalShell>
  );
}

/**
 * 결과 모달 하단의 확정 버튼.
 * 접수는 서버가 이미지를 하나씩 받아오는 동안 기다려야 해서(장수만큼 길어진다)
 * 눌린 뒤에도 몇 초 조용하다 — 그동안 무슨 일이 벌어지는지 버튼이 직접 말해준다.
 */
function AddButton({
  count,
  busy,
  onPress,
}: {
  count: number;
  busy: boolean;
  onPress: () => void;
}) {
  const theme = useTheme();
  return (
    <Pressable
      style={[
        styles.primaryButton,
        styles.addButton,
        { backgroundColor: theme.text, opacity: count && !busy ? 1 : 0.4 },
      ]}
      disabled={!count || busy}
      onPress={onPress}>
      {busy ? <ActivityIndicator color={theme.background} size="small" /> : null}
      <ThemedText style={{ color: theme.background, fontWeight: '600' }}>
        {busy ? '상품 사진을 가져오는 중…' : `내 옷장에 추가 (${count})`}
      </ThemedText>
    </Pressable>
  );
}

export default function ImportScreen() {
  const theme = useTheme();
  const toast = useToast();
  const webRef = useRef<WebView>(null);

  // /import?site=naver — 기본값은 네이버(1차 지원 대상)
  const params = useLocalSearchParams<{ site?: string }>();
  const site = resolveImportSite(params.site);

  const [currentUrl, setCurrentUrl] = useState(site.orderUrl);
  const [phase, setPhase] = useState<Phase>('other');
  const [scanning, setScanning] = useState(false);
  /** 일괄 등록 접수 중 — 이미지를 서버가 받아오는 동안이라 몇 초 걸린다 */
  const [submitting, setSubmitting] = useState(false);

  const [orders, setOrders] = useState<OrderItem[]>([]);
  const [candidates, setCandidates] = useState<ImageCandidate[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [resultMode, setResultMode] = useState<ResultMode>(null);
  const [probe, setProbe] = useState<ProbeHit[] | null>(null);

  // 로그인 화면을 거쳤는지 / 구매목록으로 이미 한 번 밀어줬는지 (렌더와 무관해 ref)
  const sawLogin = useRef(false);
  const autoJumped = useRef(false);
  const scanTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  /**
   * WebView 가 로드할 주소.
   *
   * injectJavaScript(location.href=...) 로 옮기지 않는 이유: 로드 실패 화면(ATS 차단 등)에는
   * JS 실행 맥락이 없어 주입이 통하지 않는다. 그래서 source 를 바꿔서 이동한다.
   *
   * 이동 주소를 **어느 사이트에서 정한 것인지와 함께** 들고 있다가, site 가 달라지면 버린다.
   * `/import?site=` 파라미터만 바뀔 때 이 화면은 재마운트되지 않아서, 그냥 uri 만 저장하면
   * 헤더는 새 사이트인데 화면은 이전 사이트가 뜬다.
   */
  const [nav, setNav] = useState<{ siteKey: string; uri: string } | null>(null);
  const uri = nav?.siteKey === site.key ? nav.uri : site.orderUrl;

  const navigate = useCallback(
    (next: string) => {
      if (next === uri) {
        webRef.current?.reload(); // 같은 주소면 source 가 안 바뀌어 재로드가 일어나지 않는다
        return;
      }
      setNav({ siteKey: site.key, uri: next });
    },
    [uri, site.key],
  );

  const goToOrders = useCallback(() => navigate(site.orderUrl), [navigate, site.orderUrl]);

  const stopScanTimer = () => {
    if (scanTimer.current) clearTimeout(scanTimer.current);
    scanTimer.current = null;
  };

  const startScan = (js: string) => {
    if (scanning) return;
    setScanning(true);
    stopScanTimer();
    scanTimer.current = setTimeout(() => {
      setScanning(false);
      toast('페이지를 읽지 못했어요. 잠시 후 다시 시도해 주세요', { variant: 'error' });
    }, SCAN_TIMEOUT_MS);
    webRef.current?.injectJavaScript(js);
  };

  const finishScan = () => {
    stopScanTimer();
    setScanning(false);
  };

  /** URL 이 바뀔 때마다: 지금이 로그인 화면인지 구매목록인지 판정하고, 로그인 직후면 구매목록으로 보낸다 */
  const handleUrl = useCallback(
    (url: string) => {
      setCurrentUrl(url);

      // 로그인 URL 을 먼저 본다 — 로그인 주소에는 복귀 주소(구매목록)가 파라미터로 붙어 있어
      // 순서를 바꾸면 로그인 화면을 구매목록으로 오인할 수 있다.
      const next: Phase = site.loginUrlPattern.test(url)
        ? 'login'
        : site.orderUrlPattern.test(url)
          ? 'orders'
          : 'other';
      setPhase(next);

      if (next === 'login') {
        sawLogin.current = true;
        autoJumped.current = false;
        return;
      }
      if (next === 'orders') {
        sawLogin.current = false;
        return;
      }
      // 로그인은 끝났는데 구매목록이 아닌 곳(쇼핑 홈 등)에 떨어진 경우 → 한 번만 밀어준다.
      // 리다이렉트 체인이 아직 이어지는 중일 수 있어 조금 기다렸다 이동한다.
      if (sawLogin.current && !autoJumped.current) {
        sawLogin.current = false;
        autoJumped.current = true;
        setTimeout(goToOrders, 600);
      }
    },
    [goToOrders, site.loginUrlPattern, site.orderUrlPattern],
  );

  // http→https 로 한 번 올려본 주소. 사이트가 다시 http 로 되돌리면 무한루프가 되므로 1회만 시도한다.
  const upgraded = useRef<Set<string>>(new Set());

  /**
   * 평문 http 주소면 https 로 올려 다시 태운다. 올렸으면 true.
   *
   * 네이버는 로그인 성공 후 복귀 주소를 `url=http%3A%2F%2Fshopping.naver.com%2Fmy%2Forder`
   * 처럼 **http 로** 돌려준다. 두 플랫폼 모두 평문 HTTP 를 막는다 —
   * iOS 는 ATS 가 -1022, 안드로이드는 API 28+ 기본 차단으로 ERR_CLEARTEXT_NOT_PERMITTED.
   */
  const upgradeCleartext = useCallback(
    (url: string): boolean => {
      if (!url.startsWith('http://') || upgraded.current.has(url)) return false;
      upgraded.current.add(url);
      const secure = `https://${url.slice('http://'.length)}`;
      console.log('[import] http→https 승격', secure);
      navigate(secure);
      return true;
    },
    [navigate],
  );

  /* 승격을 세 군데서 노리는 이유 — 한 곳만으로는 안드로이드를 못 막는다.
     안드로이드의 onShouldStartLoadWithRequest 는 내부적으로 shouldOverrideUrlLoading 이라
     **서버 리다이렉트(302)에는 불리지 않는다.** 네이버 로그인 복귀가 정확히 그 경로라,
     iOS 에서만 막히고 안드로이드는 그대로 평문 요청이 나가 에러 화면이 됐다.
       ① onShouldStartLoadWithRequest — iOS 전 구간, 안드로이드는 링크 클릭
       ② onNavigationStateChange     — 안드로이드 리다이렉트가 여기서 잡힌다(로드 시작 시점)
       ③ onError                     — ①②를 다 놓쳤을 때의 마지막 그물 */
  const handleNav = (nav: WebViewNavigation) => {
    if (upgradeCleartext(nav.url)) return;
    handleUrl(nav.url);
  };

  /**
   * 로드 전 가로채기.
   *  ① 평문 http 는 https 로 올려서 다시 태운다.
   *  ② 커스텀 스킴(naversearchapp://, intent:// 등)은 막는다.
   *     네이버 앱으로 튀어나가면 WebView 안 로그인 세션이 끊겨 구매목록을 못 읽는다.
   */
  const handleShouldStart = (req: WebViewNavigation) => {
    const url = req.url;

    if (upgradeCleartext(url)) return false;

    if (/^(https?:|about:)/i.test(url)) return true;

    console.log('[import] 앱 전환 차단', url);
    return false;
  };

  /**
   * 로드 실패. 평문 차단이면 https 로 올려 되살리고, 그 밖의 실패는 WebView 기본 화면에 맡긴다.
   * 실패한 주소가 응답에 없을 수 있어(안드로이드) 지금 주소로도 한 번 시도한다.
   */
  /* 패키지 루트가 WebViewErrorEvent 를 다시 내보내지 않아(index.d.ts 는 세 개만) 필요한 필드만 적는다. */
  const handleError = (e: { nativeEvent: { url?: string; description?: string; code?: number } }) => {
    const { url, description, code } = e.nativeEvent;
    console.log('[import] 로드 실패', code, description, url);
    if (upgradeCleartext(url ?? '')) return;
    if (/CLEARTEXT|-1022/i.test(description ?? '')) upgradeCleartext(currentUrl);
  };

  const handleMessage = (e: WebViewMessageEvent) => {
    let msg: ImportMessage;
    try {
      msg = JSON.parse(e.nativeEvent.data);
    } catch {
      return;
    }

    switch (msg.type) {
      case 'LOG':
        console.log('[WebView]', msg.payload);
        return;

      // SPA 라우팅은 onNavigationStateChange 가 안 불려서 페이지 안에서 직접 알려준다
      case 'URL':
        handleUrl(msg.payload);
        return;

      case 'ORDER_ITEMS': {
        finishScan();
        const { items, via, matched } = msg.payload;
        console.log(`[import] 구매목록 ${items.length}건 (${via} / ${matched})`);
        if (!items.length) {
          toast('이 페이지에서 상품을 찾지 못했어요', { variant: 'error' });
          return;
        }
        setOrders(items);
        // 패션으로 알아본 것만 기본 선택. 못 알아본 건 목록에 남기되 선택은 해제한다.
        setSelected(
          new Set(
            items.reduce<string[]>((acc, it, i) => {
              if (classifyProduct(it.name)) acc.push(String(i));
              return acc;
            }, []),
          ),
        );
        setResultMode('orders');
        return;
      }

      case 'IMAGE_CANDIDATES':
        finishScan();
        if (!msg.payload.length) {
          toast('가져올 만한 사진을 찾지 못했어요', { variant: 'error' });
          return;
        }
        setCandidates(msg.payload);
        setSelected(new Set());
        setResultMode('images');
        return;

      case 'PROBE':
        finishScan();
        setProbe(msg.payload);
        return;

      case 'SCAN_ERROR':
        finishScan();
        console.log('[import] 스캔 오류', msg.payload);
        toast('페이지를 읽는 중 문제가 생겼어요', { variant: 'error' });
        return;
    }
  };

  const toggleSelect = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  /**
   * 선택한 상품을 옷장에 담는다 → 일괄 등록 API 접수 → 진행 화면으로.
   *
   * 선택 id 는 모드마다 다르다 — 구매목록은 **행 인덱스**, 자동스캔은 이미지 URL.
   * 구매목록에서는 서로 다른 주문이 같은 썸네일을 쓰는 경우가 실제로 있어(같은 상품 재구매 등)
   * 이미지 URL 을 id 로 쓰면 항목끼리 겹친다. 자동스캔 후보는 URL 로 이미 중복 제거돼 있어 안전하다.
   *
   * 이미지는 **주소만** 보낸다. 앱이 내려받아 다시 올리면 왕복이 두 배가 되고, 쇼핑몰 이미지는
   * Referer 없는 요청을 403 으로 막는 곳이 있어 앱에서 받는 것 자체가 불안하다.
   * 상품명으로 알아낸 분류는 함께 실어 보낸다 — 서버에서 모델이 뽑은 값보다 우선한다.
   */
  const confirmSelection = async () => {
    if (submitting) return;
    const picked = Array.from(selected);
    if (!picked.length) return;

    const items = picked
      .map((id) => {
        if (resultMode !== 'orders') return toBatchItem(site, id, '');
        const order = orders[Number(id)];
        return order ? toBatchItem(site, order.image, order.name) : null;
      })
      .filter((item): item is WardrobeBatchItemInput => item !== null);

    if (!items.length) {
      toast('사진 주소를 찾지 못했어요', { variant: 'error' });
      return;
    }

    // 서버가 한 번에 받는 건수 제한 — 넘치면 앞에서부터 잘라 보낸다(요청 전체가 400 이 되느니).
    const dropped = Math.max(items.length - WARDROBE_BATCH_MAX_ITEMS, 0);
    setSubmitting(true);
    try {
      /* 접수는 서버가 이미지를 하나씩 내려받는 동안 기다린다(장수만큼 길어진다).
         그 뒤 태깅 진행은 스토어가 따라가므로 이 화면을 닫아도 계속된다. */
      const batch = await uploadJobs.startBatch(items.slice(0, WARDROBE_BATCH_MAX_ITEMS));
      setResultMode(null);
      if (batch.rejected.length) {
        toast(`${batch.rejected.length}건은 사진을 가져오지 못했어요`, { variant: 'error' });
      } else if (dropped) {
        toast(`한 번에 ${WARDROBE_BATCH_MAX_ITEMS}건까지만 담을 수 있어 ${dropped}건은 빼두었어요`);
      }
      // 진행 상황은 옷장 화면 위쪽 줄에서 이어서 보여준다
      router.replace('/(tabs)/closet');
    } catch (e) {
      toast(e instanceof Error ? e.message : '옷장에 담지 못했어요', { variant: 'error' });
    } finally {
      setSubmitting(false);
    }
  };

  // 상품명에서 추정한 옷장 분류. null = "모르겠다"(옷이 아니라는 뜻이 아니다)
  const guesses = useMemo(() => orders.map((it) => classifyProduct(it.name)), [orders]);
  const fashionCount = guesses.filter(Boolean).length;

  // WebView 가 웹을 지원하지 않으므로 브라우저에서는 안내 화면으로 대체한다.
  if (Platform.OS === 'web') return <ImportUnsupportedOnWeb />;

  const onOrderPage = phase === 'orders';
  const statusText =
    phase === 'login'
      ? // 로그인은 사용자가 직접 한다. 비밀번호를 읽는 코드가 없다는 걸 화면에서도 밝힌다.
        `${site.label}에 로그인하면 구매목록으로 이동해요 · 비밀번호는 저장하지 않아요`
      : onOrderPage
        ? '구매목록이에요. 아래 버튼으로 상품을 불러오세요'
        : '구매목록 페이지에서 상품을 불러올 수 있어요';

  return (
    <View style={styles.container}>
      {/* 상단 바: 취소 + 사이트 + 현재 URL */}
      <SafeAreaView
        edges={['top']}
        style={[styles.urlBar, { backgroundColor: theme.backgroundElement }]}>
        <Pressable hitSlop={8} onPress={() => goBack('/(tabs)/closet')}>
          <ThemedText type="smallBold">취소</ThemedText>
        </Pressable>
        <View style={styles.urlTextWrap}>
          <ThemedText type="smallBold" numberOfLines={1}>
            {site.label} 구매목록
          </ThemedText>
          <ThemedText type="small" numberOfLines={1} themeColor="textSecondary">
            {currentUrl}
          </ThemedText>
        </View>
      </SafeAreaView>

      {/* 인앱 브라우저 */}
      <WebView
        ref={webRef}
        source={{ uri }}
        style={styles.webview}
        // 로그인 세션(쿠키) 유지 — 이게 없으면 로그인해도 구매목록이 비로그인 HTML 로 온다
        sharedCookiesEnabled
        thirdPartyCookiesEnabled
        // 기본은 순정 UA. 쇼핑몰이 인앱브라우저 로그인을 막을 때만 어댑터에서 켠다.
        userAgent={
          site.spoofUserAgent
            ? Platform.OS === 'ios'
              ? MOBILE_USER_AGENT.ios
              : MOBILE_USER_AGENT.android
            : undefined
        }
        injectedJavaScriptBeforeContentLoaded={BOOTSTRAP_JS}
        onNavigationStateChange={handleNav}
        onShouldStartLoadWithRequest={handleShouldStart}
        onError={handleError}
        onMessage={handleMessage}
        startInLoadingState
      />

      {/* 하단 액션 바 */}
      <SafeAreaView edges={['bottom']} style={[styles.actionBar, { backgroundColor: theme.background }]}>
        <View style={styles.statusRow}>
          <ThemedText type="small" themeColor="textSecondary" style={styles.statusText}>
            {statusText}
          </ThemedText>
          {onOrderPage ? null : (
            <Pressable hitSlop={8} onPress={goToOrders}>
              <ThemedText type="smallBold">구매목록 열기</ThemedText>
            </Pressable>
          )}
        </View>

        <View style={styles.buttonRow}>
          <Pressable
            style={[styles.secondaryButton, { borderColor: ink(0.15) }]}
            disabled={scanning}
            onPress={() => startScan(IMAGE_SCAN_JS)}>
            <ThemedText type="smallBold">자동스캔</ThemedText>
          </Pressable>

          <Pressable
            style={[
              styles.primaryButton,
              styles.grow,
              { backgroundColor: theme.text, opacity: onOrderPage && !scanning ? 1 : 0.4 },
            ]}
            disabled={!onOrderPage || scanning}
            onPress={() => startScan(buildOrderScraperJS(site))}>
            <ThemedText style={{ color: theme.background, fontWeight: '600' }}>
              구매목록 불러오기
            </ThemedText>
          </Pressable>
        </View>

        {/* selector 조사용 (개발 빌드 전용) — 로그인해야 보이는 페이지 구조를 앱 안에서 확인 */}
        {__DEV__ ? (
          <Pressable style={styles.probeButton} disabled={scanning} onPress={() => startScan(PROBE_JS)}>
            <ThemedText type="small" themeColor="textSecondary">
              구조분석(개발용)
            </ThemedText>
          </Pressable>
        ) : null}
      </SafeAreaView>

      {/* 스크래핑 중 오버레이 — 스크롤로 전부 로드하느라 몇 초 걸린다 */}
      {scanning ? (
        <View style={styles.scanOverlay}>
          <ActivityIndicator color="#fff" />
          <ThemedText style={styles.scanText}>페이지를 읽는 중이에요…</ThemedText>
        </View>
      ) : null}

      {/* 구매목록 결과 */}
      <Modal
        visible={resultMode === 'orders'}
        animationType="slide"
        onRequestClose={() => setResultMode(null)}>
        <SafeAreaView style={[styles.modal, { backgroundColor: theme.background }]}>
          <View style={styles.modalHeader}>
            <ThemedText type="smallBold">
              구매목록 {orders.length}건 · 패션 {fashionCount}건 · 선택 {selected.size}
            </ThemedText>
            <Pressable onPress={() => setResultMode(null)}>
              <ThemedText type="small" themeColor="textSecondary">
                닫기
              </ThemedText>
            </Pressable>
          </View>

          <ScrollView contentContainerStyle={styles.list}>
            {orders.map((item, i) => {
              // 같은 썸네일을 쓰는 주문이 실제로 있어 URL 은 고유 키가 못 된다 → 행 인덱스를 쓴다
              const key = String(i);
              const isSel = selected.has(key);
              const guess = guesses[i];
              return (
                <Pressable
                  key={key}
                  onPress={() => toggleSelect(key)}
                  // 옷으로 못 알아본 행은 흐리게 — 지우지는 않는다. 사용자가 직접 고를 수 있어야 한다.
                  style={[styles.row, isSel && { borderColor: theme.text }, !guess && styles.rowMuted]}>
                  <Image
                    // 쇼핑몰 이미지는 Referer 없으면 403 인 경우가 있어 원 페이지를 실어 보낸다
                    source={{ uri: item.image, headers: { Referer: site.orderUrl } }}
                    style={styles.rowThumb}
                    contentFit="cover"
                  />
                  <View style={styles.rowBody}>
                    <ThemedText type="small" numberOfLines={2}>
                      {item.name || '(상품명 없음)'}
                    </ThemedText>
                    <ThemedText type="small" themeColor="textSecondary" numberOfLines={1}>
                      {[item.price, item.date].filter(Boolean).join(' · ') || ' '}
                    </ThemedText>
                    {guess ? (
                      <View style={[styles.badge, { backgroundColor: Editorial.surfaceTag }]}>
                        <ThemedText type="small">
                          {guess.large} · {guess.small}
                        </ThemedText>
                      </View>
                    ) : (
                      <ThemedText type="small" themeColor="textSecondary">
                        옷으로 보이지 않아요
                      </ThemedText>
                    )}
                  </View>
                  <View
                    style={[
                      styles.check,
                      { borderColor: isSel ? theme.text : ink(0.2) },
                      isSel && { backgroundColor: theme.text },
                    ]}
                  />
                </Pressable>
              );
            })}
          </ScrollView>

          <AddButton count={selected.size} busy={submitting} onPress={confirmSelection} />
        </SafeAreaView>
      </Modal>

      {/* 자동스캔 결과 (이미지 후보 그리드) */}
      <Modal
        visible={resultMode === 'images'}
        animationType="slide"
        onRequestClose={() => setResultMode(null)}>
        <SafeAreaView style={[styles.modal, { backgroundColor: theme.background }]}>
          <View style={styles.modalHeader}>
            <ThemedText type="smallBold">
              이미지 후보 {candidates.length}개 · 선택 {selected.size}
            </ThemedText>
            <Pressable onPress={() => setResultMode(null)}>
              <ThemedText type="small" themeColor="textSecondary">
                닫기
              </ThemedText>
            </Pressable>
          </View>

          <ScrollView contentContainerStyle={styles.grid}>
            {candidates.map((c) => {
              const isSel = selected.has(c.src);
              return (
                <Pressable key={c.src} onPress={() => toggleSelect(c.src)} style={styles.gridItem}>
                  <Image
                    source={{ uri: c.src }}
                    style={[
                      styles.thumb,
                      { borderColor: isSel ? theme.text : theme.backgroundElement },
                    ]}
                    contentFit="cover"
                  />
                </Pressable>
              );
            })}
          </ScrollView>

          <AddButton count={selected.size} busy={submitting} onPress={confirmSelection} />
        </SafeAreaView>
      </Modal>

      {/* 구조분석 결과 (개발용) — 여기 나온 selector 를 import-sites.ts 에 옮겨 적는다 */}
      <Modal visible={probe !== null} animationType="fade" transparent onRequestClose={() => setProbe(null)}>
        <Pressable style={styles.probeBackdrop} onPress={() => setProbe(null)}>
          <View style={[styles.probeCard, { backgroundColor: theme.background }]}>
            <ThemedText type="smallBold">itemSelector 후보</ThemedText>
            {(probe ?? []).length === 0 ? (
              <ThemedText type="small" themeColor="textSecondary">
                상품 카드처럼 보이는 요소를 못 찾았어요
              </ThemedText>
            ) : (
              (probe ?? []).map((h) => (
                <View key={h.selector} style={styles.probeRow}>
                  <ThemedText type="small">
                    {h.count}× {h.selector}
                  </ThemedText>
                  <ThemedText type="small" themeColor="textSecondary" numberOfLines={1}>
                    {h.sample}
                  </ThemedText>
                </View>
              ))
            )}
          </View>
        </Pressable>
      </Modal>
    </View>
  );
}

const THUMB_SIZE = '31%';

const styles = StyleSheet.create({
  webNotice: { flex: 1, backgroundColor: Editorial.surface },
  webNoticeSafe: { flex: 1, justifyContent: 'center' },
  container: { flex: 1 },

  urlBar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.three,
    paddingHorizontal: Spacing.three,
    paddingBottom: Spacing.two,
  },
  urlTextWrap: { flex: 1 },
  webview: { flex: 1 },

  actionBar: {
    paddingHorizontal: Spacing.three,
    paddingTop: Spacing.two,
    gap: Spacing.two,
  },
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: Spacing.two,
  },
  statusText: { flex: 1 },
  buttonRow: { flexDirection: 'row', gap: Spacing.two },
  primaryButton: {
    paddingVertical: Spacing.three,
    borderRadius: Spacing.three,
    alignItems: 'center',
  },
  // 가로 액션바에서 남는 폭을 채울 때만 붙인다.
  // primaryButton 자체에 flex 를 넣으면 세로 컨테이너(결과 모달 하단)에서 버튼이 세로로 늘어난다.
  grow: { flex: 1 },
  addButton: {
    flexDirection: 'row',
    justifyContent: 'center',
    gap: Spacing.two,
    margin: Spacing.three,
  },
  secondaryButton: {
    paddingVertical: Spacing.three,
    paddingHorizontal: Spacing.four,
    borderRadius: Spacing.three,
    borderWidth: 1,
    alignItems: 'center',
  },
  probeButton: { alignSelf: 'center', paddingVertical: Spacing.one },

  scanOverlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: ink(0.55),
    alignItems: 'center',
    justifyContent: 'center',
    gap: Spacing.two,
  },
  scanText: { color: Editorial.white },

  modal: { flex: 1 },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: Spacing.three,
  },

  list: { paddingHorizontal: Spacing.three, gap: Spacing.two },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.three,
    padding: Spacing.two,
    borderRadius: Spacing.two,
    borderWidth: 1,
    borderColor: 'transparent',
  },
  rowMuted: { opacity: 0.45 },
  rowThumb: { width: 56, height: 72, borderRadius: Spacing.one, backgroundColor: Editorial.bone },
  rowBody: { flex: 1, gap: Spacing.half, alignItems: 'flex-start' },
  badge: {
    paddingHorizontal: Spacing.two,
    paddingVertical: Spacing.half,
    borderRadius: Spacing.one,
  },
  check: { width: 20, height: 20, borderRadius: 10, borderWidth: 2 },

  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: Spacing.two,
    paddingHorizontal: Spacing.three,
  },
  gridItem: { width: THUMB_SIZE, aspectRatio: 0.75 },
  thumb: { width: '100%', height: '100%', borderRadius: Spacing.two, borderWidth: 3 },

  probeBackdrop: {
    flex: 1,
    backgroundColor: ink(0.45),
    alignItems: 'center',
    justifyContent: 'center',
    padding: Spacing.three,
  },
  probeCard: {
    width: '100%',
    maxWidth: ContentMax.card,
    borderRadius: Spacing.three,
    padding: Spacing.three,
    gap: Spacing.two,
  },
  probeRow: { gap: Spacing.half },
});
