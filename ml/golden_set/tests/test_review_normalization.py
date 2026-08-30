"""축약 검수표(goldenset-review-sheets)를 표준 승인 계약으로 펴는 규칙."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from ml.golden_set.review import (
    collect_accepted_claims,
    normalize_claim_rows,
    normalize_observation_rows,
)

OBSERVATION_SHEET_FIELDS = [
    "reviewer_label",
    "golden_id",
    "local_path",
    "style_intent",
    "detected_items",
    "image_assessable",
    "detected_items_correct",
    "corrected_detected_items",
    "human_confidence_1_3",
    "notes",
]

CLAIM_SHEET_FIELDS = [
    "reviewer_label",
    "golden_id",
    "local_path",
    "claim_id",
    "axis",
    "statement",
    "evidence_region_ids",
    "evidence_correct",
    "human_judgment",
    "overgeneralization_risk",
    "condition_tag",
    "human_confidence_1_3",
    "notes",
]


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _analysis(golden_id: str, claim_ids: list[str]) -> dict:
    return {
        "golden_id": golden_id,
        "status": "SUCCEEDED",
        "result": {
            "observations": [{"region_id": "coat", "item_name": "롱 코트"}],
            "claims": [
                {
                    "claim_id": claim_id,
                    "axis": "A1_COLOR_HARMONY",
                    "statement": f"{claim_id} 문장",
                    "evidence_region_ids": ["coat"],
                }
                for claim_id in claim_ids
            ],
        },
    }


class NormalizeObservationTests(unittest.TestCase):
    def test_yes_becomes_approve(self) -> None:
        (row,) = normalize_observation_rows([{"detected_items_correct": "YES"}])
        self.assertEqual(row["observation_verdict"], "APPROVE")
        self.assertEqual(row["items_complete"], "YES")

    def test_no_with_correction_becomes_edit(self) -> None:
        (row,) = normalize_observation_rows(
            [
                {
                    "detected_items_correct": "NO",
                    "corrected_detected_items": "shoes:블랙 레더 슈즈",
                }
            ]
        )
        self.assertEqual(row["observation_verdict"], "EDIT")
        self.assertEqual(row["items_complete"], "YES")
        self.assertEqual(row["missing_observations"], "shoes:블랙 레더 슈즈")

    def test_no_without_correction_keeps_items_incomplete(self) -> None:
        """무엇이 맞는지 모르는 상태를 승인하면 틀린 목록이 원칙 근거가 된다."""
        (row,) = normalize_observation_rows([{"detected_items_correct": "NO"}])
        self.assertEqual(row["items_complete"], "NO")

    def test_unsure_is_not_approved(self) -> None:
        (row,) = normalize_observation_rows([{"detected_items_correct": "UNSURE"}])
        self.assertEqual(row["observation_verdict"], "UNSURE")

    def test_existing_values_are_kept(self) -> None:
        """표준 검수표로 받은 기존 결과는 그대로 통과해야 한다."""
        (row,) = normalize_observation_rows(
            [
                {
                    "detected_items_correct": "YES",
                    "observation_verdict": "REJECT",
                    "items_complete": "NO",
                }
            ]
        )
        self.assertEqual(row["observation_verdict"], "REJECT")
        self.assertEqual(row["items_complete"], "NO")

    def test_blank_row_stays_blank(self) -> None:
        (row,) = normalize_observation_rows([{"detected_items_correct": ""}])
        self.assertEqual(row.get("observation_verdict", ""), "")


class NormalizeClaimTests(unittest.TestCase):
    def test_contributes_with_evidence_becomes_approve(self) -> None:
        (row,) = normalize_claim_rows(
            [{"human_judgment": "CONTRIBUTES", "evidence_correct": "YES"}]
        )
        self.assertEqual(row["verdict"], "APPROVE")

    def test_descriptive_only_is_approved_not_rejected(self) -> None:
        # 틀린 claim과 맞지만 묘사일 뿐인 claim을 한 덩어리로 만들지 않는다.
        (row,) = normalize_claim_rows(
            [{"human_judgment": "DESCRIPTIVE_ONLY", "evidence_correct": "YES"}]
        )
        self.assertEqual(row["verdict"], "APPROVE")

    def test_negative_judgment_becomes_reject(self) -> None:
        for judgment in ("UNSUPPORTED", "INCORRECT"):
            with self.subTest(judgment=judgment):
                (row,) = normalize_claim_rows(
                    [{"human_judgment": judgment, "evidence_correct": "YES"}]
                )
                self.assertEqual(row["verdict"], "REJECT")

    def test_overgeneralization_becomes_reject(self) -> None:
        (row,) = normalize_claim_rows(
            [
                {
                    "human_judgment": "CONTRIBUTES",
                    "evidence_correct": "YES",
                    "overgeneralization_risk": "YES",
                }
            ]
        )
        self.assertEqual(row["verdict"], "REJECT")

    def test_unsure_evidence_becomes_unsure(self) -> None:
        (row,) = normalize_claim_rows(
            [{"human_judgment": "CONTRIBUTES", "evidence_correct": "UNSURE"}]
        )
        self.assertEqual(row["verdict"], "UNSURE")

    def test_existing_verdict_is_kept(self) -> None:
        (row,) = normalize_claim_rows(
            [
                {
                    "human_judgment": "CONTRIBUTES",
                    "evidence_correct": "YES",
                    "verdict": "REJECT",
                }
            ]
        )
        self.assertEqual(row["verdict"], "REJECT")


class CollectFromSheetTests(unittest.TestCase):
    """축약 검수표 2인분이 승인 claim까지 도달하는지."""

    def _run(self, observation_rows, claim_rows, analyses):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with (run_dir / "analyses.jsonl").open("w", encoding="utf-8") as handle:
                for row in analyses:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            observation_csv = run_dir / "observation_reviews.csv"
            claim_csv = run_dir / "claim_reviews.csv"
            _write_csv(observation_csv, OBSERVATION_SHEET_FIELDS, observation_rows)
            _write_csv(claim_csv, CLAIM_SHEET_FIELDS, claim_rows)
            return collect_accepted_claims(
                observation_reviews_csv=observation_csv,
                claim_reviews_csv=claim_csv,
                run_dir=run_dir,
            )

    def test_two_reviewers_approve_claim(self) -> None:
        """축약 표에 없는 unassessable_complete·bbox_grounding이 승인을 막지 않는다."""
        observation_rows = [
            {
                "reviewer_label": label,
                "golden_id": "g-001",
                "image_assessable": "YES",
                "detected_items_correct": "YES",
                "human_confidence_1_3": "3",
            }
            for label in ("reviewer-a", "reviewer-b")
        ]
        claim_rows = [
            {
                "reviewer_label": label,
                "golden_id": "g-001",
                "claim_id": "C1",
                "evidence_correct": "YES",
                "human_judgment": "CONTRIBUTES",
                "overgeneralization_risk": "NO",
                "human_confidence_1_3": "3",
            }
            for label in ("reviewer-a", "reviewer-b")
        ]
        accepted, report = self._run(
            observation_rows, claim_rows, [_analysis("g-001", ["C1"])]
        )
        self.assertEqual(report["accepted_image_count"], 1)
        self.assertEqual(report["accepted_claim_count"], 1)
        self.assertEqual(accepted["g-001"][0]["claim_id"], "C1")
        self.assertEqual(accepted["g-001"][0]["human_review"]["reviewer_count"], 2)

    def test_descriptive_only_pair_is_excluded_not_rejected(self) -> None:
        observation_rows = [
            {
                "reviewer_label": label,
                "golden_id": "g-001",
                "image_assessable": "YES",
                "detected_items_correct": "YES",
            }
            for label in ("reviewer-a", "reviewer-b")
        ]
        claim_rows = [
            {
                "reviewer_label": label,
                "golden_id": "g-001",
                "claim_id": "C1",
                "evidence_correct": "YES",
                "human_judgment": "DESCRIPTIVE_ONLY",
                "overgeneralization_risk": "NO",
            }
            for label in ("reviewer-a", "reviewer-b")
        ]
        _, report = self._run(
            observation_rows, claim_rows, [_analysis("g-001", ["C1"])]
        )
        self.assertEqual(report["accepted_claim_count"], 0)
        self.assertEqual(report["excluded_claims"], ["g-001:C1"])

    def test_one_reviewer_unsure_leaves_image_pending(self) -> None:
        observation_rows = [
            {
                "reviewer_label": "reviewer-a",
                "golden_id": "g-001",
                "image_assessable": "YES",
                "detected_items_correct": "YES",
            },
            {
                "reviewer_label": "reviewer-b",
                "golden_id": "g-001",
                "image_assessable": "UNSURE",
                "detected_items_correct": "YES",
            },
        ]
        claim_rows = [
            {
                "reviewer_label": label,
                "golden_id": "g-001",
                "claim_id": "C1",
                "evidence_correct": "YES",
                "human_judgment": "CONTRIBUTES",
                "overgeneralization_risk": "NO",
            }
            for label in ("reviewer-a", "reviewer-b")
        ]
        _, report = self._run(
            observation_rows, claim_rows, [_analysis("g-001", ["C1"])]
        )
        self.assertEqual(report["accepted_image_count"], 0)
        self.assertEqual(report["pending_images"], ["g-001"])


if __name__ == "__main__":
    unittest.main()
