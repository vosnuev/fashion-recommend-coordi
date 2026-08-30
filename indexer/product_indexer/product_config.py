"""네이버·11번가 쇼핑 상품 임베딩 worker 설정."""

from __future__ import annotations

import os

from util.env import load_project_env

# 리포 체크아웃이면 루트 .env를, 컨테이너면 compose가 주입한 환경변수를 쓴다.
load_project_env(__file__)

# Django catalog 내부 API. product-indexer는 PostgreSQL에 직접 연결하지 않는다.
CATALOG_API_URL = os.getenv("PRODUCT_CATALOG_API_URL", "").strip().rstrip("/")
CATALOG_API_TOKEN = os.getenv("PRODUCT_INDEXER_INTERNAL_TOKEN", "").strip()
CATALOG_API_TIMEOUT_SECONDS = max(
    1,
    int(os.getenv("PRODUCT_CATALOG_API_TIMEOUT_SECONDS", "30")),
)

# ---------- 지원 쇼핑몰 ----------
# 작업 큐(product_embedding_job)는 source 컬럼을 가진 공용 테이블 하나를 쓰고,
# S3 저장 경로와 Qdrant 컬렉션만 쇼핑몰별로 분리한다.
SOURCES = ("naver", "eleven")


def require_source(source: str) -> str:
    if source not in SOURCES:
        raise ValueError(f"지원하지 않는 상품 source: {source}")
    return source


# Qdrant
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY") or None
# 쇼핑몰별 컬렉션. 벡터 구성은 같지만 검색·재색인·삭제를 독립적으로 하려고 나눈다.
QDRANT_COLLECTIONS = {
    "naver": os.getenv(
        "PRODUCT_NAVER_QDRANT_COLLECTION", "products_naver_v1"
    ).strip(),
    "eleven": os.getenv(
        "PRODUCT_ELEVEN_QDRANT_COLLECTION", "products_eleven_v1"
    ).strip(),
}


def qdrant_collection(source: str) -> str:
    return QDRANT_COLLECTIONS[require_source(source)]

# 모델
IMAGE_MODEL_ID = os.getenv(
    "PRODUCT_IMAGE_EMBED_MODEL",
    "hf-hub:Marqo/marqo-fashionSigLIP",
)
TEXT_MODEL_ID = os.getenv("PRODUCT_TEXT_EMBED_MODEL", "BAAI/bge-m3")
TEXT_MODEL_REVISION = os.getenv("PRODUCT_TEXT_MODEL_REVISION", "").strip() or None
EMBEDDING_VERSION = os.getenv(
    "PRODUCT_EMBEDDING_VERSION",
    "marqo-fashionSigLIP+bge-m3-v1",
).strip()
DEVICE = os.getenv("PRODUCT_INDEXER_DEVICE", os.getenv("INDEXER_DEVICE", "auto"))
TEXT_MAX_LENGTH = int(os.getenv("PRODUCT_TEXT_MAX_LENGTH", "512"))

# S3 상품 이미지 원본. 버킷은 하나를 공유하고 prefix로 쇼핑몰을 나눈다.
IMAGE_S3_BUCKET = os.getenv("PRODUCT_IMAGE_S3_BUCKET", "").strip()
# 쇼핑몰별 prefix의 공통 뿌리. 개별 prefix를 지정하지 않으면 "{루트}/{source}"가 된다
# (기존 키 규칙 products/{source}/{상품ID}/{checksum}.jpg 과 동일하다).
IMAGE_S3_PREFIX = os.getenv("PRODUCT_IMAGE_S3_PREFIX", "products").strip("/")
IMAGE_S3_PREFIXES = {
    source: os.getenv(
        f"PRODUCT_{source.upper()}_IMAGE_S3_PREFIX",
        f"{IMAGE_S3_PREFIX}/{source}" if IMAGE_S3_PREFIX else source,
    ).strip("/")
    for source in SOURCES
}


def image_s3_prefix(source: str) -> str:
    return IMAGE_S3_PREFIXES[require_source(source)]

