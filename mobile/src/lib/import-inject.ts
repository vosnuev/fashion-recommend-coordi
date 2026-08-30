/**
 * WebView 에 주입하는 JS 모음 (웹 → 앱 통신 프로토콜 포함).
 *
 * 웹↔앱 통신은 `window.ReactNativeWebView.postMessage(문자열)` 하나뿐이라
 * 항상 `{ type, payload }` 를 JSON 으로 감싸 보내고 앱에서 파싱한다.
 *
 * 주입 코드는 **ES5 스타일**로 쓴다. 구형 WebKit/화면별 CSP 환경에서
 * 화살표 함수·옵셔널체이닝이 통째로 파싱 실패하면 원인 파악이 어렵기 때문.
 */

import type { ImportSite } from '@/constants/import-sites';

export type ImageCandidate = { src: string; w: number; h: number };

/** 구매목록에서 긁어온 상품 한 칸 */
export type OrderItem = {
  name: string;
  image: string;
  price: string;
  date: string;
  link: string;
};

/** 구조분석(디버그)에서 제안하는 itemSelector 후보 */
export type ProbeHit = { selector: string; count: number; sample: string };

export type ImportMessage =
  | { type: 'LOG'; payload: string }
  | { type: 'URL'; payload: string }
  | { type: 'IMAGE_CANDIDATES'; payload: ImageCandidate[] }
  | {
      type: 'ORDER_ITEMS';
      payload: { items: OrderItem[]; via: 'selector' | 'heuristic'; matched: string; url: string };
    }
  | { type: 'PROBE'; payload: ProbeHit[] }
  | { type: 'SCAN_ERROR'; payload: string };

/**
 * 페이지 로드 전에 주입.
 *  - console.log 를 앱으로 넘겨 디버깅
 *  - SPA 라우팅(pushState/replaceState) 감지 → onNavigationStateChange 가 안 불리는 경우 대비.
 *    네이버 구매목록은 SPA 라서 이게 없으면 "구매목록 도착"을 놓친다.
 */
export const BOOTSTRAP_JS = `
(function() {
  if (window.__cozyBootstrapped) return;
  window.__cozyBootstrapped = true;

  function send(type, payload) {
    try {
      window.ReactNativeWebView.postMessage(JSON.stringify({ type: type, payload: payload }));
    } catch (e) {}
  }

  var orig = console.log;
  console.log = function() {
    send('LOG', Array.prototype.slice.call(arguments).join(' '));
    orig.apply(console, arguments);
  };

  function notifyUrl() { send('URL', location.href); }

  ['pushState', 'replaceState'].forEach(function(name) {
    var fn = history[name];
    history[name] = function() {
      var r = fn.apply(this, arguments);
      setTimeout(notifyUrl, 0);
      return r;
    };
  });
  window.addEventListener('popstate', notifyUrl);
  window.addEventListener('load', notifyUrl);
  notifyUrl();
})();
true;
`;

/**
 * 주입 코드가 공통으로 쓰는 헬퍼.
 * 상대경로 절대화 / lazy-load 이미지 fallback / 텍스트 정규화 — 스크래핑 함정 대응(WebView.md 8장).
 */
