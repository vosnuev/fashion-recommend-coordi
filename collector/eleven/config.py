"""11번가 ProductSearch collector 설정."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

COLLECTOR_ROOT = str(Path(__file__).resolve().parent.parent)
if COLLECTOR_ROOT not in sys.path:
    sys.path.insert(0, COLLECTOR_ROOT)

load_dotenv()

KST = ZoneInfo("Asia/Seoul")

ELEVEN_API_KEY = os.getenv("11ST_API_KEY", "").strip()
PRODUCT_SEARCH_URL = os.getenv(
    "ELEVEN_PRODUCT_SEARCH_URL",
    "http://openapi.11st.co.kr/openapi/OpenApiService.tmall",
)
CATEGORY_API_URL = os.getenv(
    "ELEVEN_CATEGORY_API_URL",
    "http://api.11st.co.kr/rest/cateservice/category",
)

PAGE_SIZE = max(1, min(int(os.getenv("ELEVEN_PAGE_SIZE", "50")), 200))
MAX_ITEMS_PER_KEYWORD = max(
    1,
    min(int(os.getenv("ELEVEN_MAX_ITEMS_PER_KEYWORD", "50")), 50),
)
DAILY_MAX_ITEMS = max(1, int(os.getenv("ELEVEN_DAILY_MAX_ITEMS", "1000")))
SEARCH_SORT = os.getenv("ELEVEN_SEARCH_SORT", "N").strip()
REQUEST_INTERVAL_SECONDS = float(
    os.getenv("ELEVEN_REQUEST_INTERVAL_SECONDS", "0.2")
)
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("COLLECTOR_MAX_RETRIES", "3"))
RETRY_DELAY_SECONDS = int(os.getenv("COLLECTOR_RETRY_DELAY_SECONDS", "10"))

CATEGORY_MAPPING_VERSION = os.getenv(
    "ELEVEN_CATEGORY_MAPPING_VERSION", "2026-07-21-v1"
)
CATEGORY_INACTIVE_MISS_THRESHOLD = max(
    1, int(os.getenv("ELEVEN_CATEGORY_INACTIVE_MISS_THRESHOLD", "3"))
)

KEYWORD_GENDER_PREFIXES = [
    prefix.strip()
    for prefix in os.getenv(
        "ELEVEN_KEYWORD_GENDER_PREFIXES",
        os.getenv("NAVER_KEYWORD_GENDER_PREFIXES", ""),
    ).split(",")
    if prefix.strip()
]

SCHEDULE_HOUR = int(os.getenv("ELEVEN_SCHEDULE_HOUR", "4"))
SCHEDULE_MINUTE = int(os.getenv("ELEVEN_SCHEDULE_MINUTE", "0"))
SCHEDULER_POLL_SECONDS = int(os.getenv("SCHEDULER_POLL_SECONDS", "30"))

# 공용 태거 내부 설정은 기존 NAVER_LLM_*/NAVER_CLAUDE_MODEL 키를 읽는다.
# 11번가 전용 값이 있으면 이 프로세스 안에서 해당 키보다 우선 적용한다.
for eleven_key, common_key in (
    ("ELEVEN_LLM_IMAGE_MODE", "NAVER_LLM_IMAGE_MODE"),
    ("ELEVEN_LLM_TEMPERATURE", "NAVER_LLM_TEMPERATURE"),
    ("ELEVEN_LLM_MAX_RETRIES", "NAVER_LLM_MAX_RETRIES"),
    ("ELEVEN_CLAUDE_MODEL", "NAVER_CLAUDE_MODEL"),
):
    value = os.getenv(eleven_key)
    if value:
        os.environ[common_key] = value

TAGGING_PROVIDER = os.getenv("ELEVEN_TAGGING_PROVIDER", "openai").strip().lower()
TAGGING_MODE = os.getenv("ELEVEN_TAGGING_MODE", "batch").strip().lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
BATCH_MAX_REQUESTS = max(
    1, min(int(os.getenv("ELEVEN_BATCH_MAX_REQUESTS", "10000")), 50000)
)
BATCH_COMPLETION_WINDOW = os.getenv(
    "ELEVEN_BATCH_COMPLETION_WINDOW", "24h"
).strip()
BATCH_POLL_SECONDS = max(
    1, int(os.getenv("ELEVEN_BATCH_POLL_SECONDS", "600"))
)
BATCH_INCLUDE_IMAGE = os.getenv(
    "ELEVEN_BATCH_INCLUDE_IMAGE", "false"
).strip().lower() in {"1", "true", "yes"}

if TAGGING_PROVIDER not in {"openai", "claude"}:
    raise ValueError(
        "ELEVEN_TAGGING_PROVIDER 값이 올바르지 않습니다: "
        f"{TAGGING_PROVIDER} (openai | claude)"
    )
if TAGGING_MODE not in {"sync", "batch"}:
    raise ValueError(
        f"ELEVEN_TAGGING_MODE 값이 올바르지 않습니다: {TAGGING_MODE} "
        "(sync | batch)"
    )

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_NAME = os.getenv("POSTGRES_DB", os.getenv("DB_NAME", "fashion_db"))
DB_USER = os.getenv("POSTGRES_USER", os.getenv("DB_USER", "postgres"))
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", os.getenv("DB_PASSWORD", ""))
DB_HOST = os.getenv("POSTGRES_HOST", os.getenv("DB_HOST", "localhost"))
DB_PORT = os.getenv("POSTGRES_PORT", os.getenv("DB_PORT", "5432"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("eleven_collector")

if TAGGING_PROVIDER == "claude" and TAGGING_MODE == "batch":
    logger.warning(
        "ELEVEN_TAGGING_PROVIDER=claude는 batch 모드를 지원하지 않아 "
        "sync로 전환합니다."
    )
    TAGGING_MODE = "sync"
