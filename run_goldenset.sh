#!/usr/bin/env bash
# 골든셋 스택(확인용 웹 + 선택적 스캔) 기동.
#   ./run_goldenset.sh                    # 시크릿 내보내기 + 빌드 + 웹 기동
#   SCAN=1 ./run_goldenset.sh             # 스캔 1회도 함께 실행
#   NO_BUILD=1 ./run_goldenset.sh         # 재빌드 생략
#   ./run_goldenset.sh golden-set-web     # 특정 서비스만
#
# api 컨테이너와 같은 호스트에서 도는 것을 전제로 한다. 기본 노출 포트는
# 8081이며 겹치면 .env의 GOLDEN_WEB_HOST_PORT를 바꾼다.
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE_FILE=docker-compose.golden_set.yml

# 1단계: Infisical(dev)에서 시크릿을 루트 .env로 내보내기 (실패 시 중단).
# compose는 이 파일을 두 가지 용도로 읽는다.
#   - compose 정의 안의 ${VAR} 보간 (GOLDEN_WEB_HOST_PORT 등)
#   - env_file: .env → 컨테이너 환경변수 주입
# api 스택(run.sh)과 같은 파일을 쓰므로 두 스택을 함께 올릴 때도 값이 갈리지 않는다.
infisical export --env=dev --format=dotenv | sed "s/^\([^=]*\)='\(.*\)'$/\1=\2/" > .env

# 2단계: Docker Compose 실행
BUILD_FLAG="--build"
if [[ -n "${NO_BUILD:-}" ]]; then
    BUILD_FLAG=""
fi

PROFILE_ARGS=()
if [[ -n "${SCAN:-}" ]]; then
    # 스캔은 profiles: ["scan"] 이라 명시할 때만 뜬다.
    PROFILE_ARGS=(--profile scan)
fi

# shellcheck disable=SC2086
docker compose -f "$COMPOSE_FILE" "${PROFILE_ARGS[@]}" up -d $BUILD_FLAG "$@"

docker compose -f "$COMPOSE_FILE" "${PROFILE_ARGS[@]}" ps

PORT="$(grep -E '^GOLDEN_WEB_HOST_PORT=' .env | tail -1 | cut -d= -f2)"
echo
echo "확인용 웹: http://localhost:${PORT:-8081}/"
