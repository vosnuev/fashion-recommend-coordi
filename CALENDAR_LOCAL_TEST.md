# 캘린더 로컬 테스트 가이드

캘린더 사진 등록부터 기존 옷장 이미지 프로세서 처리, `WardrobeItem` 생성,
캘린더 자동 연결까지 로컬에서 검증하는 절차다.

## 1. 테스트 범위

이 문서에서는 다음 흐름을 검증한다.

```text
캘린더 사진 등록 API
→ 캘린더·옷장 S3 원본 저장
→ WardrobeUploadJob 생성
→ wardrobe:jobs enqueue
→ 기존 image-processor worker 처리
→ 기존 wardrobe callback 호출
→ WardrobeItem 생성
→ CalendarWardrobeItem N:N 자동 연결
→ 캘린더 COMPLETED
```

프론트엔드 없이 API만 호출하며, 캘린더 전용 queue·consumer·callback은 사용하지
않는다.

## 2. 사전 준비

- Docker Desktop
- Conda `final` 환경
- PostgreSQL, Redis, Qdrant용 Docker 이미지
- 실제 S3 버킷과 접근 가능한 AWS 자격증명
- Gemini API 키
- 테스트할 JPG, PNG, WebP 또는 HEIC 이미지 한 장

로컬 E2E 테스트도 S3와 Gemini를 실제 호출한다. 자동 테스트만 실행할 때는 S3와
Gemini가 필요하지 않다.

## 3. 환경변수 설정

저장소 루트의 `.env`에 아래 값을 설정한다. 실제 키와 비밀번호는 커밋하지 않는다.

```dotenv
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=fashion_db
POSTGRES_USER=postgres
POSTGRES_PASSWORD=change-me

REDIS_URL=redis://localhost:6379/0
REDIS_PASSWORD=change-me
WARDROBE_JOB_QUEUE=wardrobe:jobs

WARDROBE_S3_BUCKET=your-local-test-bucket
CALENDAR_S3_BUCKET=your-local-test-bucket
AWS_REGION=ap-northeast-2
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...

WARDROBE_CALLBACK_URL=http://localhost:8000/api/v1/internal/wardrobe/callback/
WARDROBE_INTERNAL_TOKEN=local-calendar-test-token

GEMINI_API_KEY=...
WORKER_EMBED_ENABLED=0
```

로컬에서는 `WARDROBE_S3_BUCKET`과 `CALENDAR_S3_BUCKET`에 같은 버킷을 사용해도
된다. 객체는 각각 `wardrobe/`, `calendar/` prefix로 분리된다.

`WORKER_EMBED_ENABLED=0`은 FashionSigLIP/BGE 모델 다운로드와 임베딩을 생략한다.
의류 추출·이미지 생성·태깅에는 Gemini API가 계속 사용된다.

## 4. 자동 테스트

자동 테스트는 S3·Redis·Gemini 호출을 mock 처리한다. PostgreSQL 테스트 DB는
필요하므로 DB 컨테이너를 먼저 실행한다.

```bash
cd /e/workspace/SKN28-FINAL-1Team
docker compose up -d db

conda activate final
export PYTHONUTF8=1

cd api
python manage.py test apps.style_calendar.tests --verbosity 2
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py sqlmigrate style_calendar 0002
```

정상 기준은 다음과 같다.

- 캘린더 테스트 69개 통과
- Django system check 오류 없음
- 추가 마이그레이션 변경 없음
- `0002` SQL에 `wardrobe_upload_job_id`의 UNIQUE FK 추가와 `calendar_item`
  테이블 제거가 표시됨

## 5. E2E 테스트용 인프라 실행

첫 번째 Git Bash에서 실행한다.

```bash
cd /e/workspace/SKN28-FINAL-1Team
docker compose up -d db redis qdrant
docker compose ps
```

`db`, `redis`, `qdrant`가 실행 중인지 확인한다.

## 6. API 실행

두 번째 Git Bash에서 실행한다.

