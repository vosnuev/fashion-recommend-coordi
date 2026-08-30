"""사람 검수 앵커가 골든 코디 랭킹에 반영되는 계약.

예전에는 `human_score`가 **필터 검색 경로에서만** 유사도 자리에 들어갔다. 채팅은
질의 임베딩을 써서 벡터 검색 경로를 타므로, 사람이 검수한 결과가 실제 추천 순서에는
사실상 반영되지 않았다. 그 구멍을 다시 열지 않기 위한 테스트다.
"""

from __future__ import annotations

import unittest

from django.test import SimpleTestCase, override_settings

from apps.recommend.services.retriever import (
    RetrievalRequest,
    _score_human_review,
    retrieve_outfits,
)

WEIGHT = 15.0


def _outfit(golden_id: str, **payload) -> dict:
    base = {
        "golden_id": golden_id,
        "presentation_group": "unisex",
        "items": [],
    }
    base.update(payload)
    return base


class _FakePoint:
    def __init__(self, pid: str, payload: dict) -> None:
        self.id = pid
        self.payload = payload


class _ScrollClient:
    """필터 검색(스크롤) 경로만 타는 Qdrant."""

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads

    def scroll(self, *, scroll_filter=None, **kwargs):
        return [_FakePoint(f"p{i}", p) for i, p in enumerate(self.payloads)], None


class ScoreHumanReviewTests(unittest.TestCase):
    def test_above_median_is_a_bonus(self) -> None:
        delta, reasons = _score_human_review(
            {"human_score": 100.0, "score_confidence": 1.0}, weight=WEIGHT
        )
        self.assertAlmostEqual(delta, WEIGHT)
        self.assertEqual(reasons[0].source, "human_review")

    def test_below_median_is_a_penalty(self) -> None:
        delta, _ = _score_human_review(
            {"human_score": 0.0, "score_confidence": 1.0}, weight=WEIGHT
        )
        self.assertAlmostEqual(delta, -WEIGHT)

    def test_median_moves_nothing(self) -> None:
        delta, reasons = _score_human_review(
            {"human_score": 50.0, "score_confidence": 1.0}, weight=WEIGHT
        )
        self.assertEqual(delta, 0.0)
        self.assertEqual(reasons, [])

    def test_unreviewed_outfit_is_neither_rewarded_nor_punished(self) -> None:
        """미검수는 '나쁘다'가 아니라 '모른다'다. 645건 중 검수분은 아직 일부다."""
        delta, reasons = _score_human_review({"golden_id": "x"}, weight=WEIGHT)
        self.assertEqual(delta, 0.0)
        self.assertEqual(reasons, [])

    def test_low_confidence_anchor_moves_less(self) -> None:
        strong, _ = _score_human_review(
            {"human_score": 100.0, "score_confidence": 1.0}, weight=WEIGHT
        )
        weak, _ = _score_human_review(
            {"human_score": 100.0, "score_confidence": 0.2}, weight=WEIGHT
        )
        self.assertLess(weak, strong)
        self.assertAlmostEqual(weak, WEIGHT * 0.2)

    def test_zero_weight_disables_the_anchor(self) -> None:
        delta, _ = _score_human_review({"human_score": 100.0}, weight=0.0)
        self.assertEqual(delta, 0.0)

    def test_garbage_score_is_ignored(self) -> None:
        delta, _ = _score_human_review({"human_score": "높음"}, weight=WEIGHT)
        self.assertEqual(delta, 0.0)


@override_settings(RETRIEVER_HUMAN_SCORE_WEIGHT=WEIGHT)
class RankingTests(SimpleTestCase):
    def _run(self, payloads):
        return retrieve_outfits(
            RetrievalRequest(limit=10), client=_ScrollClient(payloads)
        )

    def test_higher_anchor_outranks_lower(self) -> None:
        got = self._run(
            [
                _outfit("low", human_score=10.0, score_confidence=1.0),
                _outfit("high", human_score=95.0, score_confidence=1.0),
            ]
        )
        self.assertEqual([c.golden_id for c in got], ["high", "low"])

    def test_anchor_is_not_counted_twice_in_the_scroll_path(self) -> None:
        """스크롤 경로가 human_score를 기준선으로도 쓰면 같은 값이 두 번 반영된다."""
        (candidate,) = self._run(
            [_outfit("only", human_score=100.0, score_confidence=1.0)]
        )
        self.assertAlmostEqual(candidate.score, WEIGHT)
        self.assertEqual(candidate.similarity, 0.0)

    def test_reason_is_recorded_for_explanation(self) -> None:
        (candidate,) = self._run(
            [
                _outfit(
                    "one",
                    human_score=90.0,
                    score_confidence=1.0,
                    score_band="high",
                )
            ]
        )
        sources = [reason.source for reason in candidate.reasons]
        self.assertIn("human_review", sources)

    def test_verified_outfit_wins_a_tie_over_tag_confidence(self) -> None:
        """tag_confidence는 LLM이 자기 태깅에 매긴 확신이라 틀려도 높을 수 있다."""
        got = self._run(
            [
                _outfit("tagged", tag_confidence=0.99),
                _outfit("verified", human_verified=True, tag_confidence=0.1),
            ]
        )
        self.assertEqual([c.golden_id for c in got], ["verified", "tagged"])

    @override_settings(RETRIEVER_HUMAN_SCORE_WEIGHT=0)
    def test_weight_zero_restores_previous_ordering(self) -> None:
        got = self._run(
            [
                _outfit("a", human_score=10.0, score_confidence=1.0),
                _outfit("b", human_score=95.0, score_confidence=1.0),
            ]
        )
        self.assertEqual({c.score for c in got}, {0.0})
        self.assertEqual([c.golden_id for c in got], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
