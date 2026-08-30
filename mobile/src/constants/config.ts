/**
 * 앱 전역 설정. API 주소 / 인증 엔드포인트.
 *
 * API_BASE_URL 은 EXPO_PUBLIC_API_URL 환경변수로 덮어쓴다.
 *   - 예) EXPO_PUBLIC_API_URL=https://api.cozy.example  npx expo start
 *   - ⚠️ 실기기에서 로컬 백엔드에 붙으려면 localhost 대신 PC의 LAN IP 를 써야 한다.
 *        예) EXPO_PUBLIC_API_URL=http://192.168.0.10:8000
 *
 * 경로/응답 형식은 팀 백엔드(SKN28-FINAL-1Team, Django/DRF + simplejwt) 실제 구현 기준.
 */
export const API_BASE_URL =
  process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';

/** 백엔드가 지원하는 소셜 제공자. ⚠️ 애플은 아래 APPLE_LOGIN_ENABLED 참고. */
export type SocialProvider = 'kakao' | 'naver' | 'google';

/**
 * 애플 로그인 노출 여부. **지금은 끈다.**
 *
 * 백엔드가 애플에 **code 방식만** 열어 두었고(`authenticate_with_token` 은 애플 미지원),
 * code 방식은 `redirect_uri` 를 필수로 받는다. 그런데 네이티브 Apple Sign In 에는
 * 리디렉트 주소라는 게 없어서 앱이 채울 값이 없다 → 누르면 반드시 실패한다.
 * 반드시 실패하는 버튼을 띄워 두느니 감춘다.
 *
 * ⚠️ App Store 정책(4.8)상 다른 소셜 로그인을 제공하면 Sign in with Apple 이 필요하다.
 *    **심사 제출 전에는 백엔드가 네이티브(bundle id 클라이언트·redirect_uri 없음)를
 *    받아주도록 고치고 이 값을 다시 true 로 되돌려야 한다.**
 * 앱 쪽 구현(loginWithApple)은 그대로 두었으므로 이 한 줄만 바꾸면 다시 나온다.
 */
export const APPLE_LOGIN_ENABLED = false;

/**
 * 카카오 네이티브 앱 키 — 네이티브 SDK 초기화(initializeKakaoSDK)에 사용.
 * 앱 바이너리(URL 스킴)에 어차피 포함되는 준공개값이라 app.json/여기에 둔다.
 * (client_secret 같은 진짜 시크릿은 백엔드 전용, 앱엔 절대 넣지 않음)
 */
export const KAKAO_NATIVE_APP_KEY =
  process.env.EXPO_PUBLIC_KAKAO_NATIVE_APP_KEY ?? '1366adcd2e8c643a4b5471fabd32b6ea';

/**
 * 카카오 **JavaScript 키** — 웹에서 Kakao JS SDK(Kakao.init) 초기화에 쓴다.
 * 네이티브 앱 키와 값이 다르며, 네이티브 키로 Kakao.init 을 부르면 공유 창이
 * 열리지 않고 조용히 실패한다(한 번 겪은 함정이라 상수를 따로 둔다).
 * 브라우저 번들에 그대로 실리는 준공개값이고, 카카오 개발자 콘솔의
 * [내 애플리케이션 > 플랫폼 > Web]에 등록된 도메인에서만 동작한다.
 *
 * ⚠️ Expo 는 **EXPO_PUBLIC_ 접두사가 붙은 변수만** 번들에 넣는다.
 *    Infisical/셸에 `KAKAO_JAVASCRIPT_KEY` 로만 넣으면 앱에는 전달되지 않는다.
 */
export const KAKAO_JAVASCRIPT_KEY = process.env.EXPO_PUBLIC_KAKAO_JAVASCRIPT_KEY ?? '';

/**
 * 공유 옷장 초대 링크의 기준 주소.
 *
 * 초대 링크는 **남에게 보내는 주소**다. 웹에서 `window.location.origin`을 그대로 쓰면
 * 개발 중에 `http://localhost:8081/invite?code=...` 같은 링크가 만들어지는데,
 * 받는 사람에게 localhost 는 **자기 컴퓨터**라 아무것도 열리지 않는다.
 * 그래서 내 컴퓨터에서만 열리는 주소일 때는 이 값으로 바꿔 링크를 만든다.
 */
export const INVITE_BASE_URL =
  process.env.EXPO_PUBLIC_INVITE_BASE_URL?.trim().replace(/\/+$/, '') ||
  'https://skn-1st-mobile.expo.app';

