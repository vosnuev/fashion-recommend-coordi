#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# 1단계: Infisical에서 시크릿을 .env 파일로 내보내기 (실패 시 중단)
infisical export --env=dev --format=dotenv | sed "s/^\([^=]*\)='\(.*\)'$/\1=\2/" > .env

# 2단계: Docker Compose 실행 (.env를 읽어 컨테이너에 주입)
# Swagger/noauth 모드는 별도 compose 파일 없이 .env의 DJANGO_SETTINGS_MODULE로 제어한다
# (config.settings.swagger / config.settings.swagger_noauth).
# -f를 생략하면 compose가 docker-compose.yml과 (있을 경우) 개인 로컬 설정용
# docker-compose.override.yml(gitignored)을 자동 병합한다.
docker compose --profile api up -d --build
