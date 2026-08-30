infisical export --env=dev --output-file=./.env
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

docker compose -f docker-compose.yml --profile api up -d --build
