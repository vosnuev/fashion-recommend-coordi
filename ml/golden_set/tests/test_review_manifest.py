"""본 검수 인벤토리·표집 배치 테스트.

이 단계가 틀리면 뒤가 조용히 틀린다. 파일명이 겹치면 검수 화면이 다른 사진을 띄운
채 판정이 쌓이고, 스타일이 taxonomy 밖 값으로 새면 그 코디는 검색에서 통째로
빠진다. 둘 다 결과물만 봐서는 알아채기 어려워 여기서 붙잡는다.
"""

from __future__ import annotations

import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ml.golden_set import review_manifest
from ml.golden_set.review_manifest import (
    Collector,
    build_review_manifest,
    scan,
)

#: 최소 PNG 1x1. 내용이 달라야 sha256이 갈리므로 색만 바꿔 쓴다.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d49444154789c6360000002000100"
)


def _write_image(path: Path, salt: bytes = b"") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_PNG + salt)


class ScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_same_original_name_across_folders_stays_unique(self) -> None:
        """수집자마다 `001.jpg`를 쓴다. 평면 폴더에서 겹치면 다른 사진이 뜬다."""
        _write_image(self.root / "전하영" / "men" / "001.png", b"a")
        _write_image(self.root / "전하영" / "women" / "001.png", b"b")
        _write_image(self.root / "김민욱" / "men" / "001.png", b"c")

        collectors = [
            Collector("jhy", "전하영", "전하영", "https://example.invalid/jhy"),
            Collector("kmw", "김민욱", "김민욱", "https://example.invalid/kmw"),
        ]
        result = scan(self.root, collectors)
        review_manifest.assign_names(result.records)

        names = [record.file_name for record in result.records]
        self.assertEqual(len(names), 3)
        self.assertEqual(len(set(names)), 3, names)

    def test_root_collector_does_not_swallow_other_collectors(self) -> None:
        """신혜지 폴더만 성별이 루트에 풀려 있어 rel_path가 "."다.

        같은 루트에 다른 수집자를 내려받아도 섞이면 안 된다 — 성별 이름이 아닌
        폴더에서 끊는 것이 그 장치다.
        """
        _write_image(self.root / "남자" / "[1] 캐주얼룩 Casual Look" / "a.png", b"a")
        _write_image(self.root / "김민욱" / "men" / "b.png", b"b")

        result = scan(self.root, [Collector("shj", "신혜지", ".", "https://example.invalid/shj", True)])

        self.assertEqual([r.original_relpath for r in result.records],
                         ["남자/[1] 캐주얼룩 Casual Look/a.png"])

    def test_style_folder_maps_to_taxonomy_and_keeps_original_label(self) -> None:
        _write_image(self.root / "여자" / "[13] 블록코어 Blokecore" / "a.png", b"a")

        result = scan(self.root, [Collector("shj", "신혜지", ".", "https://example.invalid/shj", True)])

        record = result.records[0]
        self.assertEqual(record.style_values, ["스포티", "스트릿"])
        self.assertEqual(record.style_source_label, "블록코어 Blokecore")
        self.assertEqual(record.style_slug, "blokecore")

    def test_drive_download_wrapper_folder_is_traversed(self) -> None:
        """드라이브 zip은 `김민욱-20260816T160950Z-1-001/김민욱/`처럼 한 겹 씌운다.

        래퍼 이름에 받은 시각이 들어가 설정에 적어 둘 수 없다.
        """
        wrapper = self.root / "김민욱-20260816T160950Z-1-001" / "김민욱"
        _write_image(wrapper / "men" / "a.png", b"a")

        result = scan(self.root, [Collector("kmw", "김민욱", "김민욱", "https://example.invalid/kmw")])

        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].presentation_group, "men")
        self.assertEqual(result.missing_collectors, [])

    def test_missing_collector_is_reported_not_silently_empty(self) -> None:
        result = scan(self.root, [Collector("kmw", "김민욱", "김민욱", "https://example.invalid/kmw")])

        self.assertEqual(result.records, [])
        self.assertEqual(len(result.missing_collectors), 1)

    def test_file_without_extension_is_reported_not_dropped(self) -> None:
        """드라이브에는 확장자 없이 올라온 파일이 실제로 섞여 있다."""
        _write_image(self.root / "김민욱" / "men" / "054", b"a")

        result = scan(self.root, [Collector("kmw", "김민욱", "김민욱", "https://example.invalid/kmw")])

        self.assertEqual(result.records, [])
        self.assertEqual(result.skipped, ["김민욱/men/054"])


class StyleMapTests(unittest.TestCase):
    def test_mapped_values_exist_in_taxonomy(self) -> None:
        """목록 밖 값이 하나만 섞여도 그 코디는 리트리버 필터에서 빠진다."""
        allowed = review_manifest._taxonomy_styles()
        if allowed is None:
            self.skipTest("image-processor taxonomy를 불러올 수 없음")
        used = {value for values, _ in review_manifest.STYLE_MAP.values() for value in values}
        self.assertEqual(used - allowed, set())

    def test_slugs_are_unique(self) -> None:
        """슬러그가 겹치면 서로 다른 스타일이 같은 연번 묶음을 나눠 쓴다."""
        slugs = [slug for _, slug in review_manifest.STYLE_MAP.values()]
        self.assertEqual(len(slugs), len(set(slugs)))


class BuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name) / "src"
        self.out = Path(self._tmp.name) / "out"
        self.addCleanup(self._tmp.cleanup)

        self.collectors = [
            Collector("kmw", "김민욱", "김민욱", "https://example.invalid/kmw"),
            Collector("jhy", "전하영", "전하영", "https://example.invalid/jhy"),
        ]
        for index in range(6):
            _write_image(self.root / "김민욱" / "men" / f"{index}.png", bytes([index]))
            _write_image(self.root / "전하영" / "women" / f"{index}.png", bytes([100 + index]))
        # 수집자끼리 같은 사진을 모은 경우.
        _write_image(self.root / "전하영" / "men" / "dup.png", bytes([0]))

    def test_metadata_columns_match_manifest_contract(self) -> None:
        """앞 13개 열은 manifest가 읽는 계약이다. metadata.example.csv가 기준."""
        example = Path(__file__).resolve().parents[1] / "metadata.example.csv"
        with example.open(encoding="utf-8-sig", newline="") as handle:
            expected = next(csv.reader(handle))
        self.assertEqual(review_manifest.METADATA_COLUMNS[: len(expected)], expected)

    def test_presentation_group_survives_manifest_normalization(self) -> None:
        """manifest가 다시 정규화해도 값이 그대로여야 한다 — 흔들리면 검색 누락이다."""
        try:
            from ml.golden_set.manifest import normalize_presentation_group
        except ImportError:  # Pillow 등 파이프라인 의존성이 없는 환경
            self.skipTest("manifest 모듈을 불러올 수 없음")

        summary = build_review_manifest(
            root=self.root, out_dir=self.out, collectors=self.collectors, batch_size=4
        )
        with Path(str(summary["metadata_csv"])).open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            self.assertEqual(
                normalize_presentation_group(row["presentation_group"]),
                row["presentation_group"],
            )

    def test_batch_skips_duplicates_and_spreads_across_collectors(self) -> None:
        summary = build_review_manifest(
            root=self.root, out_dir=self.out, collectors=self.collectors, batch_size=4
        )
        with Path(str(summary["batch_csv"])).open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 4)
        self.assertEqual([row["duplicate_of"] for row in rows], ["", "", "", ""])
        self.assertEqual({row["collector"] for row in rows}, {"김민욱", "전하영"})
        self.assertEqual(summary["duplicates"], 1)

    def test_exclude_continues_without_overlapping_previous_batch(self) -> None:
        """이미 검수를 시작한 배치는 그대로 두고 나머지에서 이어 뽑아야 한다."""
        first = build_review_manifest(
            root=self.root, out_dir=self.out, collectors=self.collectors, batch_size=4
        )
        second_dir = self.out / "b2"
        second = build_review_manifest(
            root=self.root,
            out_dir=second_dir,
            collectors=self.collectors,
            batch_size=4,
            batch_label="batch2",
            exclude_csvs=[Path(str(first["batch_csv"]))],
        )

        def ids(path: str) -> set[str]:
            with Path(path).open(encoding="utf-8-sig", newline="") as handle:
                return {row["golden_id"] for row in csv.DictReader(handle)}

        self.assertEqual(ids(str(first["batch_csv"])) & ids(str(second["batch_csv"])), set())

    def test_quota_shifts_weight_off_equal_collector_split(self) -> None:
        """다양성이 한 수집자에게 몰려 있으면 균등 표집은 비슷한 코디만 가져온다."""
        summary = build_review_manifest(
            root=self.root,
            out_dir=self.out,
            collectors=self.collectors,
            batch_size=8,
            quotas={"jhy": 1},
        )
        with Path(str(summary["batch_csv"])).open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))

        # 상한에 걸린 수집자는 1장에서 멈추고, 나머지는 상한 없이 계속 채운다.
        # 픽스처의 중복 아닌 이미지는 김민욱 6 + 전하영 6이라 상한 적용 시 최대 7장이다.
        self.assertEqual(sum(1 for row in rows if row["collector"] == "전하영"), 1)
        self.assertEqual(sum(1 for row in rows if row["collector"] == "김민욱"), 6)
        self.assertEqual(len(rows), 7)

    def test_apply_copies_batch_into_its_own_folder(self) -> None:
        """prepare --input-dir이 배치만 훑으려면 배치 폴더가 따로 있어야 한다."""
        summary = build_review_manifest(
            root=self.root,
            out_dir=self.out,
            collectors=self.collectors,
            batch_size=4,
            apply_rename=True,
        )
        batch_dir = self.out / "images-batch1"
        self.assertEqual(len(list(batch_dir.iterdir())), 4)
        self.assertEqual(len(list((self.out / "images").iterdir())), summary["total"])

    def test_rerun_does_not_leave_stale_images_behind(self) -> None:
        """표집이 바뀌면 이전 회차 사진이 남는다 — 검수표에 없는 사진을 보게 된다."""
        build_review_manifest(
            root=self.root,
            out_dir=self.out,
            collectors=self.collectors,
            batch_size=4,
            apply_rename=True,
        )
        stale = self.out / "images-batch1" / "stale-from-previous-run.png"
        stale.write_bytes(_PNG)

        summary = build_review_manifest(
            root=self.root,
            out_dir=self.out,
            collectors=self.collectors,
            batch_size=4,
            apply_rename=True,
        )

        self.assertFalse(stale.exists())
        self.assertEqual(len(list((self.out / "images-batch1").iterdir())), summary["batch"])

    def test_originals_are_left_in_place(self) -> None:
        """원본은 팀 공유 드라이브 사본이다. 옮기면 되돌릴 수 없다."""
        before = sorted(p.name for p in (self.root / "김민욱" / "men").iterdir())
        build_review_manifest(
            root=self.root,
            out_dir=self.out,
            collectors=self.collectors,
            batch_size=4,
            apply_rename=True,
        )
        after = sorted(p.name for p in (self.root / "김민욱" / "men").iterdir())
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
