# 골든셋 판단 지식 + 보조 점수 앵커 파일럿

골든 이미지를 추천 예시로 직접 노출하지 않고, 이미지에서 확인되는 관계를 사람이
검수한 뒤 조건부 패션 판단 지식으로 만드는 오프라인 파이프라인이다. 메인 산출물은
설명 가능한 조건부 원칙이며, 쌍대 비교에서 얻는 `Q` 상대 점수는 보조 앵커다.

```text
이미지 → VLM 관찰·claim 초안 → 2인 선택형 검수 → 승인 claim
      → 텍스트 LLM 원칙 합성 → 2인 원칙 검수 → 설명 지식 RAG
      └→ 2인 쌍대 비교 → Q 보조 점수 앵커
                        └→ S3 발행 → 코디 payload(human_score) → 추천 랭킹
```

## 빠른 실행 (컨테이너)

원본 코디 사진의 소유자는 S3 버킷이다. 컨테이너는 기동하자마자 지정 prefix를
훑어 아직 처리되지 않은 사진을 찾고, 코디 임베딩 → 아이템 분리·태깅·임베딩 →
Qdrant 적재까지 한 번에 진행한다.

```bash
docker compose -f docker-compose.gpu.yml up -d --build golden-set
docker compose -f docker-compose.gpu.yml logs -f golden-set
```

`GOLDEN_SCAN_INTERVAL_SECONDS=0`(기본)이면 1회 처리 후 종료하므로 배치 잡처럼
필요할 때만 올리면 되고, 양수로 두면 그 간격으로 상주 스캔한다.

"미처리" 판단은 두 층이다.

- 코디 임베딩: `image_embeddings.npz`의 sha 캐시 (로컬 run 볼륨)
- 아이템: `{GOLDEN_S3_OUTPUT_PREFIX}/{version}/{golden_id}/manifest.json` (S3)

로컬 run 볼륨이 날아가도 가장 비싼 단계(아이템별 이미지 편집 호출)는 다시 돌지
않는다.

리포에서 직접 돌릴 때는 아래와 같다.

```bash
python -m ml.golden_set.runner --once
```

## 진행 상황 확인 웹

임베딩이 어디까지 갔는지 브라우저로 본다.

```bash
./run_goldenset.sh            # infisical(dev) → .env → 웹 기동
open http://localhost:8081/
```

api 컨테이너와 같은 호스트에서 도는 것을 전제로 포트를 8081로 잡았다
(8000 api, 8080 product-indexer와 겹치지 않게). 바꾸려면 `.env`의
`GOLDEN_WEB_HOST_PORT`만 고치면 되고 컨테이너 내부 포트는 8081 고정이다.

페이지가 보여주는 것.

- 원본 처리 진행률 (S3 원본 수 대비 완료 수, 대기·구버전 스키마)
- Qdrant 포인트 수 (코디 / 아이템 / 원칙, 데이터셋 버전 기준)
- 마지막 실행 요약 (임베딩 신규·재사용, 모델, 아이템 수, 적재 여부)
- 코디 목록과 아이템 상세 (원본·아이템 이미지 presigned 미리보기)

API는 같은 포트에서 함께 뜬다.

| 경로 | 내용 |
|---|---|
| `GET /health` | 헬스체크 (인증 없음) |
| `GET /api/status` | 데이터셋·진행률·Qdrant·마지막 실행 |
| `GET /api/outfits` | 코디 목록 (미처리 원본 포함) |
| `GET /api/outfits/{golden_id}` | 아이템 상세 + 미리보기 URL |
| `GET`·`POST /api/scan` | 스캔 상태 조회 / 1회 실행 |

노출 관련 기본값 두 가지를 알아둘 것.

- `GOLDEN_WEB_TOKEN`이 비어 있으면 **무인증**으로 뜬다(기동 로그에 경고).
  사설망 밖에 둔다면 반드시 채운다. 토큰은 세 자리 중 아무 데나 실으면 되고
  하나라도 맞으면 통과한다: `?token=`, `Authorization: Bearer`,
  `X-Golden-Token`.

  리버스 프록시 뒤에 둘 때 주의. Cloudflare Access 같은 계층은 자기 JWT를
  `Authorization: Bearer`에 끼워 넣는다. 그 자리만 보고 판정하면 올바른
  `?token=`을 줘도 401이 나므로, 세 자리를 모두 확인하도록 되어 있다.
  프록시가 `Authorization`을 점유하는 환경에서는 `X-Golden-Token`을 쓰면 된다.

  토큰에 `+` `/` `=` 가 들어가면 URL 인코딩이 필요하다(쿼리에서 `+`는 공백으로
  해석된다). `openssl rand -hex 24`처럼 16진수로 만들면 이 문제가 없다.
