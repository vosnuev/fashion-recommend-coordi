# 네이버 쇼핑 의류 상품 Collector

네이버 쇼핑 검색 API로 의류 상품을 수집하고, 컨플루언스
["의류 상품 데이터 카테고리-태그 매핑 문서"](https://jjeoe0317.atlassian.net/wiki/spaces/SKN281team/pages/14286849)
스키마에 맞게 분류·태깅해서 PostgreSQL에 저장한다.

## 파이프라인

```
키워드 자동 생성 (keywords.py, 문서 소분류 기반)
  → 네이버 쇼핑 검색 API 호출 + 페이징 (naver_collector_db.py)
  → 노이즈 필터 (category1 ∉ {패션의류, 패션잡화} 제외) + productId 중복 제거
  → 네이버 category1~4 → 문서 분류 매핑 (category_mapping.py, 실패 시 키워드 분류)
  → title 규칙 추출: color/fit/sleeve/pattern/material/length (attribute_extractor.py)
  → LLM 태깅: season/style/usage/layer_* + 누락 속성
  → naver_product upsert
  → 신규 INSERT 상품만 product_embedding_job 등록
  → 태깅 완료 후 외부 GPU product-indexer API에 drain 시작 신호
  → GPU worker가 S3 → FashionSigLIP+BGE-M3 → Qdrant 처리
```

원격 GPU trigger는 다음 환경변수로 설정한다.

```dotenv
PRODUCT_INDEXER_TRIGGER_URL=https://<gpu-host>/v1/product-indexer/drain
PRODUCT_INDEXER_TRIGGER_TOKEN=<shared-secret>
PRODUCT_INDEXER_TRIGGER_TIMEOUT_SECONDS=10
PRODUCT_INDEXER_TRIGGER_MAX_RETRIES=2
```

태깅이 끝나면 `trigger_product_indexer(source="naver", ...)`가 위 URL로 drain
시작 신호만 보낸다(상품 데이터·이미지는 보내지 않는다). GPU worker는 payload의
`source=naver`를 보고 네이버 작업만 선점하므로 11번가 drain과 동시에 실행된다.
임베딩 결과는 네이버 전용 Qdrant 컬렉션(`PRODUCT_NAVER_QDRANT_COLLECTION`,
기본 `products_naver_v1`)에 적재되고, 상품 이미지는
`PRODUCT_NAVER_IMAGE_S3_PREFIX`(기본 `products/naver`) 아래에 보존된다.
상세는 `indexer/product_indexer/PRODUCTS_README.md`를 참고한다.

URL이 없으면 trigger만 비활성화된다. sync 수집·재태깅 완료 후와 Batch 결과 반영
후에 호출하며, GPU API 장애가 상품 저장을 롤백하지 않는다.

## 태깅 provider (`NAVER_TAGGING_PROVIDER`)

태깅 엔진(프롬프트/스키마/병합)은 **공용 패키지 `collector/util/tagging`** 에 있다.

| provider | 인증 | 모드 | 이미지 | 비고 |
| --- | --- | --- | --- | --- |
| `openai` (기본) | `OPENAI_API_KEY` | sync / batch | 지원 | Batch API 50% 할인 |
| `claude` | `CLAUDE_CODE_OAUTH_TOKEN` (구독제 계정, `claude setup-token` 발급) | sync 전용 | 미지원 | API 과금 대신 구독 사용량 소진. `claude-agent-sdk` + Claude Code CLI 필요 |

claude provider로 batch 모드를 설정하면 sync로 자동 전환된다(경고 로그).
Docker에서 claude provider를 쓰려면 `.env`에 `INSTALL_CLAUDE_CLI=true` 설정 후
`docker compose build naver-collector`로 재빌드한다 (Claude Code CLI 포함).

**openai(batch) → claude 전환 절차**: 진행 중 배치가 있으면 먼저 소진해야 한다.
`--job batch-poll`을 완료될 때까지 실행(전환 후에도 poll은 동작) → queued 상품이 0이 된 것을
확인한 뒤 `NAVER_TAGGING_PROVIDER=claude`로 운영한다.

## 태깅 모드 (`NAVER_TAGGING_MODE`)

| 모드 | 동작 | 비용 | 지연 |
| --- | --- | --- | --- |
| `batch` (기본) | pending으로 저장 → OpenAI **Batch API** 제출 → 폴링 후 반영 (batch_tagger.py) | **50% 할인** | 보통 수십 분, 최대 24h |
| `sync` | 수집 중 상품별 실시간 태깅 (util/tagging, openai는 이미지 auto 재시도 포함) | 정가 | 즉시 |

batch 모드 상태 흐름: `pending → queued(제출됨) → tagged / failed`.
배치 이력은 `naver_tagging_batch` 테이블(Django migration `catalog/0002` 소유)에 기록되고,
스케줄러가 `NAVER_BATCH_POLL_SECONDS`(기본 10분)마다 자동 폴링한다.
배치 실패/누락으로 남은 `queued` 상품은 진행 중 배치가 없을 때 자동으로 `pending` 복구된다.
배치는 조건부 이미지 재시도가 불가능하므로 `NAVER_BATCH_INCLUDE_IMAGE`로 이미지 포함을 고정한다(기본 텍스트만).

## 테이블

| 테이블 | 역할 |
| --- | --- |
| `naver_product` | 상품 원본 + 문서 분류/태그. `naver_product_id` unique upsert |
| `naver_product_size` | 사이즈별 치수/측정값 하위 테이블 (`product_id` FK). 네이버 검색 API는 치수를 제공하지 않으므로 상세페이지 수집·수동 입력 등 별도 경로로 채운다 |
| `product_embedding_job` | 신규 네이버·11번가 상품의 비동기 임베딩 작업. 기존 DB 상품은 자동 등록하지 않음 |

**스키마 소유권**: 테이블 DDL은 Django migration(`api/apps/catalog`)이 관리한다.
collector는 raw SQL upsert만 수행하며, 시작 시 테이블 존재를 확인하고 없으면 migrate 안내와 함께 종료한다.
컬럼 변경 시 catalog 모델 + 마이그레이션 + `db.PRODUCT_COLUMNS`를 함께 갱신한다.

태깅 메타: `tag_source`(필드별 rule/llm 출처), `tagging_status`(pending/tagged/failed),
`tagging_used_image`(이미지 태깅 여부).

임베딩 메타: `embedding_status`, `embedding_version`, `embedding_retry_count`,
`embedding_error`, `image_s3_key`, `image_checksum`, `image_embedded_at`,
`text_embedded_at`, `embedded_at`. Batch 태깅 완료 시 작업 행이 있는 신규 상품만
generation을 증가시켜 텍스트를 다시 색인한다.

## 실행

```bash
pip install -r requirements.naver.txt
cp ../../.env.example ../../.env   # 값 채우기 (NAVER_*, OPENAI_API_KEY, POSTGRES_*)

# 스키마는 Django migration이 관리: api/에서 `python manage.py migrate` 선행 필수
python naver_collector_db.py --job collect            # 수집 (+batch 모드면 배치 제출까지)
python naver_collector_db.py --job collect --category-large 상의
python naver_collector_db.py --job collect --keyword "린넨 셔츠" --limit 30 --dry-run
python naver_collector_db.py --job collect --skip-llm # 태깅 없이 수집만 (pending 저장)
python naver_collector_db.py --job batch-submit       # pending 상품 배치 제출
python naver_collector_db.py --job batch-poll         # 배치 상태 확인·완료분 반영
python naver_collector_db.py --job retag              # pending/failed 동기 재태깅
python naver_collector_db.py --scheduler              # 매일 03:00 자동 수집 + 배치 폴링
```

Docker (환경변수는 **프로젝트 루트의 `.env`** 하나로 관리, compose가 `env_file: ../../.env`로 참조):

```bash
docker compose -f collector/naver/docker-compose.naver.yml up -d --build
# collector/naver 안에서 실행해도 동일하게 루트 .env를 사용한다.
```

## LLM 이미지 태깅 (`NAVER_LLM_IMAGE_MODE`)

- `auto`(기본): 상품명만으로 style/color 판단이 안 되는 경우에만 상품 이미지를 포함해 재시도 → 비용 최소화
- `always`: 항상 이미지 포함 (정확도↑ 비용↑)
- `never`: 텍스트만

## API 제약 (네이버 쇼핑 검색)

- 키워드당 최대 약 1,000건 (`display` 100, `start` ≤ 1000) → 커버리지는 키워드 다양화로 확보.
  `.env`의 `NAVER_KEYWORD_GENDER_PREFIXES=남성,여성` 으로 키워드를 성별 확장할 수 있다.
- 일 25,000 호출 한도. 429 응답 시 지수 백오프 재시도.
- 응답 필드는 title/link/image/lprice/hprice/mallName/productId/productType/brand/maker/category1~4 뿐이므로
  season/style/usage 등 문서 태그는 규칙 추출 + LLM 태깅으로 생성한다.

## 운영 이관 메모 (CLAUDE.md 규칙)

- 스키마는 Django migration(`api/apps/catalog`)으로 관리 완료. collector에 DDL 없음.
- 시크릿은 .env → AWS Secrets Manager/SSM으로 대체. 코드에 하드코딩 금지.
- RunPod/로컬 전용 경로 없음. DB 접속은 전부 환경변수로 추상화되어 있다.
