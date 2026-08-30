"""
공통 설정. 환경별 차이는 dev.py / prod.py에서 오버라이드한다.

환경변수는 프로젝트 루트(SKN28-FINAL-1Team/)의 .env 하나로 관리한다.
시크릿은 코드에 하드코딩하지 않는다 (CLAUDE.md 규칙).
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

# api/config/settings/base.py → BASE_DIR = api/
BASE_DIR = Path(__file__).resolve().parent.parent.parent
# 루트 .env (api/의 상위 = 프로젝트 루트)
load_dotenv(BASE_DIR.parent / ".env")

# ML 추론 코드는 ml/ 아래에 두고 웹 계층이 import해서 쓴다 (CLAUDE.md §7).
# ml/을 경로에 올려 `from body_measurement.src import inference` 로 접근한다.
ML_ROOT = Path(os.getenv("ML_ROOT") or (BASE_DIR.parent / "ml"))
if str(ML_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_ROOT))

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "insecure-dev-only-change-me")

DEBUG = False
ALLOWED_HOSTS: list[str] = [
    h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",  # ArrayField/GinIndex 시스템 체크 지원
    # 3rd party
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    # local apps
    "apps.users",
    "apps.catalog",
    "apps.weather",
    "apps.home",
    "apps.wardrobe",
    "apps.recommend",
    "apps.chat",
    "apps.goldenset",
    "apps.style_calendar",
    "apps.lookbook",
]

# ------------------------------------------------------------
# 로깅
#
# 설정이 없으면 Django 기본 LOGGING이 적용되는데, 그 console 핸들러에는
# require_debug_true 필터가 걸려 있어 DEBUG=False(prod)에서는 아무것도
# 출력되지 않는다. 또 apps.* 로거는 핸들러 없는 root로 떨어져
# logging.lastResort(WARNING 이상, 포맷 없음)만 stderr로 나간다.
# → 여기서 명시적으로 stdout 핸들러를 붙여 DEBUG 여부와 무관하게 남긴다.
#
# 요청 단위 액세스 로그는 Django가 아니라 gunicorn이 담당한다
# (docker-compose.yml / Dockerfile의 --access-logfile -).
# ------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

LOGGING = {
    "version": 1,
    # Django/서드파티가 이미 만들어 둔 로거를 죽이지 않는다.
    "disable_existing_loggers": False,
    "filters": {
        "request_context": {
            "()": "config.observability.RequestContextFilter",
        },
    },
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "json": {
            "()": "config.observability.JsonFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            # gunicorn --capture-output이 stdout/stderr를 에러 로그로 모은다.
            "stream": "ext://sys.stdout",
        },
        "reference_recommendation": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["request_context"],
            "stream": "ext://sys.stdout",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        # 레퍼런스 추천 운영 지표는 친구 이름·스냅샷·벡터를 허용하지 않는
        # 전용 JSON 포맷으로만 남긴다.
        "apps.chat.reference_recommendation": {
            "handlers": ["reference_recommendation"],
            "level": "INFO",
            "propagate": False,
        },
        # 애플리케이션 코드 (apps.users, apps.recommend, ...)
        "apps": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "django": {
            "handlers": ["console"],
            "level": os.getenv("DJANGO_LOG_LEVEL", "INFO").upper(),
            "propagate": False,
        },
        # 4xx/5xx 응답과 처리되지 않은 예외. 기본은 ERROR라 400/404가 안 보인다.
        "django.request": {
            "handlers": ["console"],
            "level": os.getenv("DJANGO_REQUEST_LOG_LEVEL", "WARNING").upper(),
            "propagate": False,
        },
        # SQL 쿼리 로그. 기본은 끔 — 필요할 때 DJANGO_DB_LOG_LEVEL=DEBUG.
        "django.db.backends": {
            "handlers": ["console"],
            "level": os.getenv("DJANGO_DB_LOG_LEVEL", "WARNING").upper(),
            "propagate": False,
        },
    },
}

MIDDLEWARE = [
    "config.middleware.RequestIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # CORS: 응답을 생성할 수 있는 미들웨어(CommonMiddleware 등)보다 위에 있어야
    # preflight(OPTIONS)와 에러 응답에도 CORS 헤더가 붙는다.
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.chat.middleware.ChatGuestCookieRefreshMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

HEALTHCHECK_TIMEOUT_SECONDS = float(os.getenv("HEALTHCHECK_TIMEOUT_SECONDS", "1.0"))

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ------------------------------------------------------------
# Database (PostgreSQL, collector와 동일한 환경변수 키 사용)
# ------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "fashion_db"),
        "USER": os.getenv("POSTGRES_USER", "postgres"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }
}

AUTH_USER_MODEL = "users.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ------------------------------------------------------------
# DRF / JWT
# ------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    # 스로틀 클래스는 각 뷰에서 지정한다 (전역 적용 안 함). 여기엔 요율만 등록.
    # invite_preview: 초대코드가 곧 열람 권한이라 무차별 대입을 막아야 한다.
    "DEFAULT_THROTTLE_RATES": {
        "invite_preview": "20/min",
    },
}

# ── 캐시 ───────────────────────────────────────────────
#
# throttle 별칭을 따로 둔다. DRF 스로틀 카운터를 기본 LocMemCache에 저장하면
# gunicorn 워커마다 따로 세기 때문에, 실제 허용량이 (요율 x 워커 수)로 부풀어
# 오른다 (GUNICORN_WORKERS=3 환경에서 20/min 제한이 사실상 60/min이 됐다).
# Redis가 있으면 워커 간에 카운터를 공유하고, 없으면 LocMemCache로 폴백한다.
_REDIS_URL = os.getenv("REDIS_URL", "")
# requirepass 비밀번호는 URL에 내장하지 않고 별도 주입한다 (services/jobs.py와 동일 규약)
_REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    },
    "throttle": (
        {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _REDIS_URL,
            **(
                {"OPTIONS": {"password": _REDIS_PASSWORD}} if _REDIS_PASSWORD else {}
            ),
        }
        if _REDIS_URL
        else {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "throttle-fallback",
        }
    ),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=int(os.getenv("JWT_ACCESS_MINUTES", "30"))
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(
        days=int(os.getenv("JWT_REFRESH_DAYS", "14"))
    ),
    "ROTATE_REFRESH_TOKENS": True,
    # 회전된 이전 refresh 토큰 재사용 차단 (token_blacklist 앱 필요)
    "BLACKLIST_AFTER_ROTATION": True,
}

# 이메일 소유 인증. 로컬에서는 콘솔 출력으로 코드를 확인하고,
# 배포 환경에서는 EMAIL_BACKEND/SMTP 값을 주입한다.
EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "true").lower() == "true"
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "COZY <no-reply@cozy.local>")
EMAIL_VERIFICATION_CODE_TTL_SECONDS = int(
    os.getenv("EMAIL_VERIFICATION_CODE_TTL_SECONDS", "600")
)
EMAIL_VERIFICATION_RESEND_SECONDS = int(
    os.getenv("EMAIL_VERIFICATION_RESEND_SECONDS", "60")
)
EMAIL_VERIFICATION_MAX_ATTEMPTS = int(
    os.getenv("EMAIL_VERIFICATION_MAX_ATTEMPTS", "5")
)

# ------------------------------------------------------------
# 소셜 로그인 (naver / kakao / google)
# 검색 API용 NAVER_CLIENT_ID와 혼동하지 않도록 *_OAUTH_* 접두사를 쓴다.
# ------------------------------------------------------------
OAUTH_PROVIDERS = {
    "naver": {
        "client_id": os.getenv("NAVER_OAUTH_CLIENT_ID", ""),
        "client_secret": os.getenv("NAVER_OAUTH_CLIENT_SECRET", ""),
        "token_url": "https://nid.naver.com/oauth2.0/token",
        "profile_url": "https://openapi.naver.com/v1/nid/me",
    },
    "kakao": {
        "client_id": os.getenv("KAKAO_OAUTH_REST_API_KEY", ""),
        "client_secret": os.getenv("KAKAO_OAUTH_CLIENT_SECRET", ""),  # 선택(보안 강화 시)
        # token 방식 로그인(네이티브 앱 SDK) 검증용 앱 ID (숫자).
        # 다른 카카오 앱에서 발급된 access_token으로 로그인하는 것을 차단한다.
        "app_id": os.getenv("KAKAO_APP_ID", ""),
        "token_url": "https://kauth.kakao.com/oauth/token",
        "token_info_url": "https://kapi.kakao.com/v1/user/access_token_info",
        "profile_url": "https://kapi.kakao.com/v2/user/me",
    },
    "google": {
        "client_id": os.getenv("GOOGLE_OAUTH_CLIENT_ID", ""),
        "client_secret": os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", ""),
        "token_url": "https://oauth2.googleapis.com/token",
        # token 방식 로그인(네이티브 앱 SDK) 검증용. aud(발급 대상 client_id)를 대조한다.
        "token_info_url": "https://www.googleapis.com/oauth2/v3/tokeninfo",
        "profile_url": "https://www.googleapis.com/oauth2/v3/userinfo",
        # 네이티브 앱 SDK가 받아오는 토큰의 aud는 웹이 아니라 그 플랫폼의 클라이언트 ID다.
        # 웹 하나만 대조하면 앱 로그인이 전부 막히므로, 같은 프로젝트의 클라이언트를 모두 허용한다.
        "allowed_client_ids": [
            client_id
            for client_id in (
                os.getenv("GOOGLE_OAUTH_CLIENT_ID", ""),
                os.getenv("GOOGLE_OAUTH_IOS_CLIENT_ID", ""),
                os.getenv("GOOGLE_OAUTH_ANDROID_CLIENT_ID", ""),
            )
            if client_id
        ],
    },
    # 애플은 client_secret을 정적 문자열이 아닌 ES256 JWT로 동적 생성한다.
    # profile_url 없음 — 사용자 정보는 id_token(JWT) 디코딩으로 획득한다.
    "apple": {
        "client_id":   os.getenv("APPLE_CLIENT_ID", ""),    # Service ID (com.example.app)
        "team_id":     os.getenv("APPLE_TEAM_ID", ""),      # 10자리 팀 ID
        "key_id":      os.getenv("APPLE_KEY_ID", ""),       # 개인키 Key ID
        "private_key": os.getenv("APPLE_PRIVATE_KEY", ""),  # PEM 전체 문자열 (\n 포함)
        "token_url":   "https://appleid.apple.com/auth/token",
    },
}

OAUTH_REQUEST_TIMEOUT = int(os.getenv("OAUTH_REQUEST_TIMEOUT", "10"))

# ------------------------------------------------------------
# CORS (브라우저 교차 출처 요청 허용)
# 콤마 구분, 스킴 포함 origin만 (경로 없음). 예:
#   CORS_ALLOWED_ORIGINS=https://skn-1st-mobile.expo.app,http://localhost:19006
# ------------------------------------------------------------
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
]
CORS_ALLOW_CREDENTIALS = os.getenv("CORS_ALLOW_CREDENTIALS", "true").lower() in {
    "1",
    "true",
    "yes",
}
# ------------------------------------------------------------
# 옷장 (wardrobe) — S3 / 처리 큐
# 상세 값은 apps/wardrobe/services/* 에서 환경변수로 직접 읽는다.
# 필수: WARDROBE_S3_BUCKET, REDIS_URL, WARDROBE_INTERNAL_TOKEN,
#       WARDROBE_CALLBACK_URL
# ------------------------------------------------------------

# ------------------------------------------------------------
# 스타일 캘린더 (style_calendar) — S3
# 상세 값은 apps/style_calendar/services/storage.py에서 읽는다.
# 필수: CALENDAR_S3_BUCKET(미설정 시 WARDROBE_S3_BUCKET 사용)
# 사진 처리 큐와 callback은 기존 옷장 업로드 흐름을 그대로 사용한다.
# ------------------------------------------------------------

# ------------------------------------------------------------
# 룩북 (lookbook) — S3
# 상세 값은 apps/lookbook/services/storage.py에서 읽는다.
# 선택: LOOKBOOK_S3_BUCKET (미설정 시 CALENDAR_S3_BUCKET → WARDROBE_S3_BUCKET)
# 사진 처리 큐와 callback은 기존 옷장 업로드 흐름을 그대로 사용하며,
# '입은 옷'과 겹치는 대분류는 큐 페이로드의 exclude_categories로 제외한다.
# ------------------------------------------------------------

# ------------------------------------------------------------
# Qdrant 벡터 DB (apps.recommend)
# 컬렉션 스키마는 apps/recommend/services/qdrant.py가 소유하고
# `manage.py init_qdrant`로 생성한다.
# ------------------------------------------------------------
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
QDRANT_TIMEOUT = int(os.getenv("QDRANT_TIMEOUT", "10"))
# 임베딩 모델 차원 (FashionSigLIP=768, BGE-M3=1024). 모델 교체 시에만 변경.
QDRANT_IMAGE_VECTOR_DIM = int(os.getenv("QDRANT_IMAGE_VECTOR_DIM", "768"))
QDRANT_TEXT_VECTOR_DIM = int(os.getenv("QDRANT_TEXT_VECTOR_DIM", "1024"))
QDRANT_GOLDEN_OUTFIT_COLLECTION = os.getenv(
    "QDRANT_GOLDEN_OUTFIT_COLLECTION", "outfit_goldenset"
).strip()
QDRANT_GOLDEN_ITEM_COLLECTION = os.getenv(
    "QDRANT_GOLDEN_ITEM_COLLECTION", "goldenset_items"
).strip()
QDRANT_WARDROBE_COLLECTION = os.getenv(
    "QDRANT_WARDROBE_COLLECTION", "wardrobe_items"
).strip()
PRODUCT_NAVER_QDRANT_COLLECTION = os.getenv(
    "PRODUCT_NAVER_QDRANT_COLLECTION", "products_naver_v1"
).strip()
PRODUCT_ELEVEN_QDRANT_COLLECTION = os.getenv(
    "PRODUCT_ELEVEN_QDRANT_COLLECTION", "products_eleven_v1"
).strip()
QDRANT_KNOWLEDGE_COLLECTION = os.getenv(
    "QDRANT_KNOWLEDGE_COLLECTION", "knowledge"
).strip()
# 공유 옷을 기준으로 내 옷을 찾을 때 적용하는 FashionSigLIP cosine 최소 점수.
# 실제 사용자 평가 데이터가 쌓이면 환경별로 조정하며 코드에 임계값을 박지 않는다.
SHARED_REFERENCE_VISUAL_MIN_SCORE = float(
    os.getenv("SHARED_REFERENCE_VISUAL_MIN_SCORE", "0.75")
)
# 스타일 fallback은 단일 색상 일치만으로 통과하지 않도록 0.30을 기본값으로 둔다.
SHARED_REFERENCE_STYLE_MIN_SCORE = float(
    os.getenv("SHARED_REFERENCE_STYLE_MIN_SCORE", "0.30")
)

# BGE-M3 질의 임베딩은 골든셋 적재와 같은 벡터 공간을 사용한다.
TEXT_EMBEDDING_API_URL = os.getenv("TEXT_EMBEDDING_API_URL", "").strip()
TEXT_EMBEDDING_API_TOKEN = os.getenv("TEXT_EMBEDDING_API_TOKEN", "").strip()
TEXT_EMBEDDING_TIMEOUT_SECONDS = int(os.getenv("TEXT_EMBEDDING_TIMEOUT_SECONDS", "15"))
# 원칙(knowledge) 조회는 없어도 추천이 성립하는 부가 정보라 본 검색(15초)보다 짧게
# 끊는다. 다만 이 값은 **호출당** 상한이고 조회는 임베딩 1회 + Qdrant 1~2회이므로,
# 사용자가 실제로 더 기다릴 수 있는 시간은 이 값의 두 배 남짓이다. 정상 응답은 보통
# 1초 안쪽이라 6초면 느린 날에도 결과를 버리지 않으면서 지연을 통제할 수 있다.
PRINCIPLE_RETRIEVAL_TIMEOUT_SECONDS = int(
    os.getenv("PRINCIPLE_RETRIEVAL_TIMEOUT_SECONDS", "6")
)

# 코디 조합 정렬에 골든셋 원칙을 반영할지. 실측에서 코디 14건 중 8건의 조합이 바뀌어
# 기본을 켰다. 다만 "바뀐 조합이 더 낫다"를 정량으로 잴 장치는 아직 없다. 문제가 보이면
# 환경변수로 끄면 되고, 꺼진 상태의 정렬 키는 이 기능 이전과 완전히 같다.
PRINCIPLE_COMPOSITION_ENABLED = (
    os.getenv("PRINCIPLE_COMPOSITION_ENABLED", "true").strip().lower() == "true"
)
TEXT_EMBEDDING_EXPECTED_DIM = int(
    os.getenv("TEXT_EMBEDDING_EXPECTED_DIM", str(QDRANT_TEXT_VECTOR_DIM))
)

# Gemini 기반 코디 사진 평가 (apps.recommend)
# 요청/응답과 질의 컨텍스트는 outfit_analysis 테이블에 기록한다.
# 원본 사진 버킷(OUTFIT_S3_BUCKET 또는 WARDROBE_S3_BUCKET)은
# apps/recommend/services/storage.py에서 환경변수로 직접 읽는다.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_API_BASE_URL = os.getenv(
    "GEMINI_API_BASE_URL", "https://generativelanguage.googleapis.com"
).rstrip("/")
# 사진을 base64로 실어 보내므로 업로드 시간이 붙는다. 30s로는 큰 사진에서 타임아웃한다
# (전송본은 apps/recommend/services/imaging.py가 1024px로 축소한다).
GEMINI_TIMEOUT_SECONDS = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "60"))

# 코디 평가 비동기 처리 (접수 API ↔ outfit-worker)
# 큐 키·재시도는 apps/recommend/services/queue.py가 환경변수로 직접 읽는다.
# 비로그인 접수 건은 UUID를 아는 사람이 조회한다 — 무기한 열어두지 않는다.
OUTFIT_ANON_TTL_HOURS = int(os.getenv("OUTFIT_ANON_TTL_HOURS", "24"))
# 익명 접수 건의 소유권 이전(claim) 허용 시간. 조회(24h)보다 훨씬 짧게 잡는다 —
# claim은 읽기가 아니라 쓰기이고, 성공하면 사진·체형까지 열리는 권한 상승 경로다.
# 로그인 유도는 평가 결과 직후에 일어나므로 1시간이면 충분하다.
OUTFIT_CLAIM_TTL_MINUTES = int(os.getenv("OUTFIT_CLAIM_TTL_MINUTES", "60"))
# 한 번의 claim 요청에서 처리할 최대 건수
OUTFIT_CLAIM_MAX_ITEMS = int(os.getenv("OUTFIT_CLAIM_MAX_ITEMS", "20"))
# 워커가 죽어 방치된 QUEUED/PROCESSING 행을 FAILED로 정리하는 기준 (프론트 무한 폴링 방지)
OUTFIT_STALE_AFTER_MINUTES = int(os.getenv("OUTFIT_STALE_AFTER_MINUTES", "5"))
# 프론트가 폴링 간격을 하드코딩하지 않도록 서버가 응답에 실어 보낸다
OUTFIT_POLL_AFTER_MS = int(os.getenv("OUTFIT_POLL_AFTER_MS", "2000"))
OUTFIT_ESTIMATED_SECONDS = int(os.getenv("OUTFIT_ESTIMATED_SECONDS", "30"))

# ── 리트리버: 후보 수집 ────────────────────────────────────
# 벡터 질의가 없는 경로(오늘의 룩)는 scroll로 후보를 모은다. scroll은 관련도가
# 아니라 **포인트 ID 순서**라, 예전처럼 앞에서 20건만 끊으면 골든셋이 몇 건이든
# 언제나 같은 20건만 후보가 된다. 체형·취향을 바꿔도 결과가 안 변하던 원인이다.
# 이제 필터를 통과한 코디를 전부 훑고 파이썬에서 점수화한다.
#
# 하루 한 번, 사용자당 한 번 도는 작업이라 이 비용은 감당할 수 있다.
RETRIEVER_SCROLL_CAP = int(os.getenv("RETRIEVER_SCROLL_CAP", "2000"))
RETRIEVER_SCROLL_PAGE = int(os.getenv("RETRIEVER_SCROLL_PAGE", "256"))
RETRIEVER_WARDROBE_ID_CAP = int(os.getenv("RETRIEVER_WARDROBE_ID_CAP", "1000"))

#: 사람 쌍대 비교 앵커(human_score)를 규칙 가감점으로 환산할 때의 최대 폭.
#: 중앙값 50을 0으로 두고 ±이 값 범위로 옮긴 뒤 score_confidence로 줄인다.
#: rule_prefer(15)·context_match(10)와 같은 척도이며, 0이면 앵커를 쓰지 않는다.
RETRIEVER_HUMAN_SCORE_WEIGHT = float(
    os.getenv("RETRIEVER_HUMAN_SCORE_WEIGHT", "15")
)

# 코디 payload의 아이템 요약에는 fit·length·pattern이 없다. 체형 규칙은 정확히
# 그 축으로 조건을 걸기 때문에, 붙이지 않으면 모든 체형 규칙이 0점이 된다.
# 태그는 아이템 컬렉션(goldenset_items)에 이미 있으므로 조회 시점에 합친다.
# 재적재로 payload를 늘리면 이 왕복이 사라진다.
RETRIEVER_ITEM_TAG_JOIN = os.getenv("RETRIEVER_ITEM_TAG_JOIN", "1").strip().lower() in {
    "1", "true", "yes", "y",
}
# 한 번의 retrieve에 넣을 아이템 포인트 id 수.
RETRIEVER_ITEM_TAG_BATCH = int(os.getenv("RETRIEVER_ITEM_TAG_BATCH", "256"))
# 프로세스 안에서 아이템 태그를 재사용하는 시간(초). 골든셋은 자주 안 바뀌고
# 워커는 같은 코디를 사용자 수만큼 반복해서 본다. 0이면 캐시하지 않는다.
RETRIEVER_ITEM_TAG_CACHE_SECONDS = int(
    os.getenv("RETRIEVER_ITEM_TAG_CACHE_SECONDS", "300")
)

# ── 오늘의 룩: 착용 이미지 생성 (OpenRouter) ──────────────
# 지금까지 신체치수 추정(ml/body_measurement)이 os.getenv로 직접 읽고 있었다.
# settings로 올려 두 곳이 같은 출처를 보게 한다.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
# 골든셋 아이템 이미지를 입력으로 "정면을 보는 사람이 그 옷을 입은" 이미지를 만든다.
# 결과는 골든 코디와 같은 S3 위치에 저장하고, 이미 있으면 다시 만들지 않는다 —
# 같은 코디가 여러 사용자·여러 날에 추천되므로 코디당 한 번만 생성하면 된다.
DAILY_LOOK_RENDER_ENABLED = os.getenv("DAILY_LOOK_RENDER_ENABLED", "1").strip().lower() in {
    "1", "true", "yes", "y",
}
DAILY_LOOK_RENDER_MODEL = os.getenv("DAILY_LOOK_RENDER_MODEL", "qwen/qwen-image-3-pro")
# 이미지 생성은 채팅 API가 아니라 전용 엔드포인트를 쓴다. 채팅 API에
# modalities=["image","text"]를 붙이면 404 "No endpoints found that support the
# requested output modalities"를 받는다.
DAILY_LOOK_RENDER_URL = os.getenv(
    "DAILY_LOOK_RENDER_URL", "https://openrouter.ai/api/v1/images"
)
# 전신이 담겨야 하므로 세로로 긴 비율.
DAILY_LOOK_RENDER_ASPECT_RATIO = os.getenv("DAILY_LOOK_RENDER_ASPECT_RATIO", "9:16")
DAILY_LOOK_RENDER_RESOLUTION = os.getenv("DAILY_LOOK_RENDER_RESOLUTION", "1K")
# 이미지 생성은 텍스트보다 훨씬 느리다. 워커에서 도는 작업이라 넉넉히 준다.
DAILY_LOOK_RENDER_TIMEOUT_SECONDS = int(
    os.getenv("DAILY_LOOK_RENDER_TIMEOUT_SECONDS", "180")
)
# 참조로 넘길 아이템 이미지 수의 **상한**. 늘리면 입력 토큰과 요금이 함께 오른다.
# 백엔드별 한도(OpenRouter 4장 / Gemini 14장)는 outfit_render가 따로 적용한다.
DAILY_LOOK_RENDER_MAX_REFERENCES = int(
    os.getenv("DAILY_LOOK_RENDER_MAX_REFERENCES", "8")
)

# ── 착용 이미지: 참조가 많을 때 쓰는 두 번째 백엔드 (Gemini) ──
# qwen/qwen-image-3-pro는 제공자가 Alibaba 하나뿐이고 참조 이미지가 4장까지다.
#
#     Provider rejections: Alibaba: input_references:
#     must have between 0 and 4 items
#
# 아이템이 다섯 이상인 코디는 무엇을 버려도 그 코디가 아니게 되므로, 그때는
# 참조를 14장까지 받는 Gemini 3.1 Flash Image로 넘긴다. 1K 기준 장당 약 $0.067로
# Qwen($0.04~)보다 비싸지만, 착용 이미지는 코디당 한 번 만들고 재사용한다.
#
# 이 값 **이상**의 참조가 필요하면 Gemini를 쓴다. 4로 낮추면 4장짜리 코디도
# Gemini로 가고, 아주 크게 두면 항상 OpenRouter만 쓴다.
DAILY_LOOK_RENDER_GEMINI_THRESHOLD = int(
    os.getenv("DAILY_LOOK_RENDER_GEMINI_THRESHOLD", "5")
)
DAILY_LOOK_RENDER_GEMINI_MODEL = os.getenv(
    "DAILY_LOOK_RENDER_GEMINI_MODEL", "gemini-3.1-flash-image"
)
# 이미지 모델은 generateContent가 아니라 Interactions API를 쓴다. 화면비·해상도를
# response_format으로 직접 지정할 수 있어야 전신 9:16을 강제할 수 있다.
DAILY_LOOK_RENDER_GEMINI_URL = os.getenv(
    "DAILY_LOOK_RENDER_GEMINI_URL", f"{GEMINI_API_BASE_URL}/v1beta/interactions"
)
# 결과 이미지 형식. Gemini는 JPEG만 내준다.
#
#     The value 'image/png' is not supported for 'response_format.mime_type'.
#     Supported values: 'image/jpeg'.
#
# 입력은 PNG로 받는다 — 이 제약은 출력에만 걸린다. 백엔드마다 형식이 다르므로
# 저장할 때 실제 바이트를 보고 확장자와 Content-Type을 정한다 (outfit_render).
DAILY_LOOK_RENDER_GEMINI_MIME_TYPE = os.getenv(
    "DAILY_LOOK_RENDER_GEMINI_MIME_TYPE", "image/jpeg"
)

# ── 채팅 추천·혼합 출처 렌더링 ─────────────────────────────
# main의 오늘의 룩 렌더 설정은 그대로 두고, 채팅 추천 카드용 비동기 렌더가
# 같은 결과 저장소를 독립된 작업 큐로 사용한다. 어떤 이미지 모델을 부를지는
# 아래 OUTFIT_RENDER_BACKEND가 정한다.
OUTFIT_RENDER_ENABLED = os.getenv("OUTFIT_RENDER_ENABLED", "1").strip().lower() in {
    "1", "true", "yes", "y",
}
OUTFIT_RENDER_MODEL = os.getenv(
    "OUTFIT_RENDER_MODEL", DAILY_LOOK_RENDER_MODEL
).strip()
OUTFIT_RENDER_URL = os.getenv("OUTFIT_RENDER_URL", DAILY_LOOK_RENDER_URL).strip()
# 채팅 추천 카드 이미지를 만들 백엔드. gemini(기본) | openrouter
#
# 기본을 Gemini로 둔 이유: Qwen(OpenRouter)은 참조 이미지가 4장까지라 아이템이
# 다섯 이상인 코디를 통째로 넣을 수 없다. 채팅 카드는 아이템 수가 들쭉날쭉해
# 백엔드가 코디마다 갈리면 결과 톤도 갈리므로 한쪽으로 고정한다.
# 되돌릴 때는 이 값만 openrouter로 바꾸고 워커를 재시작한다 (Qwen 경로는 그대로 산다).
#
# ⚠️ 위 OUTFIT_RENDER_MODEL/URL은 openrouter 백엔드와 **가상 착장**이 계속 쓴다.
# 가상 착장은 이 스위치의 영향을 받지 않는다.
OUTFIT_RENDER_BACKEND = os.getenv("OUTFIT_RENDER_BACKEND", "gemini").strip().lower()
# Gemini 백엔드의 모델·엔드포인트·출력 형식. 오늘의 룩 렌더 설정을 기본값으로
# 물려받아, 따로 지정하지 않으면 두 경로가 같은 모델을 본다.
OUTFIT_RENDER_GEMINI_MODEL = os.getenv(
    "OUTFIT_RENDER_GEMINI_MODEL", DAILY_LOOK_RENDER_GEMINI_MODEL
).strip()
OUTFIT_RENDER_GEMINI_URL = os.getenv(
    "OUTFIT_RENDER_GEMINI_URL", DAILY_LOOK_RENDER_GEMINI_URL
).strip()
OUTFIT_RENDER_GEMINI_MIME_TYPE = os.getenv(
    "OUTFIT_RENDER_GEMINI_MIME_TYPE", DAILY_LOOK_RENDER_GEMINI_MIME_TYPE
).strip()
OUTFIT_RENDER_ASPECT_RATIO = os.getenv(
    "OUTFIT_RENDER_ASPECT_RATIO", DAILY_LOOK_RENDER_ASPECT_RATIO
).strip()
OUTFIT_RENDER_RESOLUTION = os.getenv(
    "OUTFIT_RENDER_RESOLUTION", DAILY_LOOK_RENDER_RESOLUTION
).strip()
OUTFIT_RENDER_TIMEOUT_SECONDS = float(
    os.getenv("OUTFIT_RENDER_TIMEOUT_SECONDS", str(DAILY_LOOK_RENDER_TIMEOUT_SECONDS))
)
OUTFIT_RENDER_REFERENCE_TIMEOUT_SECONDS = float(
    os.getenv("OUTFIT_RENDER_REFERENCE_TIMEOUT_SECONDS", "30")
)
OUTFIT_RENDER_MAX_REFERENCES = int(
    os.getenv("OUTFIT_RENDER_MAX_REFERENCES", str(DAILY_LOOK_RENDER_MAX_REFERENCES))
)
OUTFIT_RENDER_MAX_REFERENCE_BYTES = int(
    os.getenv("OUTFIT_RENDER_MAX_REFERENCE_BYTES", str(10 * 1024 * 1024))
)
OUTFIT_RENDER_MAX_TOTAL_REFERENCE_BYTES = int(
    os.getenv("OUTFIT_RENDER_MAX_TOTAL_REFERENCE_BYTES", str(40 * 1024 * 1024))
)
OUTFIT_RENDER_MAX_OUTPUT_BYTES = int(
    os.getenv("OUTFIT_RENDER_MAX_OUTPUT_BYTES", str(20 * 1024 * 1024))
)
OUTFIT_RENDER_WARDROBE_BUCKET = os.getenv(
    "OUTFIT_RENDER_WARDROBE_BUCKET", os.getenv("WARDROBE_S3_BUCKET", "")
).strip()
OUTFIT_RENDER_PRODUCT_BUCKET = os.getenv(
    "OUTFIT_RENDER_PRODUCT_BUCKET", os.getenv("PRODUCT_IMAGE_S3_BUCKET", "")
).strip()
OUTFIT_RENDER_GOLDENSET_BUCKET = os.getenv(
    "OUTFIT_RENDER_GOLDENSET_BUCKET", os.getenv("GOLDEN_S3_BUCKET", "")
).strip()
OUTFIT_RENDER_RESULT_BUCKET = (
    os.getenv("OUTFIT_RENDER_RESULT_BUCKET", "").strip()
    or os.getenv("OUTFIT_S3_BUCKET", "").strip()
    or os.getenv("WARDROBE_S3_BUCKET", "").strip()
)
OUTFIT_RENDER_RESULT_PREFIX = os.getenv(
    "OUTFIT_RENDER_RESULT_PREFIX", "outfit-renders/v1"
).strip("/")
OUTFIT_RENDER_PRESIGNED_GET_TTL_SECONDS = int(
    os.getenv("OUTFIT_RENDER_PRESIGNED_GET_TTL_SECONDS", "3600")
)
VIRTUAL_TRY_ON_RESULT_PREFIX = os.getenv(
    "VIRTUAL_TRY_ON_RESULT_PREFIX", "virtual-try-on/v1"
).strip("/")
VIRTUAL_TRY_ON_MAX_PERSON_IMAGE_BYTES = int(
    os.getenv("VIRTUAL_TRY_ON_MAX_PERSON_IMAGE_BYTES", str(15 * 1024 * 1024))
)
# 사용자 전신 사진을 워커가 읽을 때까지 잠시 두는 prefix.
#
# ⚠️ **삭제는 코드가 하지 않는다.** 이 prefix 에 S3 수명주기 규칙(예: 1일 만료)을
# 걸어 두어야 한다. 규칙이 없으면 사용자 사진이 버킷에 영구히 쌓인다 — 화면에서
# "생성에만 쓰고 자동으로 지워져요"라고 약속하는 값이라 배포 점검 항목이다.
VIRTUAL_TRY_ON_PERSON_PREFIX = os.getenv(
    "VIRTUAL_TRY_ON_PERSON_PREFIX", "virtual-try-on/person-tmp"
).strip("/")
# 생성 중 폴링 간격. 이미지 생성이 수십 초라 코디 평가(OUTFIT_POLL_AFTER_MS)보다
# 느슨하게 준다 — 2초마다 물어봐야 답이 바뀌지 않는다.
VIRTUAL_TRY_ON_POLL_AFTER_MS = int(
    os.getenv("VIRTUAL_TRY_ON_POLL_AFTER_MS", "5000")
)
OUTFIT_RENDER_QUEUE_PENDING_KEY = os.getenv(
    "OUTFIT_RENDER_QUEUE_PENDING_KEY", "outfit:render:pending"
)
OUTFIT_RENDER_QUEUE_PROCESSING_KEY = os.getenv(
    "OUTFIT_RENDER_QUEUE_PROCESSING_KEY", "outfit:render:processing"
)
OUTFIT_RENDER_QUEUE_DEAD_KEY = os.getenv(
    "OUTFIT_RENDER_QUEUE_DEAD_KEY", "outfit:render:dead"
)
OUTFIT_RENDER_QUEUE_RETRY_KEY = os.getenv(
    "OUTFIT_RENDER_QUEUE_RETRY_KEY", "outfit:render:retry"
)
OUTFIT_RENDER_QUEUE_BLOCK_SECONDS = int(
    os.getenv("OUTFIT_RENDER_QUEUE_BLOCK_SECONDS", "5")
)
OUTFIT_RENDER_QUEUE_MAX_RETRIES = int(
    os.getenv("OUTFIT_RENDER_QUEUE_MAX_RETRIES", "3")
)
OUTFIT_RENDER_QUEUE_CONNECT_TIMEOUT_SECONDS = float(
    os.getenv("OUTFIT_RENDER_QUEUE_CONNECT_TIMEOUT_SECONDS", "1.0")
)
OUTFIT_RENDER_QUEUE_ORPHAN_AGE_SECONDS = int(
    os.getenv("OUTFIT_RENDER_QUEUE_ORPHAN_AGE_SECONDS", "30")
)
OUTFIT_RENDER_QUEUE_ORPHAN_SWEEP_SECONDS = int(
    os.getenv("OUTFIT_RENDER_QUEUE_ORPHAN_SWEEP_SECONDS", "60")
)
OUTFIT_RENDER_QUEUE_ORPHAN_SWEEP_LIMIT = int(
    os.getenv("OUTFIT_RENDER_QUEUE_ORPHAN_SWEEP_LIMIT", "100")
)
OUTFIT_RENDER_CACHE_PREFIX = os.getenv(
    "OUTFIT_RENDER_CACHE_PREFIX", "outfit:render:cache:v1"
)
OUTFIT_RENDER_CACHE_TTL_SECONDS = int(
    os.getenv("OUTFIT_RENDER_CACHE_TTL_SECONDS", "604800")
)
OUTFIT_RENDER_EVENT_STREAM_PREFIX = os.getenv(
    "OUTFIT_RENDER_EVENT_STREAM_PREFIX", "outfit:render:events"
)
OUTFIT_RENDER_EVENT_STREAM_TTL_SECONDS = int(
    os.getenv("OUTFIT_RENDER_EVENT_STREAM_TTL_SECONDS", "86400")
)
OUTFIT_RENDER_EVENT_STREAM_MAX_LENGTH = int(
    os.getenv("OUTFIT_RENDER_EVENT_STREAM_MAX_LENGTH", "100")
)
OUTFIT_RENDER_SSE_BLOCK_MILLISECONDS = int(
    os.getenv("OUTFIT_RENDER_SSE_BLOCK_MILLISECONDS", "15000")
)
OUTFIT_RENDER_SSE_READ_COUNT = int(os.getenv("OUTFIT_RENDER_SSE_READ_COUNT", "50"))
OUTFIT_RENDER_SSE_RETRY_MILLISECONDS = int(
    os.getenv("OUTFIT_RENDER_SSE_RETRY_MILLISECONDS", "3000")
)

# 오늘의 룩 실행 메타데이터와 큐 복구 설정. 기존 큐 키 기본값과 맞춘다.
DAILY_LOOK_QUEUE_PENDING_KEY = os.getenv(
    "DAILY_LOOK_QUEUE_PENDING_KEY", "daily:look:pending"
)
DAILY_LOOK_QUEUE_PROCESSING_KEY = os.getenv(
    "DAILY_LOOK_QUEUE_PROCESSING_KEY", "daily:look:processing"
)
DAILY_LOOK_QUEUE_DEAD_KEY = os.getenv(
    "DAILY_LOOK_QUEUE_DEAD_KEY", "daily:look:dead"
)
DAILY_LOOK_QUEUE_RETRY_KEY = os.getenv(
    "DAILY_LOOK_QUEUE_RETRY_KEY", "daily:look:retry"
)
DAILY_LOOK_QUEUE_BLOCK_SECONDS = int(os.getenv("DAILY_LOOK_QUEUE_BLOCK_SECONDS", "5"))
DAILY_LOOK_QUEUE_MAX_RETRIES = int(os.getenv("DAILY_LOOK_QUEUE_MAX_RETRIES", "3"))
DAILY_LOOK_QUEUE_CONNECT_TIMEOUT_SECONDS = float(
    os.getenv("DAILY_LOOK_QUEUE_CONNECT_TIMEOUT_SECONDS", "1.0")
)
DAILY_LOOK_QUEUE_ORPHAN_AGE_SECONDS = int(
    os.getenv("DAILY_LOOK_QUEUE_ORPHAN_AGE_SECONDS", "30")
)
DAILY_LOOK_QUEUE_ORPHAN_SWEEP_SECONDS = int(
    os.getenv("DAILY_LOOK_QUEUE_ORPHAN_SWEEP_SECONDS", "60")
)
DAILY_LOOK_QUEUE_ORPHAN_SWEEP_LIMIT = int(
    os.getenv("DAILY_LOOK_QUEUE_ORPHAN_SWEEP_LIMIT", "100")
)
DAILY_LOOK_RENDER_RETRY_COOLDOWN_SECONDS = int(
    os.getenv("DAILY_LOOK_RENDER_RETRY_COOLDOWN_SECONDS", "600")
)

# 비회원 대화는 HttpOnly 쿠키 토큰으로 이어지고 회원가입·로그인 시 회원 identity로
# 원자적으로 이전된다. 원문 토큰은 저장하지 않고 HMAC 해시만 DB에 남긴다.
CHAT_GUEST_TTL_DAYS = int(os.getenv("CHAT_GUEST_TTL_DAYS", "7"))
CHAT_GUEST_COOKIE_NAME = os.getenv("CHAT_GUEST_COOKIE_NAME", "fashion_guest_chat")
CHAT_GUEST_COOKIE_SECURE = os.getenv("CHAT_GUEST_COOKIE_SECURE", "true").lower() in {
    "1", "true", "yes",
}
CHAT_GUEST_COOKIE_SAMESITE = os.getenv("CHAT_GUEST_COOKIE_SAMESITE", "Lax")

# 채팅 사진은 DB에 바이너리를 넣지 않고 비공개 S3 객체와 메타데이터로 분리한다.
# 전용 버킷이 없으면 기존 옷장 이미지 버킷을 재사용한다.
AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-2")
REFERENCE_RECOMMENDATION_LOG_GROUP = os.getenv(
    "REFERENCE_RECOMMENDATION_LOG_GROUP",
    "",
).strip()
REFERENCE_RECOMMENDATION_QUERY_LIMIT = int(
    os.getenv("REFERENCE_RECOMMENDATION_QUERY_LIMIT", "10000")
)
CHAT_ATTACHMENT_S3_BUCKET = (
    os.getenv("CHAT_ATTACHMENT_S3_BUCKET", "").strip()
    or os.getenv("WARDROBE_S3_BUCKET", "").strip()
)
CHAT_ATTACHMENT_MAX_MB = int(os.getenv("CHAT_ATTACHMENT_MAX_MB", "15"))
CHAT_ATTACHMENT_MAX_BYTES = CHAT_ATTACHMENT_MAX_MB * 1024 * 1024
CHAT_ATTACHMENT_ALLOWED_CONTENT_TYPES = (
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
)
CHAT_ATTACHMENT_PRESIGNED_GET_TTL_SECONDS = int(
    os.getenv("CHAT_ATTACHMENT_PRESIGNED_GET_TTL_SECONDS", "3600")
)
CHAT_ATTACHMENT_S3_CONNECT_TIMEOUT_SECONDS = int(
    os.getenv("CHAT_ATTACHMENT_S3_CONNECT_TIMEOUT_SECONDS", "5")
)
CHAT_ATTACHMENT_S3_READ_TIMEOUT_SECONDS = int(
    os.getenv("CHAT_ATTACHMENT_S3_READ_TIMEOUT_SECONDS", "15")
)
CHAT_MOOD_IMAGE_MAX_EDGE_PX = int(os.getenv("CHAT_MOOD_IMAGE_MAX_EDGE_PX", "1024"))
CHAT_MOOD_IMAGE_DETAIL = os.getenv("CHAT_MOOD_IMAGE_DETAIL", "low").strip().lower()
if CHAT_MOOD_IMAGE_DETAIL not in {"low", "high", "auto"}:
    CHAT_MOOD_IMAGE_DETAIL = "low"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
CHAT_OPENAI_MODEL = os.getenv("CHAT_OPENAI_MODEL", "gpt-4o-mini").strip()
CHAT_OPENAI_TIMEOUT_SECONDS = float(os.getenv("CHAT_OPENAI_TIMEOUT_SECONDS", "30"))
CHAT_OPENAI_MAX_OUTPUT_TOKENS = int(
    os.getenv("CHAT_OPENAI_MAX_OUTPUT_TOKENS", "1200")
)
CHAT_PROMPT_VERSION = os.getenv("CHAT_PROMPT_VERSION", "chat-orchestrator-v1").strip()
# 같은 세션에서 직전 실행이 쓴 골든 템플릿을 몇 개까지 검색에서 뺄지.
# 0이면 제외하지 않는다(기존 동작). 골든셋이 작을수록 크게 잡으면 후보가
# 말라붙으므로 기본은 보수적으로 3이다.
CHAT_RECENT_GOLDEN_EXCLUSION_LIMIT = int(
    os.getenv("CHAT_RECENT_GOLDEN_EXCLUSION_LIMIT", "3")
)
# 온보딩 기피를 하드 필터에서 풀 골든 후보 임계값. 후보가 이 수 미만이면 완화한다.
# 기본 2 = "0건이거나 1건일 때". 1로 낮추면 0건일 때만, 0/1 외로 올리면 기피가
# 자주 풀린다 — 골든셋 커버리지가 늘면 다시 낮춘다.
CHAT_GOLDEN_RELAX_AVOIDED_BELOW = int(
    os.getenv("CHAT_GOLDEN_RELAX_AVOIDED_BELOW", "2")
)
PERSONA_LLM_PROVIDER = os.getenv("PERSONA_LLM_PROVIDER", "openai").strip().lower()
_PERSONA_LLM_DEFAULT_MODEL = (
    GEMINI_MODEL if PERSONA_LLM_PROVIDER == "gemini" else CHAT_OPENAI_MODEL
)
PERSONA_LLM_MODEL = os.getenv(
    "PERSONA_LLM_MODEL",
    _PERSONA_LLM_DEFAULT_MODEL,
).strip()
PERSONA_LLM_TIMEOUT_SECONDS = float(
    os.getenv(
        "PERSONA_LLM_TIMEOUT_SECONDS",
        str(
            GEMINI_TIMEOUT_SECONDS
            if PERSONA_LLM_PROVIDER == "gemini"
            else CHAT_OPENAI_TIMEOUT_SECONDS
        ),
    )
)
PERSONA_LLM_MAX_OUTPUT_TOKENS = int(
    os.getenv("PERSONA_LLM_MAX_OUTPUT_TOKENS", "400")
)
PERSONA_LLM_PROMPT_VERSION = os.getenv(
    "PERSONA_LLM_PROMPT_VERSION",
    "persona-narration-v1",
).strip()
CHAT_GOLDENSET_DATASET_VERSION = os.getenv(
    "CHAT_GOLDENSET_DATASET_VERSION", ""
).strip()
CHAT_GOLDENSET_DATASET_STATUSES = tuple(
    value.strip()
    for value in os.getenv("CHAT_GOLDENSET_DATASET_STATUSES", "").split(",")
    if value.strip()
)
CHAT_PRODUCT_INDEX_VERSION = os.getenv("CHAT_PRODUCT_INDEX_VERSION", "").strip()
CHAT_CONTEXT_RECENT_MESSAGES = int(os.getenv("CHAT_CONTEXT_RECENT_MESSAGES", "12"))
CHAT_SUMMARY_TRIGGER_MESSAGES = int(os.getenv("CHAT_SUMMARY_TRIGGER_MESSAGES", "24"))
CHAT_CONTEXT_CACHE_PREFIX = os.getenv(
    "CHAT_CONTEXT_CACHE_PREFIX", "chat:context:v1"
).strip()
CHAT_CONTEXT_CACHE_TTL_SECONDS = int(os.getenv("CHAT_CONTEXT_CACHE_TTL_SECONDS", "900"))
CHAT_CONTEXT_CACHE_CONNECT_TIMEOUT_SECONDS = float(
    os.getenv("CHAT_CONTEXT_CACHE_CONNECT_TIMEOUT_SECONDS", "0.5")
)
CHAT_CONTEXT_CACHE_TIMEOUT_SECONDS = float(
    os.getenv("CHAT_CONTEXT_CACHE_TIMEOUT_SECONDS", "1.0")
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
CHAT_MESSAGE_MAX_CHARS = int(os.getenv("CHAT_MESSAGE_MAX_CHARS", "4000"))
CHAT_QUEUE_PENDING_KEY = os.getenv("CHAT_QUEUE_PENDING_KEY", "chat:runs:pending")
CHAT_QUEUE_PROCESSING_KEY = os.getenv(
    "CHAT_QUEUE_PROCESSING_KEY", "chat:runs:processing"
)
CHAT_QUEUE_DEAD_KEY = os.getenv("CHAT_QUEUE_DEAD_KEY", "chat:runs:dead")
CHAT_QUEUE_RETRY_KEY = os.getenv("CHAT_QUEUE_RETRY_KEY", "chat:runs:retry")
CHAT_QUEUE_BLOCK_SECONDS = int(os.getenv("CHAT_QUEUE_BLOCK_SECONDS", "5"))
CHAT_QUEUE_MAX_RETRIES = int(os.getenv("CHAT_QUEUE_MAX_RETRIES", "3"))
CHAT_QUEUE_ORPHAN_AGE_SECONDS = int(os.getenv("CHAT_QUEUE_ORPHAN_AGE_SECONDS", "30"))
CHAT_QUEUE_ORPHAN_SWEEP_SECONDS = int(
    os.getenv("CHAT_QUEUE_ORPHAN_SWEEP_SECONDS", "60")
)
CHAT_QUEUE_ORPHAN_SWEEP_LIMIT = int(
    os.getenv("CHAT_QUEUE_ORPHAN_SWEEP_LIMIT", "100")
)
CHAT_QUEUE_CONNECT_TIMEOUT_SECONDS = float(
    os.getenv("CHAT_QUEUE_CONNECT_TIMEOUT_SECONDS", "1.0")
)
CHAT_EVENT_STREAM_PREFIX = os.getenv(
    "CHAT_EVENT_STREAM_PREFIX", "chat:run:events"
).strip()
CHAT_EVENT_STREAM_TTL_SECONDS = int(
    os.getenv("CHAT_EVENT_STREAM_TTL_SECONDS", "86400")
)
CHAT_EVENT_STREAM_MAX_LENGTH = int(os.getenv("CHAT_EVENT_STREAM_MAX_LENGTH", "100"))
CHAT_SSE_BLOCK_MILLISECONDS = int(os.getenv("CHAT_SSE_BLOCK_MILLISECONDS", "15000"))
CHAT_SSE_READ_COUNT = int(os.getenv("CHAT_SSE_READ_COUNT", "50"))
CHAT_SSE_RETRY_MILLISECONDS = int(
    os.getenv("CHAT_SSE_RETRY_MILLISECONDS", "3000")
)