- `POST /api/scan`은 `GOLDEN_WEB_ALLOW_SCAN=1`일 때만 동작한다. 이 스택은 GPU가
  없는 API 서버에 있어 기본은 읽기 전용이고, 실제 임베딩은 GPU 스택이 돈다.
  켜고 싶으면 `SCAN=1 ./run_goldenset.sh`로 1회 배치를 돌리는 편이 낫다.

웹은 GPU 호스트의 run 디렉터리를 볼 수 없다. 그래서 상태를 **S3와 Qdrant에서만**
읽는다. 임베딩 메타(모델·신규/재사용 건수)는 러너가 사이클 끝에 남기는
`{GOLDEN_S3_OUTPUT_PREFIX}/{version}/run_summary.json`으로 전달된다.

### 환경변수 충돌 주의

이 이미지는 아이템 분리를 위해 image-processor를 함께 담고 있고, image-processor의
`config.py`는 접두사 없는 `DEVICE`·`WORKER_*`를 읽는다. 루트 `.env` 하나를 옷장
워커와 공유하므로 같은 이름이 다른 의미가 될 수 있다. `docker-compose.golden_set.yml`의
`environment` 블록이 컨테이너 안에서 값을 못 박아 이를 끊는다.

| 변수 | 골든셋 컨테이너에서 | 왜 |
|---|---|---|
| `DEVICE`, `GOLDEN_DEVICE` | `cpu` 고정 | 두 규약이 다르다. golden은 `auto`가 자동, image-processor는 **빈 값**이 자동이라 `auto`를 넘기면 `.to("auto")`로 죽는다 |
| `WORKER_PIPELINE` | `GOLDEN_ITEM_PIPELINE` 값 | 아이템 파이프라인 선택의 단일 출처 |
| `WORKER_EMBED_ENABLED` | `1` 고정 | 0이면 `goldenset_items`에 조용히 0건이 들어간다 |
| `WARDROBE_EMBEDDING_VERSION` | (그대로) | 임베더의 버전 라벨. 골든 아이템에는 `GOLDEN_EMBEDDING_VERSION`이 우선 적용돼 옷장 이름표가 찍히지 않는다 |

`REDIS_URL`·`WARDROBE_*`도 읽히지만 이 컨테이너는 `worker.py`를 실행하지 않아
큐·콜백 경로를 타지 않는다.

## 설계 경계

- 한 이미지는 구조화 멀티모달 호출 한 번으로 관찰·영역·관계·최소 수정 가설을 함께 만든다.
- 사람은 좋은 이유를 처음부터 서술하지 않는다. 모델의 최대 3개 claim을 선택형으로 판정한다.
- 모델 confidence는 사람 검수표에서 숨겨 독립 판단이 끌려가지 않게 한다.
- 단일 이미지의 claim은 곧바로 일반 원칙이나 채점 기준이 될 수 없다.
- `P` 개인 취향, `C` 상황 적합도, `Q` 스타일 의도 내 실행 품질을 섞지 않는다.
- 이미지 쌍대 비교는 `Q_OVERALL_STYLE_EXECUTION`만 측정한다. 사용자 `P`와 `C`는 별도 입력이다.
- 이미지 원본은 Git에 커밋하지 않고 비공개 S3 또는 무시된 로컬 경로에 둔다.
- 파일럿 이미지와 앵커는 사용자 응답에 노출하지 않는다.

## A1~A8 판단 축

| 축 | 의미 | 이미지 단독 기본 처리 |
|---|---|---|
| A1 | 색 조화 | 판정 |
| A2 | 실루엣·비율 | 판정 |
| A3 | TPO 적합성 | 명시 컨텍스트 없으면 보류 |
| A4 | 계절 적합성 | 명시 컨텍스트 없으면 보류 |
| A5 | 보이는 소재·패턴 | 판정, 촉감·정확한 소재는 추정 금지 |
| A6 | 스타일 응집성 | 판정 |
| A7 | 완결성·디테일 | 판정 |
| A8 | 착용자 적합성 | 신체·선호 정보 없으면 보류 |

## 사람 검수량

10장, 이미지당 claim 최대 3개, 비교 쌍 12개를 기준으로 검수자 한 명이 처리하는
선택형 판단은 다음과 같다.

- 이미지 관찰 10행
- claim 최대 30행
- 최소 수정 가설 최대 10행(반례 후보 실험용, 원칙 승인 필수 항목 아님)
- 쌍대 비교 12행
- 합성된 원칙 수만큼의 원칙 검수

