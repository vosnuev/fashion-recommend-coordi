# API 서버 (Django REST Framework)

패션 추천 서비스 백엔드. CLAUDE.md 권장 구조(설정 분리 + apps/)를 따른다.

```
api/
├── manage.py                  # 기본 settings: config.settings.dev
├── requirements.txt
├── config/
│   ├── settings/
│   │   ├── base.py            # 공통 (루트 .env 로드, DB, DRF, JWT, OAuth)
│   │   ├── dev.py             # DEBUG=True, Browsable API
│   │   ├── noauth.py          # 로컬 전용: 인증 우회 (자동 로그인)
│   │   └── prod.py            # AWS 배포용 (HTTPS, 시크릿 필수화)
│   ├── urls.py                # /admin, /api/v1/
│   └── asgi.py / wsgi.py      # 기본 settings: config.settings.prod
└── apps/
    ├── catalog/               # naver_product / naver_product_size (collector/naver가 사용)
    ├── weather/               # weather_* 6개 테이블 (collector/weather가 사용)
    └── users/                 # 사용자 + 이메일/소셜 인증
        ├── models.py          # User(커스텀), SocialAccount
        ├── serializers.py
        ├── views.py           # EmailSignup/LoginView, SocialLoginView, MeView
        ├── urls.py
        ├── services/
        │   ├── oauth.py       # naver/kakao/google code→token→profile
        │   └── accounts.py    # 프로필 → User/SocialAccount 매핑
        └── tests.py           # OAuth mock 테스트
```

## 실행

환경변수는 **프로젝트 루트의 `.env`** 를 사용한다 (`base.py`가 자동 로드).

```bash
cd api
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 루트 .env에 POSTGRES_*, DJANGO_SECRET_KEY, *_OAUTH_* 값 설정 후
python manage.py migrate               # users 0001은 커밋돼 있음 (모델 변경 시에만 makemigrations)
python manage.py runserver

python manage.py test apps.users   # 테스트
```

## Docker (통합 compose)

루트 `docker-compose.yml`이 db/api/collector를 profiles로 관리한다.

```bash
# 프로젝트 루트에서
docker compose --profile api up -d --build      # db + api
docker compose --profile all up -d --build      # db + api + collector 2종
# 또는 .env에 COMPOSE_PROFILES=api 지정 후: docker compose up -d
```

컨테이너 기동 시 `migrate` → `collectstatic` → gunicorn(8000) 순으로 실행된다.
로컬 http 테스트 시 `.env`에 `DJANGO_SECURE_SSL_REDIRECT=false`
(또는 `DJANGO_SETTINGS_MODULE=config.settings.dev`)를 설정한다.

## 인증 API

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| POST | `/api/v1/auth/signup/` | body `{email, password}` → 비활성 이메일 계정 생성 + 6자리 인증 코드 발송 |
| POST | `/api/v1/auth/email/verify/` | body `{email, code}` → 이메일 인증 완료 `{email, verified}`. **토큰은 발급하지 않는다** — 이어서 로그인 API를 호출한다 |
| POST | `/api/v1/auth/email/resend/` | body `{email}` → 인증 코드 재발송 (기본 60초 제한) |
| POST | `/api/v1/auth/login/` | body `{email, password}` → JWT(access/refresh) + user. `is_new_user`는 가입 후 첫 로그인(`last_login` NULL)일 때 true |
| POST | `/api/v1/auth/{naver\|kakao\|google}/login/` | body `{code, redirect_uri, state}` → JWT(access/refresh) + user. kakao/google은 `redirect_uri` 필수, naver는 `state` 필수 |
| POST | `/api/v1/auth/token/refresh/` | body `{refresh}` → 새 access |
| GET/PATCH | `/api/v1/users/me/` | 내 정보 조회/수정 (Bearer 토큰 필요) |

이메일 흐름: 가입(202, 비활성 계정 + 코드 발송) → 코드 인증(200, 계정 활성화·**토큰 없음**) →
로그인(200, JWT + `is_new_user`) → 첫 로그인이면 앱이 온보딩(권한 → 체형 측정 → 추구미)으로 분기.
인증 API가 토큰을 주지 않는 이유는 이메일 주소만 아는 사람이 비밀번호 없이 세션을 얻는 경로를 막기 위해서다.

흐름: 프론트가 제공사 로그인 → authorization code 수신 → 백엔드로 전달 →
백엔드가 토큰 교환·프로필 조회 → `SocialAccount` upsert → 자체 JWT 발급.
같은 (provider, provider_user_id)는 항상 같은 User로 연결되고,
이메일이 같아도 제공사가 다르면 자동 통합하지 않는다(보안상 명시적 연결만).

## 코디 평가 API (apps/recommend)

