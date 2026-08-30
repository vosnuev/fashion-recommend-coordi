"""스타일 라벨 병합과 클러스터 생성 규칙.

라벨은 원칙의 **조건**이 된다. 잘못 들어간 값은 "아메카지에서는…"이라고 적힌 채
엉뚱한 코디에서 뽑힌 원칙을 만들고, 그건 라벨이 없는 것보다 나쁘다. 그래서 병합
단계에서 걸러야 하고, 조용히 통과하면 안 되는 자리들을 여기서 고정한다.
"""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml.golden_set.style_clusters import (
    UNSTYLED_CLUSTER,
    build_style_clusters,
    merge_style_labels,
)

STYLES = {"캐주얼", "러블리", "페미닌", "미니멀", "댄디", "포멀"}

METADATA_FIELDS = ["golden_id", "file_name", "style", "presentation_group"]


def _meta(*rows) -> list[dict[str, str]]:
    return [
        {
            "golden_id": gid,
            "file_name": gid + ".jpg",
            "style": style,
            "presentation_group": "women",
        }
        for gid, style in rows
    ]


def _labels(*rows) -> list[dict[str, str]]:
    return [{"golden_id": gid, "style": style} for gid, style in rows]


class MergeTests(unittest.TestCase):
    def _merge(self, metadata, labels):
        with patch(
            "ml.golden_set.style_clusters._taxonomy_styles", return_value=STYLES
        ):
            return merge_style_labels(metadata, labels)

    def test_empty_style_is_filled(self) -> None:
        merged, report = self._merge(_meta(("a", "")), _labels(("a", "러블리")))
        self.assertEqual(merged[0]["style"], "러블리")
        self.assertEqual(report["num_filled"], 1)

    def test_existing_style_is_not_overwritten(self) -> None:
        """수집자 폴더명에서 온 값이 이미지를 보고 붙인 값보다 출처가 분명하다."""
        merged, report = self._merge(_meta(("a", "포멀")), _labels(("a", "러블리")))
        self.assertEqual(merged[0]["style"], "포멀")
        self.assertEqual(report["num_filled"], 0)

    def test_value_outside_vocabulary_is_dropped(self) -> None:
        """리트리버가 style을 필터 키로 쓴다. 어휘 밖 값은 검색에서 조용히 빠진다."""
        merged, report = self._merge(_meta(("a", "")), _labels(("a", "블로크코어")))
        self.assertEqual(merged[0]["style"], "")
        self.assertEqual(report["outside_vocabulary"], {"블로크코어": 1})

    def test_partly_valid_value_keeps_the_valid_half(self) -> None:
        merged, report = self._merge(
            _meta(("a", "")), _labels(("a", "러블리;코케트"))
        )
        self.assertEqual(merged[0]["style"], "러블리")
        self.assertEqual(report["outside_vocabulary"], {"코케트": 1})

    def test_more_than_two_styles_is_truncated(self) -> None:
        merged, report = self._merge(
            _meta(("a", "")), _labels(("a", "러블리;페미닌;미니멀"))
        )
        self.assertEqual(merged[0]["style"], "러블리;페미닌")
        self.assertEqual(report["too_many_styles"], ["a"])

    def test_unknown_golden_id_is_reported_not_crashing(self) -> None:
        merged, report = self._merge(_meta(("a", "")), _labels(("zzz", "러블리")))
        self.assertEqual(merged[0]["style"], "")
        self.assertEqual(report["unknown_golden_ids"], ["zzz"])

    def test_missing_taxonomy_skips_validation(self) -> None:
        """image-processor를 못 읽는 환경에서도 병합 자체는 돌아야 한다."""
        with patch(
            "ml.golden_set.style_clusters._taxonomy_styles", return_value=None
        ):
            merged, report = merge_style_labels(
                _meta(("a", "")), _labels(("a", "블로크코어"))
            )
        self.assertEqual(merged[0]["style"], "블로크코어")
        self.assertEqual(report["outside_vocabulary"], {})


class BuildClustersTests(unittest.TestCase):
    def _build(self, metadata, labels=None, write_metadata=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta_csv = root / "metadata.csv"
            with meta_csv.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=METADATA_FIELDS)
                writer.writeheader()
                writer.writerows(metadata)
            label_csv = None
            if labels is not None:
                label_csv = root / "labels.csv"
                with label_csv.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["golden_id", "style"])
                    writer.writeheader()
                    writer.writerows(labels)
            run_dir = root / "run"
            with patch(
                "ml.golden_set.style_clusters._taxonomy_styles", return_value=STYLES
            ):
                report = build_style_clusters(
                    run_dir=run_dir,
                    metadata_csv=meta_csv,
                    style_labels_csv=label_csv,
                    out_metadata_csv=(root / "out.csv") if write_metadata else None,
                )
            rows = [
                json.loads(line)
                for line in (run_dir / "clusters.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ]
            before = meta_csv.read_text(encoding="utf-8-sig")
            # 임시 폴더가 사라지기 전에 읽어 둔다.
            written = (
                Path(report["metadata_csv"]).read_text(encoding="utf-8-sig")
                if write_metadata
                else ""
            )
            return report, rows, before, written

    def test_cluster_id_is_the_style(self) -> None:
        _, rows, _, _ = self._build(_meta(("a", "러블리"), ("b", "캐주얼")))
        self.assertEqual(
            {r["golden_id"]: r["cluster_id"] for r in rows},
            {"a": "러블리", "b": "캐주얼"},
        )

    def test_first_style_wins_when_two(self) -> None:
        """여러 묶음에 넣으면 같은 claim이 두 원칙의 근거가 되어 지지 수가 부풀려진다."""
        _, rows, _, _ = self._build(_meta(("a", "러블리;페미닌")))
        self.assertEqual(rows[0]["cluster_id"], "러블리")

    def test_unstyled_goes_to_its_own_bucket(self) -> None:
        report, rows, _, _ = self._build(_meta(("a", "러블리"), ("b", "")))
        self.assertEqual(rows[1]["cluster_id"], UNSTYLED_CLUSTER)
        self.assertEqual(report["num_unstyled"], 1)

    def test_selection_role_is_member(self) -> None:
        """스타일 라벨에는 중심 거리가 없어 대표·경계를 고를 수 없다."""
        _, rows, _, _ = self._build(_meta(("a", "러블리")))
        self.assertEqual(rows[0]["selection_role"], "member")
        self.assertEqual(rows[0]["cluster_source"], "style_label")

    def test_labels_are_applied_before_clustering(self) -> None:
        report, rows, _, _ = self._build(_meta(("a", "")), _labels(("a", "댄디")))
        self.assertEqual(rows[0]["cluster_id"], "댄디")
        self.assertEqual(report["num_unstyled"], 0)

    def test_source_metadata_is_never_touched(self) -> None:
        """--out-metadata를 명시하지 않으면 원본을 건드리지 않는다."""
        _, _, before, _ = self._build(_meta(("a", "")), _labels(("a", "댄디")))
        self.assertIn("a.jpg,,women", before)

    def test_out_metadata_carries_the_merged_style(self) -> None:
        _, _, _, written = self._build(
            _meta(("a", "")), _labels(("a", "댄디")), write_metadata=True
        )
        self.assertIn("댄디", written)

    def test_report_counts_cluster_sizes(self) -> None:
        report, _, _, _ = self._build(
            _meta(("a", "러블리"), ("b", "러블리"), ("c", "캐주얼"))
        )
        self.assertEqual(report["num_clusters"], 2)
        self.assertEqual(report["cluster_sizes"], {"러블리": 2, "캐주얼": 1})


if __name__ == "__main__":
    unittest.main()
