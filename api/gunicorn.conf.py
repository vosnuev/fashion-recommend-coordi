"""
gunicorn 설정. WORKDIR(/app)에 있으면 gunicorn이 자동으로 읽는다.

여기 값은 커맨드라인 플래그보다 우선순위가 낮다(플래그가 이긴다).
docker-compose.yml의 api command에서 --access-logfile 등을 넘기고 있으므로
그쪽이 최종값이고, 이 파일은 기본값 + 로그 포맷을 담당한다.

액세스 로그(요청 단위)는 gunicorn이, 애플리케이션 로그는 Django LOGGING이
담당한다(config/settings/base.py 참고).
"""

import os

# ------------------------------------------------------------
# 바인딩 / 워커
# ------------------------------------------------------------
bind = os.getenv("GUNICORN_BIND", "0.0.0.0:8000")
workers = int(os.getenv("GUNICORN_WORKERS", "3"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "60"))

# ------------------------------------------------------------
# 로깅 — 전부 컨테이너 stdout/stderr로 (docker logs / CloudWatch 수집)
#
# gunicorn 기본값은 accesslog=None 이라 요청 로그가 아예 남지 않는다.
# "-"로 지정해야 stdout에 찍힌다.
# ------------------------------------------------------------
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")

# 워커의 stdout/stderr(print, 예외 트레이스백)를 에러 로그로 흡수한다.
capture_output = True

# 기본 포맷에 없는 두 가지를 추가:
#   %({x-forwarded-for}i)s  프록시(Cloudflare tunnel / ALB) 뒤의 실제 클라이언트 IP
#   %(M)s                   응답 소요 시간(ms)
access_log_format = (
    '%(h)s xff=%({x-forwarded-for}i)s "%(r)s" %(s)s %(b)s %(M)sms '
    '"%(f)s" "%(a)s"'
)

# 프록시가 보낸 X-Forwarded-* 를 신뢰할 IP 대역.
# 컨테이너 네트워크에서는 프록시가 127.0.0.1이 아니라서 기본값으로는 매칭되지
# 않는다. 신뢰 가능한 프록시 뒤에서만 "*"로 열 것.
forwarded_allow_ips = os.getenv("GUNICORN_FORWARDED_ALLOW_IPS", "127.0.0.1")

# 헬스체크 등 소음이 심한 경로는 액세스 로그에서 제외 (정규식, 콤마 구분)
_skip = os.getenv("GUNICORN_ACCESS_LOG_SKIP", "")
if _skip:
    import re

    _patterns = [re.compile(p.strip()) for p in _skip.split(",") if p.strip()]

    class _SkipPaths:
        def filter(self, record):
            path = getattr(record, "args", {})
            url = path.get("U", "") if isinstance(path, dict) else ""
            return not any(p.search(url) for p in _patterns)

    def on_starting(server):  # noqa: D103
        import logging

        logging.getLogger("gunicorn.access").addFilter(_SkipPaths())