```bash
cd /e/workspace/SKN28-FINAL-1Team/api

conda activate final
export PYTHONUTF8=1
export DJANGO_SETTINGS_MODULE=config.settings.swagger_noauth

python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

`swagger_noauth`는 로컬 개발용 자동 로그인 설정이다. 소셜 로그인 없이 보호된 API를
호출할 수 있다. 운영 환경에서는 사용하지 않는다.

- Swagger UI: <http://localhost:8000/api/docs/>

## 7. Swagger UI 입력 예시

Swagger UI에서 테스트할 때는 원하는 API를 펼친 다음 **Try it out**을 누르고 값을
입력한 뒤 **Execute**를 누른다. `swagger_noauth` 설정에서는 JWT를 입력하지 않아도
`dev_autologin` 사용자로 요청된다.

캘린더 관련 API는 다른 도메인과 섞이지 않고 Swagger의 **캘린더** 카테고리에만
표시된다.

### 7.1 API 목록

| Method | Path | 용도 |
|---|---|---|
| `GET` | `/api/v1/wardrobe/items/` | 직접 선택에 사용할 옷장 아이템 UUID 조회 |
| `POST` | `/api/v1/calendars/photo/` | 사용자 사진을 올려 캘린더 등록 및 비동기 옷장 등록 시작 |
| `POST` | `/api/v1/calendars/wardrobe/` | 기존 옷장 아이템을 직접 선택해 캘린더 등록 |
| `GET` | `/api/v1/calendars/` | 기간별 캘린더 목록 조회 |
| `GET` | `/api/v1/calendars/by-date/` | 특정 날짜 캘린더 조회 |
| `GET` | `/api/v1/calendars/{calendar_id}/` | 캘린더 상세 조회 |
| `PATCH` | `/api/v1/calendars/{calendar_id}/` | 일정·TPO·해시태그 수정 |
| `GET` | `/api/v1/calendars/{calendar_id}/processing-status/` | 사진 처리 상태 조회 |
| `DELETE` | `/api/v1/calendars/{calendar_id}/` | 완료·실패 캘린더 삭제 |

### 7.2 옷장 아이템 목록 조회

`GET /api/v1/wardrobe/items/`

Query parameters:

| 이름 | 필수 | 형식 | 입력 예시 | 설명 |
|---|---|---|---|---|
| `category_large` | 아니요 | string | `상의` | 대분류 필터 |
| `confirmed` | 아니요 | boolean 문자열 | `false` | `true`는 확정, `false`는 사용자 확인 대기 |

자동 등록된 미확정 아이템만 조회하는 예시:

```text
category_large: 입력하지 않음
confirmed: false
```

응답의 `id`가 캘린더 직접 선택 등록에서 사용할 `wardrobe_item_ids` 값이다.

### 7.3 사진 업로드 캘린더 등록

`POST /api/v1/calendars/photo/`

Request content type은 `multipart/form-data`다.

| 필드 | 필수 | 형식 | Swagger 입력 예시 | 설명 |
|---|---|---|---|---|
| `image` | 예 | binary file | `outfit.jpg` 선택 | JPG, PNG, WebP, HEIC, 최대 15MB |
| `date` | 예 | `YYYY-MM-DD` | `2026-08-20` | 사용자별 같은 날짜 한 건만 허용 |
| `wardrobe_item_ids` | 아니요 | UUID array | `6c75...` | 사진과 함께 미리 연결할 기존 옷장 아이템 |
| `schedule` | 아니요 | string | `성수동 저녁 약속` | 일정 설명 |
| `tpo` | 아니요 | string array | `데이트`, `모임` | TPO 목록 |
| `hashtags` | 아니요 | string array | `여름`, `캐주얼` | 해시태그 목록 |

가장 단순한 테스트 입력:

```text
image: 로컬 outfit.jpg 선택
date: 2026-08-20
wardrobe_item_ids: 비워 둠
schedule: 성수동 저녁 약속
tpo: ["데이트", "모임"]
hashtags: ["여름", "캐주얼"]
```

Swagger UI에서 배열 입력란이 항목 단위로 표시되면 **Add string item** 또는
**Add item**을 눌러 값을 하나씩 넣는다. 기존 아이템을 함께 연결하지 않을 때는
`wardrobe_item_ids`를 비워 둔다. Swagger가 빈 입력란을 multipart의 빈 문자열로
전송하더라도 API가 이를 빈 배열로 정규화하므로 옷장 아이템 없이 등록할 수 있다.

예상 결과:

- HTTP `202 Accepted`
- `source_type`: `PHOTO_UPLOAD`
- 최초 `status`: `REGISTERED`
- Redis `wardrobe:jobs`에 기존 옷장 job 적재
- worker callback 완료 후 `COMPLETED`

### 7.4 기존 옷장 아이템 직접 선택 등록

`POST /api/v1/calendars/wardrobe/`

Request content type은 `application/json`이다. 먼저 옷장 목록 API에서 현재 사용자의
아이템 UUID를 확인한다.

```json
{
  "date": "2026-08-21",
  "wardrobe_item_ids": [
    "11111111-1111-1111-1111-111111111111",
    "22222222-2222-2222-2222-222222222222"
  ],
  "schedule": "회사 출근 후 저녁 모임",
  "tpo": ["출근", "모임"],
  "hashtags": ["포멀", "여름"]
}
```

입력 조건:

- `wardrobe_item_ids`는 한 개 이상 필요
- 같은 UUID를 중복해서 넣을 수 없음
- 현재 로그인 사용자 소유 아이템만 허용
- 사진 없이 첫 번째 선택 아이템 이미지가 캘린더 대표 이미지가 됨
- 이미지 처리가 필요하지 않으므로 HTTP `201`, `COMPLETED`로 즉시 등록

### 7.5 기간별 캘린더 목록 조회

`GET /api/v1/calendars/`

Query parameters:

| 이름 | 필수 | 형식 | 입력 예시 |
|---|---|---|---|
| `start_date` | 예 | `YYYY-MM-DD` | `2026-08-01` |
| `end_date` | 예 | `YYYY-MM-DD` | `2026-08-31` |

두 날짜는 모두 조회 범위에 포함된다. `start_date`가 `end_date`보다 늦으면 HTTP
`400 Bad Request`가 반환된다.

### 7.6 날짜별 캘린더 조회

`GET /api/v1/calendars/by-date/`

Query parameter:

```text
date: 2026-08-20
```

해당 날짜에 현재 사용자의 캘린더가 없으면 HTTP `404 Not Found`가 반환된다.

### 7.7 캘린더 상세 조회

`GET /api/v1/calendars/{calendar_id}/`

Path parameter:

```text
calendar_id: 사진 등록 또는 직접 선택 등록 응답의 id
```

실제 입력 예시:

```text
calendar_id: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
```

응답에서 확인할 주요 값:

- `image_url`: 캘린더 대표 이미지 presigned URL
- `status`: `REGISTERED`, `PROCESSING`, `COMPLETED`, `FAILED`
- `wardrobe_items`: 직접 선택 또는 자동 등록 후 연결된 옷장 아이템 목록
- `wardrobe_items[].wardrobe_item_id`: 실제 `WardrobeItem` UUID
- `wardrobe_items[].snapshot`: 캘린더 연결 시점의 아이템 정보

### 7.8 캘린더 메타데이터 수정

`PATCH /api/v1/calendars/{calendar_id}/`

Path parameter:

```text
calendar_id: 수정할 캘린더 UUID
```

Request body 예시:

```json
{
  "schedule": "회사 회식으로 일정 변경",
  "tpo": ["출근", "회식"],
  "hashtags": ["포멀", "저녁"]
}
```

부분 수정이므로 한 필드만 보내도 된다.

```json
{
  "schedule": "점심 약속"
}
```

`date`, `status`, `image_s3_key`, `wardrobe_items` 등 허용되지 않은 필드를 보내면
HTTP `400 Bad Request`가 반환된다.

### 7.9 이미지 처리 상태 조회

`GET /api/v1/calendars/{calendar_id}/processing-status/`

Path parameter:

```text
calendar_id: 사진 업로드 캘린더 UUID
```

worker 실행 전에는 일반적으로 다음 상태다.

```json
{
  "status": "REGISTERED",
  "processing_required": true,
  "is_terminal": false,
  "result_available": false,
  "item_counts": {
    "total": 0,
    "extracted": 0,
    "failed": 0
  },
  "failure": null
}
```

callback 처리 후에는 `COMPLETED`, `is_terminal=true`, `result_available=true`가 되고
`item_counts`에 해당 job이 생성·연결한 옷장 아이템 수가 표시된다.

### 7.10 캘린더 삭제

`DELETE /api/v1/calendars/{calendar_id}/`

Path parameter:

```text
calendar_id: 삭제할 캘린더 UUID
```

삭제 조건:

- `COMPLETED` 또는 `FAILED` 상태만 삭제 가능
- `REGISTERED` 또는 `PROCESSING`은 HTTP `409 Conflict`
- 성공하면 HTTP `204 No Content`
- 캘린더 연결과 캘린더 소유 S3 경로는 삭제됨
- 실제 `WardrobeItem`과 옷장 S3 데이터는 삭제되지 않음

### 7.11 권장 Swagger 테스트 순서

1. `POST /calendars/photo/`로 사진 등록
2. 응답의 캘린더 `id` 복사
3. Redis queue 확인 후 기존 worker 실행
4. `GET /calendars/{calendar_id}/processing-status/` 반복 조회
5. `GET /wardrobe/items/?confirmed=false`로 자동 등록 아이템 확인
6. `GET /calendars/{calendar_id}/`에서 같은 아이템 연결 확인
7. `PATCH /calendars/{calendar_id}/`로 메타데이터 수정
8. 필요하면 `DELETE /calendars/{calendar_id}/`로 테스트 데이터 정리

## 8. cURL로 캘린더 사진 등록

세 번째 Git Bash에서 실행한다. 사진 경로와 날짜는 로컬 환경에 맞게 변경한다.

```bash
curl -X POST "http://localhost:8000/api/v1/calendars/photo/" \
  -F "image=@/e/test-images/outfit.jpg" \
  -F "date=2026-08-20" \
  -F "schedule=로컬 테스트" \
  -F "tpo=데이트" \
  -F "hashtags=테스트"