/**
 * 네이버 로그인 (네이티브 SDK, @react-native-seoul/naver-login).
 * consumerKey/Secret 은 네이버 개발자센터 발급값. 네이버 모바일 SDK 는 secret 을 앱에
 * 내장하도록 요구하므로(카카오 네이티브 키와 동일한 준공개값) EXPO_PUBLIC_ 로 주입한다 — .env(gitignore).
 * URL_SCHEME 은 iOS 콜백용으로 우리가 정하는 값이며, app.json 플러그인 설정(urlScheme)과 반드시 일치해야 한다.
 */
export const NAVER_CONSUMER_KEY = process.env.EXPO_PUBLIC_NAVER_CONSUMER_KEY ?? '';
export const NAVER_CONSUMER_SECRET = process.env.EXPO_PUBLIC_NAVER_CONSUMER_SECRET ?? '';
export const NAVER_URL_SCHEME =
  process.env.EXPO_PUBLIC_NAVER_URL_SCHEME ?? 'cozynaverlogin';

/**
 * 구글 로그인 (네이티브 SDK, @react-native-google-signin/google-signin).
 * webClientId = Google Cloud "웹 애플리케이션" 클라이언트(백엔드 토큰 검증/aud 기준),
 * iosClientId = "iOS" 클라이언트. app.json 의 iosUrlScheme 은 iosClientId 를 역순(reversed)한 값이어야 한다.
 */
export const GOOGLE_WEB_CLIENT_ID = process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID ?? '';
export const GOOGLE_IOS_CLIENT_ID = process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID ?? '';

/**
 * 웹 소셜 로그인(브라우저 인가 코드 방식)에 쓰는 **클라이언트 ID**.
 * 인가 URL 쿼리에 그대로 실려 주소창에 노출되는 준공개값이다 — client_secret 은
 * 백엔드 전용이며 앱에 절대 넣지 않는다 (토큰 교환은 백엔드가 한다).
 *
 * 네이티브 키와 다른 값이라는 점에 주의:
 *   카카오 — 네이티브는 '네이티브 앱 키', 웹은 **REST API 키**
 *   네이버 — 네이티브는 consumerKey, 웹은 OAuth client_id (백엔드 NAVER_OAUTH_CLIENT_ID 와 같은 값)
 *   구글  — 웹 클라이언트 ID 하나를 앱·웹이 같이 쓴다
 */
export const KAKAO_REST_API_KEY = process.env.EXPO_PUBLIC_KAKAO_REST_API_KEY ?? '';
export const NAVER_OAUTH_CLIENT_ID = process.env.EXPO_PUBLIC_NAVER_OAUTH_CLIENT_ID ?? '';

/**
 * 키가 채워졌을 때만 해당 SDK 를 초기화/호출한다.
 * 미설정(스캐폴딩) 상태에선 네이티브 SDK 를 건드리지 않아, 재빌드 전에도 앱이 안전하게 뜬다.
 */
export const isNaverConfigured = (): boolean =>
  Boolean(NAVER_CONSUMER_KEY && NAVER_CONSUMER_SECRET);
export const isGoogleConfigured = (): boolean =>
  Boolean(GOOGLE_WEB_CLIENT_ID || GOOGLE_IOS_CLIENT_ID);

/**
 * 인증 엔드포인트 (api/apps/users/urls.py 기준).
 *   POST /api/v1/auth/{provider}/login/   → { access, refresh, user, is_new_user }
 *     body (제공자별):
 *       - kakao : { access_token }        (네이티브 SDK)
 *       - naver : { access_token }        (네이티브 SDK)
 *       - google: { access_token }        (네이티브 SDK)
 *       - apple : { identity_token, ... }  (iOS 네이티브, 백엔드 미구현)
 *     ⚠️ naver/google 의 access_token 방식은 백엔드가 _TOKEN_LOGIN_PROVIDERS 에
 *        naver/google 을 추가해야 동작한다(현재 kakao 전용). 팀장 백엔드 변경 대기.
 *   POST /api/v1/auth/token/refresh/      { refresh } → { access }
 *   GET/PATCH /api/v1/users/me/           내 정보 (Bearer 필요)
 *
 * ※ simplejwt(stateless)라 서버 로그아웃 엔드포인트는 없다 → 로그아웃은 클라이언트 토큰 폐기.
 *
 * 이메일 가입/로그인 흐름:
 *   POST /api/v1/auth/signup/        { email, password } → 202 { email, verification_required, retry_after }
 *   POST /api/v1/auth/email/verify/  { email, code }     → 200 { email, verified }
 *     ⚠️ 인증 API 는 **토큰을 주지 않는다**. 계정만 활성화되므로 이어서 로그인해야 세션이 열린다.
 *   POST /api/v1/auth/email/resend/  { email }           → 200 { retry_after }
 *   POST /api/v1/auth/login/         { email, password } → 200 { access, refresh, user, is_new_user }
 *     is_new_user=true 는 가입 후 첫 로그인 → 온보딩(권한 → 체형 측정 → 추구미)으로 보낸다.
 */