서술은 `EDIT` 또는 판단 보류 사유가 있을 때만 작성한다. 같은 템플릿을 검수자마다
별도로 작성한 뒤 행을 합치며, claim·쌍대 비교·원칙 승격은 서로 다른 검수자 2명을
요구한다. 각 질문과 선택지의 정확한 뜻은 실행 시 생성되는 `review_guide.json`이
버전된 계약이다. 다른 검수자에게 전달할 사람용 절차와 작성 예시는
`HUMAN_REVIEW_GUIDE.md`를 사용한다.

## 실행 환경과 입력

```powershell
conda activate final
python -m pip install -r ml/golden_set/requirements.txt
```

`.env.example`의 `GEMINI_API_KEY`, `GOLDEN_*`, `QDRANT_*`, `AWS_*`를 로컬 `.env`,
Infisical 또는 배포 시크릿으로 주입한다. 키를 CSV나 명령행에 적지 않는다.

원본 위치는 환경변수가 정한다.

| 변수 | 뜻 |
|---|---|
| `GOLDEN_S3_BUCKET` | 원본·파생물 버킷 (필수) |
| `GOLDEN_S3_SOURCE_PREFIX` | 코디 원본 prefix |
| `GOLDEN_S3_OUTPUT_PREFIX` | 아이템 이미지·완료 manifest가 쌓이는 prefix |
| `GOLDEN_S3_METADATA_KEY` | 선택. 스타일·계절·TPO 메타데이터 CSV 키 |
| `GOLDEN_DATASET_VERSION` | run 디렉터리와 파생 prefix를 가르는 버전 |
| `GOLDEN_DATASET_STATUS` | Qdrant payload 상태. 검수 중 `PILOT`, 운영 추천은 `ACTIVE` |
| `GOLDEN_ITEM_PIPELINE` | 아이템 분리 구현 (image-processor 레지스트리 키) |

`metadata.example.csv`를 복사해 입력 메타데이터를 만들고 `GOLDEN_S3_METADATA_KEY`
위치에 올린다. 없으면 메타데이터 없이 진행하며, 이 경우 A3(TPO)·A4(계절)는
보류로 처리된다.

- `usage_scope`: `INTERNAL`, `EVALUATION`, `UNKNOWN`
- `original_exposable`: 파일럿 기본값 `false`
- `presentation_group`: 품질 기준이 아니라 분포·공정성 점검용
- `style`, `season`, `occasion` 등의 다중 값: 세미콜론으로 구분
- `split`: `KNOWLEDGE`, `VALIDATION`, `TEST`
- 확실하지 않은 값은 비워둔다.

10장 파일럿은 성별 표현 그룹 5장씩, 스타일 3종 이상, 유사 비교 쌍 2개 이상,
평가가 갈릴 수 있는 경계 사례 2개 이상을 권장한다.

## 0. 본 검수 입력 준비 (팀 드라이브 원본 → metadata.csv)

파일럿 10장은 `local/golden-pilot/metadata.csv`를 손으로 적었다. 본 검수는 수집자
4명이 각자 다른 규칙으로 모은 수백 장이라 같은 방법이 통하지 않는다.

```powershell
# 팀 드라이브의 수집자 폴더를 한 루트 아래로 내려받은 뒤
python -m ml.golden_set review-manifest `
  --root "E:\골든셋" `
  --out-dir local/golden-review `
  --batch-size 100 `
  --apply
```

기대하는 루트 구조. 신혜지 폴더만 성별이 루트에 바로 풀려 있어 수집자 폴더가 없고,
성별 이름이 아닌 하위 폴더는 스캔에서 빠지므로 한 루트에 섞어 두어도 된다.

```text
E:\골든셋\
├── 남자\[1] 캐주얼룩 Casual Look\...   # 신혜지 (스타일 폴더 20종)
├── 여자\[1] 캐주얼룩 Casual Look\...
├── 김민욱\men\  · women\               # 평면 (스타일 라벨 없음)
├── 전하영\men\  · women\
└── 이건우\남성\ · 여성\
```

산출물은 `--out-dir` 아래에 쌓인다.

| 파일 | 내용 |
|---|---|
| `metadata.csv` | 전체 인벤토리. 위 표의 입력 메타데이터 스키마 + 추적용 열 |
| `metadata.batch1.csv` | 이번 검수 배치만 (`--batch-size`) |
| `rename_map.csv` | 원본 상대경로 ↔ 정규화 파일명 ↔ sha256 |
| `inventory_summary.md` | 수집자·성별·스타일 집계와 제외된 파일 목록 |
| `images/`, `images-batch1/` | `--apply`일 때 정규화 이름으로 복사한 평면 폴더 (원본은 그대로) |

