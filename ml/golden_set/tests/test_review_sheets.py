"""모델 호출 없이 만드는 사람 검수표 테스트.

검수표는 사람이 며칠에 걸쳐 채운 뒤에야 집계로 넘어간다. 열 이름이 어긋나거나 비교
그래프가 끊겨 있으면 그 사실이 검수가 끝난 뒤에 드러나고, 그때는 다시 채우는 수밖에
없다. 그래서 값이 아니라 계약과 구조를 붙잡는다.
"""

from __future__ import annotations

import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ml.golden_set.review import OBSERVATION_REVIEW_FIELDS, PAIRWISE_FIELDS
from ml.golden_set.review_manifest import METADATA_COLUMNS
from ml.golden_set.review_sheets import build_review_sheets


def _metadata(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=METADATA_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in METADATA_COLUMNS})


def _row(index: int, *, style: str, group: str) -> dict[str, str]:
    return {
        "file_name": f"img-{index:03d}.jpg",
        "golden_id": f"img-{index:03d}",
        "presentation_group": group,
        "style": style,
        "style_source_label": f"{style} 라벨",
    }


class SheetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        base = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.metadata = base / "metadata.csv"
        self.images = base / "images"
        self.out = base / "sheets"

        # 스타일 4묶음 × 성별 2 — 스타일이 겹치지 않는 묶음이 있어야 다리 쌍이 필요해진다.
        rows = []
        index = 0
        for style in ("미니멀", "스트릿", "리조트", "빈티지"):
            for group in ("men", "women"):
                for _ in range(3):
                    index += 1
                    rows.append(_row(index, style=style, group=group))
        self.rows = rows
        _metadata(self.metadata, rows)

    def _read(self, path: Path) -> list[dict[str, str]]:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_headers_match_pipeline_contract(self) -> None:
        """집계 쪽이 이 이름으로 읽는다. 여기서 새 이름을 지으면 나중에 드러난다."""
        paths, _ = build_review_sheets(
            metadata_csv=self.metadata, images_dir=self.images, out_dir=self.out
        )
        with paths.observation.open(encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(next(csv.reader(handle)), OBSERVATION_REVIEW_FIELDS)
        with paths.pairwise.open(encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(next(csv.reader(handle)), PAIRWISE_FIELDS)

    def test_one_observation_row_per_image_with_model_columns_blank(self) -> None:
        paths, counts = build_review_sheets(
            metadata_csv=self.metadata, images_dir=self.images, out_dir=self.out
        )
        rows = self._read(paths.observation)

        self.assertEqual(len(rows), len(self.rows))
        self.assertEqual(counts["images"], len(self.rows))
        for row in rows:
            self.assertEqual(row["detected_items"], "")
            self.assertEqual(row["observation_verdict"], "")
            self.assertTrue(row["golden_id"])
            self.assertTrue(row["style_intent"])

    def test_style_intent_falls_back_to_source_label(self) -> None:
        """스타일 폴더가 없는 수집자는 taxonomy 값이 비어 있다."""
        _metadata(self.metadata, [{"file_name": "a.jpg", "golden_id": "a", "presentation_group": "men", "style": "", "style_source_label": "원본 라벨"}])
        paths, _ = build_review_sheets(
            metadata_csv=self.metadata, images_dir=self.images, out_dir=self.out
        )
        self.assertEqual(self._read(paths.observation)[0]["style_intent"], "원본 라벨")

    def test_pairwise_graph_is_connected(self) -> None:
        """비교 그래프가 끊기면 Bradley-Terry 상대 점수가 나오지 않는다."""
        paths, _ = build_review_sheets(
            metadata_csv=self.metadata, images_dir=self.images, out_dir=self.out, pair_count=40
        )
        pairs = self._read(paths.pairwise)

        parent = {row["golden_id"]: row["golden_id"] for row in self.rows}

        def find(value: str) -> str:
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        for pair in pairs:
            parent[find(pair["right_id"])] = find(pair["left_id"])
        self.assertEqual(len({find(key) for key in parent}), 1)

    def test_pairs_are_unique_and_never_self_compare(self) -> None:
        paths, _ = build_review_sheets(
            metadata_csv=self.metadata, images_dir=self.images, out_dir=self.out, pair_count=40
        )
        pairs = self._read(paths.pairwise)

        keys = [frozenset((pair["left_id"], pair["right_id"])) for pair in pairs]
        self.assertEqual(len(keys), len(set(keys)))
        for pair in pairs:
            self.assertNotEqual(pair["left_id"], pair["right_id"])

    def test_pair_count_is_respected_above_connectivity_floor(self) -> None:
        _, counts = build_review_sheets(
            metadata_csv=self.metadata, images_dir=self.images, out_dir=self.out, pair_count=40
        )
        self.assertEqual(counts["pairs"], 40)

    def test_result_is_deterministic(self) -> None:
        """배치를 늘릴 때 앞 배치와 겹치지 않게 이어붙이려면 재현돼야 한다."""
        first, _ = build_review_sheets(
            metadata_csv=self.metadata, images_dir=self.images, out_dir=self.out / "a", pair_count=30
        )
        second, _ = build_review_sheets(
            metadata_csv=self.metadata, images_dir=self.images, out_dir=self.out / "b", pair_count=30
        )
        self.assertEqual(
            first.pairwise.read_text(encoding="utf-8-sig"),
            second.pairwise.read_text(encoding="utf-8-sig"),
        )


if __name__ == "__main__":
    unittest.main()
