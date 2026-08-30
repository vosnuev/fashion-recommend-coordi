"""검수자에게 받은 파일을 run 디렉터리로 들인다.

검수표를 `goldenset-review-sheets` 스킬로 만들면 두 가지가 파이프라인 계약과 다르다.

- **관찰·claim 초안이 run 밖에 있다.** 스킬은 `analysis.jsonl`을 평평한 형태
  (`{golden_id, observations, claims, minimum_edit}`)로 남기는데, `validate-reviews`는
  `analyses.jsonl`에서 `result.claims`를 찾는다. 감싸 주지 않으면 모든 claim이
  pending으로 빠진다 — 에러 없이.
- **검수자마다 파일이 따로 온다.** CLI는 검수표를 종류당 하나만 받고, 2인 요건은
  같은 파일 안의 `reviewer_label`로 센다. 합치지 않으면 전부 1인 검수로 보인다.

둘 다 손으로 처리하면 그때뿐이고 재현되지 않는다. 이 모듈이 그 두 변환을 맡는다.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .artifacts import read_jsonl, write_jsonl

logger = logging.getLogger("golden_set.review_intake")

#: 스킬이 만든 초안임을 산출물에 남긴다. Gemini 분석(`analyze`)과 섞이면 안 된다.
SHEET_MODEL_VERSION = "human-review-sheet"
SHEET_PROMPT_VERSION = "goldenset-review-sheets"
SHEET_SCHEMA_VERSION = "golden-analysis-v2"


@dataclass(frozen=True)
class IntakeResult:
    run_dir: Path
    num_analyses: int
    counts: dict[str, int]
    reviewers: dict[str, list[str]]


def convert_analysis(analysis_jsonl: Path, run_dir: Path) -> int:
    """스킬 `analysis.jsonl` → run `analyses.jsonl`.

    이미 run 스키마(`result` 키 보유)면 그대로 통과시킨다 — `analyze`가 만든 파일을
    잘못 넣었을 때 덮어써 망가뜨리지 않기 위해서다.
    """
    rows: list[dict[str, Any]] = []
    for row in read_jsonl(analysis_jsonl):
        golden_id = str(row.get("golden_id", "")).strip()
        if not golden_id:
            raise ValueError(f"golden_id가 없는 행이 있습니다: {analysis_jsonl}")
        if "result" in row:
            rows.append(row)
            continue
        rows.append(
            {
                "golden_id": golden_id,
                "model_version": SHEET_MODEL_VERSION,
                "prompt_version": SHEET_PROMPT_VERSION,
                "schema_version": SHEET_SCHEMA_VERSION,
                "status": "SUCCEEDED",
                "result": {
                    key: value for key, value in row.items() if key != "golden_id"
                },
            }
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(run_dir / "analyses.jsonl", rows)
    return len(rows)


def merge_sheets(paths: Iterable[Path], destination: Path) -> list[str]:
    """검수자별 CSV를 한 파일로 합친다. 검수자 라벨 목록을 돌려준다.

    같은 검수자가 두 번 들어오면 여기서 멈춘다. 그대로 두면 1인 검수가 2인으로
    보여 승인 요건을 통과해 버린다 — 검수 기록이 조용히 부풀려지는 경우다.
    """
    paths = [Path(path) for path in paths]
    if not paths:
        return []
    rows: list[dict[str, str]] = []
    fieldnames: list[str] | None = None
    seen_reviewers: dict[str, Path] = {}
    for path in paths:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = list(reader.fieldnames or [])
            if fieldnames is None:
                fieldnames = columns
            elif columns != fieldnames:
                raise ValueError(
                    f"검수표 열이 서로 다릅니다: {path}\n"
                    f"  기준: {fieldnames}\n  이 파일: {columns}"
                )
            file_rows = list(reader)
        labels = {
            str(row.get("reviewer_label", "")).strip()
            for row in file_rows
            if str(row.get("reviewer_label", "")).strip()
        }
        if not labels:
            raise ValueError(f"reviewer_label이 비어 있습니다: {path}")
        for label in labels:
            if label in seen_reviewers:
                raise ValueError(
                    f"같은 검수자가 두 번 들어왔습니다: {label} "
                    f"({seen_reviewers[label].name}, {path.name})"
                )
            seen_reviewers[label] = path
        rows.extend(file_rows)

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames or [])
        writer.writeheader()
        writer.writerows(rows)
    return sorted(seen_reviewers)


def prepare_review_run(
    *,
    run_dir: Path,
    analysis_jsonl: Path,
    observation_csvs: list[Path],
    claim_csvs: list[Path],
    pairwise_csvs: list[Path],
) -> IntakeResult:
    num_analyses = convert_analysis(analysis_jsonl, run_dir)

    plans = (
        ("observation_reviews.csv", observation_csvs),
        ("claim_reviews.csv", claim_csvs),
        ("pairwise_reviews.csv", pairwise_csvs),
    )
    counts: dict[str, int] = {}
    reviewers: dict[str, list[str]] = {}
    for name, sources in plans:
        if not sources:
            continue
        destination = run_dir / name
        reviewers[name] = merge_sheets(sources, destination)
        with destination.open("r", encoding="utf-8-sig", newline="") as handle:
            counts[name] = sum(1 for _ in csv.DictReader(handle))
        if len(reviewers[name]) < 2:
            # 2인 승인이 계약이다. 여기서 알리지 않으면 validate-reviews가
            # "승인 0건"만 말해 주고 원인은 알려주지 않는다.
            logger.warning(
                "%s: 검수자가 %d명뿐입니다 (%s). 2인 승인 요건을 채우지 못합니다.",
                name,
                len(reviewers[name]),
                ", ".join(reviewers[name]) or "없음",
            )

    known = {str(row["golden_id"]) for row in read_jsonl(run_dir / "analyses.jsonl")}
    _warn_unknown_ids(run_dir / "observation_reviews.csv", known, "golden_id")
    _warn_unknown_ids(run_dir / "claim_reviews.csv", known, "golden_id")

    return IntakeResult(
        run_dir=run_dir,
        num_analyses=num_analyses,
        counts=counts,
        reviewers=reviewers,
    )


def _warn_unknown_ids(path: Path, known: set[str], column: str) -> None:
    """분석 초안에 없는 코디를 검수했으면 알린다. 그 행은 승인될 수 없다."""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        unknown = sorted(
            {
                str(row.get(column, "")).strip()
                for row in csv.DictReader(handle)
                if str(row.get(column, "")).strip() not in known
                and str(row.get(column, "")).strip()
            }
        )
    if unknown:
        logger.warning(
            "%s: analyses.jsonl에 없는 코디 %d건은 승인될 수 없습니다: %s",
            path.name,
            len(unknown),
            unknown[:10],
        )
