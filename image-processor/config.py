"""image-processor 설정.

모든 값은 루트 .env / 환경변수에서 읽는다 (하드코딩 금지 — CLAUDE.md 규칙).
참조 문서:
- Confluence > 설계 > "옷장 이미지 파이프라인 설계서" (큐 3단 구조, manifest)
- Confluence > 설계 > "옷장 기능 전체 설계" (콜백 계약, 임베딩 책임)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

_logger = logging.getLogger(__name__)

# 리포에서 실행할 때 거슬러 올라갈 최대 단계 (리포 바깥 .env 오탐 방지)
_MAX_PARENT_DEPTH = 5


def _load_project_env() -> Path | None:
    """루트 .env를 찾아 로드한다 (컨테이너·리포 체크아웃 공용).

    `.env` 위치를 `parent.parent`로 고정하면 실행 위치에 따라 깨진다.
    리포에서는 `image-processor/`의 한 단계 위가 루트지만, 이미지 안에는
    `/app/config.py`만 있고 루트 `.env`는 복사되지 않는다. docker compose가
    `env_file: .env`로 값을 컨테이너 환경변수에 직접 주입하므로 파일이
    없는 게 정상이다.

    (1) ENV_FILE 명시 지정 → (2) 상위 디렉터리 탐색 → (3) 조용히 통과 순.
    항상 override=False라 compose가 주입한 환경변수를 파일이 덮어쓰지 않는다.
    """
    explicit = os.getenv("ENV_FILE", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            load_dotenv(path, override=False)
            return path
        _logger.warning("ENV_FILE 경로에 파일이 없습니다: %s", path)
        return None

    origin = Path(__file__).resolve()
    for parent in list(origin.parents)[:_MAX_PARENT_DEPTH]:
        candidate = parent / ".env"
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return candidate
    return None


_load_project_env()

# ── Redis 큐 (reliable queue: pending → processing → done/dead) ──
# wardrobe-api의 WARDROBE_JOB_QUEUE와 같은 키를 pending으로 사용한다.
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0").strip()
# requirepass 비밀번호 (Infisical: REDIS_PASSWORD). URL에 비밀번호를 내장하지 않고
# 이 변수로 별도 주입한다 — URL에 비밀번호가 이미 들어 있으면 URL 쪽이 우선한다.
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
PENDING_KEY = os.getenv("WARDROBE_JOB_QUEUE", "wardrobe:jobs").strip()
PROCESSING_KEY = f"{PENDING_KEY}:processing"
DEAD_KEY = f"{PENDING_KEY}:dead"
RETRY_HASH = f"{PENDING_KEY}:retries"
REINDEX_PENDING_KEY = os.getenv(
    "WARDROBE_REINDEX_QUEUE",
    "wardrobe:reindex",
).strip()
REINDEX_DEDUP_KEY = f"{REINDEX_PENDING_KEY}:dedupe"
REINDEX_PROCESSING_KEY = f"{REINDEX_PENDING_KEY}:processing"
REINDEX_DEAD_KEY = f"{REINDEX_PENDING_KEY}:dead"
REINDEX_RETRY_HASH = f"{REINDEX_PENDING_KEY}:retries"
MAX_RETRIES = int(os.getenv("WORKER_MAX_RETRIES", "3"))
QUEUE_BLOCK_SEC = int(os.getenv("WORKER_QUEUE_BLOCK_SEC", "5"))

# ── 콜백 (wardrobe-api 구현 계약: X-Internal-Token + job_id 멱등) ──
INTERNAL_TOKEN = os.getenv("WARDROBE_INTERNAL_TOKEN", "")
# 원칙적으로 큐 페이로드의 callback_url을 쓰고, 없을 때만 이 값을 쓴다.
CALLBACK_FALLBACK_URL = os.getenv("WARDROBE_CALLBACK_URL", "")
CALLBACK_RETRIES = int(os.getenv("WORKER_CALLBACK_RETRIES", "3"))
CALLBACK_TIMEOUT = int(os.getenv("WORKER_CALLBACK_TIMEOUT", "15"))

# ── 파이프라인 선택 (pipeline/__init__.py 레지스트리 키) ──
PIPELINE_IMPL = os.getenv("WORKER_PIPELINE", "gemini-edit")

# ── 모델 ──
DEVICE = os.getenv("DEVICE", "")  # 비우면 cuda 가능 시 cuda (임베딩용)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_ENUM_MODEL = os.getenv("GEMINI_ENUM_MODEL", "gemini-3.5-flash")
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_FLASH_IMAGE_MODEL", "gemini-3.1-flash-image")
GEMINI_TAG_MODEL = os.getenv("GEMINI_TAG_MODEL", "gemini-3.5-flash")

QWEN_MODEL = os.getenv("WORKER_QWEN_MODEL", "Qwen/Qwen3-VL-8B-Instruct")
QWEN_MAX_NEW_TOKENS = int(os.getenv("WORKER_QWEN_MAX_NEW_TOKENS", "768"))
QWEN_MIN_PIXELS = int(os.getenv("WORKER_QWEN_MIN_PIXELS", str(256 * 28 * 28)))
QWEN_MAX_PIXELS = int(os.getenv("WORKER_QWEN_MAX_PIXELS", str(1024 * 28 * 28)))
ITEM_NORMALIZE_MAX_PX = int(os.getenv("WORKER_ITEM_NORMALIZE_MAX_PX", "1024"))

# ── 임베딩 (설계서에 없던 단계 — 조율안에 따라 Worker 책임으로 추가) ──
EMBED_ENABLED = os.getenv("WORKER_EMBED_ENABLED", "1") == "1"
IMAGE_EMBED_MODEL = os.getenv("WORKER_IMAGE_EMBED_MODEL", "hf-hub:Marqo/marqo-fashionSigLIP")
TEXT_EMBED_MODEL = os.getenv("WORKER_TEXT_EMBED_MODEL", "BAAI/bge-m3")
EMBEDDING_VERSION = os.getenv("WARDROBE_EMBEDDING_VERSION", "fashionsiglip-v1")

# ── 처리 스키마 버전 (manifest에 기록) ──
SCHEMA_VERSION = "1.0"
PIPELINE_VERSION = os.getenv("WORKER_PIPELINE_VERSION", "gemini-edit-v1")
