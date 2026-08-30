$ErrorActionPreference = "Stop"

# Expo는 EXPO_PUBLIC_* 값을 빌드 시점에 번들링하므로 반드시 Infisical 안에서 시작한다.
infisical run --env=dev -- npm --prefix mobile run web -- --lan --port 8081 --clear
