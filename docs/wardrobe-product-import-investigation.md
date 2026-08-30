# Wardrobe 상품 가져오기 사전 조사

- 조사일: 2026-08-05
- 작업 브랜치: `product-wardrobe`
- 범위: Wardrobe 저장 계약, 네이버·11번가 상품 수집 구조, Catalog → Wardrobe 변환 위험
- 제외: 모델 설치·실행, GPU 서버 점검, API·DB 스키마 변경

## 1. 결론

현재 저장소에는 다음 기능이 이미 있다.

- 사용자 사진을 S3에 올리고 Redis 작업 큐로 전달하는 Wardrobe 비동기 업로드 API
- 이미지 프로세서의 처리 결과를 받는 내부 callback API
- `wardrobe_item` 저장 및 Qdrant 벡터 동기화
- 네이버 쇼핑 및 11번가 ProductSearch 상품 수집기
- 두 상품 소스의 공통 태깅 필드와 상품 임베딩 작업 API
- Wardrobe API의 Swagger 문서

따라서 상품 수집기를 새로 만들 필요는 없다. 새 기능은 Catalog 상품을 조회해 Wardrobe 저장 형식으로 정규화하고 사용자 옷장으로 가져오는 경로다.

다만 현재 상태에서는 Catalog 태그를 `wardrobe_item`에 그대로 복사하면 안 된다.

1. Catalog의 `color`, `pattern`, `material`은 배열이고 Wardrobe는 단일 문자열이다.
2. collector가 생성할 수 있는 값 중 Wardrobe taxonomy에 없는 값이 있다.
3. Wardrobe serializer는 일부 필드만 taxonomy로 검증한다.
4. `wardrobe_item`에는 원본 쇼핑몰과 외부 상품 ID를 보존할 전용 필드가 없다.
5. API, 이미지 프로세서, 모바일, 테스트 코드에 taxonomy가 복제되어 있어 변경 시 drift 위험이 있다.

모델 비교 전에 이 문서의 데이터 계약과 미결정 사항을 먼저 확정해야 한다.

## 2. 코드 기준 Source of Truth

현재 Wardrobe 저장값의 기준으로 취급해야 할 파일은 다음과 같다.

| 역할 | 파일 |
|---|---|
| DB 모델 | `api/apps/wardrobe/models.py` |
| API taxonomy | `api/apps/wardrobe/taxonomy.py` |
| 입력 검증 | `api/apps/wardrobe/serializers.py` |
| callback 저장 | `api/apps/wardrobe/views.py` |
| 이미지 프로세서 taxonomy | `image-processor/pipeline/taxonomy.py` |
| 모바일 taxonomy | `mobile/src/constants/wardrobe-taxonomy.ts` |

`api/apps/wardrobe/taxonomy.py`를 기준 taxonomy로 보고 나머지 복제본의 일치 여부를 검사하는 것이 안전하다. PostgreSQL 컬럼에는 enum 또는 `CheckConstraint`가 없으므로 DB 자체가 목록 밖 문자열을 차단하지는 않는다.

## 3. WardrobeItem 저장 계약

테이블명은 `wardrobe_item`이다. 이미지·사용자·처리 출처 필드까지 포함한 실제 모델 계약은 다음과 같다.

