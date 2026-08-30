"""상품 임베딩 내부 API 인증."""

from __future__ import annotations

import hmac
import os

from rest_framework.permissions import BasePermission


class HasProductIndexerToken(BasePermission):
    """GPU product-indexer와 공유한 Bearer 토큰을 검증한다."""

    message = "상품 임베딩 내부 API 토큰이 유효하지 않습니다."

    def has_permission(self, request, view) -> bool:
        expected = os.getenv("PRODUCT_INDEXER_INTERNAL_TOKEN", "").strip()
        authorization = request.headers.get("Authorization", "")
        scheme, _, provided = authorization.partition(" ")
        if not expected or scheme.lower() != "bearer":
            return False
        return hmac.compare_digest(expected, provided.strip())