export const AuthEndpoints = {
  /** 프로필 사진 올리기(POST multipart {image}) · 지우기(DELETE). 둘 다 갱신된 사용자를 돌려준다. */
  profileImage: '/api/v1/users/me/profile-image/',
  signup: '/api/v1/auth/signup/',
  login: '/api/v1/auth/login/',
  verifyEmail: '/api/v1/auth/email/verify/',
  resendEmail: '/api/v1/auth/email/resend/',
  socialLogin: (provider: SocialProvider) => `/api/v1/auth/${provider}/login/`,
  refresh: '/api/v1/auth/token/refresh/',
  me: '/api/v1/users/me/',
} as const;

/**
 * 홈 화면 통합 조회 (api/apps/home/urls.py 기준).
 *   GET /api/v1/home/?lat=&lon=  → { nickname, weather, today_look, quick_recommends, closet_count, saved_look_count }
 *   - lat/lon 생략 시 백엔드가 서울시청 좌표로 대체. (위치 권한 붙기 전까진 생략 호출)
 *   - JWT 필요.
 */
export const HomeEndpoint = '/api/v1/home/';

/**
 * 오늘의 룩 (api/apps/recommend/urls.py 기준). JWT 필요.
 *   GET /api/v1/looks/today/?lat=&lon=  → { look_id, look_date, status, result, context, poll_after_ms, detail }
 *   - 그날 첫 호출이 곧 생성 트리거다 (홈 API 진입 시 백엔드가 미리 걸어 두므로 보통은 완성돼 있다).
 *   - status 분기: QUEUED/PROCESSING → poll_after_ms 뒤 재조회 | SUCCEEDED → result 표시
 *                  | EMPTY → 폴링 중단(프로필 입력 유도) | FAILED → 자동 재시도 없음
 *   - result 의 이미지 URL 은 조회마다 새로 서명된다 — 캐시하면 만료된다.
 *   - 대표 이미지는 result.render_image_url, null 이면 items[].image_url 카드로 화면을 만든다.
 */
export const DailyLookEndpoint = '/api/v1/looks/today/';
export const DailyLookVirtualTryOnEndpoint = (lookId: string) =>
  `/api/v1/looks/${lookId}/virtual-try-on/`;

/**
 * 오늘의 룩 저장 (홈 카드의 '저장'). POST, **본문 없음**.
 *
 * 담을 대상은 그날의 추천 하나로 정해져 있어서 클라이언트가 golden_id 를 보내지
 * 않는다 — 보내게 하면 남의 코디도 담을 수 있는 구멍이 된다.
 *
 * 201 새로 담음 / 200 이미 담아 둔 코디(같은 룩북을 돌려준다) /
 * 409 아직 담을 수 없음(응답 status 가 이유: QUEUED·PROCESSING·EMPTY·FAILED·MISSING)
 */
export const DailyLookSaveEndpoint = '/api/v1/looks/today/save/';

/**
 * 착장 사진 분석. 인증 없이 호출할 수 있고, JWT가 있으면 개인화 정보를 반영한다.
 * POST multipart { image, lat?, lon? } → { status, evaluation, context }
 */
export const OutfitAnalysisEndpoint = '/api/v1/outfits/analyze/';

/**
 * 착장 분석 기록 (api/apps/recommend/urls.py 기준).
 *   GET  /api/v1/outfits/analyses/?limit=&offset=&status=  → { count, limit, offset, results[] }
 *   GET  /api/v1/outfits/analyses/{id}/                    → 단건 (진행 상태 겸 결과)
 *   POST /api/v1/outfits/analyses/claim/  { claim_tokens[] } → 비로그인 접수 건을 계정으로 이전
 *
 * ⚠️ 목록만 JWT 필수다. 단건 조회는 AllowAny — UUID를 아는 사람이면 24시간 안에 볼 수 있다.
 *    익명 기록(user=NULL)은 목록에서 빠지므로, 비회원이 분석한 건은 claim 을 거쳐야 목록에 나타난다.
 *    claim 토큰은 발급 후 60분만 유효하다(조회 24시간과 다름).
 */
export const OutfitHistoryEndpoints = {
  list: '/api/v1/outfits/analyses/',
  detail: (analysisId: string) => `/api/v1/outfits/analyses/${analysisId}/`,
  claim: '/api/v1/outfits/analyses/claim/',
} as const;

