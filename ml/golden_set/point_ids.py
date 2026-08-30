"""Qdrant point ID 생성 — api/apps/recommend/services/qdrant.py와 같은 규칙.

네임스페이스가 갈리면 같은 원본 키에 대해 서로 다른 UUID가 나오고, 예외 없이
조용히 중복 포인트가 생기거나 삭제가 아무것도 지우지 않는다. 그래서 값을
Django 쪽과 **반드시 동일하게** 유지한다.

golden_set은 Django 없이 도는 오프라인 패키지라 services.qdrant를 import할 수
없어(=django.conf.settings 의존) 상수만 복제한다. 한쪽을 바꾸면 다른 쪽도
같이 바꿔야 한다.
"""

from __future__ import annotations

import uuid

#: api/apps/recommend/services/qdrant.py:_POINT_NAMESPACE 와 동일. 변경 금지.
POINT_NAMESPACE = uuid.UUID("6b2c1f3a-9d4e-4c8b-8a71-2f0e5d9c3b17")


def point_id(source_key: str) -> str:
    """원본 식별자 → 결정적 Qdrant point ID (재실행 시 upsert가 멱등)."""
    return str(uuid.uuid5(POINT_NAMESPACE, source_key))
