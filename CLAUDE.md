# CLAUDE.md

이 문서는 Claude 및 AI 코딩 어시스턴트가 **SKN28-FINAL-1Team** 프로젝트에서 작업할 때 따라야 할 기초 지침이다. 사람 팀원에게도 프로젝트 개요와 규칙의 단일 진실 공급원(Single Source of Truth) 역할을 한다.

---

## 1. 프로젝트 개요

- **목표**: 사용자에게 개인화된 패션을 추천하는 AI 시스템 구축
- **핵심 가치**: 추천 정확도, 응답 속도, 확장 가능한 배포 구조
- **배포 환경**: AWS (프로덕션)
- **GPU 환경**: RunPod GPU (모델 학습·추론 실험용 **임시** 환경)

> RunPod은 AWS GPU 비용을 아끼기 위한 임시 개발/학습 환경이다. 최종 서비스는 AWS로 이관하는 것을 전제로, RunPod에 종속적인 코드(하드코딩된 경로, 로컬 전용 설정 등)를 작성하지 않는다.

---

## 2. 기술 스택

| 영역 | 기술 |
|------|------|
| 언어 | Python 3.11+ |
| 백엔드 | Django / Django REST Framework |
| AI/ML | PyTorch (모델 학습·추론), 추천 모델 |
| 데이터 | PostgreSQL (권장), Redis (캐시), Qdrant (벡터 검색) |
| GPU (임시) | RunPod |
| 배포 | AWS (EC2 / ECS / S3 / RDS 등) |
| 형상관리 | Git |

> 스택 세부 사항(DB, 추천 알고리즘 등)은 확정되는 대로 이 표를 갱신한다.

---

## 3. 디렉터리 구조

```
SKN28-FINAL-1Team/
├── CLAUDE.md              # 본 문서 (기초 지침)
├── README.md              # 프로젝트 소개·실행법
├── .env.example           # 환경변수 템플릿 (실제 .env는 커밋 금지, 루트 .env 하나로 통합 관리)
├── .gitignore
├── docker-compose.yml     # 통합 compose: db + qdrant + migrate + api + collector 2종 (profiles로 선택 실행)
├── api/                   # Django REST API 서버
│   ├── manage.py
│   ├── requirements.txt
│   ├── config/            # 프로젝트 설정 (urls, wsgi/asgi)
│   │   └── settings/      # base.py / dev.py / prod.py 분리
│   └── apps/              # Django 앱 모음
│       ├── users/         # 사용자·인증 (naver/kakao/google OAuth + JWT)
│       ├── catalog/       # 상품 (naver_product 등, collector/naver가 사용)
│       ├── weather/       # 날씨 (weather_* 테이블, collector/weather가 사용)
│       ├── recommend/     # 추천: Qdrant 클라이언트·컬렉션 스키마 소유 (manage.py init_qdrant)
│       ├── style_calendar/ # 착장 캘린더 (하루 한 건, 사진→옷장 job 재사용)
│       └── lookbook/      # 룩북 (날짜 없이 여러 건, 겹치는 부위는 사진 등록 제외)
├── collector/             # 독립 실행 데이터 수집기 (스키마는 Django migration이 소유)
│   ├── weather/           # 기상청 APIHub 수집
│   └── naver/             # 네이버 쇼핑 상품 수집 + LLM 태깅
├── indexer/               # 임베딩 → Qdrant 적재 GPU worker (util/ 공용 · product_indexer/ 운영 · old/ 레거시)
│                          # product_indexer/text_embedding_api.py: 채팅 질의문 → BGE-M3 벡터 HTTP API
├── ml/                    # 모델 학습·추론 코드 (예정)
├── scripts/               # 배포·데이터 처리 스크립트
└── docs/                  # 설계·아키텍처 문서
```

> **스키마 소유권**: 모든 테이블(collector가 쓰는 테이블 포함)의 DDL은 Django migration이 관리한다.
> collector는 raw SQL upsert만 수행하며, 테이블 컬럼 변경 시 Django 모델·마이그레이션과
> collector의 INSERT 컬럼 목록을 함께 갱신한다.

---

## 4. 개발 환경 세팅

```bash
# 가상환경
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 의존성
pip install -r requirements.txt

# 환경변수
cp .env.example .env             # 값 채우기

# DB 마이그레이션 & 실행
python manage.py migrate
python manage.py runserver
```

- 새 라이브러리를 추가하면 반드시 `requirements.txt`(또는 `pyproject.toml`)에 반영한다.
- 환경변수는 코드에 하드코딩하지 않고 `.env` / AWS 파라미터 스토어에서 읽는다.

---

## 5. 코딩 규칙

