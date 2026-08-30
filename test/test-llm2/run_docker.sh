#!/usr/bin/env bash
# test-llm2 빌드 + input/, output/ 볼륨 마운트 실행.
#
#   ./run_docker.sh                 # input/ 전체
#   NO_BUILD=1 ./run_docker.sh      # 재빌드 생략
#
# API 키: 저장소 루트 .env 를 --env-file로 주입하고,
#         현재 셸에 export된 키가 있으면 그것이 우선한다.
set -euo pipefail
cd "$(dirname "$0")"

IMAGE_NAME=test-llm2

if [[ "${NO_BUILD:-}" != "1" ]]; then
  docker build -t "$IMAGE_NAME" .
fi

ENV_ARGS=()
ROOT_ENV="$(cd ../.. && pwd)/.env"
[[ -f "$ROOT_ENV" ]] && ENV_ARGS+=(--env-file "$ROOT_ENV")

for key in GEMINI_API_KEY GEMINI_ENUM_MODEL GEMINI_FLASH_IMAGE_MODEL \
           GEMINI_TAG_MODEL; do
  if [[ -n "${!key:-}" ]]; then
    ENV_ARGS+=(-e "$key=${!key}")
  fi
done

mkdir -p input output

docker run --rm \
  "${ENV_ARGS[@]}" \
  -v "$PWD/input:/app/input" \
  -v "$PWD/output:/app/output" \
  "$IMAGE_NAME" "$@"
