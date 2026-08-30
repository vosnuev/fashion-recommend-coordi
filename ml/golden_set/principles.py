"""사람이 승인한 claim을 조건부 원칙으로 합성하고 다시 검수한다."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

from . import PRINCIPLE_VERSION
from .artifacts import read_json, read_jsonl, write_json, write_jsonl
from .config import GoldenSettings
from .gemini import GeminiStructuredClient
from .prompts import KNOWLEDGE_ROLES, PRINCIPLE_SCHEMA, principle_prompt
from .review import (
    PRINCIPLE_REVIEW_FIELDS,
    collect_accepted_claims,
    read_csv_rows,
)


class PrincipleClient(Protocol):
    #: 실제로 호출한 모델 이름. 캐시 키와 산출물 기록에 쓴다.
    model: str

    def generate_text_json(
        self,
        *,
        prompt: str,
        system_instruction: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]: ...


PRINCIPLE_SYSTEM_INSTRUCTION = """당신은 사람에게 승인된 패션 이미지 claim을 조건부
판단 원칙으로 통합합니다. 좋은 결론을 먼저 정하지 말고 반복되는 관계만 일반화하세요.
원본에 없는 golden_id나 claim_id를 만들지 마세요. 원칙은 취향과 무관한 절대 법칙이
아니며, 적용 조건과 예외를 가진 소프트 지식입니다. 하드 규칙을 생성하지 마세요.
답변은 한국어 JSON으로만 작성하세요."""


def synthesize_principles(
    *,
    run_dir: Path,
    observation_reviews_csv: Path,
    claim_reviews_csv: Path,
    settings: GoldenSettings,
    client: PrincipleClient | None = None,
    minimum_reviewers: int = 2,
) -> list[dict[str, Any]]:
    accepted_claims, validation_report = collect_accepted_claims(
        observation_reviews_csv=observation_reviews_csv,
        claim_reviews_csv=claim_reviews_csv,
        run_dir=run_dir,
        minimum_reviewers=minimum_reviewers,
    )
    if not accepted_claims:
        raise ValueError("서로 다른 검수자 2명이 승인한 이미지 claim이 없습니다.")

    clusters = {
        str(row["golden_id"]): str(row.get("cluster_id", "cluster-unknown"))
        for row in read_jsonl(run_dir / "clusters.jsonl")
    }
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for golden_id, claims in accepted_claims.items():
        cluster_id = clusters.get(golden_id, "cluster-unknown")
        for claim in claims:
            axis = str(claim.get("axis", ""))
            grouped.setdefault((cluster_id, axis), []).append(
                {
                    "golden_id": golden_id,
                    "claim": claim,
                }
            )

    api_client = client
    # 캐시 키와 model_version은 **실제로 부른 모델**을 따라야 한다. 예전에는 공급자를
    # 바꿔도 settings.gemini_model이 박혀서, 다른 모델의 결과를 캐시에서 그대로
    # 재사용하고 산출물에도 부르지 않은 모델 이름이 남았다.
    model_version = getattr(client, "model", None) or settings.gemini_model
    provider = "openai" if client is not None else "gemini"
    cache_dir = run_dir / "cache" / "principles"
    cache_dir.mkdir(parents=True, exist_ok=True)
    principles: list[dict[str, Any]] = []
    skipped_single_image_groups: list[str] = []
    for (cluster_id, axis), evidence in sorted(grouped.items()):
        support_ids = {str(row["golden_id"]) for row in evidence}
        if len(support_ids) < 2:
            skipped_single_image_groups.append(f"{cluster_id}:{axis}")
            continue
        evidence_json = json.dumps(evidence, ensure_ascii=False, indent=2)
        cache_key = hashlib.sha256(
            (
                model_version
                + PRINCIPLE_VERSION
                + cluster_id
                + axis
                + evidence_json
            ).encode("utf-8")
        ).hexdigest()
        cache_path = cache_dir / f"{cache_key}.json"
        if cache_path.exists():
            payload = read_json(cache_path)
        else:
            if api_client is None:
                api_client = GeminiStructuredClient(settings)
            payload = api_client.generate_text_json(
                prompt=principle_prompt(
                    cluster_id=cluster_id,
                    axis=axis,
                    evidence_json=evidence_json,
                    allowed_refs="\n".join(
                        f"- {row['golden_id']} / {row['claim'].get('claim_id', '')}"
                        for row in evidence
                    ),
                ),
                system_instruction=PRINCIPLE_SYSTEM_INSTRUCTION,
                schema=PRINCIPLE_SCHEMA,
            )
            _validate_principles(payload, evidence, expected_axis=axis)
            write_json(cache_path, payload)

        for index, principle in enumerate(payload.get("principles", []), start=1):
            key = str(principle.get("principle_key") or f"principle-{index:02d}")
            evidence_refs = principle.get("evidence", [])
            support_images = {
                str(row.get("golden_id", "")) for row in evidence_refs
            }
            reviewer_count = min(
                (
                    int(row["claim"].get("human_review", {}).get("reviewer_count", 0))
                    for row in evidence
                    if (
                        str(row["golden_id"]),
                        str(row["claim"].get("claim_id", "")),
                    )
                    in {
                        (
                            str(ref.get("golden_id", "")),
                            str(ref.get("claim_id", "")),
                        )
                        for ref in evidence_refs
                    }
                ),
                default=0,
            )
            comparison_evidence_count = 0
            exception_count = len(principle.get("exceptions", []))
            eligible_for_scoring = (
                len(support_images) >= 3
                and comparison_evidence_count >= 2
                and exception_count >= 1
                and reviewer_count >= 2
                and bool(evidence_refs)
            )
            requested_role = str(
                principle.get("knowledge_role", "NEEDS_COUNTEREXAMPLE")
            )
            knowledge_role = (
                requested_role
                if eligible_for_scoring or requested_role != "SCORE_AND_EXPLANATION"
                else "NEEDS_COUNTEREXAMPLE"
            )
            if not eligible_for_scoring and knowledge_role == "SCORE_AND_EXPLANATION":
                knowledge_role = "NEEDS_COUNTEREXAMPLE"
            principles.append(
                {
                    **principle,
                    "axis": axis,
                    "dimension": axis,
                    "principle_key": f"{cluster_id}:{axis}:{key}",
                    "cluster_id": cluster_id,
                    "status": "DRAFT",
                    "version": PRINCIPLE_VERSION,
                    "model_version": model_version,
                    "provider": provider,
                    "model_confidence": principle.get("model_confidence", 0.0),
                    "confidence": principle.get("model_confidence", 0.0),
                    "knowledge_role": knowledge_role,
                    "support_image_count": len(support_images),
                    "comparison_evidence_count": comparison_evidence_count,
                    "exception_count": exception_count,
                    "reviewer_count": reviewer_count,
                    "eligible_for_scoring": eligible_for_scoring,
                }
            )

    write_jsonl(run_dir / "principles.jsonl", principles)
    create_principle_review_template(run_dir=run_dir, principles=principles)
    write_json(
        run_dir / "principle_synthesis.meta.json",
        {
            "version": PRINCIPLE_VERSION,
            "accepted_image_count": validation_report["accepted_image_count"],
            "accepted_claim_count": validation_report["accepted_claim_count"],
            "principle_count": len(principles),
            "score_eligible_count": sum(
                bool(row.get("eligible_for_scoring")) for row in principles
            ),
            "skipped_single_image_groups": skipped_single_image_groups,
        },
    )
    return principles


def create_principle_review_template(
    *,
    run_dir: Path,
    principles: list[dict[str, Any]],
) -> Path:
    path = run_dir / "principle_reviews.template.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PRINCIPLE_REVIEW_FIELDS)
        writer.writeheader()
        for principle in principles:
            writer.writerow(
                {
                    "reviewer_label": "",
                    "principle_key": principle["principle_key"],
                    "axis": principle.get("axis", ""),
                    "statement": principle.get("statement", ""),
                    "applies_when_json": json.dumps(
                        principle.get("applies_when", {}), ensure_ascii=False
                    ),
                    "exceptions": ";".join(principle.get("exceptions", [])),
                    "support_image_count": principle.get("support_image_count", 0),
                    "comparison_evidence_count": principle.get(
                        "comparison_evidence_count", 0
                    ),
                    "eligible_for_scoring": str(
                        bool(principle.get("eligible_for_scoring"))
                    ).upper(),
                    "verdict": "",
                    "knowledge_role": principle.get(
                        "knowledge_role", "NEEDS_COUNTEREXAMPLE"
                    ),
                    "edited_statement": "",
                    "edited_applies_when_json": "",
                    "edited_exceptions": "",
                    "human_confidence_1_3": "",
                    "notes": "",
                }
            )
    return path


def apply_principle_reviews(
    *,
    run_dir: Path,
    principle_reviews_csv: Path,
    minimum_reviewers: int = 2,
) -> list[dict[str, Any]]:
    principles = {
        row["principle_key"]: row for row in read_jsonl(run_dir / "principles.jsonl")
    }
    review_rows = read_csv_rows(principle_reviews_csv)
    grouped: dict[str, list[dict[str, str]]] = {}
    seen: set[tuple[str, str]] = set()
    for row in review_rows:
        key = row.get("principle_key", "")
        reviewer = row.get("reviewer_label", "").strip()
        verdict = row.get("verdict", "").upper()
        if key not in principles or not reviewer or verdict not in {
            "APPROVE",
            "EDIT",
            "REJECT",
            "UNSURE",
        }:
            continue
        duplicate = (key, reviewer)
        if duplicate in seen:
            raise ValueError(f"동일 검수자의 원칙 중복 검수가 있습니다: {duplicate}")
        seen.add(duplicate)
        grouped.setdefault(key, []).append({**row, "verdict": verdict})

    for key, principle in principles.items():
        rows = grouped.get(key, [])
        positive = [row for row in rows if row["verdict"] in {"APPROVE", "EDIT"}]
        rejected = [row for row in rows if row["verdict"] == "REJECT"]
        principle["review_count"] = len(rows)
        principle["reviewer_count"] = len(
            {row.get("reviewer_label", "") for row in rows}
        )
        if len(positive) >= minimum_reviewers and not rejected:
            _apply_consistent_edits(principle, positive)
            requested_roles = {
                row.get("knowledge_role", "").strip()
                for row in positive
                if row.get("knowledge_role", "").strip() in KNOWLEDGE_ROLES
            }
            role = (
                next(iter(requested_roles))
                if len(requested_roles) == 1
                else principle.get("knowledge_role", "NEEDS_COUNTEREXAMPLE")
            )
            if role == "DISCARD":
                principle["status"] = "REJECTED"
                principle["knowledge_role"] = "DISCARD"
            elif role == "NEEDS_COUNTEREXAMPLE":
                principle["status"] = "DRAFT"
                principle["knowledge_role"] = role
            else:
                if role == "SCORE_AND_EXPLANATION" and not principle.get(
                    "eligible_for_scoring", False
                ):
                    role = "EXPLANATION_ONLY"
                principle["status"] = "APPROVED"
                principle["knowledge_role"] = role
        elif rejected and not positive:
            principle["status"] = "REJECTED"
            principle["knowledge_role"] = "DISCARD"
        else:
            principle["status"] = "DRAFT"
        if rows:
            top_count = max(
                [row["verdict"] for row in rows].count(value)
                for value in {row["verdict"] for row in rows}
            )
            principle["reviewer_agreement"] = round(top_count / len(rows), 3)

    result = [principles[key] for key in sorted(principles)]
    write_jsonl(run_dir / "principles.jsonl", result)
    return result


def _apply_consistent_edits(
    principle: dict[str, Any],
    positive_rows: list[dict[str, str]],
) -> None:
    statement_edits = {
        row.get("edited_statement", "").strip()
        for row in positive_rows
        if row["verdict"] == "EDIT" and row.get("edited_statement", "").strip()
    }
    condition_edits = {
        row.get("edited_applies_when_json", "").strip()
        for row in positive_rows
        if row["verdict"] == "EDIT"
        and row.get("edited_applies_when_json", "").strip()
    }
    exception_edits = {
        row.get("edited_exceptions", "").strip()
        for row in positive_rows
        if row["verdict"] == "EDIT" and row.get("edited_exceptions", "").strip()
    }
    for label, values in (
        ("statement", statement_edits),
        ("applies_when", condition_edits),
        ("exceptions", exception_edits),
    ):
        if len(values) > 1:
            raise ValueError(f"원칙 수정안이 검수자 사이에서 충돌합니다: {label}")
    if statement_edits:
        principle["statement"] = next(iter(statement_edits))
    if condition_edits:
        principle["applies_when"] = json.loads(next(iter(condition_edits)))
    if exception_edits:
        principle["exceptions"] = _split_semicolon(next(iter(exception_edits)))


def _validate_principles(
    payload: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    expected_axis: str,
) -> None:
    if "principles" not in payload:
        raise ValueError("원칙 합성 결과에 principles가 없습니다.")
    valid_claims = {
        (str(row["golden_id"]), str(row["claim"].get("claim_id", "")))
        for row in evidence
    }
    for principle in payload.get("principles", []):
        if principle.get("axis") != expected_axis:
            raise ValueError(
                f"원칙 축이 합성 그룹과 다릅니다: {principle.get('axis')}"
            )
        refs = {
            (str(row.get("golden_id", "")), str(row.get("claim_id", "")))
            for row in principle.get("evidence", [])
        }
        if not refs:
            raise ValueError("근거 없는 원칙이 생성되었습니다.")
        if len({golden_id for golden_id, _ in refs}) < 2:
            raise ValueError(
                "단일 이미지의 claim만으로 패션 원칙을 생성할 수 없습니다."
            )
        unknown = refs - valid_claims
        if unknown:
            raise ValueError(f"원칙이 존재하지 않는 승인 claim을 참조합니다: {unknown}")
        if principle.get("principle_type") not in {
            "SOFT_PRINCIPLE",
            "EXPLANATION_KNOWLEDGE",
        }:
            raise ValueError("골든 이미지에서 하드 규칙을 생성할 수 없습니다.")


def _split_semicolon(value: str) -> list[str]:
    return [item.strip() for item in value.split(";") if item.strip()]