/**
 * 신체치수 (api/apps/users/urls.py 기준). 전부 JWT 필요.
 *   GET   /api/v1/users/me/body/         → 전체 치수 (미입력 필드는 null)
 *   PUT   /api/v1/users/me/body/basic/   { gender, height, weight }  (셋 다 필수, gender=male|female)
 *   PATCH /api/v1/users/me/body/detail/  상세 **10개** (전부 선택)
 *   POST  /api/v1/users/me/body/estimate/  { gender?, height?, weight? } → 상세 10개 추정·저장 (동기)
 *   POST  /api/v1/users/me/body/photos/  multipart front_image/side_image → 202 { transaction_id, status }
 *   GET   /api/v1/users/me/body/photos/{id}/  → 트랜잭션 조회 (폴링)
 *
 *   상세 10개 = 둘레·너비 7개(chest,waist,hip,thigh,calf,arm,shoulder)
 *             + 체형 지표 3개(neck_length, thigh_calf_ratio, torso_leg_ratio).
 *   지표 3개는 2026-08-10 백엔드에 추가됐다(users 마이그레이션 0014~0016, PR#10).
 *   항목별 라벨·단위·범위는 constants/body-measures.ts 가 단일 출처다.
 *   ※ 수치는 Decimal 소수 1자리(1~999.9), 비율 2개는 3자리(thigh_calf 0.7~1.3 · torso_leg는 골든 임계값 기준).
 *   ※ estimate 와 photos/{id} 는 같은 결과 형식을 준다 —
 *     { status, source, transaction_id, measurement, error_message }. 추정 치수가 응답에 들어 있어
 *     따로 GET body 를 부를 필요가 없다. estimate 는 본문을 비우면 저장된 기본 정보를 쓴다.
 */
export const BodyEndpoints = {
  me: '/api/v1/users/me/body/',
  basic: '/api/v1/users/me/body/basic/',
  detail: '/api/v1/users/me/body/detail/',
  estimate: '/api/v1/users/me/body/estimate/',
  photos: '/api/v1/users/me/body/photos/',
  photo: (transactionId: string) => `/api/v1/users/me/body/photos/${transactionId}/`,
} as const;

/**
 * 추구미·선호도 (api/apps/users/urls.py 기준). JWT 필요.
 *   GET /api/v1/users/me/pursuit/  → { preferred:{카테고리키:[code]}, avoided:{카테고리키:[code]} }
 *   PUT /api/v1/users/me/pursuit/  같은 형식으로 통째로 저장(upsert, 전체 교체)
 *   ⚠️ PUT 은 카테고리 키가 백엔드 PREFERENCE_CATEGORIES(11개)와 정확히 일치해야 통과한다.
 *   ※ 옵션 목록(GET /api/v1/preference-options/)은 로컬 pursuit-options.ts 를 그대로 쓴다(프론트 기준).
 */
export const PursuitEndpoint = '/api/v1/users/me/pursuit/';