이어서 배치만 파이프라인에 태운다.

```powershell
python -m ml.golden_set prepare `
  --input-dir local/golden-review/images-batch1 `
  --metadata-csv local/golden-review/metadata.batch1.csv
```

알아둘 것 세 가지.

- **파일명은 정규화해야 한다.** 수집자마다 이름 규칙이 다르고(`001.jpg` 연번,
  핀터레스트 원본명, 해시 이름) 성별 폴더 사이에서도 겹친다. 검수 화면은 경로가
  아니라 파일 이름으로 이미지를 찾으므로, 겹친 이름을 그대로 두면 다른 사진 위에
  판정이 쌓인다. `{수집자}-{성별}-{스타일}-{연번}` 형태가 그 대책이다.
- **`style`은 신혜지 폴더에서만 채워진다.** 나머지 셋은 평면 구조라 비어 있고,
  README 규칙대로 확실하지 않은 값은 비워 둔다. 폴더명 → taxonomy 매핑은
  `review_manifest.STYLE_MAP`에 모여 있고 원본 폴더명은 `style_source_label`에
  남으므로, 매핑이 어색하면 그 열을 보고 고치면 된다.
- **배치는 무작위 표집이 아니다.** 수집자 → 성별 → 스타일 순으로 라운드로빈해
  결정적으로 고른다. 배치를 늘릴 때 앞 배치와 겹치지 않게 이어붙이려면 순서가
  재현돼야 한다.

### 0-1. 사람이 채울 검수표 (모델 호출 없음)

검수표를 만드는 길은 둘이고 목적이 다르다. 헷갈리면 필요 없는 분석 비용을 쓴다.

| | `review-sheets` | `templates` (3장) |
|---|---|---|
| 사람이 하는 일 | 이미지를 보고 처음부터 적는다 | 모델이 적어 온 관찰·claim이 맞는지 판정한다 |
| 선행 단계 | 없음 (metadata CSV만) | `prepare` → `analyze` |
| 모델 호출 | 0건 | 이미지 1장당 1건 |
| 만드는 표 | 관찰, 쌍대 비교 | 관찰, claim, 최소 수정, 쌍대 비교 |

```powershell
python -m ml.golden_set review-sheets `
  --metadata-csv local/golden-review/metadata.batch1.csv `
  --images-dir local/golden-review/images-batch1 `
  --out-dir local/golden-review/sheets `
  --pair-count 120 --reviewer-label reviewer-a
```

claim 검수표와 최소 수정 검수표는 여기서 만들지 않는다. 둘 다 "모델이 낸 문장"을
판정하는 표라서 판정 대상이 없으면 빈 껍데기가 된다.

쌍대 비교 쌍은 임베딩 없이 metadata만으로 고른다. 스타일 의도가 겹치는 쌍을 먼저
쓰되(의도가 다르면 검수자는 `context_dependent`를 고를 수밖에 없고 그 표는 점수에서
빠진다), 스타일 묶음끼리는 겹치는 쌍이 아예 없으므로 묶음을 잇는 `VISUAL_BRIDGE` 쌍을
반드시 남긴다 — 비교 그래프가 끊기면 Bradley-Terry 상대 점수가 나오지 않는다.

## 1. manifest·임베딩·클러스터 생성

```powershell
python -m ml.golden_set prepare --limit 10
```

입력은 기본적으로 `GOLDEN_S3_*`가 가리키는 S3 prefix다. run 디렉터리·데이터셋
이름도 환경변수에서 가져오며, 필요하면 `--run-dir`/`--dataset-version`으로
덮어쓴다. 테스트나 오프라인 실험에서는 `--input-dir`로 로컬 디렉터리를 쓸 수
있다.

같은 sha의 코디는 다시 임베딩하지 않는다(모델 버전이 바뀌면 전량 재계산).

GPU·모델 다운로드 없이 구조만 검사할 때는 `--embedding-backend deterministic`을
사용한다. 이 벡터는 테스트 전용이므로 실제 Qdrant에 적재하지 않는다.

## 1-1. 의상 아이템 분리·태깅·임베딩

```powershell
python -m ml.golden_set extract-items
```

image-processor의 `WardrobePipeline`을 그대로 호출한다(열거 → 아이템 이미지
생성 → taxonomy 태깅 → 임베딩). 구현 선택은 `GOLDEN_ITEM_PIPELINE`이 하므로
image-processor에 `sam3-crop`이 등록되면 값만 바꿔 교체된다.

산출물은 `items.jsonl`, `item_embeddings.npz`, 그리고 코디별 S3 manifest다.
아이템 태그 축은 `apps.wardrobe.WardrobeItem`과 동일하다 — 코디의 상의를 옷장
아이템이나 네이버 상품으로 교체하려면 세 저장소가 같은 필터 언어를 써야 한다.

