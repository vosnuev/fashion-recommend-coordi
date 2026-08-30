"""이미지당 한 번의 통합 멀티모달 분석과 버전 캐시."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Protocol

from . import PROMPT_VERSION, SCHEMA_VERSION
from .artifacts import read_json, read_jsonl, upsert_jsonl, write_json
from .config import GoldenSettings
from .gemini import GeminiStructuredClient
from .prompts import (
    ANALYSIS_SCHEMA,
    ANALYSIS_SYSTEM_INSTRUCTION,
    AXES,
    analysis_prompt,
)


class AnalysisClient(Protocol):
    def analyze_image(
        self,
        *,
        image_path: Path,
        prompt: str,
        system_instruction: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]: ...


def analyze_run(
    *,
    run_dir: Path,
    settings: GoldenSettings,
    analyze_all: bool = False,
    client: AnalysisClient | None = None,
) -> list[dict[str, Any]]:
    images = {row["golden_id"]: row for row in read_jsonl(run_dir / "images.jsonl")}
    clusters = read_jsonl(run_dir / "clusters.jsonl")
    selected = {
        row["golden_id"]
        for row in clusters
        if analyze_all or row["selection_role"] in {"representative", "boundary"}
    }
    existing = {
        row["golden_id"]: row
        for row in read_jsonl(run_dir / "analyses.jsonl")
        if row.get("status") == "SUCCEEDED"
        and row.get("model_version") == settings.gemini_model
        and row.get("prompt_version") == PROMPT_VERSION
        and row.get("schema_version") == SCHEMA_VERSION
    }
    api_client = client or GeminiStructuredClient(settings)
    cache_dir = run_dir / "cache" / "analysis"
    cache_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    attempted_calls = 0
    successful_calls = 0

    for golden_id in sorted(selected):
        if golden_id in existing:
            continue
        image = images[golden_id]
        cache_key = _cache_key(
            str(image["image_sha256"]),
            settings.gemini_model,
            PROMPT_VERSION,
            SCHEMA_VERSION,
        )
        cache_path = cache_dir / f"{cache_key}.json"
        started = time.perf_counter()
        record: dict[str, Any] = {
            "golden_id": golden_id,
            "model_version": settings.gemini_model,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "status": "SUCCEEDED",
            "result": {},
            "error_message": "",
            "latency_seconds": None,
            "cache_hit": False,
        }
        try:
            if cache_path.exists():
                cached = read_json(cache_path)
                _validate_analysis(cached)
                record["result"] = cached
                record["cache_hit"] = True
            else:
                if attempted_calls >= settings.max_multimodal_calls:
                    raise RuntimeError(
                        "멀티모달 호출 상한에 도달했습니다: "
                        f"{settings.max_multimodal_calls}회"
                    )
                # 잘못된 응답이나 네트워크 오류도 한도에는 보수적으로 포함한다.
                # 성공 건만 세면 반복 실패 시 비용 안전 상한을 우회할 수 있다.
                attempted_calls += 1
                payload = api_client.analyze_image(
                    image_path=Path(str(image["local_path"])),
                    prompt=analysis_prompt(
                        metadata_json=json.dumps(
                            image.get("metadata", {}),
                            ensure_ascii=False,
                            indent=2,
                        )
                    ),
                    system_instruction=ANALYSIS_SYSTEM_INSTRUCTION,
                    schema=ANALYSIS_SCHEMA,
                )
                _validate_analysis(payload)
                write_json(cache_path, payload)
                record["result"] = payload
                successful_calls += 1
        except Exception as exc:  # noqa: BLE001 — 부분 실패를 기록하고 배치를 계속한다
            record["status"] = "FAILED"
            record["error_message"] = f"{type(exc).__name__}: {exc}"
        record["latency_seconds"] = round(time.perf_counter() - started, 3)
        results.append(record)

    upsert_jsonl(run_dir / "analyses.jsonl", results, key="golden_id")
    manifest = read_json(run_dir / "run_manifest.json")
    manifest.update(
        {
            "status": "ANALYZED",
            "analysis_model": settings.gemini_model,
            "analysis_prompt_version": PROMPT_VERSION,
            "analysis_schema_version": SCHEMA_VERSION,
            "multimodal_call_attempts_last_run": attempted_calls,
            "successful_multimodal_calls_last_run": successful_calls,
            # 기존 report 소비자와의 호환 필드. 실제 청구 여부는 API 제공자 사용량이 SSOT다.
            "paid_multimodal_calls_last_run": successful_calls,
        }
    )
    write_json(run_dir / "run_manifest.json", manifest)
    return results


def _cache_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _validate_analysis(payload: dict[str, Any]) -> None:
    required = {
        "observations",
        "look_tags",
        "axis_assessability",
        "claims",
        "relationship_summary",
        "minimum_edit",
        "unassessable",
    }
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"분석 필수 필드 누락: {sorted(missing)}")
    region_ids = {str(row.get("region_id")) for row in payload.get("observations", [])}
    region_ids.add("whole-look")
    claims = payload.get("claims", [])
    if len(claims) > 3:
        raise ValueError("핵심 claim은 최대 3개여야 합니다.")
    claim_ids = [str(row.get("claim_id")) for row in claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError("claim_id가 중복되었습니다.")

    assessability = payload.get("axis_assessability", [])
    assessed_axes = {str(row.get("axis")) for row in assessability}
    missing_axes = set(AXES) - assessed_axes
    if missing_axes:
        raise ValueError(f"판정 가능성 축 누락: {sorted(missing_axes)}")

    for claim in claims:
        evidence = {str(value) for value in claim.get("evidence_region_ids", [])}
        if not evidence:
            raise ValueError(f"claim 근거 영역 누락: {claim.get('claim_id')}")
        unknown = evidence - region_ids
        if unknown:
            raise ValueError(
                f"claim이 존재하지 않는 영역을 참조합니다: {sorted(unknown)}"
            )
        if str(claim.get("axis")) not in AXES:
            raise ValueError(f"알 수 없는 판단 축: {claim.get('axis')}")

    summary = payload.get("relationship_summary", {})
    summary_refs = {
        str(summary.get("strongest_harmony_claim_id", "")),
        *(str(value) for value in summary.get("conflict_claim_ids", [])),
    }
    summary_refs.discard("")
    unknown_claims = summary_refs - set(claim_ids)
    if unknown_claims:
        raise ValueError(
            f"관계 요약이 존재하지 않는 claim을 참조합니다: {sorted(unknown_claims)}"
        )

    minimum_edit = payload.get("minimum_edit", {})
    target_region = str(minimum_edit.get("target_region_id", ""))
    if target_region not in region_ids:
        raise ValueError(f"최소 수정 대상 영역이 존재하지 않습니다: {target_region}")
    if minimum_edit.get("hypothesis_only") is not True:
        raise ValueError("최소 수정은 검증 전 가설(hypothesis_only=true)이어야 합니다.")