| 필드 | DB 형식 | 빈 값 | 의도된 허용값 | 현재 callback 검증 |
|---|---|---|---|---|
| `id` | UUID PK | 불가 | 자동 생성 | 저장 시 생성 |
| `user` | users FK | 불가 | 요청 사용자 | callback이 job 사용자로 지정 |
| `job` | upload job FK | `NULL` 가능 | 사진 업로드 처리 job | callback 경로에서는 지정 |
| `s3_key` | varchar(512) | 불가 | S3 객체 키 | 문자열 길이만 검증 |
| `item_name` | varchar(120) | `""` 가능 | 표시용 자유 텍스트 | 길이만 검증 |
| `category_large` | varchar(20) | 불가 | `CATEGORY_LARGE` | enum 검증 |
| `category_small` | varchar(30) | `""` 가능 | 대분류별 `CATEGORY_SMALL` | 값이 있으면 대·소분류 짝 검증 |
| `season` | varchar(10)[] | `[]` 가능 | `SEASONS` | 원소별 enum 검증 |
| `style` | varchar(10)[] | `[]` 가능 | `STYLES` | 원소별 enum 검증 |
| `color` | varchar(10) | `""` 가능 | `COLORS` | 자유 문자열 |
| `pattern` | varchar(10) | `""` 가능 | `PATTERNS` | 자유 문자열 |
| `fit` | varchar(10) | `""` 가능 | `FITS` | 자유 문자열, `null`은 `""`로 정규화 |
| `material` | varchar(10) | `""` 가능 | `MATERIALS` | 자유 문자열, `null`은 `""`로 정규화 |
| `sleeve` | varchar(10) | `""` 가능 | `SLEEVES` | 자유 문자열, `null`은 `""`로 정규화 |
| `length` | varchar(10) | `""` 가능 | `LENGTHS` | 자유 문자열, `null`은 `""`로 정규화 |
| `usage` | varchar(20)[] | `[]` 가능 | 현재 공식 enum 없음 | 자유 문자열 배열 |
| `layer_role` | varchar(10) | `""` 가능 | `LAYER_ROLES` | 자유 문자열, `null`은 `""`로 정규화 |
| `layer_order` | positive smallint | `NULL` 가능 | 문서상 안쪽부터 1 | serializer는 일반 정수만 검증 |
| `seg_meta` | JSON | `{}` 가능 | 분리 결과 메타 | 임의 JSON |
| `confirmed` | boolean | 불가 | 기본 `false` | callback에서 직접 받지 않고 모델 기본값 사용 |

### 검증 공백

`WardrobeItemUpdateSerializer`도 `category_large`와 대·소분류 조합만 별도로 검사한다. 모델 필드에 `choices`가 없기 때문에 사용자 PATCH에서도 `color`, `pattern`, `fit`, `material`, `sleeve`, `length`, `usage`, `layer_role`에 목록 밖 값이 들어갈 수 있다.

`layer_order`는 DB 타입상 음수를 막지만 의도된 범위인 1~3을 API에서 강제하지 않는다.

## 4. Wardrobe taxonomy

### 4.1 대분류와 소분류

| `category_large` | 허용 `category_small` |
|---|---|
| 상의 | 티셔츠, 셔츠/블라우스, 니트/스웨터, 후드/맨투맨, 민소매 |
| 하의 | 데님 팬츠, 슬랙스, 코튼 팬츠, 트레이닝 팬츠, 숏팬츠, 스커트, 레깅스 |
| 아우터 | 자켓, 코트, 패딩, 점퍼/블루종, 가디건, 후드집업, 베스트 |
| 원피스/세트 | 원피스, 점프수트/오버롤, 셋업, 파자마/홈웨어 세트 |
| 신발 | 스니커즈, 구두/로퍼, 부츠, 샌들/슬리퍼, 플랫/단화 |
| 가방 | 백팩, 크로스백, 숄더백, 토트백, 에코백, 클러치/파우치, 지갑 |
| 액세서리 | 모자, 벨트, 주얼리, 머플러/스카프, 양말, 안경/선글라스, 헤어 액세서리 |
| 언더웨어/이너웨어 | 브라, 팬티/드로즈, 런닝/캐미솔, 속바지, 보정속옷, 내복/발열 이너 |

코드상 표준 표기는 `재킷`이 아니라 `자켓`이다.

### 4.2 태그 enum

| 필드 | 허용값 |
|---|---|
| `season` | 봄, 여름, 가을, 겨울, 간절기 |
| `style` | 캐주얼, 포멀, 미니멀, 스트릿, 스포티, 러블리, 페미닌, 시크, 빈티지, 아웃도어, 댄디, 아메카지, 트렌디, 리조트, 베이직 |
| `color` | 화이트, 블랙, 그레이, 네이비, 블루, 스카이블루, 레드, 핑크, 오렌지, 옐로우, 그린, 카키, 브라운, 베이지, 아이보리, 퍼플, 멀티 |
| `pattern` | 무지, 체크, 스트라이프, 도트, 플로럴, 그래픽/로고, 카모, 애니멀 |
| `fit` | 오버핏, 레귤러핏, 슬림핏, 와이드핏 |
| `material` | 코튼, 데님, 니트, 울, 린넨, 레더, 나일론, 폴리에스터, 시폰, 코듀로이, 트위드, 퍼/무스탕, 패딩충전재 |
| `sleeve` | 반팔, 긴팔, 민소매 |
| `length` | 크롭, 기본, 롱 |
| `layer_role` | 기본 상의, 레이어드 상의, 아우터 |
| `usage` | 공식 enum 없음. 현재 프롬프트 예시는 데일리, 외출, 출근, 운동, 홈웨어, 수면, 휴양지 |

