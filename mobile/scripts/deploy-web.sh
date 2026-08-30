#!/usr/bin/env bash
#
# 웹(EAS Hosting) 배포. 사용: ./scripts/deploy-web.sh <API_URL>
#
# 이 스크립트가 있는 이유 — expo export 는 .env 가 없어도 오류를 내지 않는다.
# EXPO_PUBLIC_* 가 빠진 번들이 조용히 만들어져 그대로 배포되고, 앱에서는
# "웹 로그인 설정이 아직 없습니다" 같은 엉뚱한 증상으로만 드러난다.
# (worktree 에는 .env 가 gitignore 라 따라오지 않아 특히 잘 밟는다.)
# 그래서 빌드 전·후로 값이 실제로 박혔는지 직접 확인한다.
set -euo pipefail
cd "$(dirname "$0")/.."

RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; OFF=$'\033[0m'
die() { echo "${RED}✗ $*${OFF}" >&2; exit 1; }
ok()  { echo "${GRN}✓ $*${OFF}"; }

# 번들에 반드시 들어가야 하는 키 (웹 소셜 로그인)
REQUIRED=(
  EXPO_PUBLIC_KAKAO_REST_API_KEY
  EXPO_PUBLIC_NAVER_OAUTH_CLIENT_ID
  EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID
  EXPO_PUBLIC_KAKAO_JAVASCRIPT_KEY   # 웹 카카오 공유(초대 링크)
)

# ── 0. 무엇을 배포하는지 먼저 확인 ───────────────────────────
# 이 검사가 있는 이유 — 2026-08-19 fetch 하지 않은 낡은 로컬 main 을 배포해 프론트 커밋
# 15개(오늘의 룩 상세·채팅 게스트·카카오 초대 링크 등)가 하루치 뒤로 돌아갔다.
# 배포 자체는 '성공'했고 번들 해시 전파도 정상이라 아무도 눈치채지 못했다.
# 결과(전파)만 확인해서는 이 사고가 안 잡힌다 — 입력(소스 커밋)부터 확인한다.
BASE_REF="${DEPLOY_BASE_REF:-origin/main}"

if [ -n "${DEPLOY_SKIP_REF_CHECK:-}" ]; then
  echo "${YEL}⚠ 소스 커밋 검사를 건너뜁니다 (DEPLOY_SKIP_REF_CHECK)${OFF}"
else
  # BASE_REF 가 origin/main 처럼 '원격/브랜치' 일 때만 fetch 한다.
  # 로컬 ref(예: DEPLOY_BASE_REF=HEAD)를 주면 fetch 할 원격이 없으므로 건너뛴다.
  REMOTE="${BASE_REF%%/*}"
  if [ "$REMOTE" != "$BASE_REF" ] && git remote | grep -qx "$REMOTE"; then
    git fetch "$REMOTE" --quiet \
      || die "git fetch 실패 — 원격과 대조할 수 없습니다. 낡은 코드를 배포하지 않으려면 여기서 멈춰야 합니다.
    확인을 건너뛰려면:  DEPLOY_SKIP_REF_CHECK=1 $0 $*"
  fi

  HEAD_SHA=$(git rev-parse HEAD)
  BASE_SHA=$(git rev-parse "$BASE_REF")
  if [ "$HEAD_SHA" != "$BASE_SHA" ]; then
    die "빌드할 트리가 $BASE_REF 와 다릅니다 — 낡은 코드를 배포할 뻔했습니다.
    HEAD      : $HEAD_SHA
    $BASE_REF : $BASE_SHA
    뒤처짐 $(git rev-list --count "HEAD..$BASE_REF") 커밋 / 앞섬 $(git rev-list --count "$BASE_REF..HEAD") 커밋
    맞추려면:       git checkout --detach $BASE_REF
    의도한 것이면:  DEPLOY_SKIP_REF_CHECK=1 $0 $*"
  fi
  ok "소스 커밋 = $BASE_REF ($(git rev-parse --short HEAD))"

  # 추적 중인 파일의 미커밋 변경은 그대로 번들에 들어간다. 여러 세션이 한 작업트리를
  # 공유하므로, 남의 작업 중인 코드가 배포에 섞여 나가기 쉽다.
  git diff --quiet HEAD -- . \
    || die "커밋되지 않은 변경이 있어 그대로 번들에 들어갑니다:
$(git status --short -- . | head -10)
    의도한 것이면:  DEPLOY_SKIP_REF_CHECK=1 $0 $*"
  ok "작업트리 깨끗함 (추적 파일 기준)"

  # 추적되지 않은 소스 파일도 번들에 들어갈 수 있다. 다만 이 스크립트 자체가
  # 아직 커밋 전이라 중단시키지는 않고 알리기만 한다.
  UNTRACKED=$(git ls-files --others --exclude-standard -- src assets app.json 2>/dev/null | head -5)
  [ -z "$UNTRACKED" ] || echo "${YEL}⚠ 추적되지 않은 파일이 번들에 포함될 수 있습니다:${OFF}
