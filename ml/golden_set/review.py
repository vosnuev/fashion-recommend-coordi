"""사람 선택형 검수와 비교 가능한 쌍 생성을 위한 CSV 계약."""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from . import REVIEW_RUBRIC_VERSION
from .artifacts import read_jsonl, write_json, write_jsonl
from .embedding import load_embeddings

OBSERVATION_REVIEW_FIELDS = [
    "reviewer_label",
    "golden_id",
    "local_path",
    "style_intent",
    "detected_items",
    "image_assessable",
    "items_complete",
    "bbox_grounding_1_3",
    "unassessable_complete",
    "q_color_1_5",
    "q_silhouette_proportion_1_5",
    "q_material_pattern_1_5",
    "q_style_cohesion_1_5",
    "q_completeness_detail_1_5",
    "observation_verdict",
    "human_confidence_1_3",
    "missing_observations",
    "notes",
]

CLAIM_REVIEW_FIELDS = [
    "reviewer_label",
    "golden_id",
    "local_path",
    "claim_id",
    "axis",
    "statement",
    "evidence_region_ids",
    "model_relation_polarity",
    "model_contribution_direction",
    "evidence_correct",
    "human_judgment",
    "verdict",
    "human_confidence_1_3",
    "overgeneralization_risk",
    "stereotype_risk",
    "edited_statement",
    "notes",
]

MINIMUM_EDIT_REVIEW_FIELDS = [
    "reviewer_label",
    "golden_id",
    "local_path",
    "target_region_id",
    "target_attribute",
    "change",
    "tested_axis",
    "expected_effect",
    "single_variable_change",
    "preserves_style_intent",
    "verdict",
    "human_confidence_1_3",
    "notes",
]

PAIRWISE_FIELDS = [
    "pair_id",
    "reviewer_label",
    "comparison_scope",
    "comparison_axis",
    "context_id",
    "left_id",
    "left_local_path",
    "left_style_intent",
    "right_id",
    "right_local_path",
    "right_style_intent",
    "presentation_order",
    "winner",
    "confidence_1_3",
    "reason_axis",
    "notes",
]

PRINCIPLE_REVIEW_FIELDS = [
    "reviewer_label",
    "principle_key",
    "axis",
    "statement",
    "applies_when_json",
    "exceptions",
    "support_image_count",
    "comparison_evidence_count",
    "eligible_for_scoring",
    "verdict",
    "knowledge_role",
    "edited_statement",
    "edited_applies_when_json",
    "edited_exceptions",
    "human_confidence_1_3",
    "notes",
]

VERDICTS = {"APPROVE", "EDIT", "REJECT", "UNSURE"}
POSITIVE_CLAIM_JUDGMENTS = {"CONTRIBUTES", "CONTEXT_DEPENDENT"}
PAIRWISE_OUTCOMES = {
    "left",
    "right",
    "tie",
    "context_dependent",
    "unassessable",
}

_LEGACY_AXIS_MAP = {
    "color_harmony": "A1_COLOR_HARMONY",
    "silhouette_balance": "A2_SILHOUETTE_PROPORTION",
    "proportion": "A2_SILHOUETTE_PROPORTION",
    "material_pattern": "A5_MATERIAL_PATTERN",
    "style_cohesion": "A6_STYLE_COHESION",
    "completeness_detail": "A7_COMPLETENESS_DETAIL",
}


@dataclass(frozen=True)
class ReviewTemplatePaths:
    observation: Path
    claim: Path
    minimum_edit: Path
    pairwise: Path
    guide: Path