### 4.3 필수 필드 정의 불일치

필수값 정책은 코드 사이에서 일치하지 않는다.

- `image-processor/pipeline/taxonomy.py`
  - 공통 필수: `item_name`, 대·소분류, `season`, `style`, `color`, `pattern`
  - 의류 추가 필수: 대분류에 따라 `fit`, `sleeve`, `length`
- `test/test-llm2/common/taxonomy.py`
  - 의류·신발·액세서리: 주로 `season`, `style`, `color`
  - 가방: `style`, `color`
  - 언더웨어/이너웨어: `season`, `usage`
- API serializer
  - `category_large` 외 대부분 빈 값 허용

벤치마크 정답지를 만들기 전에 어느 정책을 운영 기준으로 쓸지 확정해야 한다.

## 5. 기존 상품 수집 구조

### 5.1 네이버

| 항목 | 내용 |
|---|---|
| API | 네이버 쇼핑 검색 JSON API |
| 호출 코드 | `collector/naver/naver_collector_db.py::search_shop` |
| endpoint 설정 | `NAVER_SHOP_API_URL`, 기본값 `https://openapi.naver.com/v1/search/shop.json` |
| 인증 | `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` 헤더 |
| Django 모델 | `api/apps/catalog/models.py::NaverProduct` |
| 테이블 | `naver_product` |
| 외부 상품 ID | `naver_product_id` (`productId`) |
| 대표 이미지 | `image_url` (`image`) |
| 상품 링크 | `link` |
| 저장 코드 | `collector/naver/db.py::upsert_products` |
| 충돌 정책 | `naver_product_id` 충돌 시 상품·태그·메타 갱신 |

수집 흐름은 `검색 키워드 → API 호출 → 패션 카테고리 필터 → 제목 규칙 추출 → 선택적 LLM 태깅 → upsert → 임베딩 작업 등록`이다.

### 5.2 11번가

| 항목 | 내용 |
|---|---|
| API | 11번가 ProductSearch XML API |
| 호출 코드 | `collector/eleven/eleven_collector_db.py::fetch_keyword_products` |
| 인증 | `11ST_API_KEY` |
| Django 모델 | `api/apps/catalog/models.py::ElevenProduct` |
| 테이블 | `eleven_product` |
| 외부 상품 ID | `eleven_product_id` (`ProductCode`) |
| 대표 이미지 | `image_url` (`ProductImage`, `ProductImage300`, `BasicImage` 순서) |
| 상품 링크 | `link` (`ProductDetailUrl`, `DetailPageUrl`) |
| 저장 코드 | `collector/eleven/db.py::insert_products` |
| 충돌 정책 | `eleven_product_id` 충돌 시 `DO NOTHING` |

11번가는 성공·실패 API 원문을 `eleven_api_response`에도 저장한다. 카테고리는 11번가 분류 매핑을 우선하고 실패하면 검색 키워드의 대·소분류를 사용한다.

### 5.3 공통 Catalog 태그

두 모델에 공통으로 다음 필드가 있다.

- 분류: `category_large`, `category_small`, `category_source`
- 태그: `season[]`, `style[]`, `color[]`, `pattern[]`, `fit`, `material[]`, `sleeve`, `length`, `usage[]`, `layer_role`, `layer_order`
- 태깅 메타: `tag_source`, `tagging_status`, `tagging_model`, `tagging_used_image`, `tagged_at`
- 이미지/임베딩 메타: `image_s3_key`, `image_checksum`, `embedding_status`, `embedding_version`

Catalog 내부 임베딩 API는 이미 `source + external_product_id`로 Naver/Eleven 모델을 선택하고 공통 상품 payload를 만든다. 관련 로직은 `api/apps/catalog/services/product_embeddings.py`에 있다. 상품 가져오기 서비스에서도 같은 source dispatch 패턴을 재사용할 수 있다.

## 6. Catalog와 Wardrobe의 값 차이

collector의 규칙 추출기와 LLM 스키마는 Wardrobe보다 넓은 값을 허용한다.

