"""골든셋과 같은 BGE-M3 공간의 질의 벡터를 가져오는 내부 API 클라이언트."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import requests
from django.conf import settings


class TextEmbeddingError(RuntimeError):
    """질의 임베딩을 만들지 못했거나 응답 계약이 잘못된 경우.

    `code`는 이 예외를 삼키는 쪽(예: 채팅 run 기록)이 실패 원인을 그대로 남길 수 있게 둔다.
    이게 없으면 호출자가 자기 기본 코드로 뭉개 버려서, 로그를 직접 보기 전까지는 임베딩이
    원인이라는 사실 자체가 드러나지 않는다.
    """

    code = "TEXT_EMBEDDING_FAILED"


class TextEmbeddingConfigurationError(TextEmbeddingError):
    """임베딩 서비스 연결 설정이 없는 경우."""

    code = "TEXT_EMBEDDING_NOT_CONFIGURED"


@dataclass(frozen=True)
class TextEmbeddingResult:
    vector: tuple[float, ...]
    model: str
    version: str


class TextEmbeddingClient:
    """인증된 내부 HTTP 서비스에서 BGE-M3 dense vector를 조회한다."""

    def __init__(
        self,
        *,
        url: str,
        token: str,
        timeout: int,
        expected_dimension: int,
        session: requests.Session | None = None,
    ) -> None:
        self.url = url.strip()
        self.token = token.strip()
        self.timeout = timeout
        self.expected_dimension = expected_dimension
        self.session = session or requests.Session()

    def embed(self, text: str) -> TextEmbeddingResult:
        query = text.strip()
        if not query:
            raise TextEmbeddingError("임베딩할 질의가 비어 있습니다.")
        if len(query) > 2_000:
            raise TextEmbeddingError("임베딩 질의는 2,000자를 넘을 수 없습니다.")
        if not self.url or not self.token:
            raise TextEmbeddingConfigurationError(
                "TEXT_EMBEDDING_API_URL과 TEXT_EMBEDDING_API_TOKEN이 필요합니다."
            )

        try:
            response = self.session.post(
                self.url,
                json={"texts": [query]},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise TextEmbeddingError(f"텍스트 임베딩 서비스 호출 실패: {exc}") from exc

        if response.status_code != 200:
            detail = _error_detail(response)
            raise TextEmbeddingError(
                f"텍스트 임베딩 서비스 오류 ({response.status_code}): {detail}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise TextEmbeddingError("텍스트 임베딩 응답이 JSON이 아닙니다.") from exc
        return self._parse(payload)

    def _parse(self, payload: Any) -> TextEmbeddingResult:
        if not isinstance(payload, dict):
            raise TextEmbeddingError("텍스트 임베딩 응답은 JSON 객체여야 합니다.")
        vectors = payload.get("vectors")
        if not isinstance(vectors, list) or len(vectors) != 1:
            raise TextEmbeddingError(
                "텍스트 임베딩 응답의 vectors 개수가 잘못됐습니다."
            )
        raw_vector = vectors[0]
        if not isinstance(raw_vector, list):
            raise TextEmbeddingError("텍스트 임베딩 vector 형식이 잘못됐습니다.")
        if len(raw_vector) != self.expected_dimension:
            raise TextEmbeddingError(
                "텍스트 임베딩 차원 불일치: "
                f"expected={self.expected_dimension}, actual={len(raw_vector)}"
            )

        try:
            vector = tuple(float(value) for value in raw_vector)
        except (TypeError, ValueError) as exc:
            raise TextEmbeddingError(
                "텍스트 임베딩에 숫자가 아닌 값이 있습니다."
            ) from exc
        if not all(math.isfinite(value) for value in vector):
            raise TextEmbeddingError("텍스트 임베딩에 유한하지 않은 값이 있습니다.")

        model = str(payload.get("model") or "").strip()
        version = str(payload.get("version") or "").strip()
        if not model or not version:
            raise TextEmbeddingError("텍스트 임베딩 모델과 버전 정보가 없습니다.")
        return TextEmbeddingResult(vector=vector, model=model, version=version)


def _error_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return (response.text or "unknown error")[:500]
    if isinstance(payload, dict):
        return str(payload.get("detail") or payload)[:500]
    return str(payload)[:500]


@lru_cache(maxsize=1)
def get_text_embedding_client(
    *, timeout: int | None = None
) -> TextEmbeddingClient:
    """기본 타임아웃은 설정값. 없어도 되는 조회는 `timeout`으로 짧게 끊는다."""
    return TextEmbeddingClient(
        url=settings.TEXT_EMBEDDING_API_URL,
        token=settings.TEXT_EMBEDDING_API_TOKEN,
        timeout=timeout or settings.TEXT_EMBEDDING_TIMEOUT_SECONDS,
        expected_dimension=settings.TEXT_EMBEDDING_EXPECTED_DIM,
    )