def create_review_templates(
    *,
    run_dir: Path,
    pair_count: int = 12,
) -> ReviewTemplatePaths:
    analyses = {
        str(row["golden_id"]): row
        for row in read_jsonl(run_dir / "analyses.jsonl")
        if row.get("status") == "SUCCEEDED"
    }
    if not analyses:
        raise ValueError("성공한 분석이 없어 검수 템플릿을 만들 수 없습니다.")
    images = {
        str(row["golden_id"]): row for row in read_jsonl(run_dir / "images.jsonl")
    }

    observation_path = run_dir / "image_observation_reviews.template.csv"
    claim_path = run_dir / "claim_reviews.template.csv"
    minimum_edit_path = run_dir / "minimum_edit_reviews.template.csv"
    pairwise_path = run_dir / "pairwise_reviews.template.csv"
    guide_path = run_dir / "review_guide.json"

    with observation_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OBSERVATION_REVIEW_FIELDS)
        writer.writeheader()
        for golden_id, analysis in sorted(analyses.items()):
            result = analysis.get("result", {})
            image = images.get(golden_id, {})
            items = [
                f"{row.get('region_id', '')}:{row.get('item_name', '')}"
                for row in result.get("observations", [])
            ]
            writer.writerow(
                {
                    "reviewer_label": "",
                    "golden_id": golden_id,
                    "local_path": image.get("local_path", ""),
                    "style_intent": _join_values(
                        image.get("metadata", {}).get("style", [])
                        or result.get("look_tags", {}).get("style", [])
                    ),
                    "detected_items": ";".join(items),
                    "image_assessable": "",
                    "items_complete": "",
                    "bbox_grounding_1_3": "",
                    "unassessable_complete": "",
                    "q_color_1_5": "",
                    "q_silhouette_proportion_1_5": "",
                    "q_material_pattern_1_5": "",
                    "q_style_cohesion_1_5": "",
                    "q_completeness_detail_1_5": "",
                    "observation_verdict": "",
                    "human_confidence_1_3": "",
                    "missing_observations": "",
                    "notes": "",
                }
            )

    with claim_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CLAIM_REVIEW_FIELDS)
        writer.writeheader()
        for golden_id, analysis in sorted(analyses.items()):
            image = images.get(golden_id, {})
            for claim in analysis.get("result", {}).get("claims", []):
                writer.writerow(
                    {
                        "reviewer_label": "",
                        "golden_id": golden_id,
                        "local_path": image.get("local_path", ""),
                        "claim_id": claim.get("claim_id", ""),
                        "axis": _claim_axis(claim),
                        "statement": claim.get("statement", ""),
                        "evidence_region_ids": _join_values(
                            claim.get("evidence_region_ids", [])
                        ),
                        "model_relation_polarity": claim.get(
                            "relation_polarity", "UNSPECIFIED"
                        ),
                        "model_contribution_direction": claim.get(
                            "contribution_direction", "UNSPECIFIED"
                        ),
                        "evidence_correct": "",
                        "human_judgment": "",
                        "verdict": "",
                        "human_confidence_1_3": "",
                        "overgeneralization_risk": "",
                        "stereotype_risk": "",
                        "edited_statement": "",
                        "notes": "",
                    }
                )

    with minimum_edit_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MINIMUM_EDIT_REVIEW_FIELDS)
        writer.writeheader()
        for golden_id, analysis in sorted(analyses.items()):
            image = images.get(golden_id, {})
            edit = analysis.get("result", {}).get("minimum_edit", {})
            writer.writerow(
                {
                    "reviewer_label": "",
                    "golden_id": golden_id,
                    "local_path": image.get("local_path", ""),
                    "target_region_id": edit.get("target_region_id", ""),
                    "target_attribute": edit.get("target_attribute", ""),
                    "change": edit.get("change", ""),
                    "tested_axis": edit.get("tested_axis", ""),
                    "expected_effect": edit.get("expected_effect", ""),
                    "single_variable_change": "",
                    "preserves_style_intent": "",
                    "verdict": "",
                    "human_confidence_1_3": "",
                    "notes": "",
                }
            )

    pairs = build_pairwise_pairs(run_dir=run_dir, pair_count=pair_count)
    with pairwise_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PAIRWISE_FIELDS)
        writer.writeheader()
        for index, pair in enumerate(pairs, start=1):
            writer.writerow(
                {
                    "pair_id": f"pair-{index:03d}",
                    "reviewer_label": "",
                    **pair,
                    "winner": "",
                    "confidence_1_3": "",
                    "reason_axis": "",
                    "notes": "",
                }
            )

    write_json(
        guide_path,
        {
            "rubric_version": REVIEW_RUBRIC_VERSION,
            "purpose": (
                "사람이 좋은 코디의 이유를 처음부터 서술하는 문서가 아니라, "
                "모델이 이미지에서 뽑은 관찰과 주장에 근거가 있는지 독립 판정하는 검수 계약"
            ),
            "human_workflow": [
                "검수자마다 같은 템플릿을 복사해 독립적으로 작성한 뒤 행을 합친다.",
                "모델 문장을 새로 작성하지 말고 선택형 판정을 우선한다.",
                "EDIT일 때만 수정 문장을 작성한다.",
                "모델 confidence를 보지 않고 이미지 근거로 독립 판단한다.",
                "개인 취향과 스타일 의도 안의 완성도를 구분한다.",
                "판단할 수 없는 축은 빈 점수 또는 UNSURE/UNASSESSABLE로 남긴다.",
            ],
            "review_questions": {
                "image_observation": [
                    {
                        "field": "image_assessable",
                        "question": "이 이미지로 코디의 주요 아이템과 관계를 판정할 수 있는가?",
                        "answer": "YES/NO/UNSURE",
                    },
                    {
                        "field": "items_complete",
                        "question": "화면에 보이는 주요 의류·신발·가방·액세서리가 빠짐없이 식별됐는가?",
                        "answer": "YES/NO/UNSURE",
                    },
                    {
                        "field": "bbox_grounding_1_3",
                        "question": "식별 영역이 실제 아이템 위치를 얼마나 정확히 가리키는가?",
                        "answer": "1=부정확, 2=일부 오차, 3=정확",
                    },
                    {
                        "field": "unassessable_complete",
                        "question": "사진만으로 알 수 없는 소재·TPO·계절·착용자 적합성이 적절히 보류됐는가?",
                        "answer": "YES/NO/UNSURE",
                    },
                    {
                        "field": "q_*_1_5",
                        "question": "판정 가능한 A1·A2·A5·A6·A7 축은 이 스타일 의도를 얼마나 잘 실행하는가?",
                        "answer": "선택 입력 1~5; 판정 불가는 빈칸",
                    },
                    {
                        "field": "observation_verdict",
                        "question": "관찰 묶음을 다음 claim 검수에 사용해도 되는가?",
                        "answer": "APPROVE/EDIT/REJECT/UNSURE",
                    },
                ],
                "claim": [
                    {
                        "field": "evidence_correct",
                        "question": "statement가 가리키는 영역에서 실제로 확인되는가?",
                        "answer": "YES/NO/UNSURE",
                    },
                    {
                        "field": "human_judgment",
                        "question": "이 관계는 코디 완성도에 어떤 역할을 하는가?",
                        "answer": (
                            "CONTRIBUTES=기여, DESCRIPTIVE_ONLY=관찰뿐, "
                            "CONTEXT_DEPENDENT=조건부 기여, UNSUPPORTED=근거 부족, "
                            "INCORRECT=틀림"
                        ),
                    },
                    {
                        "field": "overgeneralization_risk",
                        "question": "단일 사례를 모든 스타일·상황의 법칙처럼 넓힌 표현인가?",
                        "answer": "YES/NO/UNSURE",
                    },
                    {
                        "field": "stereotype_risk",
                        "question": "성별·체형·연령 등을 품질 기준으로 고정하는 표현인가?",
                        "answer": "YES/NO/UNSURE",
                    },
                    {
                        "field": "verdict",
                        "question": "이 claim을 원칙 합성 근거로 보낼 수 있는가?",
                        "answer": "APPROVE/EDIT/REJECT/UNSURE; EDIT일 때만 edited_statement 작성",
                    },
                ],
                "minimum_edit": [
                    {
                        "field": "single_variable_change",
                        "question": "한 번에 눈에 보이는 속성 하나만 바꾸는 가설인가?",
                        "answer": "YES/NO/UNSURE",
                    },
                    {
                        "field": "preserves_style_intent",
                        "question": "변경 후에도 원래 추구한 스타일 의도가 유지되는가?",
                        "answer": "YES/NO/UNSURE",
                    },
                    {
                        "field": "verdict",
                        "question": "이 변경은 반례·경계 사례를 만들기 위한 타당한 실험 가설인가?",
                        "answer": "PLAUSIBLE_HYPOTHESIS/TASTE_DEPENDENT/INCORRECT/UNSURE",
                    },
                ],
                "pairwise": [
                    {
                        "field": "winner",
                        "question": "제시된 스타일 의도와 공통 컨텍스트 안에서 어느 쪽이 더 일관되게 완성됐는가?",
                        "answer": "left/right/tie/context_dependent/unassessable",
                    },
                    {
                        "field": "reason_axis",
                        "question": "판정 차이를 가장 크게 만든 A1·A2·A5·A6·A7 축은 무엇인가?",
                        "answer": "단일 축 또는 MIXED",
                    },
                ],
                "principle": [
                    {
                        "field": "verdict",
                        "question": "여러 승인 이미지에서 반복된 관계를 정확히 요약하며 조건과 예외가 충분한가?",
                        "answer": "APPROVE/EDIT/REJECT/UNSURE",
                    },
                    {
                        "field": "knowledge_role",
                        "question": "이 원칙을 현재 어떤 강도로 사용할 수 있는가?",
                        "answer": (
                            "SCORE_AND_EXPLANATION/EXPLANATION_ONLY/"
                            "NEEDS_COUNTEREXAMPLE/DISCARD"
                        ),
                    },
                ],
            },
            "axis_score_rubric": {
                "scope": "개인 취향 점수 P가 아니라 주어진 스타일 의도 내 실행 품질 Q",
                "axes": {
                    "q_color_1_5": "A1 색 조화: 색의 반복·대비·강조가 의도를 지지하는 정도",
                    "q_silhouette_proportion_1_5": "A2 실루엣·비율: 길이·볼륨·레이어 관계가 의도를 지지하는 정도",
                    "q_material_pattern_1_5": "A5 소재·패턴: 보이는 질감·패턴 조합이 충돌 없이 의도를 지지하는 정도",
                    "q_style_cohesion_1_5": "A6 스타일 응집성: 아이템 간 격식·무드가 하나의 의도로 읽히는 정도",
                    "q_completeness_detail_1_5": "A7 완결성·디테일: 신발·가방·액세서리 등 마무리가 의도를 지지하는 정도",
                },
                "scale": {
                    "1": "의도 실행을 명확히 방해함",
                    "2": "약점이 눈에 띄며 일부 방해함",
                    "3": "중립적이거나 무난함",
                    "4": "의도를 분명히 지지함",
                    "5": "핵심 강점으로 작동함",
                },
            },
            "promotion_gate": {
                "approved_claim": "동일 이미지·claim에 서로 다른 검수자 2명 이상 승인",
                "explanation_only": "2인 원칙 승인 후 가능",
                "score_and_explanation": (
                    "지지 이미지 3장 이상, 비교·반례 근거 2건 이상, 예외 1개 이상, "
                    "검수자 2명 이상, 이미지 영역 근거가 모두 있어야 가능"
                ),
            },
            "allowed_values": {
                "yes_no_unsure": ["YES", "NO", "UNSURE"],
                "verdict": sorted(VERDICTS),
                "claim_human_judgment": [
                    "CONTRIBUTES",
                    "DESCRIPTIVE_ONLY",
                    "CONTEXT_DEPENDENT",
                    "UNSUPPORTED",
                    "INCORRECT",
                ],
                "minimum_edit_verdict": [
                    "PLAUSIBLE_HYPOTHESIS",
                    "TASTE_DEPENDENT",
                    "INCORRECT",
                    "UNSURE",
                ],
                "pairwise_winner": sorted(PAIRWISE_OUTCOMES),
                "reason_axis": [
                    "A1_COLOR_HARMONY",
                    "A2_SILHOUETTE_PROPORTION",
                    "A5_MATERIAL_PATTERN",
                    "A6_STYLE_COHESION",
                    "A7_COMPLETENESS_DETAIL",
                    "MIXED",
                ],
            },
        },
    )
    return ReviewTemplatePaths(
        observation=observation_path,
        claim=claim_path,
        minimum_edit=minimum_edit_path,
        pairwise=pairwise_path,
        guide=guide_path,
    )