| 필드 | Catalog에서 발생 가능한 비표준 예 | Wardrobe 표준 |
|---|---|---|
| `color` | 옐로, 실버, 골드 | 옐로우 또는 현재 목록 안의 색상 |
| `pattern` | 레오파드, 그래픽, 로고, 아가일, 페이즐리 | 애니멀, 그래픽/로고 등 8종 |
| `fit` | 테이퍼드핏, 스트레이트핏, 부츠컷, 릴렉스핏 | 오버핏, 레귤러핏, 슬림핏, 와이드핏 |
| `material` | 캐시미어, 레이온, 기모, 플리스, 가죽, 스웨이드, 구스다운 등 | 13종 taxonomy |
| `sleeve` | 5부, 7부 | 반팔, 긴팔, 민소매 |
| `length` | 숏, 미니, 미디 | 크롭, 기본, 롱 |
| `layer_role` | 하의, 원피스, 신발, 가방, 액세서리, 이너웨어 | 기본 상의, 레이어드 상의, 아우터 |

추가로 `collector/util/tagging/base.py::merge_tags`가 목록 밖 값을 제거하는 필드는 `season`, `style`, `layer_role`뿐이다. 색상·패턴·핏·소재·소매·기장은 목록 밖 값이 Catalog DB에 저장될 수 있다.

## 7. Catalog → Wardrobe 변환 초안

아래는 구현 전 합의가 필요한 변환 원칙이다.

| Wardrobe 필드 | Catalog 원천 | 제안 |
|---|---|---|
| `item_name` | `title` | HTML 제거된 제목을 120자 이내로 사용 |
| `category_large` | 동명 필드 | Wardrobe enum 검증. 실패하면 저장 중단 또는 모델 재분류 |
| `category_small` | 동명 필드 | 대·소분류 짝 검증. 실패하면 저장 중단 또는 모델 재분류 |
| `season` | 배열 | Wardrobe enum과 교집합만 유지 |
| `style` | 배열 | Wardrobe enum과 교집합만 유지. 최대 개수 정책은 별도 결정 |
| `color` | 배열 | 정규화 후 1개면 해당 색상, 복수 대표색이면 `멀티`, 없으면 모델 보완 |
| `pattern` | 배열 | 정규화 후 단일 대표값 선택 규칙 필요. 안전한 선택이 없으면 모델 보완 |
| `fit` | 문자열 | 정확히 일치하거나 승인된 alias만 변환, 나머지는 모델 보완 또는 빈 값 |
| `material` | 배열 | 정규화 후 대표 소재 선택 규칙 필요 |
| `sleeve` | 문자열 | 정확히 일치하거나 승인된 alias만 변환 |
| `length` | 문자열 | 정확히 일치하거나 승인된 alias만 변환 |
| `usage` | 배열 | 공식 enum 확정 전에는 길이·중복만 정리하거나 그대로 유지 |
| `layer_role` | 문자열 | Wardrobe enum에 없는 역할은 빈 값 처리. taxonomy 확장 여부 별도 결정 |
| `layer_order` | 정수 | 의도한 1~3 범위만 허용 |
| `s3_key` | `image_s3_key` 또는 `image_url` | 기존 S3 이미지가 있으면 재사용 가능성 검토, 없으면 외부 이미지를 검증 후 S3에 저장 |

모델은 모든 필드를 다시 생성하는 용도가 아니라 다음 경우에만 호출하는 것이 적절하다.

- Catalog 값이 비어 있음
- Catalog 값이 Wardrobe taxonomy로 안전하게 정규화되지 않음
- 대·소분류 조합이 맞지 않음
- 이미지 기반 확인이 필요한 필드임

기존 값과 모델 결과를 합친 뒤 최종 payload 전체를 다시 검증해야 한다.

## 8. 상품 출처 보존에 필요한 스키마 검토

현재 `WardrobeItem`은 사진 업로드를 전제로 하므로 `job`, `s3_key`, `seg_meta`는 있지만 Catalog 상품 출처 필드는 없다. `seg_meta`에 상품 출처를 넣는 방식은 조회·중복 방지·인덱싱에 불리하므로 권장하지 않는다.

상품 가져오기 API 구현 전 다음 필드 또는 별도 연결 모델을 검토해야 한다.

