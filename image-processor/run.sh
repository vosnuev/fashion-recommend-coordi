#!/usr/bin/env bash
# 이미지 프로세서 워커 빌드 & 실행 (Linux)
# 사용법: ./run.sh          # 빌드 + 실행
#        NO_BUILD=1 ./run.sh # 재빌드 생략
#
# 필요 환경변수는 저장소 루트 .env에서 로드한다:
#   GEMINI_API_KEY, REDIS_URL, WARDROBE_JOB_QUEUE, WARDROBE_INTERNAL_TOKEN,
#   WARDROBE_CALLBACK_URL, AWS_* (S3 자격증명)
set -euo pipefail
cd "$(dirname "$0")"

IMAGE=wardrobe-image-processor

if [[ -z "${NO_BUILD:-}" ]]; then
    docker build -t "$IMAGE" .
fi

GPU_FLAG=""
if command -v nvidia-smi >/dev/null 2>&1; then
    GPU_FLAG="--gpus all"
fi

# --network host: 로컬 Redis/wardrobe-api(localhost)에 접근하기 위함.
# 원격 Redis/API를 쓰면 제거해도 된다.
docker run --rm $GPU_FLAG \
    --network host \
    --env-file ../.env \
    "$IMAGE"