# worker
BATCH_SIZE = min(
    256,
    max(1, int(os.getenv("PRODUCT_INDEXER_BATCH_SIZE", "32"))),
)
POLL_SECONDS = max(1, int(os.getenv("PRODUCT_INDEXER_POLL_SECONDS", "10")))
# 이미지 다운로드·S3 입출력을 병렬 처리하는 스레드 수. 한 배치에 naver와 eleven
# 작업이 섞여 있어도 서로를 기다리지 않고 동시에 진행된다 (GPU 임베딩은 배치로 1회).
IMAGE_WORKERS = min(
    32,
    max(1, int(os.getenv("PRODUCT_INDEXER_IMAGE_WORKERS", "8"))),
)
MAX_RETRIES = min(
    20,
    max(0, int(os.getenv("PRODUCT_INDEXER_MAX_RETRIES", "2"))),
)
RETRY_BASE_SECONDS = max(1, int(os.getenv("PRODUCT_INDEXER_RETRY_BASE_SECONDS", "30")))
STALE_JOB_MINUTES = max(1, int(os.getenv("PRODUCT_INDEXER_STALE_JOB_MINUTES", "30")))
DRAIN_MAX_WAIT_SECONDS = max(
    0,
    int(os.getenv("PRODUCT_INDEXER_DRAIN_MAX_WAIT_SECONDS", "120")),
)
DRAIN_MAX_RUNTIME_MINUTES = max(
    1,
    int(os.getenv("PRODUCT_INDEXER_DRAIN_MAX_RUNTIME_MINUTES", "120")),
)
IMAGE_DOWNLOAD_TIMEOUT = max(1, int(os.getenv("PRODUCT_IMAGE_DOWNLOAD_TIMEOUT", "30")))
MAX_IMAGE_BYTES = max(
    1, int(os.getenv("PRODUCT_IMAGE_MAX_BYTES", str(20 * 1024 * 1024)))
)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def validate_runtime_config() -> None:
    if not CATALOG_API_URL:
        raise RuntimeError(
            "PRODUCT_CATALOG_API_URL이 필요합니다. Django catalog 내부 API의 "
            "product-embeddings URL을 설정하세요."
        )
    if not CATALOG_API_TOKEN:
        raise RuntimeError(
            "PRODUCT_INDEXER_INTERNAL_TOKEN이 필요합니다. catalog API와 "
            "product-indexer에 같은 토큰을 주입하세요."
        )
    if not IMAGE_S3_BUCKET:
        raise RuntimeError(
            "PRODUCT_IMAGE_S3_BUCKET이 필요합니다. 상품 이미지를 S3에 보존한 뒤 "
            "임베딩하도록 .env 또는 실행 환경에 설정하세요."
        )
    if not EMBEDDING_VERSION:
        raise RuntimeError("PRODUCT_EMBEDDING_VERSION은 비어 있을 수 없습니다.")
    for source in SOURCES:
        if not QDRANT_COLLECTIONS[source]:
            raise RuntimeError(
                f"PRODUCT_{source.upper()}_QDRANT_COLLECTION이 비어 있습니다. "
                "쇼핑몰별 Qdrant 컬렉션 이름을 설정하세요."
            )
        if not IMAGE_S3_PREFIXES[source]:
            raise RuntimeError(
                f"PRODUCT_{source.upper()}_IMAGE_S3_PREFIX가 비어 있습니다. "
                "쇼핑몰별 S3 prefix를 설정하세요."
            )
    if len(set(QDRANT_COLLECTIONS.values())) != len(SOURCES):
        raise RuntimeError(
            "쇼핑몰별 Qdrant 컬렉션 이름이 중복됩니다: "
            f"{QDRANT_COLLECTIONS}"
        )
    if len(set(IMAGE_S3_PREFIXES.values())) != len(SOURCES):
        raise RuntimeError(
            f"쇼핑몰별 S3 prefix가 중복됩니다: {IMAGE_S3_PREFIXES}"
        )