```

정상 응답은 HTTP `202 Accepted`이며 응답의 `id`가 캘린더 UUID다.

```json
{
  "id": "캘린더 UUID",
  "date": "2026-08-20",
  "source_type": "PHOTO_UPLOAD",
  "status": "REGISTERED",
  "wardrobe_items": []
}
```

같은 사용자의 같은 날짜 캘린더가 이미 있으면 `409 Conflict`가 반환된다. 이 경우
다른 날짜로 다시 요청한다.

## 9. 기존 옷장 Queue 사용 확인

worker를 실행하기 전에 `wardrobe:jobs` 길이를 확인한다.

```bash
cd /e/workspace/SKN28-FINAL-1Team

docker compose exec redis sh -lc \
  'redis-cli -a "$REDIS_PASSWORD" LLEN wardrobe:jobs'
```

worker가 아직 소비하지 않았다면 일반적으로 `1`이 출력된다. 별도의
`calendar:jobs` queue는 사용하지 않는다.

## 10. 기존 image-processor worker 실행

네 번째 Git Bash에서 실행한다.

```bash
cd /e/workspace/SKN28-FINAL-1Team/image-processor

conda activate final
export PYTHONUTF8=1
export WORKER_EMBED_ENABLED=0

python worker.py
```

worker 로그에서 다음 단계를 확인한다.

1. `wardrobe:jobs` 작업 수신
2. 원본 이미지 S3 다운로드
3. 패션 아이템 열거·이미지 생성·태깅
4. 옷장 S3 경로에 아이템 결과와 manifest 저장
5. `/api/v1/internal/wardrobe/callback/` 호출
6. queue ack

## 11. 처리 상태 확인

사진 등록 응답의 캘린더 UUID를 사용한다.

```bash
curl "http://localhost:8000/api/v1/calendars/캘린더_UUID/processing-status/"
```

정상 완료 예시는 다음과 같다.

```json
{
  "calendar_id": "캘린더 UUID",
  "status": "COMPLETED",
  "processing_required": true,
  "is_terminal": true,
  "result_available": true,
  "item_counts": {
    "total": 2,
    "extracted": 2,
    "failed": 0
  },
  "failure": null
}
```

worker가 아직 처리하지 않았다면 `REGISTERED` 상태가 유지되는 것이 정상이다.

## 12. 자동 옷장 등록·캘린더 연결 확인

캘린더 상세 조회:

```bash
curl "http://localhost:8000/api/v1/calendars/캘린더_UUID/"
```

미확정 옷장 아이템 조회:

```bash
curl "http://localhost:8000/api/v1/wardrobe/items/?confirmed=false"
```

다음을 확인한다.

- 이미지 프로세서가 찾은 옷마다 실제 `WardrobeItem`이 생성됨
- 생성된 아이템의 `confirmed`가 `false`임
- 캘린더 상세의 `wardrobe_items`에 같은 옷장 아이템 ID가 있음
- 캘린더 대표 이미지는 사용자가 올린 원본 사진을 유지함
- 직접 선택한 기존 옷장 아이템이 있었다면 자동 생성 아이템이 그 뒤에 추가됨

## 13. DB 직접 확인

```bash
cd /e/workspace/SKN28-FINAL-1Team