const HELPERS_JS = `
  var PRICE_RE = /[0-9][0-9,]{2,}\\s*원/;
  var DATE_RE = /(20\\d{2}[.\\-\\/년]\\s?\\d{1,2}[.\\-\\/월]\\s?\\d{1,2})|(\\d{1,2}[.\\-\\/월]\\s?\\d{1,2}\\s?일?)/;
  // 주문 상태 문구 — 상품명 자리에 잘 끼어든다(네이버는 상태를 강조 태그로 넣는다)
  var STATUS_RE = /^(구매확정|배송|결제|주문|취소|반품|교환|입금|리뷰|정기결제|네이버페이)/;

  function send(type, payload) {
    window.ReactNativeWebView.postMessage(JSON.stringify({ type: type, payload: payload }));
  }
  function abs(u) { try { return new URL(u, location.href).href; } catch (e) { return u || ''; } }
  function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim(); }
  // innerText 는 "화면에 보이는 대로"(숨김 제외·줄바꿈 유지)라 스크래핑에 더 정확하지만,
  // 요소에 따라 비어 있을 수 있어 textContent 로 떨어뜨린다.
  function rawText(el) { return el ? (el.innerText || el.textContent || '') : ''; }
  function textOf(el) { return norm(rawText(el)); }
  function slice(list) { return Array.prototype.slice.call(list); }

  function firstMatch(root, selectors) {
    for (var i = 0; i < selectors.length; i++) {
      var el = null;
      try { el = root.querySelector(selectors[i]); } catch (e) {}
      if (el) return el;
    }
    return null;
  }

  // src 가 빈 lazy-load 이미지 / CSS background-image 까지 훑는다
  function srcOf(img) {
    if (!img) return '';
    var s = img.currentSrc || img.getAttribute('src') || img.getAttribute('data-src')
      || img.getAttribute('data-original') || img.getAttribute('data-lazy-src')
      || img.getAttribute('data-srcset') || '';
    if (s && s.indexOf(' ') > -1) s = s.split(' ')[0];      // srcset "url 2x" 형태
    if (!s) {
      var bg = '';
      try { bg = window.getComputedStyle(img).backgroundImage || ''; } catch (e) {}
      var m = bg.match(/url\\(["']?(.*?)["']?\\)/);
      if (m) s = m[1];
    }
    if (!s || s.indexOf('data:image') === 0) return '';
    return abs(s);
  }

  function matchIn(el, re) {
    var m = norm(rawText(el)).match(re);
    return m ? m[0] : '';
  }

  // 상품명 selector 가 안 맞을 때의 폴백.
  // innerText 의 줄 + "자식 요소가 없는 말단 요소"의 텍스트를 모두 후보로 놓고,
  // 가격·날짜가 아닌 가장 긴 문구를 상품명으로 본다.
  // (말단 요소까지 보는 이유: 카드 전체 텍스트가 한 줄로 붙어 오는 경우 줄 분리만으로는 못 건진다)
  function longestLine(el) {
    var texts = rawText(el).split('\\n');
    slice(el.querySelectorAll('*')).forEach(function(node) {
      if (node.children.length === 0) texts.push(rawText(node));
    });
    var lines = texts.map(norm).filter(function(t) {
      return t.length >= 4 && t.length <= 90
        && !PRICE_RE.test(t) && !DATE_RE.test(t) && !STATUS_RE.test(t);
    });
    lines.sort(function(a, b) { return b.length - a.length; });
    return lines[0] || '';
  }

  /**
   * 같은 selector 가 카드와 **그 내부 요소에 동시에** 매칭되는 경우가 있다(네이버가 그렇다).
   * 그대로 두면 조각까지 카드로 세어 개수가 부풀고, 상품명 자리에 '구매확정완료' 같은
   * 조각 텍스트가 들어간다. → 다른 매칭 요소 안에 들어 있는 것은 버리고 가장 바깥만 남긴다.
   */
  function outermost(els) {
    return els.filter(function(e) {
      return !els.some(function(o) { return o !== e && o.contains(e); });
    });
  }

  // 이미지에서 위로 올라가며 "가격 텍스트를 품은 가장 작은 조상"을 상품 카드로 본다.
  // 동적 클래스명(css-1a2b3c) 사이트에서 selector 가 전부 깨져도 이 경로로 건진다.
  function heuristicCards() {
    var cards = [];
    slice(document.images).forEach(function(img) {
      if (img.naturalWidth < 48 || img.naturalHeight < 48) return;
      var el = img.parentElement, hop = 0;
      while (el && hop < 7) {
        var t = norm(rawText(el));
        if (PRICE_RE.test(t) && t.length > 6) {
          if (cards.indexOf(el) === -1) cards.push(el);
          return;
        }
        el = el.parentElement; hop++;
      }
    });
    // 다른 카드를 통째로 품은 조상은 버린다(중첩 제거)
    return cards.filter(function(c) {
      return !cards.some(function(o) { return o !== c && c.contains(o); });
    });
  }

  // 끝까지 스크롤해 lazy-load 를 전부 로드시킨 뒤 콜백 (수집이 필요 없는 경우용)
  function loadAllThen(done) {
    var y = 0, guard = 0;
    var timer = setInterval(function() {
      window.scrollTo(0, y);
      y += window.innerHeight;
      guard++;
      if (y >= document.body.scrollHeight || guard > 40) {
        clearInterval(timer);
        window.scrollTo(0, 0);
        setTimeout(done, 900);   // 마지막 이미지 로딩 대기
      }
    }, 320);
  }

  // "더보기" 류 버튼을 한 번 누른다. 누를 게 있었으면 true.
  // 주문내역은 무한스크롤이 아니라 "더보기"로 다음 묶음을 부르는 경우가 흔하다.
  function clickMore() {
    var re = /더\\s*보기|더보기|이전\\s*주문|more/i;
    var nodes = slice(document.querySelectorAll('button, a, [role="button"]'));
    for (var i = 0; i < nodes.length; i++) {
      var b = nodes[i];
      if (b.__cozyClicked) continue;
      if (!re.test(norm(rawText(b)))) continue;
      var r = b.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;   // 숨김 버튼 제외
      b.__cozyClicked = true;
      b.click();
      return true;
    }
    return false;
  }

  /**
   * 스크롤을 내리면서 **매 스텝 수집**하고, 바닥에 닿으면 "더보기"를 눌러 계속한다.
   * 끝까지 내린 뒤 한 번만 긁으면, 화면 밖 항목을 DOM 에서 지우는 가상 스크롤 리스트에서는
   * 마지막 화면 몇 개만 남고 나머지를 전부 잃는다. 그래서 누적 방식이어야 한다.
   * collect() 는 이번 스텝에 새로 담은 개수를 반환한다.
   */
  function scrollCollect(collect, done) {
    var idle = 0, guard = 0;
    var timer = setInterval(function() {
      guard++;
      var added = collect();
      var atBottom = (window.innerHeight + window.scrollY) >= (document.body.scrollHeight - 120);

      if (added > 0) idle = 0;
      else if (atBottom) { if (!clickMore()) idle++; else idle = 0; }

      if (!atBottom) window.scrollBy(0, window.innerHeight);

      // 새로 담긴 것도 없고 더 부를 것도 없는 상태가 3번 이어지면 끝
      if (idle >= 3 || guard > 40) {
        clearInterval(timer);
        window.scrollTo(0, 0);
        setTimeout(function() { collect(); done(); }, 700);   // 마지막 로딩분 한 번 더
      }
    }, 400);
  }
`;