/**
 * 옷장 (api/apps/wardrobe/urls.py 기준). 전부 JWT 필요.
 *
 * 등록은 **비동기**다 — 사진을 올리면 202 로 job_id 만 받고, 이미지 프로세서가
 * 누끼·분류를 끝내면 콜백으로 아이템이 채워진다. 프론트는 job 을 폴링해야 한다.
 * 사진 1장에서 아이템이 **여러 개** 나올 수 있다(세그멘테이션).
 *
 *   POST  /api/v1/wardrobe/uploads/           multipart { image } → 202 { job_id, status }
 *   GET   /api/v1/wardrobe/uploads/{job_id}/  → { id, status, error_message, created_at, finished_at, items[] }
 *         status: PENDING | PROCESSING | DONE | FAILED
 *   GET   /api/v1/wardrobe/categories/        → 기본 카테고리 + 개인 옷장 해시태그
 *   POST  /api/v1/wardrobe/hashtags/          { name, item_ids } → 옷과 함께 해시태그 생성
 *   GET   /api/v1/wardrobe/items/             → WardrobeApiItem[]  (?category_large=&confirmed=true|false)
 *   PATCH /api/v1/wardrobe/items/{id}/        태그 수정 + confirmed → 수정된 아이템
 *   DELETE /api/v1/wardrobe/items/{id}/       → 204
 *
 * ⚠️ 새로 만들어진 아이템은 confirmed=false(사용자 확인 대기)이고 추천 검색에서 제외된다.
 *    사용자가 태그를 확인·수정한 뒤 PATCH 로 confirmed=true 를 보내야 옷장에 정식 편입된다.
 * ⚠️ 업로드 제한: 15MB 이하, jpeg/png/webp/heic.
 *
 * ── 일괄 등록(batches) — 인앱 브라우저로 긁어온 외부 상품 전용 ──
 *   POST /api/v1/wardrobe/batches/  json { source, items[] } → 202 배치 접수
 *         items[] 는 **이미지 주소**와 우리가 이미 아는 태그만 넣는다.
 *         이미지는 서버가 직접 내려받아 S3 에 저장한다 — 앱이 파일을 올리지 않는다
 *         (쇼핑몰 이미지는 핫링크 403 이 잦고, 앱에서 받아 다시 올리면 왕복이 두 배가 된다).
 *   GET  /api/v1/wardrobe/batches/{batch_id}/  → 진행률 + job 별 상태
 *   GET  /api/v1/wardrobe/batches/?status=&limit=&offset=  → 최근 배치 목록
 *
 * 서버 처리: 이미지 1장 = job 1개 → Qwen VL 태깅 워커(qwen-tag) → 콜백으로 아이템 생성.
 * 앱이 함께 보낸 태그가 모델 결과보다 **우선**한다(구매목록의 상품명이 더 정확하므로).
 *
 * ⚠️ 한 번에 30건·합계 100MB 까지. 개별 이미지는 단건 업로드와 같은 15MB 제한.
 * ⚠️ items 중 **하나라도** 값이 백엔드 taxonomy 와 어긋나면 요청 전체가 400 이다
 *    (DRF ChoiceField). 확신 없는 태그는 아예 빼고 보낸다 — 그 자리는 모델이 채운다.
 * ⚠️ 이미지 주소는 공개 http(s) 여야 한다. 사설망 주소·data: URL 은 서버가 거절한다.
 */
export const WardrobeEndpoints = {
  uploads: '/api/v1/wardrobe/uploads/',
  uploadJob: (jobId: string) => `/api/v1/wardrobe/uploads/${jobId}/`,
  categories: '/api/v1/wardrobe/categories/',
  hashtags: '/api/v1/wardrobe/hashtags/',
  hashtag: (hashtagId: string) => `/api/v1/wardrobe/hashtags/${hashtagId}/`,
  hashtagItems: (hashtagId: string) =>
    `/api/v1/wardrobe/hashtags/${hashtagId}/items/`,
  hashtagOrder: '/api/v1/wardrobe/hashtags/order/',
  viewPreferences: '/api/v1/wardrobe/view-preferences/',
  items: '/api/v1/wardrobe/items/',
  item: (itemId: string) => `/api/v1/wardrobe/items/${itemId}/`,
  itemHashtags: (itemId: string) =>
    `/api/v1/wardrobe/items/${itemId}/hashtags/`,
  /* 룩 사진에서 뽑혀 아직 옷장 밖에 있는 옷을 옷장에 들인다(멱등). */
  addToCloset: (itemId: string) => `/api/v1/wardrobe/items/${itemId}/add-to-closet/`,
  batches: '/api/v1/wardrobe/batches/',
  batch: (batchId: string) => `/api/v1/wardrobe/batches/${batchId}/`,
} as const;

/**
 * 스타일 캘린더 — 하루에 기록 하나.
 *
 *   GET    /api/v1/calendars/?start_date=&end_date=   → CalendarEntry[] (배열 그대로, 페이지네이션 없음)
 *   GET    /api/v1/calendars/by-date/?date=           → CalendarEntry · **기록이 없으면 404**
 *   POST   /api/v1/calendars/photo/                   multipart → 202 (사진 처리는 비동기)
 *   POST   /api/v1/calendars/wardrobe/                json      → 201 (옷만 고르면 즉시 완료)
 *   GET    /api/v1/calendars/{id}/                    → CalendarEntry
 *   PATCH  /api/v1/calendars/{id}/                    → CalendarEntry
 *   DELETE /api/v1/calendars/{id}/                    → 204
 *   POST   /api/v1/calendars/{id}/items/              → CalendarEntry (옷 연결 추가, 멱등)
 *   DELETE /api/v1/calendars/{id}/items/{itemId}/     → CalendarEntry (옷 연결만 해제)
 *   GET    /api/v1/calendars/{id}/processing-status/  사진 처리 폴링
 *
 * ⚠️ **날짜당 1건이고 서버에 upsert 가 없다.** 이미 있는 날짜로 등록하면 409 다.
 *    **사진을 바꿀 때만** DELETE 후 다시 등록한다(PATCH 로는 못 바꾼다).
 *    옷을 더하고 빼는 것은 items POST/DELETE 로 연결만 손댄다 — 기록 id 도 사진도 그대로다.
 *    사진 기록을 지우고 다시 만들면 같은 사진을 서버가 다시 분석해, 같은 옷이 서로 다른
 *    두 벌로 옷장에 쌓인다. 옷 구성 변경에 재등록을 쓰면 안 되는 이유다.
 * ⚠️ **PATCH 는 schedule·tpo·hashtags 만 받는다.** 서버가 미선언 필드를 400 으로 거절하므로
 *    프론트에만 있는 개념(shared·lookId)을 실어 보내면 요청 전체가 실패한다.
 * ⚠️ 업로드 제한: 15MB 이하, jpeg/png/webp/heic.
 */