Gemini 호출이 30초를 넘겨 gunicorn 워커를 붙잡던 문제 때문에 **접수와 분석을 분리**했다.
설계: Confluence > 설계 > "코디 평가 비동기화 설계(접수·워커 분리 · 익명 폴링)".

```
[앱] --사진--> [api] --S3--> [DB: QUEUED] --> [Redis 큐] --202--> [앱]
                                                  │
                                            [outfit-worker]
                                            축소 → Gemini → DB: SUCCEEDED
                                                  │
[앱] --2초마다 GET /outfits/analyses/{id}/ -------> 완료되면 결과
```

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| POST | `/api/v1/outfits/analyze/` | multipart `image`(+선택 `lat`/`lon`/`save_to_wardrobe`) → **202** + `analysis_id`/`poll_url`. 비로그인 가능, JWT를 보내면 추구미·체형 반영 |
| GET | `/api/v1/outfits/analyses/{id}/` | 진행 상태 겸 결과 (폴링용). 익명 접수 건은 토큰 없이, 로그인 접수 건은 본인만 |
| GET | `/api/v1/outfits/analyses/` | 내 이력 목록 (`status`/`limit`/`offset`). 로그인 필요 |
| POST | `/api/v1/outfits/analyses/claim/` | 비로그인으로 접수한 건의 소유권 이전. 로그인 필요 |

요청 1건은 `outfit_analysis` 테이블에 1행으로 남는다. **LLM 질의를 구성한 정보(날씨·체형·
추구미 스냅샷)와 LLM 요청·응답 원본을 함께 보관**해 사후에 평가를 재현·비교할 수 있게 한다.
컨텍스트는 **접수 시점에 굳어서**, 큐에서 대기하는 사이 날씨가 바뀌어도 사진을 올린 순간의
조건으로 평가한다 (워커는 `analysis.llm_context()`를 쓰고 다시 만들지 않는다).

- 상태: `QUEUED` → `PROCESSING` → `SUCCEEDED` / `FAILED`
- 원본 사진은 S3(`outfits/{user_id|anonymous}/{analysis_id}/original.<ext>`)에 두고 키만 저장.
  LLM에는 1024px로 축소한 전송본만 보낸다(워커가 축소). `image_bytes`는 원본,
  `llm_image_bytes`는 전송본 크기다.
- **S3는 필수 경로다.** 워커가 사진을 S3에서만 읽으므로 버킷 미설정·업로드 실패는 접수를
  503으로 거절한다. 버킷은 `OUTFIT_S3_BUCKET`, 없으면 `WARDROBE_S3_BUCKET`.
- 익명 요청은 `user=NULL`로 기록되고 `analysis_id`(UUID4)를 아는 사람만 조회할 수 있다.
  응답에서 사진 URL·체형·LLM 원본을 빼고, `OUTFIT_ANON_TTL_HOURS`(기본 24시간)이 지나면 닫힌다.

### 비로그인 접수 건의 소유권 이전 (`claim`)

비로그인으로 평가한 뒤 로그인하면, 앱이 보관해 둔 `claim_token`을 모아
`POST /api/v1/outfits/analyses/claim/` 에 `{"claim_tokens": [...]}` 로 보낸다.
토큰 안에 대상 식별자가 들어 있어 `analysis_id`를 따로 보낼 필요가 없다.

응답은 `{"claimed": [...], "skipped": [{"analysis_id", "reason"}]}` 형태이고,
`reason`은 `invalid_token` / `expired` / `not_found` / `already_owned` 중 하나다.

**왜 UUID만으로는 안 되는가.** 조회는 UUID를 아는 사람에게 열어두지만 claim은 성격이
다르다. 조회는 읽기라 UUID가 새어도 평가 문구만 보이지만(사진 URL·체형은 응답에서 제외),
claim은 쓰기이고 성공하면 소유자 응답으로 바뀌어 **사진 presigned URL과 체형 스냅샷까지
열린다.** 권한 상승 경로라 두 겹을 건다 — 접수 202 응답에만 실어 보내는 **서명 토큰**
(`TimestampSigner`, 서버 미저장)과 **짧은 TTL**(`OUTFIT_CLAIM_TTL_MINUTES`, 기본 60분,
조회 24시간과 별개). 토큰이 살아 있어도 행이 오래됐으면 DB 쪽에서 한 번 더 막는다.

- **평가를 다시 하지 않는다.** 익명 평가는 `personalized=false`, `body/pursuit=NULL`로
  이미 끝나 있다. 주인만 바꾸고, 개인화 없이 나온 결과라는 사실을 `accepted_anonymously`로
  남긴다. 이 필드와 `claimed_at`은 내부 기록이라 **API 응답에 싣지 않는다**.