/**
 * 구매목록 스크래퍼.
 * 1) 어댑터 selector 로 카드 찾기 → 2) 전부 실패하면 휴리스틱 → 3) 필드별로도 selector→폴백 순.
 * 어느 경로로 뽑았는지(`via`, `matched`)를 함께 보내 selector 를 고칠 때 근거로 쓴다.
 */
export function buildOrderScraperJS(site: ImportSite): string {
  return `
(function() {
  try {
    var RULES = ${JSON.stringify(site.scrape)};
${HELPERS_JS}

    var acc = [], seen = {}, via = 'selector', matched = '';

    function findCards() {
      for (var i = 0; i < RULES.itemSelector.length; i++) {
        var found = [];
        try { found = slice(document.querySelectorAll(RULES.itemSelector[i])); } catch (e) {}
        if (!found.length) continue;
        via = 'selector';
        matched = RULES.itemSelector[i];
        return outermost(found);
      }
      via = 'heuristic'; matched = '(heuristic)';
      return heuristicCards();
    }

    function toItem(card) {
      var img = firstMatch(card, RULES.imageSelector) || card.querySelector('img');
      var nameEl = firstMatch(card, RULES.nameSelector);
      var priceEl = firstMatch(card, RULES.priceSelector);
      var dateEl = firstMatch(card, RULES.dateSelector);
      var linkEl = firstMatch(card, RULES.linkSelector) || card.querySelector('a[href]');

      // selector 가 상태 문구나 가격을 집어오는 경우가 흔해 그럴 땐 폴백으로 넘긴다
      var name = textOf(nameEl);
      if (!name || name.length > 90 || STATUS_RE.test(name) || PRICE_RE.test(name)) {
        name = longestLine(card) || name;
      }

      // 가격 요소에 결제수단 배지('9,700원 네이버페이플러스')가 같이 들어오는 경우가 있어
      // 금액 부분만 뽑아 쓴다.
      var priceText = textOf(priceEl).match(PRICE_RE);
      var price = priceText ? priceText[0] : matchIn(card, PRICE_RE);

      var date = textOf(dateEl);
      if (!DATE_RE.test(date)) date = matchIn(card, DATE_RE);

      return {
        name: norm(name).slice(0, 90),
        image: srcOf(img),
        price: norm(price),
        date: norm(date),
        link: linkEl ? abs(linkEl.getAttribute('href')) : ''
      };
    }

    // 지금 DOM 에 있는 카드를 훑어 새로운 것만 누적한다 (이미지+이름 기준 중복 제거)
    function collect() {
      var added = 0;
      findCards().forEach(function(card) {
        var it;
        try { it = toItem(card); } catch (e) { return; }
        if (!it.image && !it.name) return;
        var k = it.image + '|' + it.name;
        if (seen[k]) return;
        seen[k] = 1;
        acc.push(it);
        added++;
      });
      return added;
    }

    scrollCollect(collect, function() {
      console.log('order scrape:', via, matched, acc.length, 'items');
      send('ORDER_ITEMS', { items: acc, via: via, matched: matched, url: location.href });
    });
  } catch (e) {
    try {
      window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'SCAN_ERROR', payload: 'scraper: ' + (e && e.message) }));
    } catch (e2) {}
  }
})();
true;
`;
}

