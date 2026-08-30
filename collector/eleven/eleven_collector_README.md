# 11번가 ProductSearch collector

11번가 ProductSearch XML API로 상품을 수집하고 PostgreSQL에 upsert한다.
스키마는 Django catalog migration이 소유하며 collector는 DDL을 실행하지 않는다.

## 수집 및 태깅 흐름

1. 카테고리조회 응답을 `eleven_category`에 동기화한다.
2. 패션 키워드로 ProductSearch를 호출한다.
3. 카테고리 경로를 공통 추천 분류로 매핑한다.
4. 제목 규칙 속성을 추출한다.
5. `.env`의 provider와 mode에 따라 태깅한다.
6. 원본 XML은 `eleven_api_response`, 상품은 `eleven_product`에 저장한다.
7. 신규 INSERT 상품만 `product_embedding_job`에 등록한다.
8. 태깅 완료 후 외부 GPU `product-indexer` API에 drain 시작 신호를 보낸다.
9. GPU worker가 S3 이미지와 상품 텍스트를 임베딩해 Qdrant에 적재한다.

| provider | mode | 동작 |
|---|---|---|
| `openai` | `batch` (기본) | pending 저장 → OpenAI Batch 제출 → 스케줄러 폴링 → 결과 반영 |
| `openai` | `sync` | 수집 중 상품별 동기 태깅 |
| `claude` | `sync` | Claude Agent SDK로 상품별 동기 태깅 |

Claude에 `batch`를 설정하면 경고 후 자동으로 `sync`로 전환된다. Batch 요청 생성과
결과 파싱은 공용 `collector/util/tagging/openai_batch.py`를 사용한다.

Batch 상태 흐름은 `pending → queued → tagged | failed`이며 작업 이력은
`eleven_tagging_batch`에 저장한다.

상품 임베딩 작업은 collector 안에서 모델을 실행하지 않는다. 상품 INSERT와 같은
DB 트랜잭션에서 작업만 등록하고 GPU worker가 비동기로 처리한다. Batch 태깅 결과가
반영되면 작업 행이 있는 신규 상품만 재색인하며 기존 DB 상품은 자동 백필하지 않는다.
상세 실행법은 `indexer/product_indexer/PRODUCTS_README.md`를 참고한다.

작업 큐(`product_embedding_job`)는 네이버와 공용이지만 S3 prefix
(`PRODUCT_ELEVEN_IMAGE_S3_PREFIX`, 기본 `products/eleven`)와 Qdrant 컬렉션
(`PRODUCT_ELEVEN_QDRANT_COLLECTION`, 기본 `products_eleven_v1`)은 분리돼 있다.
drain 트리거의 `source=eleven`이 11번가 작업만 선점하므로 네이버 drain과
동시에 실행된다.

원격 GPU trigger 설정:

    PRODUCT_INDEXER_TRIGGER_URL=https://<gpu-host>/v1/product-indexer/drain
    PRODUCT_INDEXER_TRIGGER_TOKEN=<shared-secret>
    PRODUCT_INDEXER_TRIGGER_TIMEOUT_SECONDS=10
    PRODUCT_INDEXER_TRIGGER_MAX_RETRIES=2

URL이 비어 있으면 원격 trigger만 비활성화되고 기존 수집·태깅·DB 작업 등록은
그대로 동작한다. sync는 수집과 태깅 저장 후, Batch는 완료 결과 반영 후 한 번
호출한다. GPU API 호출 실패는 저장된 상품을 롤백하지 않는다.

## 환경 설정

OpenAI Batch:

    ELEVEN_TAGGING_PROVIDER=openai
    ELEVEN_TAGGING_MODE=batch
    OPENAI_API_KEY=...
    OPENAI_MODEL=gpt-4o-mini
    ELEVEN_BATCH_MAX_REQUESTS=10000
    ELEVEN_BATCH_POLL_SECONDS=600
    ELEVEN_BATCH_COMPLETION_WINDOW=24h
    ELEVEN_BATCH_INCLUDE_IMAGE=false

OpenAI 동기 태깅:

    ELEVEN_TAGGING_PROVIDER=openai
    ELEVEN_TAGGING_MODE=sync
    OPENAI_API_KEY=...

Claude 동기 태깅:

    ELEVEN_TAGGING_PROVIDER=claude
    ELEVEN_TAGGING_MODE=sync
    CLAUDE_CODE_OAUTH_TOKEN=...
    ELEVEN_CLAUDE_MODEL=
    INSTALL_CLAUDE_CLI=true

`ANTHROPIC_API_KEY`를 설정하면 OAuth 대신 Anthropic API 과금으로 Claude를 사용할 수
있다. Claude 태거는 텍스트 전용이다.

## 실행

Django migration을 먼저 적용한다.

    docker compose --profile eleven up -d --build eleven-collector

일회성 수집은 설정된 mode를 따른다. Batch mode이면 수집 후 자동 제출한다.

    python eleven_collector_db.py --job collect --keyword "반팔 티셔츠" --limit 30

Batch 수동 제출과 결과 확인:

    python eleven_collector_db.py --job batch-submit
    python eleven_collector_db.py --job batch-poll

pending/failed 상품을 선택한 provider로 즉시 동기 재태깅:

    python eleven_collector_db.py --job retag --limit 30

LLM 호출 없이 수집만 확인:

    python eleven_collector_db.py --job collect --keyword "반팔 티셔츠" --limit 1 --skip-llm

`--scheduler`는 매일 카테고리 동기화와 상품 수집을 실행한다. 한 번의 일일 수집은
DB에 새로 INSERT된 상품 기준 `ELEVEN_DAILY_MAX_ITEMS`(기본 1,000건)에서 멈춘다.
같은 실행에서 반복된 상품 ID와 DB에 이미 존재하는 상품은 태깅·저장·일일 수량
계산에서 제외한다. INSERT 시점에 충돌한 상품도 `ON CONFLICT DO NOTHING`으로
건너뛰므로 기존 상품을 새 상품으로 잘못 계산하지 않는다.

키워드당 조회는 최대 `ELEVEN_MAX_ITEMS_PER_KEYWORD`(기본 및 최대 50건)다.
스케줄러는 대분류가 번갈아 나오도록 키워드를 배치하고, 날짜마다 예상 일일 처리
키워드 수만큼 시작 위치를 이동한다. 따라서 매일 같은 티셔츠 키워드부터 시작하지
않고 전체 키워드를 순환한다. 중복 상품 때문에 1,000건에 못 미치면 다음 순환
키워드까지 계속 조회한다.

수동 `--job collect`의 `--limit`도 키워드당 최대 50건 안에서 동작한다. Batch
mode에서는 `ELEVEN_BATCH_POLL_SECONDS` 간격으로 진행 중 Batch 결과를 자동
확인한다.

`INSTALL_CLAUDE_CLI`는 이미지 빌드 옵션이므로 값을 변경한 뒤에는 이미지를 다시
빌드해야 한다. API 키는 요청 원문이나 로그에 저장하지 않는다.