## 2. 이미지 통합 분석

```powershell
python -m ml.golden_set analyze `
  --run-dir ml/golden_set/runs/pilot-v2 `
  --all
```

한 이미지의 출력은 다음을 함께 포함한다.

- 실제로 보이는 아이템, 0~1000 bbox, 보이는 속성과 불확실한 속성
- A1~A8의 `FULL/DEGRADED/UNAVAILABLE`
- 이미지 영역을 참조하는 핵심 claim 최대 3개
- 조화·충돌·중립 및 기여·조건부·단순 묘사 구분
- 스타일 의도를 유지하며 속성 하나만 바꾸는 최소 수정 가설
- 사진만으로 판정할 수 없는 항목과 사유

성공 artifact는 이미지 해시, 모델, 프롬프트, 스키마 버전이 모두 같을 때만 재사용한다.
구형 `golden-analysis-v1` 결과가 있어도 v2 분석을 막지 않는다.

## 3. 선택형 검수표 생성

```powershell
python -m ml.golden_set templates `
  --run-dir ml/golden_set/runs/pilot-v2 `
  --pair-count 12
```

생성 파일:

- `image_observation_reviews.template.csv`: 아이템·영역·판정 불가 처리와 선택적 A축 점수
- `claim_reviews.template.csv`: 근거 정확성 및 기여/묘사/조건부/근거 없음/오류 판정
- `minimum_edit_reviews.template.csv`: 반례·경계 사례 제작용 최소 수정 가설 판정
- `pairwise_reviews.template.csv`: 비교 가능한 쌍의 상대 `Q` 판정
- `review_guide.json`: 질문 문구, 선택지 의미, 1~5 점수 기준, 승격 조건

쌍대 비교 결과는 `left`, `right`, `tie`, `context_dependent`, `unassessable` 중
하나다. 컨텍스트가 달라 공정한 비교가 아니면 억지로 승자를 고르지 않는다.

## 4. 이미지·claim 2인 검수 검증

두 검수자의 행을 합친 뒤 먼저 누락·중복·합의 상태를 검사한다.

```powershell
python -m ml.golden_set validate-reviews `
  --run-dir ml/golden_set/runs/pilot-v2 `
  --observation-reviews local/golden-pilot/observation_reviews.csv `
  --claim-reviews local/golden-pilot/claim_reviews.csv
```

`approved_claims.jsonl`에는 이미지 관찰이 승인되고, 근거가 맞으며,
`CONTRIBUTES` 또는 `CONTEXT_DEPENDENT`로 2인 승인된 claim만 남는다.
`DESCRIPTIVE_ONLY`는 관찰 데이터로 보존할 수 있지만 원칙 합성 근거로 승격하지 않는다.

### 4-1. 축약 검수표도 그대로 받는다

검수 시간을 줄이려고 판정 열을 합친 검수표(`goldenset-review-sheets` 스킬 산출)를
쓸 수 있다. `validate-reviews`가 읽기 전에 표준 열로 펴므로 명령은 같다.

| 축약 열 | 표준 열 | 규칙 |
|---|---|---|
| `detected_items_correct=YES` | `observation_verdict`, `items_complete` | APPROVE / YES |
| `=NO` + `corrected_detected_items` | 〃 + `missing_observations` | EDIT / YES / 수정값 |
| `=NO` 수정값 없음 | 〃 | EDIT / **NO** |
| `=UNSURE` | 〃 | UNSURE (pending) |
| `human_judgment` + `evidence_correct` | `verdict` | 아래 |

`verdict`는 근거·판정에서 유도한다 — 근거 NO거나 `UNSUPPORTED`·`INCORRECT`거나
과일반화 YES면 REJECT, 근거 YES면 APPROVE, 근거 UNSURE면 UNSURE다.
`DESCRIPTIVE_ONLY`는 REJECT가 아니라 APPROVE로 둔다. 제외 처리는
`POSITIVE_CLAIM_JUDGMENTS`가 이미 하므로, 여기서 기각하면 "틀린 claim"과 "맞지만
묘사일 뿐인 claim"이 한 덩어리가 된다.

값이 이미 있으면 덮지 않아 표준 검수표는 지금까지와 똑같이 동작한다.

**축약 표가 묻지 않는 열은 승인 조건에서 뺀다.** `unassessable_complete`와
`bbox_grounding_1_3`이 그렇다(둘 다 빈 값이면 전 건이 pending으로 떨어진다).
사람이 판정하지 않은 항목을 YES로 채워 통과시키면 검수 기록이 거짓말이 되므로,
**열의 유무**로 가른다.

## 5. 보조 Q 앵커 계산

```powershell
python -m ml.golden_set fit-anchors `
  --run-dir ml/golden_set/runs/pilot-v2 `
  --pairwise-reviews local/golden-pilot/pairwise_reviews.csv `
  --observation-reviews local/golden-pilot/observation_reviews.csv
```

