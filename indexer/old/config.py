"""indexer 설정. 모든 값은 루트 .env / 환경변수에서 읽는다 (하드코딩 금지).

RunPod(GPU)·AWS 어디서든 동작해야 하므로 경로·디바이스·자격증명을
전부 환경변수로 추상화한다 (CLAUDE.md 이식성 규칙).
"""

from __future__ import annotations

import os

from util.env import load_project_env

# 리포 체크아웃 상태면 루트 .env를 읽고, 컨테이너면 --env-file로 주입된다.
load_project_env(__file__)

# ---------- S3 (원본 데이터) ----------
S3_BUCKET = os.getenv("INDEXER_S3_BUCKET", "skn28-cozy")
# 11번 ETRI 패션 코디 데이터셋 프리픽스
ETRI11_PREFIX = os.getenv(
    "INDEXER_ETRI11_PREFIX",
    "11. 한국전자통신연구원_자율성장 인공지능 기술검증(PoC)을 위한 패션 코디 데이터셋/",
)
ETRI11_MDATA_KEY = ETRI11_PREFIX + os.getenv(
    "INDEXER_ETRI11_MDATA_FILE", "mdata.wst.txt.2020.6.23"
)
ETRI11_IMG_PREFIX = ETRI11_PREFIX + "img/"

# ---------- Qdrant (적재 대상, REST) ----------
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY") or None
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "fashion_items")

# ---------- 임베딩 모델 ----------
# Marqo Fashion SigLIP: 패션 도메인 특화 이미지-텍스트 공동 임베딩 (open_clip 로드)
EMBED_MODEL_ID = os.getenv("INDEXER_EMBED_MODEL", "hf-hub:Marqo/marqo-fashionSigLIP")
# auto: cuda 가능하면 cuda, 아니면 cpu
DEVICE = os.getenv("INDEXER_DEVICE", "auto")

# ---------- 실행 파라미터 ----------
BATCH_SIZE = int(os.getenv("INDEXER_BATCH_SIZE", "64"))
DOWNLOAD_WORKERS = int(os.getenv("INDEXER_DOWNLOAD_WORKERS", "8"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
