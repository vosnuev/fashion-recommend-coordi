#!/usr/bin/env bash
# 3개 세그멘테이션 테스트(segformer / sam2 / sam3)를 순차 실행한다.
# 컨테이너 ENTRYPOINT이지만, 의존성이 설치된 호스트에서 직접 실행해도 된다.
#
# 사용법: ./run_all_tests.sh [all|segformer|sam2|sam3]...
#   - 인자 생략 시 3개 모두 실행
#   - 입력: test/input/ 의 jpg 전체 / 출력: test/output/<모델명>/<이미지명>/
set -uo pipefail
cd "$(dirname "$0")"

SELECTED=("${@:-all}")

should_run() {
    local name="$1"
    for sel in "${SELECTED[@]}"; do
        [[ "$sel" == "all" || "$sel" == "$name" ]] && return 0
    done
    return 1
}

declare -A RESULTS
FAILED=0

run_test() {
    local name="$1" script="$2"
    echo
    echo "========================================"
    echo "테스트 시작: $name ($script)"
    echo "========================================"
    if python "$script"; then
        RESULTS["$name"]="성공"
    else
        RESULTS["$name"]="실패"
        FAILED=1
    fi
}

should_run segformer && run_test segformer segformer/test_segformer.py
should_run sam2      && run_test sam2      sam2/test_grounded_sam2_common_package.py
should_run sam3      && run_test sam3      sam3/test_sam3_gemini.py

echo
echo "========================================"
echo "전체 결과"
for name in "${!RESULTS[@]}"; do
    echo "  $name: ${RESULTS[$name]}"
done
echo "출력 폴더: $(pwd)/output"
echo "========================================"

exit "$FAILED"
