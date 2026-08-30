# Swagger / OpenAPI

기존 실행 설정과 코드는 변경하지 않고 Swagger 전용 설정을 별도로 제공한다.
`config.urls`의 전체 URL 패턴을 포함하므로 앞으로 루트 URL에 연결되는 DRF API도
서버 재시작 후 문서에 자동으로 나타난다.

## 로컬 실행

```powershell
pip install -r requirements.txt
$env:DJANGO_SETTINGS_MODULE = "config.settings.swagger"
python manage.py runserver
```

Linux/macOS에서는 다음과 같이 실행한다.

```bash
pip install -r requirements.txt
DJANGO_SETTINGS_MODULE=config.settings.swagger python manage.py runserver
```

## Docker 실행

별도 compose 파일 없이 `DJANGO_SETTINGS_MODULE`로 모드를 지정한다
(구 `docker-compose.swagger.yml`은 `docker-compose.yml`에 병합·폐기됨).

```bash
# 일회성 실행 (셸 환경변수가 .env 값보다 우선)
DJANGO_SETTINGS_MODULE=config.settings.swagger docker compose --profile api up -d --build
```

상시 사용하려면 Infisical(dev)의 `DJANGO_SETTINGS_MODULE` 값을 바꾼 뒤 `.env`를 다시 export한다.

## 문서 URL

- Swagger UI: `http://localhost:8000/api/docs/`
- ReDoc: `http://localhost:8000/api/redoc/`
- OpenAPI schema: `http://localhost:8000/api/schema/`

Swagger 설정은 개발 환경 설정을 확장하므로 운영 환경 설정으로 사용하지 않는다.
보호된 API는 Swagger UI의 Authorize에서 정상 JWT를 입력해 호출한다.
