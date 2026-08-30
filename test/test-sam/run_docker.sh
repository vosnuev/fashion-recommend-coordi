#!/usr/bin/env bash
# 통합 테스트 이미지를 빌드하고 input/의 jpg 전체를 대상으로 실행한다 (Linux + NVIDIA GPU).
#
# 사용법:
#   ./run_docker.sh                      # 3개 테스트 모두 실행
#   ./run_docker.sh segformer            # 특정 테스트만 (segformer|sam2|sam3)
#   NO_BUILD=1 ./run_docker.sh           # 빌드 생략하고 실행만
#
# 준비물:
#   1) test/input/*.jpg   — 테스트 대상 이미지
#   2) test/sam3/sam3.pt  — SAM3 가중치 (HF gated. python sam3/download_sam3.py 로 준비)
#   3) GEMINI_API_KEY     — sam3 테스트 필수. 미설정 시 저장소 루트 ../.env 에서 읽는다.
set -euo pipefail
cd "$(dirname "$0")"

IMAGE_TAG="seg-tests"
WEIGHTS="${SAM3_WEIGHTS:-$PWD/sam3/sam3.pt}"

# GEMINI_API_KEY 미설정 시 루트 .env에서 로드 (값은 출력하지 않는다)
if [[ -z "${GEMINI_API_KEY:-}" && -f ../.env ]]; then
    set -a; source ../.env; set +a
fi

# sam3 실행이 포함되는데 준비물이 없으면 미리 안내
RUN_SAM3=1
if [[ $# -gt 0 ]]; then
    RUN_SAM3=0
    for sel in "$@"; do
        [[ "$sel" == "all" || "$sel" == "sam3" ]] && RUN_SAM3=1
    done
fi
if [[ "$RUN_SAM3" -eq 1 ]]; then
    if [[ ! -f "$WEIGHTS" ]]; then
        echo "오류: SAM3 가중치가 없습니다: $WEIGHTS" >&2
        echo "python sam3/download_sam3.py 로 받거나 SAM3_WEIGHTS로 경로를 지정하세요." >&2
        exit 1
    fi
    if [[ -z "${GEMINI_API_KEY:-}" ]]; then
        echo "오류: GEMINI_API_KEY가 설정되지 않았습니다 (sam3 테스트 필수)." >&2
        exit 1
    fi
fi

# 입력 이미지 확인
shopt -s nullglob nocaseglob
INPUT_IMAGES=(input/*.jpg input/*.jpeg)
shopt -u nullglob nocaseglob
if [[ "${#INPUT_IMAGES[@]}" -eq 0 ]]; then
    echo "오류: test/input/ 에 jpg 이미지가 없습니다." >&2
    exit 1
fi

if [[ -z "${NO_BUILD:-}" ]]; then
    docker build -t "$IMAGE_TAG" .
fi

mkdir -p output

DOCKER_ARGS=(
    --rm --gpus all
    -v "$PWD/input":/app/input:ro
    -v "$PWD/output":/app/output
)
if [[ "$RUN_SAM3" -eq 1 ]]; then
    DOCKER_ARGS+=(
        -e GEMINI_API_KEY
        -e GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.5-flash}"
        -v "$WEIGHTS":/app/sam3/sam3.pt:ro
    )
fi

docker run "${DOCKER_ARGS[@]}" "$IMAGE_TAG" "$@"

echo "결과: $PWD/output/"