export const CalendarEndpoints = {
  list: '/api/v1/calendars/',
  byDate: '/api/v1/calendars/by-date/',
  photo: '/api/v1/calendars/photo/',
  wardrobe: '/api/v1/calendars/wardrobe/',
  detail: (calendarId: string) => `/api/v1/calendars/${calendarId}/`,
  /** 입은 옷 연결 추가 — 이미 걸린 옷은 서버가 건너뛴다(멱등). */
  items: (calendarId: string) => `/api/v1/calendars/${calendarId}/items/`,
  /** 입은 옷 연결 해제 — itemId 는 옷장 아이템 id(wardrobe_item_id)다. */
  item: (calendarId: string, wardrobeItemId: string) =>
    `/api/v1/calendars/${calendarId}/items/${wardrobeItemId}/`,
  processingStatus: (calendarId: string) => `/api/v1/calendars/${calendarId}/processing-status/`,
} as const;

/**
 * 룩북 — 캘린더와 거의 같은 모양이지만 **날짜에 매이지 않아 여러 건**을 올릴 수 있다.
 *
 *   GET    /api/v1/lookbooks/?hashtag=&status=&limit=&offset=  → { count, next_offset, results[] }
 *   POST   /api/v1/lookbooks/photo/                    multipart → 202 (사진 처리는 비동기)
 *   POST   /api/v1/lookbooks/wardrobe/                 json      → 201 (옷만 고르면 즉시 완료)
 *   GET    /api/v1/lookbooks/{id}/                     → LookbookPost
 *   PATCH  /api/v1/lookbooks/{id}/                     → schedule·tpo·hashtags 만
 *   DELETE /api/v1/lookbooks/{id}/                     → 204
 *   GET    /api/v1/lookbooks/{id}/processing-status/   사진 처리 폴링
 *
 * ⚠️ **이 목록은 '내 룩북'이다.** 남들이 올린 피드(둘러보기)는 서버에 없다 —
 *    state/lookbook.ts 의 로컬 시드가 계속 그 자리를 맡는다.
 * ⚠️ 등록 요청이 `calendar_date`·`overwrite_calendar` 를 받는다. 켜면 **한 번의 호출로**
 *    룩북과 캘린더가 함께 남는다 — 캘린더를 따로 부르지 않는다.
 * ⚠️ PATCH 는 캘린더와 같은 제약: schedule·tpo·hashtags 만. 사진·옷 구성은 못 바꾼다.
 */
/**
 * 대분류별 상품 1개 최대 가격 — 상품 추천에서 '예산 내' 표시를 가르는 값.
 *
 *   GET /api/v1/users/me/budget/  → { category_budgets, effective_category_budgets }
 *   PUT /api/v1/users/me/budget/    { category_budgets }  전체 교체
 *
 * ⚠️ **1만원 단위, 10,000 이상**만 받는다. 지울 때는 키를 빼는 게 아니라 **명시적으로 null**.
 */
export const BudgetEndpoint = '/api/v1/users/me/budget/';

export const LookbookEndpoints = {
  discover: '/api/v1/lookbooks/discover/',
  discoverDetail: (lookId: string) => `/api/v1/lookbooks/discover/${lookId}/`,
  list: '/api/v1/lookbooks/',
  /* 전체 공개된 룩 — 앱 '둘러보기'가 읽는 목록. 비회원도 볼 수 있다. */
  publicFeed: '/api/v1/lookbooks/public/',
  photo: '/api/v1/lookbooks/photo/',
  wardrobe: '/api/v1/lookbooks/wardrobe/',
  detail: (lookbookId: string) => `/api/v1/lookbooks/${lookbookId}/`,
  processingStatus: (lookbookId: string) => `/api/v1/lookbooks/${lookbookId}/processing-status/`,
} as const;

