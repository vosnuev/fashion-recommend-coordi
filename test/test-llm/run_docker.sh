#!/usr/bin/env bash
# test-llm 빌드 + input/, output/ 볼륨 마운트 실행.
#
#   ./run_docker.sh                              # input/ 전체 × 모든 모델
#   ./run_docker.sh --models gpt-image-2         # 특정 모델만
#   NO_BUILD=1 ./run_docker.sh                   # 재빌드 생략
#
# API 키: 저장소 루트 .env 를 --env-file로 주입하고,
#         현재 셸에 export된 키가 있으면 그것이 우선한다.
set -euo pipefail
cd "$(dirname "$0")"

IMAGE_NAME=test-llm

if [[ "${NO_BUILD:-}" != "1" ]]; then
  docker build -t "$IMAGE_NAME" .
fi

ENV_ARGS=()
ROOT_ENV="$(cd ../.. && pwd)/.env"
[[ -f "$ROOT_ENV" ]] && ENV_ARGS+=(--env-file "$ROOT_ENV")

for key in OPENAI_API_KEY GEMINI_API_KEY ARK_API_KEY DASHSCOPE_API_KEY \
           OPENAI_IMAGE_MODEL GEMINI_ENUM_MODEL GEMINI_PRO_IMAGE_MODEL \
           GEMINI_FLASH_IMAGE_MODEL SEEDREAM_MODEL SEEDREAM_BASE_URL \
           SEEDREAM_SIZE QWEN_IMAGE_MODEL DASHSCOPE_BASE_URL TEST_MODELS; do
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