검수자 2명이 완료한 쌍만 사용하며, 비교 그래프가 연결돼야 Bradley-Terry 상대 점수를
계산한다. `context_dependent`와 `unassessable` 표는 점수 계산에서 제외한다.
`anchor_scores.jsonl`의 0~100 점수와 high/mid/low는 파일럿 내부 `Q` 상대값이지
보편적인 패션 점수나 개인화 점수가 아니다.

### 5-1. `anchor_graph` — 서로 비교하지 않는 묶음

남성 코디와 여성 코디처럼 애초에 맞붙이지 않는 묶음은 한 파일에 담겨도 하나의
그래프가 아니라 **독립된 그래프 여러 개**다. 합쳐서 계산하면 연결이 끊겨
`fit-anchors`가 실패한다. 검수표의 `anchor_graph` 열이 그 경계를 적고, 명령은 그
값으로 나눠 각각 fit한 뒤 한 파일로 합친다. 열이 없는 검수표는 단일 그래프로
처리돼 이전과 동작이 같다.

⚠️ **그래프가 다르면 점수를 비교하지 마라.** 0~100 환산은 그래프 안에서 최저~최고를
펴는 것이라 men의 80점과 women의 80점은 같은 뜻이 아니다. `score_band`도 마찬가지다.
리트리버가 `presentation_group`(성별)으로 먼저 거르기 때문에 실제 랭킹에서는 같은
그래프끼리만 경쟁하지만, **그 필터를 푸는 순간 이 전제가 조용히 깨진다.**

한 코디가 두 그래프에 걸치면 멈춘다 — 비교 불가능한 점수가 두 개 생기고 적재에서
뒤쪽이 앞쪽을 덮어써 어느 쪽이 반영됐는지 알 수 없게 된다.

## 6. 승인 claim으로 원칙 합성

```powershell
python -m ml.golden_set synthesize-principles `
  --run-dir ml/golden_set/runs/pilot-v2 `
  --observation-reviews local/golden-pilot/observation_reviews.csv `
  --claim-reviews local/golden-pilot/claim_reviews.csv
```

이 단계는 이미지를 다시 보내지 않고 승인된 텍스트 claim만 LLM에 전달한다. 원칙은
`applies_when`, `exceptions`, 원본 `golden_id/claim_id`, `knowledge_role`을 가진다.

원칙 역할:

- `EXPLANATION_ONLY`: 추천 이유 설명과 RAG 검색에는 사용 가능, 점수에는 미사용
- `NEEDS_COUNTEREXAMPLE`: 지지 사례만 있어 경계·반례 수집이 더 필요
- `SCORE_AND_EXPLANATION`: 충분한 비교·반례까지 검증된 경우에만 점수와 설명에 사용
- `DISCARD`: 잘못된 일반화 또는 활용 가치 없음

채점 승격은 지지 이미지 3장 이상, 비교·반례 근거 2건 이상, 예외 1개 이상,
검수자 2명 이상, 영역 근거를 모두 요구한다. 현재 10장 첫 사이클은 비교·반례가
충분하지 않을 가능성이 높으므로 `EXPLANATION_ONLY` 또는 `NEEDS_COUNTEREXAMPLE`이
정상 결과다.

최소 수정 가설 자체는 반례가 아니다. 동일 조건의 실제 이미지나 한 속성만 바꾼
시각 변형을 만들고 사람이 결과를 비교한 후에만 비교·반례 근거로 등록할 수 있다.

## 7. 원칙 2인 검수 반영

```powershell
python -m ml.golden_set approve `
  --run-dir ml/golden_set/runs/pilot-v2 `
  --principle-reviews local/golden-pilot/principle_reviews.csv
```

원칙도 2인 승인을 요구한다. 두 수정안이 서로 다르면 자동 병합하지 않고 충돌로
중단한다. 승격 조건이 부족한 `SCORE_AND_EXPLANATION` 요청은 자동으로
`EXPLANATION_ONLY`로 낮춘다.

## 8. PostgreSQL SSOT import

```powershell
python api/manage.py migrate
python api/manage.py import_golden_run `
  --run-dir ml/golden_set/runs/pilot-v2 `
  --observation-reviews local/golden-pilot/observation_reviews.csv `
  --claim-reviews local/golden-pilot/claim_reviews.csv `
  --minimum-edit-reviews local/golden-pilot/minimum_edit_reviews.csv `
  --pairwise-reviews local/golden-pilot/pairwise_reviews.csv `
  --principle-reviews local/golden-pilot/principle_reviews.csv
