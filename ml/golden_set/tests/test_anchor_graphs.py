"""anchor_graph별로 쌍대 비교를 나눠 fit하는 규칙.

남성·여성처럼 서로 비교하지 않는 묶음이 한 파일에 담기면 비교 그래프가 끊긴다.
나누지 않으면 Bradley-Terry가 아예 계산되지 않는다는 것이 이 테스트의 핵심이다.
"""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from ml.golden_set.anchors import build_anchor_scores

PAIRWISE_FIELDS = [
    "pair_id",
    "reviewer_label",
    "anchor_graph",
    "left_id",
    "right_id",
    "winner",
    "confidence_1_3",
]

REVIEWERS = ("reviewer-a", "reviewer-b")


def _chain(graph: str, ids: list[str], winners: list[str]) -> list[dict[str, str]]:
    """ids를 사슬로 이어 2인 검수를 채운다. 좌우는 검수자마다 뒤집는다."""
    rows = []
    for position, (left, right) in enumerate(zip(ids, ids[1:])):
        for reviewer in REVIEWERS:
            swapped = reviewer == "reviewer-b"
            rows.append(
                {
                    "pair_id": f"{graph}-pair-{position:04d}",
                    "reviewer_label": reviewer,
                    "anchor_graph": graph,
                    "left_id": right if swapped else left,
                    "right_id": left if swapped else right,
                    "winner": winners[position],
                    "confidence_1_3": "3",
                }
            )
    return rows


def _write_pairwise(path: Path, rows: list[dict[str, str]], *, graph_column=True):
    fields = list(PAIRWISE_FIELDS)
    if not graph_column:
        fields.remove("anchor_graph")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


MEN = _chain("men", ["m-1", "m-2", "m-3", "m-4"], ["m-1", "m-2", "m-3"])
WOMEN = _chain("women", ["w-1", "w-2", "w-3", "w-4"], ["w-1", "w-2", "w-3"])


class AnchorGraphTests(unittest.TestCase):
    def _build(self, rows, *, graph_column=True, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            pairwise = run_dir / "pairwise_reviews.csv"
            _write_pairwise(pairwise, rows, graph_column=graph_column)
            anchors = build_anchor_scores(
                pairwise_csv=pairwise, run_dir=run_dir, **kwargs
            )
            meta = json.loads(
                (run_dir / "anchor_scores.meta.json").read_text(encoding="utf-8")
            )
            return anchors, meta

    def test_disconnected_graphs_fail_without_the_column(self) -> None:
        """이 실패를 막으려고 분리 fit을 넣었다."""
        with self.assertRaises(ValueError) as caught:
            self._build(MEN + WOMEN, graph_column=False)
        self.assertIn("연결되지 않았습니다", str(caught.exception))

    def test_graphs_are_fitted_separately(self) -> None:
        anchors, meta = self._build(MEN + WOMEN)
        self.assertEqual(len(anchors), 8)
        self.assertEqual(len({row["golden_id"] for row in anchors}), 8)
        self.assertEqual(meta["num_graphs"], 2)
        self.assertEqual(meta["graphs"]["men"]["num_images"], 4)
        self.assertEqual(meta["graphs"]["women"]["num_images"], 4)

    def test_each_row_carries_its_graph(self) -> None:
        anchors, _ = self._build(MEN + WOMEN)
        by_id = {row["golden_id"]: row["anchor_graph"] for row in anchors}
        self.assertEqual(by_id["m-1"], "men")
        self.assertEqual(by_id["w-1"], "women")

    def test_score_range_is_per_graph(self) -> None:
        """0~100 환산이 그래프 안에서 펴지므로 양쪽 모두 최저 0·최고 100이 나온다."""
        anchors, _ = self._build(MEN + WOMEN)
        for graph in ("men", "women"):
            scores = [
                row["human_score"] for row in anchors if row["anchor_graph"] == graph
            ]
            with self.subTest(graph=graph):
                self.assertAlmostEqual(min(scores), 0.0)
                self.assertAlmostEqual(max(scores), 100.0)

    def test_missing_column_keeps_single_graph_behaviour(self) -> None:
        anchors, meta = self._build(MEN, graph_column=False)
        self.assertEqual(len(anchors), 4)
        self.assertEqual(meta["num_graphs"], 1)
        self.assertEqual({row["anchor_graph"] for row in anchors}, {""})

    def test_outfit_in_two_graphs_is_rejected(self) -> None:
        """점수가 두 개 생기면 적재에서 조용히 덮어써진다."""
        crossed = MEN + _chain("women", ["m-1", "w-1"], ["m-1"])
        with self.assertRaises(ValueError) as caught:
            self._build(crossed)
        self.assertIn("anchor_graph에 걸친 코디", str(caught.exception))

    def test_graph_without_eligible_pairs_is_skipped(self) -> None:
        """1인만 판정한 그래프는 빠지고, 나머지 그래프는 그대로 계산된다."""
        single = [row for row in WOMEN if row["reviewer_label"] == "reviewer-a"]
        anchors, meta = self._build(MEN + single)
        self.assertEqual({row["anchor_graph"] for row in anchors}, {"men"})
        self.assertEqual(meta["graphs"]["women"]["num_eligible_pairs"], 0)

    def test_context_dependent_rows_are_counted_as_skipped(self) -> None:
        rows = MEN + [
            {
                "pair_id": "men-pair-9999",
                "reviewer_label": reviewer,
                "anchor_graph": "men",
                "left_id": "m-1",
                "right_id": "m-4",
                "winner": "context_dependent",
                "confidence_1_3": "2",
            }
            for reviewer in REVIEWERS
        ]
        _, meta = self._build(rows)
        self.assertEqual(meta["num_skipped_rows"], 2)

    def test_no_eligible_pair_anywhere_raises(self) -> None:
        single = [row for row in MEN if row["reviewer_label"] == "reviewer-a"]
        with self.assertRaises(ValueError) as caught:
            self._build(single)
        self.assertIn("비교 가능한 쌍대 비교가 없습니다", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