- **사진을 계정 폴더로 옮긴다.** `outfits/anonymous/{id}/` → `outfits/{user_id}/{id}/`
  (서버 사이드 CopyObject 후 원본 삭제). 익명 사진은 보관 기간을 짧게 두고 정리하게 되는데,
  주인이 생긴 사진이 `anonymous/` 프리픽스에 남아 있으면 함께 쓸려나간다.
  이동은 best-effort — 실패해도 기존 키로 읽히므로 소유권 이전을 되돌리지 않고 ERROR 로그만 남긴다.
- **멱등하다.** 이미 본인 것이면 성공으로 친다. 남의 것이면 `already_owned`로 거절하고,
  동시 요청은 `select_for_update`로 한 명만 성공한다.

> ⚠️ **프론트 주의**: 이전이 끝나면 그 기록은 익명 조회가 닫힌다. 분석이 진행 중인 건을
> 넘겨받았다면 이후 폴링에 반드시 `Authorization` 헤더를 실어야 하며, 그렇지 않으면 404가 난다.

### 옷장 등록 연계 (`save_to_wardrobe`)

로그인 사용자가 `save_to_wardrobe=true`로 접수하면 **같은 사진을 옷장 아이템 등록
파이프라인에도 넘긴다** (`WardrobeUploadJob` 생성 → `wardrobe:jobs` 큐 → image-processor).
평가 큐에 적재한 직후 이어서 요청하고, 202 응답의 `wardrobe_job_id`로
`GET /api/v1/wardrobe/uploads/{job_id}/` 에서 등록 진행 상황을 조회한다.

- **비로그인 요청에서는 무시된다.** 옷장은 사용자 소유 데이터라 주인을 특정할 수 없다.
- **사진을 다시 올리지 않는다.** 접수 때 올린 `outfits/{user}/{analysis}/original.jpg`를
  옷장 job의 원본으로 재사용한다. 그래서 `wardrobe_upload_job.source_s3_key`가 항상
  옷장 버킷의 키인 것은 아니다 — 큐 페이로드가 `source.bucket`/`output.bucket`을
  각각 실어 보내고, 결과물(아이템 크롭·manifest)은 항상 옷장 버킷에 쌓인다.
- **실패해도 평가는 진행된다.** 옷장 등록은 곁가지라 job을 FAILED로 남기고 넘어간다.

크로스 앱 호출은 `services/wardrobe_link.py` 하나에 모여 있다.

### 워커 실행

```bash
python manage.py migrate recommend          # outfit_analysis 테이블
python manage.py run_outfit_worker          # 평가 워커 (compose: outfit-worker)
python manage.py run_outfit_worker --once   # 1건만 처리하고 종료 (디버깅)
python manage.py sweep_stale_analyses --dry-run   # 방치된 작업 확인
python manage.py test apps.recommend
```

**워커는 1대만 띄운다.** 재시작 복구(`recover_stale`)가 processing 큐를 통째로 되돌리기
때문에 2대 이상이면 다른 워커가 처리 중인 작업까지 회수한다. 늘리려면 processing 키를
워커별로 분리해야 한다 (`apps/recommend/services/queue.py` 주석 참고).

워커가 죽어 방치된 행은 프론트를 무한 폴링에 가두므로, 워커 루프가 60초마다
`sweep_stale`을 돌려 `OUTFIT_STALE_AFTER_MINUTES`를 넘긴 작업을 `FAILED`로 정리한다.
별도 크론은 필요 없고, 워커가 내려간 동안 쌓인 것만 `sweep_stale_analyses`로 손으로 치운다.

## 스키마 소유권 (collector 연동)

collector가 쓰는 테이블의 스키마는 전부 Django migration이 관리한다:
`apps/catalog`(naver_product, naver_product_size), `apps/weather`(weather_* 6개).
collector는 raw SQL upsert만 하므로 **모델 변경 시 collector의 INSERT 컬럼 목록도 함께 갱신**해야 한다.
기존에 collector의 init_schema로 테이블이 이미 생성된 DB라면 최초 1회
`python manage.py migrate --fake-initial`로 이력을 동기화한다 (신규 DB는 일반 migrate).
주의: fake-initial은 인덱스/제약 이름을 검사하지 않으므로, 기존 DB의 인덱스 이름이
마이그레이션 정의(`ix_naver_product_tag_status` 등)와 다르면 이후 스키마 변경 시 문제가 될 수 있다.
수집 데이터가 소량이면 볼륨을 초기화하고 신규 migrate로 시작하는 것이 가장 깔끔하다.

## 배포 메모

- prod 실행: `DJANGO_SETTINGS_MODULE=config.settings.prod` (wsgi/asgi 기본값).
- 시크릿은 AWS Secrets Manager/SSM으로 주입. `DJANGO_SECRET_KEY` 없으면 기동 실패하도록 되어 있다.
- 배포 전: `migrate`, `collectstatic`, 헬스체크 확인 (CLAUDE.md 8장).
