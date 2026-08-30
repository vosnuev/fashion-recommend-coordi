# indexer — 임베딩 → Qdrant 적재 GPU worker

컨테이너별로 패키지를 분리했다. 각 패키지는 자기 Dockerfile과 requirements를
가지며, 공용 모듈만 `util/`에 둔다.

```text
indexer/
├── util/                 # 두 컨테이너가 공유하는 모듈
│   ├── embedder.py       #   FashionSigLIPEmbedder (model_id·device를 인자로 받음)
│   └── requirements.txt  #   torch / open_clip / transformers 등 공용 의존성
├── product_indexer/      # [운영] 네이버·11번가 상품 임베딩 worker
│   ├── Dockerfile.product-indexer
│   ├── requirements.txt  #   -r ../util/requirements.txt + 상품 파이프라인 의존성
│   ├── product_indexer.py / product_indexer_api.py / product_config.py
│   ├── product_assets.py / product_catalog_api.py / product_qdrant.py
│   ├── product_text.py / bge_embedder.py
│   ├── tests/
│   └── PRODUCTS_README.md
└── old/                  # [레거시] ETRI 패션 코디 데이터셋 적재
    ├── Dockerfile.indexer.old
    ├── requirements.txt
    ├── config.py / etri_dataset.py / fashion_indexer.py / qdrant_loader.py
    └── README.md
```

## 빌드 컨텍스트

두 Dockerfile 모두 `util/`을 함께 COPY 해야 하므로 **빌드 컨텍스트는 `indexer/`**이고
`-f`로 패키지 안의 Dockerfile을 지정한다.

```bash
# 운영 상품 임베딩 worker (기본 CMD: drain 트리거 HTTP API)
docker build -f indexer/product_indexer/Dockerfile.product-indexer \
  -t skn28-product-indexer indexer/

# 레거시 ETRI 적재 배치
docker build -f indexer/old/Dockerfile.indexer.old \
  -t skn28-indexer-old indexer/
```

## GPU 서버 (docker compose)

운영에서는 루트 `docker-compose.gpu.yml`로 image-processor와 함께 띄운다.

```bash
./run-gpu.sh                                          # 시크릿 내보내기 + 기동
docker compose -f docker-compose.gpu.yml up -d --build product-indexer
```

`.env`는 루트 하나만 쓴다. compose가 `env_file: .env`로 값을 컨테이너 환경변수에
직접 주입하므로 **이미지 안에는 `.env` 파일이 없다**. 반대로 아래처럼 리포에서
직접 실행할 때는 코드가 루트 `.env` 파일을 읽어야 하는데, 두 경우를 모두
만족시키려고 `util/env.py`의 `load_project_env()`가
`ENV_FILE` 지정 → 상위 디렉터리 탐색 → (없으면) 조용히 통과 순으로 동작한다.
항상 `override=False`라 compose가 주입한 값을 `.env` 파일이 덮어쓰지 않는다.

## 로컬 실행

`indexer/`를 import 루트로 삼는다.

```bash
cd indexer
pip install -r product_indexer/requirements.txt
python -m product_indexer.product_indexer --once --batch-size 2
python -m product_indexer.product_indexer_api

pip install -r old/requirements.txt
python -m old.fashion_indexer --limit 32
```

## 테스트

```bash
cd indexer
python -m unittest discover -s product_indexer/tests -t .
```

자세한 내용은 `product_indexer/PRODUCTS_README.md`(운영)와 `old/README.md`(레거시)를 본다.