docker compose exec db sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
SELECT
    ce.id AS calendar_id,
    ce.date,
    ce.status AS calendar_status,
    wuj.id AS job_id,
    wuj.status AS job_status,
    COUNT(cwi.id) AS linked_item_count
FROM calendar_entry ce
LEFT JOIN wardrobe_upload_job wuj
    ON wuj.id = ce.wardrobe_upload_job_id
LEFT JOIN calendar_wardrobe_item cwi
    ON cwi.calendar_id = ce.id
GROUP BY ce.id, ce.date, ce.status, wuj.id, wuj.status
ORDER BY ce.created_at DESC
LIMIT 10;
"'
```

정상 완료된 사진 캘린더는 `calendar_status=COMPLETED`, `job_status=DONE`이며
`linked_item_count`가 처리 성공 아이템 수만큼 증가한다.

## 14. 장애 확인

### 캘린더가 REGISTERED에서 멈춤

- worker 실행 여부 확인
- `REDIS_URL`과 `REDIS_PASSWORD` 확인
- `wardrobe:jobs`와 processing queue 확인

```bash
docker compose exec redis sh -lc \
  'redis-cli -a "$REDIS_PASSWORD" LLEN wardrobe:jobs'

docker compose exec redis sh -lc \
  'redis-cli -a "$REDIS_PASSWORD" LLEN wardrobe:jobs:processing'
