# image-processor — 옷장 이미지 프로세서 (AI Worker)

Redis 큐에서 옷장 업로드 job을 받아 사진 속 패션 아이템을 처리하는 이미지
프로세서. 캘린더 사진도 별도 consumer 없이 동일한 옷장 업로드 job으로 처리한다.

- 설계: Confluence > 설계 > "옷장 이미지 파이프라인 설계서" + "옷장 기능 전체 설계"
- 코어 로직: `test/test-llm2` (Gemini 열거 → 이미지 편집 생성 → 태깅) 이식

## 구조 (컴포넌트 교체 가능 — 전략 패턴)

```
worker.py                 # 메인 루프: 큐 → 처리 → S3 → manifest → 콜백 → ack
reindex_worker.py         # 기존 크롭 이미지·DB 태그 → 임베딩 재생성 전용
config.py                 # 환경변수 (루트 .env)
pipeline/
├── base.py               # 인터페이스: ItemEnumerator / ProductImageGenerator
│                         #            / ItemTagger / Embedder + dataclass
├── __init__.py           # WardrobePipeline(컴포지션) + build_pipeline() factory
├── taxonomy.py           # Confluence 태그 체계 (api/wardrobe와 동일 라벨)
├── embedding.py          # SigLIPBgeEmbedder(768d/1024d), NullEmbedder
└── gemini/               # 기본 구현 "gemini-edit" (test-llm2 로직)
    ├── enumerator.py     # ① 열거: 비전 structured output
    ├── editor.py         # ② 생성: gemini-3.1-flash-image 편집 프롬프트
    └── tagger.py         # ③ 태깅: taxonomy enum 강제 + 짝 보정
services/
├── queue.py              # Redis reliable queue (pending/processing/dead)
├── reindex_queue.py      # 재인덱싱 전용 pending/processing/dead
├── s3io.py               # 원본 다운로드, 크롭·manifest 업로드
└── callback.py           # wardrobe-api 콜백 (X-Internal-Token, 재시도)
```

캘린더 API는 사진 원본을 캘린더와 옷장 S3 경로에 각각 보관하고 기존
`WardrobeUploadJob`을 enqueue한다. worker와 옷장 callback은 기존과 동일하게
옷장 아이템을 생성하며, API가 해당 job으로 생성된 아이템을 캘린더에 연결한다.

룩북 API도 같은 job을 쓰되 페이로드에 **`exclude_categories`**(예: `["상의"]`)를
싣는다. 사용자가 '입은 옷'으로 이미 지정한 부위라 다시 등록하면 옷장에 같은 옷이
두 벌 생긴다. worker는 이 목록을 **열거 직후** 걸러 내므로 생성·태깅·임베딩 비용
자체가 발생하지 않는다. 제외 결과는 manifest의 `counts.excluded` ·
`excluded_categories` · `excluded_items`에 남는다. 전부 제외돼 남는 아이템이 없으면
실패가 아니라 `status=success` + 빈 items로 콜백한다 — 사용자가 사진 속 부위를
직접 다 지정한 정상 흐름이기 때문이다. 키가 없는 기존 페이로드는 동작이 같다.

**구현 교체**: 새 컴포넌트로 `pipeline/base.py` 인터페이스를 구현하고
`pipeline/__init__.py`의 `_REGISTRY`에 빌더를 등록한 뒤
`WORKER_PIPELINE` 환경변수로 선택한다 (예정: `sam3-crop`).

## 신뢰성 정책 (설계서 4·7장)

- 큐: `BLMOVE pending→processing` 원자 이동 → 성공(ack) 시에만 제거,
  실패 시 재시도(기본 3회) 초과하면 `:dead` 큐로 이동
- 멱등: 같은 job_id는 같은 S3 경로 재사용. **manifest.json이 이미 있으면
  이미지 처리를 건너뛰고 콜백만 재시도**
- 아이템 단위 부분 실패 허용: 실패 아이템은 manifest에 error로 기록하고
  콜백에서 제외 (전부 실패 시 status=failed)
- Worker는 DB를 직접 수정하지 않는다 (Qdrant 적재도 wardrobe-api 담당)

## 환경변수 (루트 .env)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `GEMINI_API_KEY` | (필수) | 열거·생성·태깅 공용 |
| `REDIS_URL` | redis://localhost:6379/0 | |
| `WARDROBE_JOB_QUEUE` | wardrobe:jobs | pending 키 (processing/dead는 파생) |
| `WARDROBE_REINDEX_QUEUE` | wardrobe:reindex | 기존 옷 재인덱싱 전용 pending 키 |
| `WARDROBE_REINDEX_CALLBACK_URL` | | 재인덱싱 결과를 받을 내부 API 전체 URL |
| `WARDROBE_INTERNAL_TOKEN` | (필수) | 옷장 callback 인증 — api와 동일 값 |
| `WARDROBE_CALLBACK_URL` | | 페이로드에 callback_url 없을 때 폴백 |
| `WORKER_PIPELINE` | gemini-edit | 파이프라인 구현 선택 |
| `GEMINI_FLASH_IMAGE_MODEL` | gemini-3.1-flash-image | 상품 이미지 생성 모델 |
| `GEMINI_ENUM_MODEL` / `GEMINI_TAG_MODEL` | gemini-3.5-flash | |
| `WORKER_EMBED_ENABLED` | 1 | 0이면 임베딩 생략(빈 벡터) |
| `WORKER_MAX_RETRIES` | 3 | job 재시도 한도 |
| `AWS_ACCESS_KEY_ID` 등 | | S3 자격증명 (IAM 역할이면 불필요) |

## 실행

```bash
# GPU 서버 스택 (product-indexer와 함께) — 권장
./run-gpu.sh                                             # 리포 루트에서
docker compose -f docker-compose.gpu.yml up -d --build image-processor

# 단독 실행
cd image-processor
./run.sh                 # Docker 빌드 + 실행 (GPU 자동 감지)
NO_BUILD=1 ./run.sh      # 재빌드 생략

# 로컬 (개발)
pip install -r requirements.txt
python worker.py
python reindex_worker.py  # 유료 모델 호출 없이 기존 옷 벡터만 복구
```

`.env`는 루트 하나만 쓴다. compose로 띄우면 `env_file: .env`가 값을 컨테이너
환경변수로 주입하므로 이미지 안에는 `.env` 파일이 없고, 리포에서 직접 실행하면
`config.py`가 상위 디렉터리를 훑어 루트 `.env`를 읽는다. 다른 경로를 쓰려면
`ENV_FILE=/path/to/.env`로 지정한다. 파일 값은 `override=False`로 읽으므로
compose가 주입한 환경변수를 덮어쓰지 않는다.

첫 실행 시 임베딩 모델(FashionSigLIP ~0.8GB, bge-m3 ~2.3GB)이 HF에서
다운로드된다. 임베딩 없이 빠르게 확인하려면 `WORKER_EMBED_ENABLED=0`.