- `source`: `naver` 또는 `eleven`
- `external_product_id`: 외부 상품 ID
- `source_url`: 원본 상품 상세 URL
- 선택 사항: Catalog 내부 PK 또는 generic relation이 아닌 명시적 참조 정보
- 중복 방지: `(user, source, external_product_id)` unique constraint

상품이 판매 종료되거나 Catalog에서 갱신되어도 사용자 옷장 아이템은 유지해야 하므로, Wardrobe에는 가져오기 시점의 이름·태그·이미지를 스냅샷으로 보존하는 편이 안전하다.

## 9. 기존 Wardrobe API와 새 API의 경계

현재 API는 다음과 같다.

- `POST /api/v1/wardrobe/uploads/`: 사용자 사진 업로드
- `GET /api/v1/wardrobe/uploads/{job_id}/`: 비동기 처리 상태 조회
- `POST /api/v1/internal/wardrobe/callback/`: 이미지 프로세서 callback
- `GET /api/v1/wardrobe/items/`: 내 옷장 목록
- `PATCH /api/v1/wardrobe/items/{item_id}/`: 태그 수정·확정
- `DELETE /api/v1/wardrobe/items/{item_id}/`: 삭제

상품 가져오기는 사진 업로드와 입력 및 처리 방식이 다르므로 별도 endpoint가 적절하다. 요청 계약 후보는 다음과 같다.

```json
{
  "source": "naver",
  "external_product_id": "123456789"
}
```

응답 동기/비동기 여부는 선택 모델의 실행 위치와 호출 시간에 따라 결정한다. GPU 모델을 즉시 호출한다면 기존 Redis job 패턴을 재사용하는 편이 안전하다.

## 10. 모델 비교 데이터 준비 상태

현재 `test/` 아래 이미지 파일은 49개지만 대부분 `test/output/grounded_sam2/`의 분리 결과물이다. 공통 정답지와 원본별 라이선스 정보가 없어 HyperCLOVA/Qwen 비교 데이터로 바로 쓰기 어렵다.

권장 구조는 다음과 같다.

```text
test/fashion-vlm-benchmark/
├── README.md
├── ground_truth.json
├── prompts/
├── runners/
└── results/
```

이미지는 저작권과 Git 용량을 확인한 뒤 추적 여부를 결정한다. GPU 서버 경로는 `~/fashion_model_test`로 고정하지 않고 CLI 인자 또는 환경변수로 받는다.

최초 10장은 티셔츠, 셔츠, 니트, 자켓, 바지, 스커트, 원피스, 신발, 가방, 패턴 의류를 각각 포함하고 사람이 taxonomy 기준 정답을 검수해야 한다.

## 11. 구현 전에 확정할 사항

1. `usage`의 공식 enum을 만들 것인가.
2. 필수 필드 정책은 API, 이미지 프로세서, `test-llm2` 중 무엇을 기준으로 통합할 것인가.
3. Catalog 배열형 `color`, `pattern`, `material`을 Wardrobe 단일값으로 줄이는 규칙은 무엇인가.
4. collector의 넓은 어휘를 Wardrobe taxonomy로 축소할 alias 표를 만들 것인가, Wardrobe taxonomy를 확장할 것인가.
5. 상품 출처를 `WardrobeItem` 필드로 추가할 것인가, 별도 연결 모델로 둘 것인가.
6. 이미 `image_s3_key`가 있는 Catalog 상품의 S3 객체를 Wardrobe가 공유할지 복사할지 결정해야 한다.
7. 모델 호출이 필요한 상품 가져오기를 동기 API로 할지 기존 job/queue 구조를 재사용할지 결정해야 한다.
8. 사용자가 같은 상품을 중복 등록했을 때 기존 아이템 반환, 409, 새 스냅샷 생성 중 어떤 정책을 쓸지 결정해야 한다.

## 12. 권장 다음 순서

1. 위 8개 정책 결정
2. taxonomy 검증 함수를 공통 서비스로 정리하고 serializer 검증 누락 보완
3. 10장 벤치마크 정답지와 공통 프롬프트 준비
4. PuTTY로 GPU 서버 상태 확인
5. HyperCLOVA smoke test 후 전체 실행
6. 같은 입력으로 Qwen 실행 및 비교
7. 선택 모델 adapter 구현
8. 상품 가져오기 모델·서비스·API·Swagger·테스트 구현
9. 전체 등록 흐름 완성 후 Agent 개발
