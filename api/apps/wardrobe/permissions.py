"""내부 서비스 간 호출 인증.

이미지 프로세서 → 메인 API 콜백은 사용자 JWT가 아닌
공유 시크릿 헤더(X-Internal-Token)로 인증한다 (설계 결정 2).
"""
from __future__ import annotations

import hmac
import os

from rest_framework.permissions import BasePermission

HEADER = "X-Internal-Token"


class HasInternalToken(BasePermission):
    """WARDROBE_INTERNAL_TOKEN과 헤더 값을 상수 시간 비교한다."""

    message = "내부 서비스 토큰이 유효하지 않습니다."

    def has_permission(self, request, view) -> bool:
        expected = os.getenv("WARDROBE_INTERNAL_TOKEN", "")
        provided = request.headers.get(HEADER, "")
        if not expected:  # 미설정 시 전면 차단 (열어두는 기본값 금지)
            return False
        return hmac.compare_digest(expected, provided)
