"""개발 환경 설정."""

from .base import *  # noqa: F401,F403

DEBUG = True

CHAT_GUEST_COOKIE_SECURE = False

# DJANGO_ALLOWED_HOSTS 환경변수(콤마 구분)가 있으면 사용, 없으면 로컬 기본값.
# base.py에서 이미 환경변수를 파싱하므로 비어 있을 때만 기본값으로 대체한다.
if not ALLOWED_HOSTS:  # noqa: F405
    ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# 개발 편의: 웹(Expo) 개발 서버에서 API를 부를 수 있게 한다.
# ALLOWED_HOSTS와 같은 방식으로, 환경변수가 비어 있을 때만 로컬 기본값을 준다.
# 포트를 열거하지 않고 정규식을 쓰는 이유 — expo는 8081이 잡혀 있으면 8082, 8099처럼
# 다른 포트로 뜬다. 열거해 두면 그때마다 CORS가 막혀 룩북이 빈 화면으로 보인다.
# dev 설정에만 있으므로 배포(prod.py)에는 영향이 없다.
if not CORS_ALLOWED_ORIGINS:  # noqa: F405
    CORS_ALLOWED_ORIGIN_REGEXES = [r"^http://(localhost|127\.0\.0\.1):\d+$"]

# 개발 편의: 브라우저에서 API 탐색 가능
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}
