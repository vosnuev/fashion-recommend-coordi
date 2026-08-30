#!/usr/bin/env bash
# ============================================================
#  골든셋 태깅  —  ★ API 서버에서 실행 ★
# ============================================================
# S3의 코디 manifest에 presentation_group / style / season / occasion을 붙인다.
# 원본 사진을 Gemini가 보고 판단하며, 결과는 같은 manifest.json에 되쓴다.
# GPU가 필요 없다 — Gemini API와 S3만 쓴다.
#
#   ./run_goldenset_tagging.sh --limit 3 --dry-run   # 시험 (저장 안 함)
#   ./run_goldenset_tagging.sh                       # 미태깅분만
#   ./run_goldenset_tagging.sh --force               # 전량 다시 태깅
#   DETACH=1 ./run_goldenset_tagging.sh              # 터미널과 분리해 실행
#
# 중간에 끊겨도 안전하다. 코디 한 장을 태깅할 때마다 manifest에 바로 기록하므로,
# 다시 돌리면 남은 것부터 이어간다.
#
# 끝나면 GPU 서버에서 ./run_goldenset_sync.sh 를 돌려 Qdrant에 반영한다.
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE_FILE=docker-compose.golden_set.yml
RUN_NAME=skn28-golden-tagging
MODULE=ml.golden_set.tag_manifests
SERVICE=golden-set-scan

# 시크릿을 .env로 내보낸다 (compose 보간 + 컨테이너 주입 양쪽에 쓰인다).
infisical export --env=dev --format=dotenv \
  | sed "s/^\([^=]*\)='\(.*\)'$/\1=\2/" > .env

# golden-set-scan 서비스의 이미지를 그대로 쓰고 커맨드만 바꾼다.
if [[ -n "${DETACH:-}" ]]; then
    # docker compose run은 터미널에 붙는다. Ctrl+C나 SSH 끊김이 그대로
    # 컨테이너로 전달되므로, 오래 걸리는 전량 작업은 분리해서 돌린다.
    # -d와 --rm은 함께 쓸 수 없어 이름을 붙이고 끝난 뒤 직접 지운다.
    docker rm -f "$RUN_NAME" >/dev/null 2>&1 || true
    docker compose -f "$COMPOSE_FILE" run -d --build --name "$RUN_NAME" \
      "$SERVICE" python -m "$MODULE" "$@"
    echo
    echo "백그라운드로 실행 중입니다."
    echo "  로그:  docker logs -f $RUN_NAME"
    echo "  정리:  docker rm $RUN_NAME      (끝난 뒤)"
else
    docker compose -f "$COMPOSE_FILE" run --rm --build \
      "$SERVICE" python -m "$MODULE" "$@"
fi
