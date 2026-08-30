from __future__ import annotations

from django.test import SimpleTestCase

from apps.chat.services.recommendation_pipeline import ChatRecommendationPipeline
from apps.recommend.services.retriever import RetrievalRequest, RetrievalResult
from apps.recommend.services.text_embedding import (
    TextEmbeddingConfigurationError,
    TextEmbeddingError,
)


class _RecordingRetriever:
    """설정이 없는 실제 리트리버를 흉내낸다 — 질의문이 있으면 임베딩을 요구한다."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        self.queries.append(request.query_text)
        if request.query_text.strip():
            raise TextEmbeddingConfigurationError(
                "TEXT_EMBEDDING_API_URL과 TEXT_EMBEDDING_API_TOKEN이 필요합니다."
            )
        return RetrievalResult(
            candidates=(),
            search_mode="filter",
            embedding_model="",
            embedding_version="",
        )


class _FlakyRetriever:
    """서비스는 설정돼 있으나 호출이 실패하는 경우."""

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        raise TextEmbeddingError("텍스트 임베딩 서비스 호출 실패: timeout")


class GoldenRetrievalFallbackTests(SimpleTestCase):
    """임베딩 설정이 없을 때 추천이 통째로 멈추지 않는지 확인한다.

    설정 누락은 시간이 지나도 낫지 않는데, 예전에는 이게 일반 오류로 뭉개진 채
    워커가 두 번 더 재시도했다. 지금은 질의문을 비워 필터 검색으로 내려간다.
    """

    def test_설정이_없으면_질의문을_비워_필터검색으로_재시도한다(self) -> None:
        retriever = _RecordingRetriever()
        pipeline = ChatRecommendationPipeline(golden_retriever=retriever)

        result = pipeline._retrieve_golden(RetrievalRequest(query_text="제주도 캐주얼룩"))

        # 첫 시도는 질의문 그대로, 두 번째는 비운 채로 — 체형·계절 등 필터 조건은 그대로 간다.
        self.assertEqual(retriever.queries, ["제주도 캐주얼룩", ""])
        self.assertEqual(result.search_mode, "filter")

    def test_일시적인_호출_실패는_폴백하지_않고_그대로_올린다(self) -> None:
        """잠깐의 장애로 추천 품질을 낮추면 안 된다. 재시도는 워커가 맡는다."""
        pipeline = ChatRecommendationPipeline(golden_retriever=_FlakyRetriever())

        with self.assertRaises(TextEmbeddingError) as caught:
            pipeline._retrieve_golden(RetrievalRequest(query_text="제주도 캐주얼룩"))

        # code 가 있어야 run 기록에 원인이 남는다 (없으면 호출부 기본 코드로 뭉개진다).
        self.assertEqual(caught.exception.code, "TEXT_EMBEDDING_FAILED")

    def test_설정_누락_예외도_고유한_코드를_가진다(self) -> None:
        self.assertEqual(
            TextEmbeddingConfigurationError("x").code, "TEXT_EMBEDDING_NOT_CONFIGURED"
        )