def build_pairwise_pairs(*, run_dir: Path, pair_count: int) -> list[dict[str, str]]:
    ids, vectors = load_embeddings(run_dir / "image_embeddings.npz")
    if len(ids) < 2:
        return []
    images = {
        str(row["golden_id"]): row for row in read_jsonl(run_dir / "images.jsonl")
    }
    clusters = {
        str(row["golden_id"]): str(row.get("cluster_id", ""))
        for row in read_jsonl(run_dir / "clusters.jsonl")
    }
    target = min(
        max(len(ids) - 1, pair_count),
        len(ids) * (len(ids) - 1) // 2,
    )
    similarities = vectors @ vectors.T
    candidates: list[tuple[tuple[int, int, int, float], int, int, str]] = []
    for left in range(len(ids)):
        for right in range(left + 1, len(ids)):
            left_image, right_image = images[ids[left]], images[ids[right]]
            style_overlap = bool(
                _metadata_set(left_image, "style")
                & _metadata_set(right_image, "style")
            )
            context_overlap = _has_context_overlap(left_image, right_image)
            same_cluster = clusters.get(ids[left]) == clusters.get(ids[right])
            scope = (
                "MATCHED_STYLE_CONTEXT"
                if style_overlap and context_overlap
                else (
                    "MATCHED_STYLE"
                    if style_overlap
                    else ("NEAR_NEIGHBOR" if same_cluster else "VISUAL_BRIDGE")
                )
            )
            priority = (
                int(style_overlap),
                int(context_overlap),
                int(same_cluster),
                float(similarities[left, right]),
            )
            candidates.append((priority, left, right, scope))
    candidates.sort(reverse=True)

    selected: list[tuple[int, int, str]] = []
    selected_keys: set[tuple[int, int]] = set()
    parent = list(range(len(ids)))

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> bool:
        left_root, right_root = find(left), find(right)
        if left_root == right_root:
            return False
        parent[right_root] = left_root
        return True

    # 비교 그래프는 연결하되, 가능한 한 같은 스타일·컨텍스트의 edge를 우선한다.
    for _, left, right, scope in candidates:
        if union(left, right):
            selected.append((left, right, scope))
            selected_keys.add((left, right))
        if len(selected) == len(ids) - 1:
            break
    for _, left, right, scope in candidates:
        if len(selected) >= target:
            break
        if (left, right) in selected_keys:
            continue
        selected.append((left, right, scope))
        selected_keys.add((left, right))

    rows = []
    for left_index, right_index, scope in selected[:target]:
        original_left, original_right = ids[left_index], ids[right_index]
        pair_seed = hashlib.sha256(
            f"{run_dir.name}|{original_left}|{original_right}|{REVIEW_RUBRIC_VERSION}".encode()
        ).digest()[0]
        left, right = (
            (original_right, original_left)
            if pair_seed % 2
            else (original_left, original_right)
        )
        left_image, right_image = images[left], images[right]
        rows.append(
            {
                "comparison_scope": scope,
                "comparison_axis": "Q_OVERALL_STYLE_EXECUTION",
                "context_id": _pair_context(left_image, right_image),
                "left_id": left,
                "left_local_path": str(left_image.get("local_path", "")),
                "left_style_intent": _join_values(
                    left_image.get("metadata", {}).get("style", [])
                ),
                "right_id": right,
                "right_local_path": str(right_image.get("local_path", "")),
                "right_style_intent": _join_values(
                    right_image.get("metadata", {}).get("style", [])
                ),
                "presentation_order": "DETERMINISTIC_RANDOM_V1",
            }
        )
    return rows


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            {str(key): str(value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


#: 축약 검수표(goldenset-review-sheets)가 관찰 판정에 쓰는 단일 열.
#: 표준 검수표는 `items_complete`·`observation_verdict`를 따로 물었지만, 검수 시간을
#: 줄이려고 "아이템 목록이 맞는가" 한 질문으로 합쳤다.
SHEET_ITEMS_CORRECT = "detected_items_correct"
SHEET_ITEMS_CORRECTION = "corrected_detected_items"

#: 부정 판정. 하나라도 걸리면 claim은 승격되지 않는다.
NEGATIVE_CLAIM_JUDGMENTS = {"UNSUPPORTED", "INCORRECT"}


def sheet_asks(rows: list[dict[str, str]], field: str) -> bool:
    """이 검수표가 그 열을 물었는지.

    축약 검수표는 `unassessable_complete`·`bbox_grounding_1_3`을 묻지 않는다. 사람이
    판정하지 않은 항목을 YES로 채워 통과시키면 검수 기록이 거짓말이 되고, 빈 값으로
    두면 승인 조건이 전 건을 pending으로 떨어뜨린다. 그래서 **열의 유무**로 가른다 —
    물었으면 검사하고, 묻지 않았으면 그 조건을 빼는 것이 두 검수표에 모두 정직하다.
    """
    return any(field in row for row in rows)


def normalize_observation_rows(
    rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    """축약 관찰 검수표를 표준 열로 편다.

    `detected_items_correct` 한 열이 표준 검수표의 `items_complete`와
    `observation_verdict` 두 열을 겸한다. 값이 이미 들어 있으면 덮지 않는다 —
    표준 검수표로 받은 기존 검수 결과가 이 함수를 그대로 통과해야 한다.

    `NO`인데 고친 목록이 없으면 `items_complete`를 NO로 둔다. 목록이 틀렸다는 것만
    알고 무엇이 맞는지는 모르는 상태라, 승인해 버리면 틀린 아이템 목록이 그대로
    원칙 합성 근거가 된다.
    """
    normalized: list[dict[str, str]] = []
    for row in rows:
        updated = dict(row)
        answer = row.get(SHEET_ITEMS_CORRECT, "").strip().upper()
        correction = row.get(SHEET_ITEMS_CORRECTION, "").strip()
        if answer == "YES":
            verdict, items_complete = "APPROVE", "YES"
        elif answer == "NO":
            verdict = "EDIT"
            items_complete = "YES" if correction else "NO"
        elif answer:
            # UNSURE 등 판단 보류. 승인도 기각도 아니므로 pending으로 남는다.
            verdict, items_complete = "UNSURE", answer
        else:
            normalized.append(updated)
            continue
        updated.setdefault("observation_verdict", "")
        updated.setdefault("items_complete", "")
        if not updated["observation_verdict"]:
            updated["observation_verdict"] = verdict
        if not updated["items_complete"]:
            updated["items_complete"] = items_complete
        if correction and not updated.get("missing_observations", ""):
            updated["missing_observations"] = correction
        normalized.append(updated)
    return normalized


def normalize_claim_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """축약 claim 검수표에 `verdict`를 채운다.

    축약 표는 검수자에게 `verdict`를 따로 묻지 않는다. 근거가 맞는지
    (`evidence_correct`)와 그 관계가 무엇인지(`human_judgment`)를 물으면 승인 여부는
    거기서 결정되기 때문이다. 같은 판단을 두 번 적게 하면 두 열이 어긋난 행이 생긴다.

    `DESCRIPTIVE_ONLY`는 REJECT가 아니라 APPROVE로 둔다. 검수 자체는 유효하고,
    "원칙 근거로 승격하지 않는다"는 처리는 `POSITIVE_CLAIM_JUDGMENTS`가 이미 한다.
    여기서 기각으로 적으면 '틀린 claim'과 '맞지만 묘사일 뿐인 claim'이 한 덩어리가 된다.
    """
    normalized: list[dict[str, str]] = []
    for row in rows:
        updated = dict(row)
        judgment = row.get("human_judgment", "").strip().upper()
        evidence = row.get("evidence_correct", "").strip().upper()
        if not judgment:
            normalized.append(updated)
            continue
        if (
            evidence == "NO"
            or judgment in NEGATIVE_CLAIM_JUDGMENTS
            or row.get("overgeneralization_risk", "").strip().upper() == "YES"
            or row.get("stereotype_risk", "").strip().upper() == "YES"
        ):
            verdict = "REJECT"
        elif evidence == "YES":
            verdict = "APPROVE"
        else:
            # 근거를 확신하지 못한 상태(UNSURE). 2인 승인 수를 채우지 못해 pending이 된다.
            verdict = "UNSURE"
        updated.setdefault("verdict", "")
        if not updated["verdict"]:
            updated["verdict"] = verdict
        normalized.append(updated)
    return normalized


def collect_accepted_claims(
    *,
    observation_reviews_csv: Path,
    claim_reviews_csv: Path,
    run_dir: Path,
    minimum_reviewers: int = 2,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    observations = normalize_observation_rows(read_csv_rows(observation_reviews_csv))
    claim_reviews = normalize_claim_rows(read_csv_rows(claim_reviews_csv))
    # 검수표가 묻지 않은 항목은 승인 조건에서 뺀다. 축약 검수표에는 이 두 열이 없다.
    asks_unassessable = sheet_asks(observations, "unassessable_complete")
    asks_bbox_grounding = sheet_asks(observations, "bbox_grounding_1_3")
    _assert_unique_reviews(observations, ("golden_id",))
    _assert_unique_reviews(claim_reviews, ("golden_id", "claim_id"))

    accepted_images: set[str] = set()
    observation_groups = _group_rows(observations, ("golden_id",))
    pending_images: list[str] = []
    excluded_images: list[str] = []
    for (golden_id,), rows in observation_groups.items():
        positive = [
            row
            for row in rows
            if row.get("observation_verdict", "").upper() in {"APPROVE", "EDIT"}
            and row.get("image_assessable", "").upper() == "YES"
            and row.get("items_complete", "").upper() == "YES"
            and (
                not asks_unassessable
                or row.get("unassessable_complete", "").upper() == "YES"
            )
            and (
                not asks_bbox_grounding
                or _optional_score_at_least(row, "bbox_grounding_1_3", 2)
            )
        ]
        rejected = any(
            row.get("observation_verdict", "").upper() == "REJECT" for row in rows
        )
        if len(positive) >= minimum_reviewers and not rejected:
            accepted_images.add(golden_id)
        elif rejected:
            excluded_images.append(golden_id)
        else:
            pending_images.append(golden_id)

    analyses = {
        str(row["golden_id"]): row
        for row in read_jsonl(run_dir / "analyses.jsonl")
        if row.get("status") == "SUCCEEDED"
    }
    accepted: dict[str, list[dict[str, Any]]] = {}
    pending_claims: list[str] = []
    excluded_claims: list[str] = []
    for (golden_id, claim_id), rows in _group_rows(
        claim_reviews, ("golden_id", "claim_id")
    ).items():
        if golden_id not in accepted_images:
            pending_claims.append(f"{golden_id}:{claim_id}")
            continue
        positive = [
            row
            for row in rows
            if row.get("verdict", "").upper() in {"APPROVE", "EDIT"}
            and row.get("evidence_correct", "").upper() == "YES"
            and row.get("human_judgment", "").upper()
            in POSITIVE_CLAIM_JUDGMENTS
            and row.get("overgeneralization_risk", "").upper() != "YES"
            and row.get("stereotype_risk", "").upper() != "YES"
        ]
        rejected = any(
            row.get("verdict", "").upper() == "REJECT"
            or row.get("evidence_correct", "").upper() == "NO"
            or row.get("human_judgment", "").upper()
            in {"UNSUPPORTED", "INCORRECT"}
            or row.get("overgeneralization_risk", "").upper() == "YES"
            or row.get("stereotype_risk", "").upper() == "YES"
            for row in rows
        )
        descriptive_only = (
            len(rows) >= minimum_reviewers
            and all(
                row.get("human_judgment", "").upper() == "DESCRIPTIVE_ONLY"
                for row in rows
            )
        )
        if rejected or descriptive_only:
            excluded_claims.append(f"{golden_id}:{claim_id}")
            continue
        if len(positive) < minimum_reviewers:
            pending_claims.append(f"{golden_id}:{claim_id}")
            continue
        model_claim = _find_claim(analyses.get(golden_id, {}), claim_id)
        if not model_claim:
            pending_claims.append(f"{golden_id}:{claim_id}")
            continue
        edited_values = {
            row.get("edited_statement", "").strip()
            for row in positive
            if row.get("verdict", "").upper() == "EDIT"
            and row.get("edited_statement", "").strip()
        }
        if len(edited_values) > 1:
            pending_claims.append(f"{golden_id}:{claim_id}:EDIT_CONFLICT")
            continue
        human_confidences = [
            _bounded_number(row.get("human_confidence_1_3", ""), 1, 3)
            for row in positive
            if row.get("human_confidence_1_3", "")
        ]
        accepted.setdefault(golden_id, []).append(
            {
                **model_claim,
                "axis": _claim_axis(model_claim),
                "statement": next(iter(edited_values), model_claim.get("statement", "")),
                "human_review": {
                    "reviewer_count": len(positive),
                    "judgments": sorted(
                        {row.get("human_judgment", "").upper() for row in positive}
                    ),
                    "mean_confidence_1_3": (
                        round(float(np.mean(human_confidences)), 3)
                        if human_confidences
                        else None
                    ),
                },
            }
        )

    report = {
        "rubric_version": REVIEW_RUBRIC_VERSION,
        "minimum_reviewers": minimum_reviewers,
        "accepted_image_count": len(accepted_images),
        # 승인된 이미지 목록. 개수만으로는 "어느 코디가 사람 검수를 통과했는지"를
        # 알 수 없어 Qdrant payload의 human_verified를 만들 수 없다.
        "accepted_images": sorted(accepted_images),
        "accepted_claim_count": sum(len(rows) for rows in accepted.values()),
        "pending_images": sorted(pending_images),
        "excluded_images": sorted(excluded_images),
        "pending_claims": sorted(pending_claims),
        "excluded_claims": sorted(excluded_claims),
    }
    write_jsonl(
        run_dir / "approved_claims.jsonl",
        (
            {"golden_id": golden_id, "claims": claims}
            for golden_id, claims in sorted(accepted.items())
        ),
    )
    write_json(run_dir / "review_validation.json", report)
    return accepted, report


def aggregate_axis_scores(path: Path) -> dict[str, dict[str, Any]]:
    rows = read_csv_rows(path)
    axis_fields = {
        "A1_COLOR_HARMONY": "q_color_1_5",
        "A2_SILHOUETTE_PROPORTION": "q_silhouette_proportion_1_5",
        "A5_MATERIAL_PATTERN": "q_material_pattern_1_5",
        "A6_STYLE_COHESION": "q_style_cohesion_1_5",
        "A7_COMPLETENESS_DETAIL": "q_completeness_detail_1_5",
    }
    grouped = _group_rows(rows, ("golden_id",))
    result: dict[str, dict[str, Any]] = {}
    for (golden_id,), review_rows in grouped.items():
        axis_scores: dict[str, float] = {}
        for axis, field in axis_fields.items():
            values = [
                _bounded_number(row[field], 1, 5)
                for row in review_rows
                if row.get(field, "")
            ]
            if values:
                axis_scores[axis] = round(float(np.mean(values)), 3)
        result[golden_id] = {
            "axis_scores_1_5": axis_scores,
            "reviewer_count": len(
                {row.get("reviewer_label", "") for row in review_rows}
            ),
        }
    return result


def _assert_unique_reviews(
    rows: list[dict[str, str]],
    target_fields: tuple[str, ...],
) -> None:
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        reviewer = row.get("reviewer_label", "").strip()
        if not reviewer:
            continue
        key = (*[row.get(field, "") for field in target_fields], reviewer)
        if key in seen:
            raise ValueError(f"동일 검수자의 중복 검수 행이 있습니다: {key}")
        seen.add(key)


def _group_rows(
    rows: list[dict[str, str]],
    fields: tuple[str, ...],
) -> dict[tuple[str, ...], list[dict[str, str]]]:
    grouped: dict[tuple[str, ...], list[dict[str, str]]] = {}
    for row in rows:
        if not row.get("reviewer_label", "").strip():
            continue
        key = tuple(row.get(field, "") for field in fields)
        grouped.setdefault(key, []).append(row)
    return grouped


def _find_claim(analysis: dict[str, Any], claim_id: str) -> dict[str, Any]:
    for claim in analysis.get("result", {}).get("claims", []):
        if str(claim.get("claim_id", "")) == claim_id:
            return dict(claim)
    return {}


def _claim_axis(claim: dict[str, Any]) -> str:
    return str(
        claim.get("axis")
        or _LEGACY_AXIS_MAP.get(str(claim.get("dimension", "")), "")
    )


def _metadata_set(image: dict[str, Any], field: str) -> set[str]:
    value = image.get("metadata", {}).get(field, [])
    if isinstance(value, str):
        return {item.strip().lower() for item in value.split(";") if item.strip()}
    return {str(item).strip().lower() for item in value if str(item).strip()}


def _has_context_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    for field in ("season", "occasion"):
        left_values, right_values = _metadata_set(left, field), _metadata_set(right, field)
        if left_values and right_values and left_values & right_values:
            return True
    return False


def _pair_context(left: dict[str, Any], right: dict[str, Any]) -> str:
    parts = []
    for field in ("style", "season", "occasion"):
        overlap = sorted(_metadata_set(left, field) & _metadata_set(right, field))
        if overlap:
            parts.append(f"{field}:{','.join(overlap)}")
    return "|".join(parts) or "visual-only"


def _join_values(value: Any) -> str:
    if isinstance(value, str):
        return value
    return ";".join(str(item) for item in value)


def _bounded_number(value: str, lower: int, upper: int) -> float:
    number = float(value)
    if not lower <= number <= upper:
        raise ValueError(f"점수 범위는 {lower}~{upper}입니다: {value}")
    return number


def _optional_score_at_least(
    row: dict[str, str],
    field: str,
    minimum: int,
) -> bool:
    value = row.get(field, "")
    return bool(value) and _bounded_number(value, 1, 3) >= minimum