/**
 * 채팅 (api/apps/chat/urls.py 기준). JWT 필요 — 게스트 채팅은 붙이지 않았다(아래 참고).
 *
 *   POST   /api/v1/chat/sessions/                      { mode }               → 201 세션
 *   GET    /api/v1/chat/sessions/                                             → 세션 배열
 *   PATCH  /api/v1/chat/sessions/{id}/                 { title }              → 200
 *   DELETE /api/v1/chat/sessions/{id}/                                        → 204
 *   GET    /api/v1/chat/sessions/search/?query=&limit=&cursor=                → 제목·본문 검색
 *   GET    /api/v1/chat/sessions/{id}/messages/                               → 메시지 배열(시간순)
 *   GET    /api/v1/chat/sessions/{id}/messages/page/?limit=&cursor=           → 최신부터 커서 페이지
 *   POST   /api/v1/chat/sessions/{id}/messages/  { content, client_message_id } → 202 { message, run, events_url }
 *   POST   /api/v1/chat/sessions/{id}/attachments/  multipart{ image, client_message_id } → 201 { message, attachment }
 *   POST   .../attachments/{attachmentId}/analysis/                           → 202 { attachment, run }
 *   POST   .../attachments/{attachmentId}/mood-decision/  { decision }        → 200 { attachment, applied }
 *   GET    /api/v1/chat/runs/{runId}/                                         → run 상태(폴링용)
 *   GET    /api/v1/chat/runs/{runId}/events/                                  → SSE 진행 이벤트
 *
 * 사진은 **세 단계**다. 올리기만 해서는 아무 일도 일어나지 않는다 —
 * 업로드(첨부 전용 사용자 메시지가 생김) → 무드 분석 요청(run 이 생김) →
 * 사용자가 그 무드를 쓸지 정하기(승인해야 세션 추천 조건에 들어간다).
 *
 * 답변은 **동기 응답이 아니다.** 메시지를 POST 하면 202 와 함께 run 이 생기고,
 * 실제 답변은 별도 워커가 만들어 SSE(또는 run 폴링)로 전달된다. lib/chatStream.ts 참고.
 *
 * ⚠️ 응답 본문의 `events_url` 을 그대로 쓰지 말 것. 서버가 build_absolute_uri 로 만드는데
 *    터널/프록시 뒤에서는 스킴이 `http://` 로 유실돼, https 로 열린 웹에서 mixed content 로
 *    차단된다. 아래 runEvents() 로 직접 조립한다.
 * ⚠️ 게스트 채팅(/chat/guest/)은 **HttpOnly 쿠키**로 신원을 잡는다. apiClient와 multipart
 *    업로더 모두 자격증명을 포함해야 세션 생성 뒤 메시지·사진 요청에서도 같은 게스트로 이어진다.
 */
/**
 * 사진 첨부 → 무드 분석 → 반영 여부는 **세 번의 호출**로 나뉜다.
 *
 *   POST /chat/sessions/{id}/attachments/                      multipart → 201 { message, attachment, created }
 *   POST /chat/sessions/{id}/attachments/{aid}/analysis/                → 202 { attachment, run, events_url }
 *   POST /chat/sessions/{id}/attachments/{aid}/mood-decision/  { decision } → 200 { attachment, changed, applied, context_state }
 *
 * 올리는 것과 분석하는 것이 나뉜 이유 — 사진만 보내고 분석은 원할 때 시킬 수 있다.
 * 분석도 답변과 같은 run 구조라 끝날 때까지 기다려야 한다(lib/chatStream.ts).
 *
 * ⚠️ 요청과 저장값의 철자가 다르다. 보낼 때는 `APPROVE`/`REJECT`, 첨부에 남는 값은
 *    `APPROVED`/`REJECTED`(미결정은 `UNDECIDED`)다. 둘을 섞어 비교하면 결정 상태를 놓친다.
 * ⚠️ 무드를 승인해도 **추천이 자동으로 만들어지지 않는다.** 세션의 context_state 에만
 *    반영되고, 다음 질문부터 그 무드가 조건으로 쓰인다.
 */
