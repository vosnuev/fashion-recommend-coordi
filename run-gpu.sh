#!/usr/bin/env bash
# GPU 서버(RunPod / GPU EC2) 스택 기동.
#   ./run-gpu.sh              # 시크릿 내보내기 + 빌드 + 기동
#   NO_BUILD=1 ./run-gpu.sh   # 재빌드 생략
#   ./run-gpu.sh product-indexer   # 특정 서비스만
#
# 대상 서비스는 docker-compose.gpu.yml 참고 (product-indexer, image-processor).
# db/qdrant/redis/api/collector는 AWS 스택(docker-compose.yml)에 있고,
# 접속 주소는 .env의 원격 호스트 값을 그대로 쓴다.
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE_FILE=docker-compose.gpu.yml

# 1단계: Infisical에서 시크릿을 .env 파일로 내보내기 (실패 시 중단)
# compose는 이 루트 .env를 두 가지 용도로 읽는다.
#   - compose 파일 안의 ${VAR} 보간
#   - env_file: .env → 컨테이너 환경변수 주입
infisical export --env=gpu --format=dotenv | sed "s/^\([^=]*\)='\(.*\)'$/\1=\2/" > .env

# 2단계: Docker Compose 실행
BUILD_FLAG="--build"
if [[ -n "${NO_BUILD:-}" ]]; then
    BUILD_FLAG=""
fi

# shellcheck disable=SC2086
docker compose -f "$COMPOSE_FILE" up -d $BUILD_FLAG "$@"

docker compose -f "$COMPOSE_FILE" ps