- **스타일**: PEP 8 준수. 포매터 `black`, 린터 `ruff`(또는 `flake8`) 사용.
- **명명**: 변수·함수 `snake_case`, 클래스 `PascalCase`, 상수 `UPPER_SNAKE_CASE`.
- **타입 힌트**: 함수 시그니처에 타입 힌트를 작성한다.
- **Django 관례**: fat model / thin view 지향, 비즈니스 로직은 서비스 계층 또는 모델 메서드로.
- **DB 테이블 명명**: 새 Django 모델에는 반드시 `Meta.db_table`을 명시한다 (기존 예: `users`, `naver_product`, `weather_area`, `wardrobe_item`). 기본 규칙(`<앱라벨>_<모델명소문자>`)에 맡기면 모델명에 도메인 접두사가 있는 경우 `wardrobe_wardrobeitem`처럼 문구가 중복된다. migrate 전에 `python manage.py sqlmigrate <앱> <번호>`로 실제 생성될 테이블 이름을 확인한다.
- **DB comment 필수**: 새 모델(테이블)에는 `Meta.db_table_comment`, 새 필드(컬럼)에는 `db_comment`를 **반드시** 작성한다 — DB 툴에서 스키마만 봐도 의미가 읽히게 하는 것이 목적이며, 전 테이블·컬럼 comment는 users 0009·0010 / catalog 0003 / weather 0002 / wardrobe 0003 마이그레이션으로 일괄 적용돼 있다.
  - comment는 한글로, "무엇인지 + 단위/코드값/제약"을 담는다. 예) `db_comment="허리둘레(cm)"`, `db_comment="측정 상태 (in_progress/succeeded/failed)"`.
  - 모델로 커버되지 않는 컬럼(자동 `id` PK, 자동 생성 M2M through 테이블 등)을 만들면 해당 마이그레이션에 `migrations.RunSQL`로 `COMMENT ON` 구문을 함께 추가한다 (`reverse_sql`은 `IS NULL`). 기존 예: `apps/users/migrations/0010_system_table_comments.py`.
  - ⚠️ comment 문자열에 `%` 문자를 쓰지 않는다 (psycopg 파라미터 보간과 충돌 — "백분율"로 풀어 쓴다). migrate 전 `sqlmigrate`로 COMMENT 구문 생성 여부를 확인한다.
- **설정 분리**: `settings/base.py`를 공통으로 두고 `dev`/`prod`로 분리. 시크릿은 설정 파일에 직접 쓰지 않는다.
- **주석/문서화**: 왜(why) 그렇게 했는지 위주로 작성. 자명한 코드에 불필요한 주석 금지.

---

## 6. Git / 협업 규칙

- **브랜치 전략**
  - `main`: 배포 가능한 안정 브랜치 (직접 push 금지)
  - `develop`: 통합 개발 브랜치
  - `feature/<이름>-<기능>`: 기능 단위 작업 브랜치
- **커밋 메시지** (Conventional Commits 권장)
  - `feat:` 기능 추가 / `fix:` 버그 수정 / `docs:` 문서 / `refactor:` 리팩터링 / `test:` 테스트 / `chore:` 설정·기타
  - 예) `feat(recommend): 사용자 임베딩 기반 추천 API 추가`
- **PR**: 리뷰어 1명 이상 승인 후 머지. 작은 단위로 자주 올린다.
- **금지**: `.env`, 모델 가중치, 대용량 데이터, 개인정보 커밋 금지.

---

## 7. AI/ML 작업 지침

- 모델 코드는 `ml/`에 두고, Django 앱은 추론 인터페이스를 통해 호출한다(웹 계층과 ML 계층 분리).
- **재현성**: 랜덤 시드 고정, 데이터 버전·하이퍼파라미터를 기록한다.
- **모델 가중치**: Git에 커밋하지 않는다. S3 등 오브젝트 스토리지에 저장하고 경로/버전으로 참조한다.
  - **예외**: `ml/body_measurement/artifacts/models/*.joblib`. 신체치수 추정 API(`/body/estimate`, `/body/photos`)가 서빙 시 직접 읽어야 해서 `.gitignore`에 예외 규칙을 뒀다. 서빙에 쓰는 건 `hist_gradient_boosting.joblib`(7.6MB) 하나이며, `random_forest.joblib`은 51MB라 커밋하면 저장소가 무거워진다. 배포 환경에서는 `BODY_MODEL_PATH`로 S3에서 받은 경로를 주입할 수 있다.
  - ⚠️ 이 아티팩트는 scikit-learn 1.8.0으로 저장돼 있어 **실행 환경도 1.8.0이어야 한다** (1.9.0에서 언피클 실패). 재학습하면 `api/requirements.txt`의 핀도 함께 올린다.
- **RunPod ↔ AWS 이식성**: 경로·디바이스(`cuda`/`cpu`)·자격증명을 환경변수로 추상화한다. RunPod 전용 하드코딩 금지.
- **추론 성능**: 배치 처리·캐싱(Redis)·모델 워밍업을 고려한다.

---

## 8. 배포 (AWS) 지침

- **프로덕션 = AWS**를 전제로 설계한다.
- 시크릿은 AWS Secrets Manager / SSM Parameter Store에서 주입.
- 정적/미디어 파일은 S3, DB는 RDS(PostgreSQL) 사용 권장.
- 컨테이너화(Docker) 후 ECS/EC2 배포를 기본 가정. 이미지에 시크릿을 포함하지 않는다.
- 환경 구분: `dev` / `staging` / `prod` 설정과 자격증명을 분리한다.
- 배포 전 체크: 마이그레이션 적용, 정적 파일 수집(`collectstatic`), 헬스체크 엔드포인트 확인.

---

## 9. 테스트 & 검증

- 신규/변경 로직에는 테스트를 추가한다 (`pytest` 또는 Django `TestCase`).
- PR 전 로컬에서 테스트·린트·포맷 통과를 확인한다.
- 추천 결과는 정량 지표(예: precision@k, recall@k)로 평가한다.

---

## 10. AI 어시스턴트(Claude) 작업 규칙

- **작업 언어**: 한국어로 소통한다.
- **변경 범위**: 요청 범위를 벗어난 대규모 리팩터링을 임의로 하지 않는다. 필요하면 먼저 제안한다.
- **시크릿 보호**: 자격증명·개인정보·`.env` 값을 코드나 로그에 노출하지 않는다.
- **이식성 우선**: RunPod 전용, 로컬 전용 코드를 지양하고 AWS 이관을 항상 염두에 둔다.
- **불확실할 때**: 스택·설계가 확정 안 된 부분은 추측 대신 확인을 요청한다.
- **문서 갱신**: 구조·스택·규칙이 바뀌면 이 CLAUDE.md를 함께 업데이트한다.

---

_마지막 업데이트: 2026-08-07_