$UNTRACKED"
fi

# ── 1. .env 와 키 존재 확인 ───────────────────────────────────
[ -f .env ] || die ".env 가 없습니다. worktree 라면 메인 트리에서 복사하세요:
    cp <메인>/mobile/.env $(pwd)/.env"
for k in "${REQUIRED[@]}"; do
  grep -qE "^$k=.+" .env || die ".env 에 $k 가 비어 있습니다."
done
ok ".env 확인 (키 ${#REQUIRED[@]}개)"

# ── 2. API 주소 결정 ─────────────────────────────────────────
API_URL="${1:-${EXPO_PUBLIC_API_URL:-}}"
[ -n "$API_URL" ] || die "API 주소를 넘기세요:  ./scripts/deploy-web.sh https://api.example.com
    (.env 의 값은 로컬 개발용이라 배포에 쓰지 않습니다)"
case "$API_URL" in
  https://*) ;;
  *) die "배포에는 https 주소만 됩니다 (받은 값: $API_URL).
    브라우저가 https 페이지에서 http 요청을 막습니다." ;;
esac
ok "API 주소: $API_URL"

# ── 3. 빌드 ─────────────────────────────────────────────────
echo "${YEL}▶ expo export (--clear)${OFF}"
rm -rf dist
EXPO_PUBLIC_API_URL="$API_URL" npx expo export -p web --clear >/dev/null
BUNDLE=$(ls dist/_expo/static/js/web/entry-*.js) || die "번들을 찾지 못했습니다."
ok "빌드 완료 $(basename "$BUNDLE")"

# ── 4. 번들 내용 검증 ────────────────────────────────────────
for k in "${REQUIRED[@]}"; do
  v=$(grep -E "^$k=" .env | cut -d= -f2- | tr -d '"')
  grep -qF "$v" "$BUNDLE" || die "$k 가 번들에 없습니다. (export 가 .env 를 못 읽었습니다)"
done
grep -qF "$API_URL" "$BUNDLE" || die "API 주소가 번들에 없습니다."
ok "번들 검증 — 키 ${#REQUIRED[@]}개 + API 주소 확인"

# ── 5. 배포 ─────────────────────────────────────────────────
echo "${YEL}▶ eas deploy --prod${OFF}"
npx eas-cli deploy --prod --non-interactive

# ── 6. 프로덕션 확인 ─────────────────────────────────────────
PROD=https://skn-1st-mobile.expo.app
echo "${YEL}▶ 전파 확인${OFF}"
# 엣지(cloudflare)가 직전 HTML 을 붙잡고 있으면 몇 분이 걸린다 — 2026-08-20 실측 약 2분.
# 30초로 끊으면 정상 배포가 실패로 보고돼, 무엇을 손봐야 하는지 판단이 어려워진다.
for i in $(seq 1 18); do
  sleep 10
  served=$(curl -fsS -m 20 -H 'Cache-Control: no-cache' "$PROD/?cb=$RANDOM$RANDOM" \
           | grep -oE '/_expo/static/js/web/entry-[a-f0-9]+\.js' | head -1 || true)
  [ "$(basename "${served:-x}")" = "$(basename "$BUNDLE")" ] && break
  [ $((i % 3)) -eq 0 ] && echo "  ...아직 이전 번들 ($((i * 10))초 경과, 최대 180초)"
done
[ "$(basename "${served:-x}")" = "$(basename "$BUNDLE")" ] \
  || die "3분이 지나도 프로덕션이 이전 번들을 서빙합니다: ${served:-없음}

    ⚠️ 빌드와 업로드는 성공했습니다 — 남은 건 전파뿐이니 다시 배포하지 마세요.
       · 대개 엣지 캐시라 몇 분 더 기다리면 풀립니다(응답의 age 헤더로 확인).
       · 예전 배포가 프로덕션 별칭을 물고 있으면:
           npx eas-cli deploy:alias --prod --id <deployment-id>
         (id 는 위 Deployment URL 의 skn-1st-mobile--<id>.expo.app 에서 읽습니다)"
ok "전파 확인 $(basename "$BUNDLE")"

curl -fsS -m 60 "$PROD$served" -o /tmp/deploy-web-live.js
for k in "${REQUIRED[@]}"; do
  v=$(grep -E "^$k=" .env | cut -d= -f2- | tr -d '"')
  grep -qF "$v" /tmp/deploy-web-live.js || die "라이브 번들에 $k 가 없습니다."
done
ok "라이브 검증 — 키 ${#REQUIRED[@]}개 확인"
echo "${GRN}배포 완료: $PROD${OFF}"