```

### 사진 등록 API가 503 반환

- `WARDROBE_S3_BUCKET`, `CALENDAR_S3_BUCKET` 확인
- AWS 자격증명과 S3 Put/Get/Copy/Delete/List 권한 확인
- Redis 연결과 비밀번호 확인

### callback이 403 반환

API와 worker가 읽는 `WARDROBE_INTERNAL_TOKEN` 값이 같은지 확인한다.

### callback 연결 실패

`WARDROBE_CALLBACK_URL`이 다음 로컬 주소인지 확인한다.

```text
http://localhost:8000/api/v1/internal/wardrobe/callback/
```

### dead queue 확인

```bash
docker compose exec redis sh -lc \
  'redis-cli -a "$REDIS_PASSWORD" LRANGE wardrobe:jobs:dead 0 -1'
```

## 15. 테스트 종료

API와 worker는 각각 `Ctrl+C`로 종료한다. Docker 서비스는 다음 명령으로 중지한다.

```bash
cd /e/workspace/SKN28-FINAL-1Team
docker compose stop db redis qdrant
```

데이터까지 삭제하려면 별도 합의 없이 `docker compose down -v`를 실행하지 않는다.
`-v`는 PostgreSQL·Redis·Qdrant 볼륨 데이터를 삭제한다.