```

PostgreSQL이 원본 manifest·아이템·분석·사람 검수·원칙의 단일 진실 공급원이다.
쌍대 비교는 좌우 이미지, 검수자, 컨텍스트, 결과, 확신도를 별도 테이블에 보존한다.

골든셋 테이블은 전부 `goldenset` 스키마에 만들어진다(`golden_dataset`,
`golden_image`, `golden_outfit_item`, `golden_analysis`, `golden_principle`,
`golden_principle_evidence`, `golden_review`, `golden_pairwise_review`).
마이그레이션 사용자에게 `CREATE SCHEMA` 권한이 필요하다.

## 9. Qdrant 파생 적재

```powershell
python api/manage.py init_qdrant
python -m ml.golden_set index `
  --run-dir ml/golden_set/runs/pilot-v2 `
  --dry-run
```

계획을 확인한 뒤 `--dry-run`을 제거한다. 컬렉션은 셋이다.

기존 포인트를 재임베딩하지 않고 파일럿에서 운영 상태로 승격할 때는 먼저 대상을
확인한 뒤 같은 명령에서 `--dry-run`만 제거한다.

```powershell
python api/manage.py set_goldenset_qdrant_status `
  --dataset-version v1 --from-status PILOT --status ACTIVE --dry-run
python api/manage.py set_goldenset_qdrant_status `
  --dataset-version v1 --from-status PILOT --status ACTIVE
```

이후 API와 채팅 워커에는 같은 버전과 상태를 설정한다.

```text
CHAT_GOLDENSET_DATASET_VERSION=v1
CHAT_GOLDENSET_DATASET_STATUSES=ACTIVE
```

| 컬렉션 | 포인트 | 벡터 |
|---|---|---|
| `outfit_goldenset` | 코디 1장 | image + text |
| `goldenset_items` | 분리된 의상 아이템 1개 | image + text |
| `knowledge` | 승인된 조건부 원칙 | text |

코디 payload의 `items[]`가 아이템 포인트(`point_id`)로 가는 다리이고, 아이템
payload의 `outfit_point_id`가 그 역참조다. 아이템 태그 인덱스는
`products`/`wardrobe`와 동일하므로 "이 코디의 상의를 옷장 아이템으로 교체"가
같은 필터 언어로 성립한다.

코디 포인트는 쌍대 비교 앵커가 없어도 만든다 — 앵커 점수는 있으면 얹는 선택
정보다. 노출 여부는 `GOLDEN_ANCHOR_EXPOSABLE`와 이미지별 `original_exposable`을
모두 만족할 때만 참이다. `--allow-draft`는 격리된 개발 Qdrant에서만 사용한다.

## 10. 검수 결과를 운영 코디에 반영

앞 단계까지는 run 디렉터리 안의 이야기다. 실제 추천 랭킹을 바꾸려면 검수 결과가
운영 Qdrant의 코디 payload로 가야 한다. GPU도 모델도 필요 없고 두 단계다.

```powershell
python -m ml.golden_set publish-review `
  --run-dir ml/golden_set/runs/main-batch1-v1 `
  --metadata-csv local/golden-review/metadata.csv `
  --dry-run
```

건수를 확인한 뒤 `--dry-run`을 빼면 S3에 올라간다.

```powershell
python -m ml.golden_set apply-review --dry-run
```

역시 확인 뒤 `--dry-run`을 빼면 payload가 갱신된다.

### 왜 sha256으로 잇는가

**검수표의 `golden_id`와 S3의 `golden_id`는 다르다.** 검수표는
`review-manifest`가 만든 정규화 이름(`shj-m-casual-001`)이고, S3는 업로드 당시의
원본 파일명(`001`, `042-2`, 32자 해시…)이다. 원본 파일명은 수집자 사이에서 겹쳐서
S3 쪽이 임의로 `-2`를 붙여 갈랐고 그 대응표는 남아 있지 않다.

이름을 맞추려면 S3 전량을 다시 올려야 하는데 코디마다 아이템 이미지가 딸려 있어
재생성 비용이 크다. 대신 양쪽 모두 같은 원본 사진의 sha256을 들고 있다 — S3는
`manifest.json`의 `image_sha256`, 로컬은 `metadata.csv`의 같은 열이다. **sha256이
이름 규칙과 무관하게 같은 사진을 가리키는 유일한 값이라 이걸 조인 키로 쓴다.**