/**
 * 방식② 웹 자동스캔 — 아무 상품 페이지에서 "옷 사진처럼 생긴" 큰 이미지 후보를 뽑는다.
 * (로고/아이콘/배너 제외: 일정 크기 이상 + 정사각형~세로형)
 */
export const IMAGE_SCAN_JS = `
(function() {
  try {
${HELPERS_JS}
    var acc = [], seen = {};

    // 구매목록과 같은 이유로 스크롤하며 누적한다 (가상 스크롤 대응)
    function collect() {
      var added = 0;
      slice(document.images).forEach(function(i) {
        var ratio = i.naturalHeight / i.naturalWidth;
        if (!(i.naturalWidth >= 200 && i.naturalHeight >= 200 && ratio > 0.7)) return;
        var src = srcOf(i);
        if (!src || seen[src]) return;
        seen[src] = 1;
        acc.push({ src: src, w: i.naturalWidth, h: i.naturalHeight });
        added++;
      });
      return added;
    }

    scrollCollect(collect, function() {
      var candidates = acc
        .sort(function(a, b) { return (b.w * b.h) - (a.w * a.h); })   // 큰 이미지 먼저
        .slice(0, 30);
      console.log('image scan:', candidates.length, 'candidates');
      send('IMAGE_CANDIDATES', candidates);
    });
  } catch (e) {
    try {
      window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'SCAN_ERROR', payload: 'imagescan: ' + (e && e.message) }));
    } catch (e2) {}
  }
})();
true;
`;

/**
 * 구조분석(개발 전용) — 로그인해야 보이는 페이지의 selector 를 **앱 안에서** 조사한다.
 * Mac + Safari 웹 인스펙터를 못 붙이는 팀원도 selector 후보를 뽑을 수 있게 하는 용도.
 * 휴리스틱으로 찾은 카드들의 태그/클래스/data-* 를 selector 문자열로 만들어 빈도순으로 돌려준다.
 */
export const PROBE_JS = `
(function() {
  try {
${HELPERS_JS}
    loadAllThen(function() {
      var groups = {};

      function selectorFor(el) {
        var sel = el.tagName.toLowerCase();
        var data = slice(el.attributes).filter(function(a) {
          return a.name.indexOf('data-') === 0 && a.value.length < 24;
        })[0];
        if (data) return sel + '[' + data.name + '="' + data.value + '"]';
        var cls = (el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className) || '';
        var token = String(cls).split(/\\s+/).filter(function(c) {
          return c && c.length > 2 && !/^(css|sc)-/.test(c);   // emotion/styled 해시는 제외
        })[0];
        // 해시 접미사(_abc12)를 잘라 부분일치 selector 로 제안 → HTML 이 바뀌어도 잘 버틴다
        if (token) return sel + '[class*="' + token.replace(/[_-][a-z0-9]{4,}$/i, '') + '" i]';
        return sel;
      }

      heuristicCards().forEach(function(card) {
        var sel = selectorFor(card);
        if (!groups[sel]) groups[sel] = { selector: sel, count: 0, sample: longestLine(card).slice(0, 40) };
        groups[sel].count++;
      });

      var hits = Object.keys(groups).map(function(k) { return groups[k]; })
        .sort(function(a, b) { return b.count - a.count; })
        .slice(0, 8);

      send('PROBE', hits);
    });
  } catch (e) {
    try {
      window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'SCAN_ERROR', payload: 'probe: ' + (e && e.message) }));
    } catch (e2) {}
  }
})();
true;
`;