export const ChatEndpoints = {
  guest: '/api/v1/chat/guest/',
  sessions: '/api/v1/chat/sessions/',
  session: (sessionId: string) => `/api/v1/chat/sessions/${sessionId}/`,
  messages: (sessionId: string) => `/api/v1/chat/sessions/${sessionId}/messages/`,
  /** 최근 메시지부터 커서로 끊어 받는다. 대화가 길어지면 messages 대신 이쪽을 쓴다. */
  messagePage: (sessionId: string) => `/api/v1/chat/sessions/${sessionId}/messages/page/`,
  /** 제목과 **저장된 메시지 본문**까지 서버가 찾아준다. */
  sessionSearch: '/api/v1/chat/sessions/search/',
  attachments: (sessionId: string) => `/api/v1/chat/sessions/${sessionId}/attachments/`,
  attachmentAnalysis: (sessionId: string, attachmentId: string) =>
    `/api/v1/chat/sessions/${sessionId}/attachments/${attachmentId}/analysis/`,
  attachmentMoodDecision: (sessionId: string, attachmentId: string) =>
    `/api/v1/chat/sessions/${sessionId}/attachments/${attachmentId}/mood-decision/`,
  run: (runId: string) => `/api/v1/chat/runs/${runId}/`,
  runEvents: (runId: string) => `/api/v1/chat/runs/${runId}/events/`,

  /* ── 스타일리스트 모드 ──
     ⚠️ 아래 네 자리는 아직 **배포 서버에 없다**(origin/feature/chat-main-integration 전용).
        없는 서버에서는 404 가 오고 lib/stylistApi.ts 가 목업으로 대신한다. */
  stylists: '/api/v1/chat/stylists/',
  responseMode: (sessionId: string) => `/api/v1/chat/sessions/${sessionId}/response-mode/`,
  personaRetry: (runId: string, personaId: string) =>
    `/api/v1/chat/runs/${runId}/personas/${personaId}/retry/`,
  personaAlternative: (runId: string, personaId: string) =>
    `/api/v1/chat/runs/${runId}/personas/${personaId}/alternative/`,
} as const;

/**
 * 추천 결과 (api/apps/recommend/urls.py 기준). JWT 필요.
 *
 *   GET /api/v1/recommendations/{resultId}/  → { result_id, mode, cards[] }
 *
 * 채팅 답변이 추천까지 만들면 그 메시지의 metadata.recommendation_result_id 로 여기를 부른다.
 * 카드 하나가 코디 한 벌이고, 그 안의 items 가 착장 아이템이다.
 *
 *   GET    /api/v1/recommendations/{resultId}/cards/{cardId}/           → 카드 상세
 *   PUT    .../cards/{cardId}/feedback/  { reaction, reason_codes }     → 최신 피드백 교체
 *   DELETE .../cards/{cardId}/feedback/                                 → 피드백 삭제
 *   GET    .../cards/{cardId}/render/                                   → 코디 이미지 생성 상태
 *   POST   .../cards/{cardId}/render/                                   → 이미지 생성 접수
 *
 * 이미지 생성은 추천이 저장될 때 서버가 미리 걸어둔다. 그래서 보통은 GET 만으로 결과가
 * 나오고, 없거나(404) 실패했을 때만 POST 로 다시 건다.
 */
/**
 * 스타일리스트 API 가 없을 때 목업으로 대신 그려도 되는지 (lib/stylistApi.ts).
 *
 * ⚠️ **배포된 실서버에서는 켜면 안 된다.** 게이트웨이 설정이 틀려 404 가 나는 상황까지
 *    "라우트가 아직 없구나"로 삼켜 버리면, 사용자에게 **지어낸 코디**를 진짜 추천인 것처럼
 *    보여주게 된다. 장애가 목업 뒤에 숨는 쪽이 오류 화면보다 나쁘다.
 *
 * 그래서 기본은 개발 빌드에서만 열어 둔다. 팀원 체험용 웹 배포처럼 백엔드가 아직 안 붙은
 * 곳에서 화면을 보여줘야 하면 그 빌드에만 EXPO_PUBLIC_STYLIST_MOCK=1 을 준다.
 */
export const ALLOW_STYLIST_MOCK =
  __DEV__ || process.env.EXPO_PUBLIC_STYLIST_MOCK === '1';

export const RecommendEndpoints = {
  result: (resultId: string) => `/api/v1/recommendations/${resultId}/`,
  card: (resultId: string, cardId: string) =>
    `/api/v1/recommendations/${resultId}/cards/${cardId}/`,
  cardFeedback: (resultId: string, cardId: string) =>
    `/api/v1/recommendations/${resultId}/cards/${cardId}/feedback/`,
  cardRender: (resultId: string, cardId: string) =>
    `/api/v1/recommendations/${resultId}/cards/${cardId}/render/`,
  /** 카드 한 장을 내 룩으로 저장 — 스타일리스트 카드의 '이 코디로 할래요'가 부른다.
      ⚠️ 이 자리도 아직 배포 서버에 없다(위 stylists 주석과 같은 브랜치). */
  saveCard: (resultId: string, cardId: string) =>
    `/api/v1/recommendations/${resultId}/cards/${cardId}/save/`,
} as const;