그래서 `publish-review`에 `--metadata-csv`가 필수다. 정규화 이름을 sha로 바꿔 주는
표가 그것뿐이다.

### 발행 파일

`{GOLDEN_S3_OUTPUT_PREFIX}/{GOLDEN_DATASET_VERSION}/human_review.json` —
`run_summary.json`과 같은 자리다. 버전이 갈리면 검수 결과도 같이 갈린다.

`anchor_scores.jsonl`(쌍대 비교 앵커)과 `review_validation.json`의
`accepted_images`(2인 관찰 승인)를 sha256으로 묶어 코디마다 한 줄로 만든다.

### payload에 실리는 것

| 키 | 출처 | 리트리버에서 |
|---|---|---|
| `human_score` | 앵커 0~100 | **유사도 기준선.** 없으면 규칙 점수만 남는다 |
| `score_band` | high/mid/low | 필터 가능 (`keyword` 인덱스) |
| `score_confidence` | 0~1 | 기록용 |
| `anchor_graph` | men/women 등 | 점수 비교 범위 표시 |
| `human_verified` | 2인 관찰 승인 | 태그 신뢰도보다 나은 tiebreak 축 |
| `human_review_golden_id` | 정규화 이름 | 역추적용 |

**점수가 없는 코디에는 키를 쓰지 않는다.** `human_score=0`을 적으면 리트리버에서
"미검수"와 "최하점"이 같은 값이 되고, 앵커를 나중에 늘려도 그 차이를 볼 수 없다.

### `apply-review`가 지우는 일까지 하는 이유

`set_payload`는 덮어쓰기만 한다. 검수를 고쳐 다시 발행했을 때 어떤 코디가 승인에서
빠졌다면 옛 `human_score`가 그대로 남는다 — **검수를 되돌렸는데 랭킹은 안 되돌아가는
상태**다. 그래서 검수가 없는 코디에서는 검수 키를 명시적으로 지운다.

### 조용한 실패를 막는 장치

이 경로는 어긋나도 에러가 안 나는 자리가 많다. 세 곳에 확인을 넣었다.

- **sha가 하나도 안 맞으면 `apply-review`가 멈춘다.** 발행에 쓴 metadata CSV가 그
  S3 데이터셋과 다른 원본이면 한 건도 안 붙는데, 그냥 두면 "적재는 성공했는데 점수가
  없다"를 한참 뒤에 발견한다
- **`set_payload`는 없는 포인트에 조용히 아무것도 하지 않는다.** 그래서 미리
  `retrieve`로 적재 여부를 확인하고 미적재 건수를 따로 센다 — `sync_qdrant`를 아직
  안 돌린 코디가 여기 잡힌다
- **`metadata.csv`에서 sha를 못 찾은 코디는 `unmatched_golden_ids`에 남긴다** — 버리지
  않는다

### `sync_qdrant`와의 관계

`sync_qdrant`도 같은 파일을 읽어 payload에 얹으므로 전량 재적재를 하면 검수 결과가
함께 들어간다. 다만 그쪽은 코디 이미지 벡터를 매번 새로 계산한다(S3에 저장된 적이
없다). **payload만 바꾸려고 전량 재임베딩을 도는 건 낭비이므로 `apply-review`를 쓴다.**

S3가 없는 개발 환경에서는 두 명령 모두 `--human-review <경로>`로 로컬 JSON을 쓴다.

## 주요 산출물 계약

```text
images.jsonl                              원본·권리·해시·split manifest (S3 키 포함)
image_embeddings.npz                      FashionSigLIP 파생 벡터 (ids/shas/vectors)
items.jsonl                               분리된 의상 아이템과 taxonomy 태그
item_embeddings.npz                       아이템 이미지·캡션 벡터
clusters.jsonl                            클러스터·대표·경계 역할
analyses.jsonl                            bbox 관찰·관계 claim·최소 수정 가설
image_observation_reviews.csv             사람 관찰 검수와 선택적 Q 축 점수
claim_reviews.csv                         사람 claim 근거·역할 검수
minimum_edit_reviews.csv                  반례 후보 실험 가설 검수
pairwise_reviews.csv                      사람 상대 Q 비교
approved_claims.jsonl                     2인 승인된 합성 입력
anchor_scores.jsonl                       보조 Q 점수 앵커 (anchor_graph별)
principles.jsonl                          조건부 원칙과 이미지 claim 근거
principle_reviews.csv                     사람 원칙 검수
review_validation.json                    누락·보류·합의 검증 결과 (accepted_images 포함)
human_review.json                         S3 발행분: 앵커+승인 이미지를 sha256으로 묶은 것
qdrant_index_plan.json                    파생 적재 전 개수·상태 확인
```
