# [레거시] old — S3 패션아이템 → 임베딩 → Qdrant

> 운영 상품 파이프라인은 `indexer/product_indexer/`로 이동했다.
> 이 패키지는 이전 `Dockerfile.indexer`(현 `old/Dockerfile.indexer.old`) 컨테이너가
> 쓰던 ETRI 데이터셋 적재 기능만 그대로 보존한다.

S3의 패션 아이템(사진 + 텍스트)을 **Marqo Fashion SigLIP**으로 임베딩해
Qdrant(REST API)에 적재하는 독립 실행 배치 구성요소.
`collector`가 "외부 소스 → PostgreSQL"이라면, indexer는 "S3 → 벡터 DB"를 담당한다.

현재 대상: **11번 ETRI 패션 코디 데이터셋** (`s3://skn28-cozy/11. 한국전자통신연구원_...패션 코디 데이터셋/`)

## 데이터셋 구조 (11번)

| 파일 | 내용 |
|------|------|
| `mdata.wst.txt.2020.6.23` | 아이템 메타데이터 (EUC-KR, 탭 구분). 아이템ID/구분/카테고리/속성/설명 |
| `img/*.jpg` | 아이템 이미지 3,351장 (`BL-001.jpg` 형식) |
| `ddata.wst.txt.*`, `ac_eval_*` | 코디 추천 대화 데이터 (indexer에서는 사용 안 함) |

- 구분: `T` 상의, `B` 하의, `O` 아우터, `S` 신발
- 카테고리(13종): BL 블라우스, CD 가디건, CT 코트, JK 재킷, JP 점퍼, KN 니트,
  OP 원피스, PT 팬츠, SE 신발, SH 셔츠, SK 스커트, SW 스웨터, VT 베스트
- 속성: `F` 형태, `M` 소재, `C` 색상, `E` 감성

## Qdrant 스키마

- 컬렉션: `QDRANT_COLLECTION` (기본 `fashion_items`)
- named vector 2개 (cosine, 768차원):
  - `image` — 모든 아이템
  - `text` — mdata 설명이 있는 아이템만 (카테고리+색상+감성+형태+소재 문장)
- payload: `item_id`, `part(_ko)`, `category(_ko)`, `features`(속성별 설명), `s3_bucket`, `s3_key`, `text`, `dataset`
- point id = `uuid5("etri_fashion_poc_11:{item_id}")` → **재실행 멱등** (중복 적재 없음)

## 실행

### 필요 환경변수 (루트 `.env` 또는 컨테이너 주입)

```bash
QDRANT_URL=http://<qdrant-호스트>:6333   # 프로젝트 qdrant 컨테이너
QDRANT_API_KEY=                          # 설정된 경우만
QDRANT_COLLECTION=fashion_items
AWS_ACCESS_KEY_ID=...                    # S3 읽기 권한 (또는 IAM 역할/프로파일)
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=ap-southeast-2
```

### GPU 환경에서 직접 실행 (RunPod 등)

```bash
cd indexer
pip install -r old/requirements.txt   # torch는 CUDA 빌드가 이미 있으면 재설치 안 됨
python -m old.fashion_indexer --limit 32    # 스모크 테스트
python -m old.fashion_indexer               # 전체 적재 (3090 기준 수 분)
```

### Docker

```bash
docker build -f indexer/old/Dockerfile.indexer.old -t skn28-indexer-old indexer/
docker run --gpus all --env-file .env skn28-indexer-old --limit 32
docker run --gpus all --env-file .env skn28-indexer-old
```

빌드 컨텍스트는 `indexer/`다. 공용 임베더(`util/embedder.py`)를 함께 COPY 해야 한다.

옵션: `--limit N`(상한), `--batch-size N`(기본 64), `--recreate`(컬렉션 재생성), `--category BL`(특정 카테고리만)

## 적재 확인 / 검색 예시 (Qdrant REST)

```bash
# 적재 수 확인
curl "$QDRANT_URL/collections/fashion_items" | jq .result.points_count

# 텍스트 벡터로 유사 아이템 검색 (쿼리 벡터는 동일 모델로 임베딩해서 사용)
curl -X POST "$QDRANT_URL/collections/fashion_items/points/query" \
  -H "Content-Type: application/json" \
  -d '{"query": [/* 768-dim */], "using": "image", "limit": 5, "with_payload": true}'
```

## 주의 / 알려진 한계

- **한국어 텍스트 임베딩 품질**: Marqo Fashion SigLIP은 영어 패션 텍스트로 학습된
  모델이라 한국어 mdata 설명의 임베딩 품질에 한계가 있다. 이미지 벡터를 기본 검색
  축으로 쓰고, 한국어 텍스트 검색 품질이 중요해지면 다국어 모델 도입을 검토할 것.
- **텍스트 컨텍스트 64 토큰**: 초과분은 잘린다. 중요한 속성(카테고리·색상·감성)을
  앞쪽에 배치해 완화했다.
- 모델 래퍼는 `indexer/util/embedder.py`로 옮겨 product_indexer와 공유한다.
  추후 추천 API가 쿼리 임베딩에 재사용할 수 있도록 `ml/`로 이동을 검토한다.
