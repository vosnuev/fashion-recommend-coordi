"""오프라인 골든셋 run artifact를 PostgreSQL SSOT로 승격한다."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.goldenset.models import (
    GoldenAnalysis,
    GoldenDataset,
    GoldenImage,
    GoldenOutfitItem,
    GoldenPairwiseReview,
    GoldenPrinciple,
    GoldenPrincipleEvidence,
    GoldenReview,
)


class Command(BaseCommand):
    help = "ml/golden_set 실행 산출물을 PostgreSQL에 멱등 import한다."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--run-dir", type=Path, required=True)
        parser.add_argument("--image-reviews", type=Path)
        parser.add_argument("--observation-reviews", type=Path)
        parser.add_argument("--claim-reviews", type=Path)
        parser.add_argument("--minimum-edit-reviews", type=Path)
        parser.add_argument("--pairwise-reviews", type=Path)
        parser.add_argument("--principle-reviews", type=Path)

    def handle(self, *args: Any, **options: Any) -> None:
        run_dir: Path = options["run_dir"].resolve()
        manifest_path = run_dir / "run_manifest.json"
        if not manifest_path.exists():
            raise CommandError(f"run_manifest.json이 없습니다: {run_dir}")
        manifest = _read_json(manifest_path)

        with transaction.atomic():
            dataset, _ = GoldenDataset.objects.update_or_create(
                name=manifest["dataset_name"],
                version=manifest["dataset_version"],
                defaults={
                    "status": GoldenDataset.Status.PILOT,
                    "run_id": manifest.get("run_id", run_dir.name),
                    "source_metadata": manifest,
                },
            )
            image_map = self._import_images(dataset, run_dir)
            self._import_items(image_map, run_dir)
            self._import_analyses(image_map, run_dir)
            principle_map = self._import_principles(dataset, image_map, run_dir)
            if options.get("image_reviews"):
                self._import_image_reviews(
                    dataset,
                    image_map,
                    options["image_reviews"],
                )
            if options.get("observation_reviews"):
                self._import_observation_reviews(
                    dataset,
                    image_map,
                    options["observation_reviews"],
                )
            if options.get("claim_reviews"):
                self._import_claim_reviews(
                    dataset,
                    image_map,
                    options["claim_reviews"],
                )
            if options.get("minimum_edit_reviews"):
                self._import_minimum_edit_reviews(
                    dataset,
                    image_map,
                    options["minimum_edit_reviews"],
                )
            if options.get("pairwise_reviews"):
                self._import_pairwise_reviews(
                    dataset,
                    image_map,
                    options["pairwise_reviews"],
                )
            if options.get("principle_reviews"):
                self._import_principle_reviews(
                    dataset,
                    principle_map,
                    options["principle_reviews"],
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"import 완료: {dataset} "
                f"(images={len(image_map)}, principles={len(principle_map)})"
            )
        )

    def _import_images(
        self,
        dataset: GoldenDataset,
        run_dir: Path,
    ) -> dict[str, GoldenImage]:
        clusters = {
            row["golden_id"]: row for row in _read_jsonl(run_dir / "clusters.jsonl")
        }
        anchors = {
            row["golden_id"]: row
            for row in _read_jsonl(run_dir / "anchor_scores.jsonl")
        }
        embedding_meta = _read_json(run_dir / "image_embeddings.meta.json")
        result: dict[str, GoldenImage] = {}
        valid_splits = {value for value, _ in GoldenImage.Split.choices}
        valid_scopes = {value for value, _ in GoldenImage.UsageScope.choices}
        for row in _read_jsonl(run_dir / "images.jsonl"):
            golden_id = str(row["golden_id"])
            anchor = anchors.get(golden_id, {})
            split = str(row.get("split", GoldenImage.Split.KNOWLEDGE)).upper()
            usage_scope = str(
                row.get("usage_scope", GoldenImage.UsageScope.UNKNOWN)
            ).upper()
            image, _ = GoldenImage.objects.update_or_create(
                dataset=dataset,
                golden_id=golden_id,
                defaults={
                    "source_uri": row["source_uri"],
                    "source_name": row.get("source_name", ""),
                    "usage_scope": (
                        usage_scope
                        if usage_scope in valid_scopes
                        else GoldenImage.UsageScope.UNKNOWN
                    ),
                    "original_exposable": bool(row.get("original_exposable", False)),
                    "image_sha256": row["image_sha256"],
                    "perceptual_hash": row.get("perceptual_hash", ""),
                    "split": (
                        split if split in valid_splits else GoldenImage.Split.KNOWLEDGE
                    ),
                    "presentation_group": row.get("presentation_group", ""),
                    "cluster_id": clusters.get(golden_id, {}).get("cluster_id", ""),
                    "metadata": {
                        **row.get("metadata", {}),
                        "duplicate_of": row.get("duplicate_of", ""),
                        "duplicate_kind": row.get("duplicate_kind", ""),
                        "selection_role": clusters.get(golden_id, {}).get(
                            "selection_role", ""
                        ),
                    },
                    "human_score": anchor.get("human_score"),
                    "score_band": anchor.get("score_band", ""),
                    "score_confidence": anchor.get("score_confidence"),
                    "embedding_version": embedding_meta.get("model", ""),
                },
            )
            result[golden_id] = image
        return result

    def _import_items(
        self,
        image_map: dict[str, GoldenImage],
        run_dir: Path,
    ) -> None:
        """items.jsonl(코디에서 분리한 의상 아이템)을 PG로 승격한다.

        벡터는 넣지 않는다 — Qdrant(goldenset_items)가 소유하고 여기는 태그와
        S3 위치, 버전만 보관한다(이미지·앵커와 같은 원칙).
        """
        items_path = run_dir / "items.jsonl"
        if not items_path.exists():
            return
        meta = _read_json(run_dir / "items.meta.json") if (
            run_dir / "items.meta.json"
        ).exists() else {}
        valid_statuses = {value for value, _ in GoldenOutfitItem.Status.choices}
        seen: dict[str, set[int]] = {}
        for row in _read_jsonl(items_path):
            image = image_map.get(str(row["golden_id"]))
            if image is None:
                continue
            status = str(row.get("status", GoldenOutfitItem.Status.SUCCEEDED)).upper()
            index = int(row.get("item_index", 0))
            GoldenOutfitItem.objects.update_or_create(
                image=image,
                item_index=index,
                defaults={
                    "item_key": row.get("item_key", ""),
                    "s3_bucket": row.get("s3_bucket", ""),
                    "s3_key": row.get("s3_key", ""),
                    "item_name": row.get("item_name", ""),
                    "category_large": row.get("category_large", ""),
                    "category_small": row.get("category_small", ""),
                    "season": list(row.get("season") or []),
                    "style": list(row.get("style") or []),
                    "usage": list(row.get("usage") or []),
                    "color": row.get("color", ""),
                    "pattern": row.get("pattern", ""),
                    "fit": row.get("fit", ""),
                    "material": row.get("material", ""),
                    "sleeve": row.get("sleeve", ""),
                    "length": row.get("length", ""),
                    "layer_role": row.get("layer_role", ""),
                    "layer_order": row.get("layer_order"),
                    "label_ko": row.get("label_ko", ""),
                    "descriptor_en": row.get("descriptor_en", ""),
                    "view_angle": row.get("view_angle", ""),
                    "occluded_by": list(row.get("occluded_by") or []),
                    "bbox": row.get("bbox"),
                    "missing_required": list(row.get("missing_required") or []),
                    "pipeline_key": row.get("pipeline_key", ""),
                    "image_embedding_version": row.get("image_embedding_version", "")
                    or str(meta.get("embedding_version", "")),
                    "text_embedding_version": row.get("text_embedding_version", "")
                    or str(meta.get("embedding_version", "")),
                    "status": (
                        status
                        if status in valid_statuses
                        else GoldenOutfitItem.Status.FAILED
                    ),
                    "error_message": row.get("error_message", ""),
                },
            )
            seen.setdefault(str(row["golden_id"]), set()).add(index)

        # 재실행에서 아이템 수가 줄어든 코디의 잔재를 지운다 (파이프라인 교체 등).
        for golden_id, indexes in seen.items():
            image = image_map.get(golden_id)
            if image is not None:
                GoldenOutfitItem.objects.filter(image=image).exclude(
                    item_index__in=indexes
                ).delete()

    def _import_analyses(
        self,
        image_map: dict[str, GoldenImage],
        run_dir: Path,
    ) -> None:
        for row in _read_jsonl(run_dir / "analyses.jsonl"):
            image = image_map.get(str(row["golden_id"]))
            if image is None:
                continue
            GoldenAnalysis.objects.update_or_create(
                image=image,
                model_version=row["model_version"],
                prompt_version=row["prompt_version"],
                schema_version=row["schema_version"],
                defaults={
                    "status": row.get("status", GoldenAnalysis.Status.FAILED),
                    "result": row.get("result", {}),
                    "error_message": row.get("error_message", ""),
                    "latency_seconds": row.get("latency_seconds"),
                },
            )

    def _import_principles(
        self,
        dataset: GoldenDataset,
        image_map: dict[str, GoldenImage],
        run_dir: Path,
    ) -> dict[str, GoldenPrinciple]:
        result: dict[str, GoldenPrinciple] = {}
        analyses = {
            row["golden_id"]: row for row in _read_jsonl(run_dir / "analyses.jsonl")
        }
        valid_statuses = {value for value, _ in GoldenPrinciple.Status.choices}
        for row in _read_jsonl(run_dir / "principles.jsonl"):
            status = str(row.get("status", GoldenPrinciple.Status.DRAFT)).upper()
            principle, _ = GoldenPrinciple.objects.update_or_create(
                dataset=dataset,
                principle_key=row["principle_key"],
                version=row["version"],
                defaults={
                    "dimension": row["dimension"],
                    "statement": row["statement"],
                    "applies_when": row.get("applies_when", []),
                    "exceptions": row.get("exceptions", []),
                    "confidence": row.get("confidence", 0.0),
                    "status": (
                        status
                        if status in valid_statuses
                        else GoldenPrinciple.Status.DRAFT
                    ),
                    "metadata": {
                        "cluster_id": row.get("cluster_id", ""),
                        "model_version": row.get("model_version", ""),
                        "review_count": row.get("review_count", 0),
                        "knowledge_role": row.get(
                            "knowledge_role", "NEEDS_COUNTEREXAMPLE"
                        ),
                        "principle_type": row.get(
                            "principle_type", "SOFT_PRINCIPLE"
                        ),
                        "eligible_for_scoring": bool(
                            row.get("eligible_for_scoring", False)
                        ),
                        "support_image_count": row.get("support_image_count", 0),
                        "comparison_evidence_count": row.get(
                            "comparison_evidence_count", 0
                        ),
                        "reviewer_count": row.get("reviewer_count", 0),
                        "reviewer_agreement": row.get("reviewer_agreement", 0.0),
                    },
                },
            )
            result[row["principle_key"]] = principle
            for evidence in row.get("evidence", []):
                golden_id = str(evidence.get("golden_id", ""))
                claim_id = str(evidence.get("claim_id", ""))
                image = image_map.get(golden_id)
                if image is None:
                    continue
                claim = _find_claim(analyses.get(golden_id, {}), claim_id)
                GoldenPrincipleEvidence.objects.update_or_create(
                    principle=principle,
                    image=image,
                    claim_key=claim_id,
                    polarity=GoldenPrincipleEvidence.Polarity.SUPPORT,
                    defaults={
                        "region_ids": claim.get("evidence_region_ids", []),
                        "confidence": claim.get(
                            "model_confidence", claim.get("confidence", 0.0)
                        ),
                    },
                )
        return result

    def _import_image_reviews(
        self,
        dataset: GoldenDataset,
        image_map: dict[str, GoldenImage],
        path: Path,
    ) -> None:
        for row_number, row in enumerate(_read_csv(path), start=2):
            image = image_map.get(row.get("golden_id", ""))
            verdict = row.get("verdict", "").upper()
            if image is None or verdict not in dict(GoldenReview.Verdict.choices):
                continue
            reviewer = row.get("reviewer_label", "") or "anonymous"
            GoldenReview.objects.update_or_create(
                dataset=dataset,
                review_key=f"image:{image.golden_id}:{reviewer}:{row_number}",
                defaults={
                    "image": image,
                    "principle": None,
                    "reviewer_label": reviewer,
                    "verdict": verdict,
                    "scores": {
                        "overall_score_1_5": _number_or_none(
                            row.get("overall_score_1_5", "")
                        ),
                        "confidence_1_3": _number_or_none(
                            row.get("confidence_1_3", "")
                        ),
                        "approved_claim_ids": _split_values(
                            row.get("approved_claim_ids", "")
                        ),
                    },
                    "rationale": row.get("edited_rationale") or row.get("notes", ""),
                },
            )

    def _import_observation_reviews(
        self,
        dataset: GoldenDataset,
        image_map: dict[str, GoldenImage],
        path: Path,
    ) -> None:
        for row in _read_csv(path):
            image = image_map.get(row.get("golden_id", ""))
            verdict = row.get("observation_verdict", "").upper()
            reviewer = row.get("reviewer_label", "").strip()
            if (
                image is None
                or not reviewer
                or verdict not in dict(GoldenReview.Verdict.choices)
            ):
                continue
            GoldenReview.objects.update_or_create(
                dataset=dataset,
                review_key=f"observation:{image.golden_id}:{reviewer}",
                defaults={
                    "image": image,
                    "principle": None,
                    "reviewer_label": reviewer,
                    "verdict": verdict,
                    "scores": {
                        "review_type": "OBSERVATION",
                        "image_assessable": row.get("image_assessable", ""),
                        "items_complete": row.get("items_complete", ""),
                        "bbox_grounding_1_3": _number_or_none(
                            row.get("bbox_grounding_1_3", "")
                        ),
                        "unassessable_complete": row.get(
                            "unassessable_complete", ""
                        ),
                        "human_confidence_1_3": _number_or_none(
                            row.get("human_confidence_1_3", "")
                        ),
                        "axis_scores_1_5": {
                            "A1_COLOR_HARMONY": _number_or_none(
                                row.get("q_color_1_5", "")
                            ),
                            "A2_SILHOUETTE_PROPORTION": _number_or_none(
                                row.get("q_silhouette_proportion_1_5", "")
                            ),
                            "A5_MATERIAL_PATTERN": _number_or_none(
                                row.get("q_material_pattern_1_5", "")
                            ),
                            "A6_STYLE_COHESION": _number_or_none(
                                row.get("q_style_cohesion_1_5", "")
                            ),
                            "A7_COMPLETENESS_DETAIL": _number_or_none(
                                row.get("q_completeness_detail_1_5", "")
                            ),
                        },
                    },
                    "rationale": " | ".join(
                        value
                        for value in (
                            row.get("missing_observations", ""),
                            row.get("notes", ""),
                        )
                        if value
                    ),
                },
            )

    def _import_claim_reviews(
        self,
        dataset: GoldenDataset,
        image_map: dict[str, GoldenImage],
        path: Path,
    ) -> None:
        for row in _read_csv(path):
            image = image_map.get(row.get("golden_id", ""))
            claim_id = row.get("claim_id", "")
            verdict = row.get("verdict", "").upper()
            reviewer = row.get("reviewer_label", "").strip()
            if (
                image is None
                or not claim_id
                or not reviewer
                or verdict not in dict(GoldenReview.Verdict.choices)
            ):
                continue
            GoldenReview.objects.update_or_create(
                dataset=dataset,
                review_key=f"claim:{image.golden_id}:{claim_id}:{reviewer}",
                defaults={
                    "image": image,
                    "principle": None,
                    "reviewer_label": reviewer,
                    "verdict": verdict,
                    "scores": {
                        "review_type": "CLAIM",
                        "claim_id": claim_id,
                        "axis": row.get("axis", ""),
                        "evidence_correct": row.get("evidence_correct", ""),
                        "human_judgment": row.get("human_judgment", ""),
                        "human_confidence_1_3": _number_or_none(
                            row.get("human_confidence_1_3", "")
                        ),
                        "overgeneralization_risk": row.get(
                            "overgeneralization_risk", ""
                        ),
                        "stereotype_risk": row.get("stereotype_risk", ""),
                        "edited_statement": row.get("edited_statement", ""),
                    },
                    "rationale": row.get("notes", ""),
                },
            )

    def _import_minimum_edit_reviews(
        self,
        dataset: GoldenDataset,
        image_map: dict[str, GoldenImage],
        path: Path,
    ) -> None:
        verdict_map = {
            "PLAUSIBLE_HYPOTHESIS": GoldenReview.Verdict.APPROVE,
            "TASTE_DEPENDENT": GoldenReview.Verdict.EDIT,
            "INCORRECT": GoldenReview.Verdict.REJECT,
            "UNSURE": GoldenReview.Verdict.UNSURE,
        }
        for row in _read_csv(path):
            image = image_map.get(row.get("golden_id", ""))
            raw_verdict = row.get("verdict", "").upper()
            reviewer = row.get("reviewer_label", "").strip()
            if image is None or not reviewer or raw_verdict not in verdict_map:
                continue
            GoldenReview.objects.update_or_create(
                dataset=dataset,
                review_key=f"minimum-edit:{image.golden_id}:{reviewer}",
                defaults={
                    "image": image,
                    "principle": None,
                    "reviewer_label": reviewer,
                    "verdict": verdict_map[raw_verdict],
                    "scores": {
                        "review_type": "MINIMUM_EDIT",
                        "raw_verdict": raw_verdict,
                        "tested_axis": row.get("tested_axis", ""),
                        "single_variable_change": row.get(
                            "single_variable_change", ""
                        ),
                        "preserves_style_intent": row.get(
                            "preserves_style_intent", ""
                        ),
                        "human_confidence_1_3": _number_or_none(
                            row.get("human_confidence_1_3", "")
                        ),
                    },
                    "rationale": row.get("notes", ""),
                },
            )

    def _import_pairwise_reviews(
        self,
        dataset: GoldenDataset,
        image_map: dict[str, GoldenImage],
        path: Path,
    ) -> None:
        valid_outcomes = dict(GoldenPairwiseReview.Outcome.choices)
        for row in _read_csv(path):
            left = image_map.get(row.get("left_id", ""))
            right = image_map.get(row.get("right_id", ""))
            reviewer = row.get("reviewer_label", "").strip()
            outcome = row.get("winner", "").lower()
            if left is None or right is None or not reviewer or left == right:
                continue
            if outcome == row.get("left_id", ""):
                outcome = GoldenPairwiseReview.Outcome.LEFT
            elif outcome == row.get("right_id", ""):
                outcome = GoldenPairwiseReview.Outcome.RIGHT
            if outcome not in valid_outcomes:
                continue
            pair_key = row.get("pair_id", "") or ":".join(
                sorted((left.golden_id, right.golden_id))
            )
            GoldenPairwiseReview.objects.update_or_create(
                dataset=dataset,
                pair_key=pair_key,
                reviewer_label=reviewer,
                defaults={
                    "left_image": left,
                    "right_image": right,
                    "comparison_scope": row.get("comparison_scope", ""),
                    "comparison_axis": row.get(
                        "comparison_axis", "Q_OVERALL_STYLE_EXECUTION"
                    ),
                    "context": {
                        "context_id": row.get("context_id", ""),
                        "presentation_order": row.get("presentation_order", ""),
                    },
                    "outcome": outcome,
                    "confidence": _bounded_integer_or_none(
                        row.get("confidence_1_3", ""),
                        lower=1,
                        upper=3,
                    ),
                    "reason_axis": row.get("reason_axis", ""),
                    "rationale": row.get("notes", ""),
                    "rubric_version": "golden-review-v2",
                },
            )

    def _import_principle_reviews(
        self,
        dataset: GoldenDataset,
        principle_map: dict[str, GoldenPrinciple],
        path: Path,
    ) -> None:
        for row in _read_csv(path):
            principle = principle_map.get(row.get("principle_key", ""))
            verdict = row.get("verdict", "").upper()
            if principle is None or verdict not in dict(GoldenReview.Verdict.choices):
                continue
            reviewer = row.get("reviewer_label", "") or "anonymous"
            GoldenReview.objects.update_or_create(
                dataset=dataset,
                review_key=(
                    f"principle:{principle.principle_key}:{reviewer}"
                ),
                defaults={
                    "image": None,
                    "principle": principle,
                    "reviewer_label": reviewer,
                    "verdict": verdict,
                    "scores": {
                        "review_type": "PRINCIPLE",
                        "knowledge_role": row.get("knowledge_role", ""),
                        "human_confidence_1_3": _number_or_none(
                            row.get("human_confidence_1_3", "")
                        ),
                        "edited_statement": row.get("edited_statement", ""),
                        "edited_applies_when_json": row.get(
                            "edited_applies_when_json", ""
                        ),
                        "edited_exceptions": row.get("edited_exceptions", ""),
                    },
                    "rationale": row.get("notes", ""),
                },
            )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            {str(key): str(value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def _find_claim(analysis: dict[str, Any], claim_id: str) -> dict[str, Any]:
    for claim in analysis.get("result", {}).get("claims", []):
        if str(claim.get("claim_id")) == claim_id:
            return claim
    return {}


def _number_or_none(value: str) -> float | None:
    return float(value) if value else None


def _bounded_integer_or_none(
    value: str,
    *,
    lower: int,
    upper: int,
) -> int | None:
    if not value:
        return None
    number = int(float(value))
    if not lower <= number <= upper:
        raise CommandError(f"점수 범위는 {lower}~{upper}입니다: {value}")
    return number


def _split_values(value: str) -> list[str]:
    return [item.strip() for item in value.split(";") if item.strip()]
