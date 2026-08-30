"""모델 호출 없이 사람이 채울 검수표를 만든다.

`review.create_review_templates`는 검수표를 `analyses.jsonl`에서 만든다. 그 표는
"모델이 적어 온 관찰과 claim이 맞는지" 판정하는 표라서 Gemini 분석이 선행돼야 한다.

여기서 만드는 표는 목적이 다르다. 사람이 이미지를 직접 보고 처음부터 적는 표이고,
필요한 입력은 `review-manifest`가 만든 metadata CSV뿐이다. 모델 호출이 0건이므로
분석 비용·할당량·503 재시도와 무관하게 언제든 다시 만들 수 있다.

열 이름은 `review.py`의 필드 목록을 그대로 가져온다. 검수 결과를 받는 쪽
(`validate-reviews`, `fit-anchors`, `import_golden_run`)이 그 이름으로 읽으므로,
여기서 이름을 새로 지으면 검수가 끝난 뒤에야 어긋난 것이 드러난다.

모델이 채우는 열(`detected_items`, `statement`, `change` 등)은 비워 둔다. claim
검수표와 최소 수정 검수표는 아예 만들지 않는다 — 둘 다 "모델이 낸 문장"을 판정하는
표라서 판정 대상이 없으면 빈 껍데기가 된다.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from .review import (
    CLAIM_REVIEW_FIELDS,
    MINIMUM_EDIT_REVIEW_FIELDS,
    OBSERVATION_REVIEW_FIELDS,
    PAIRWISE_FIELDS,
)

#: 쌍대 비교가 재는 축. review.py와 같은 값이어야 fit-anchors가 읽는다.
COMPARISON_AXIS = "Q_OVERALL_STYLE_EXECUTION"

#: 좌우 배치 규칙 이름. 어떤 규칙으로 좌우를 놓았는지 남겨야 순서 편향을 나중에
#: 확인할 수 있다. 여기서는 임베딩이 없어 결정적 순서만 쓴다.
PRESENTATION_ORDER = "DETERMINISTIC_METADATA_V1"


@dataclass(frozen=True)
class SheetPaths:
    observation: Path
    claim: Path
    minimum_edit: Path
    pairwise: Path


def _read_metadata(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            {key: (value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


#: 수집자 폴더가 아니라 이미지를 보고 붙인 스타일임을 검수표에 드러내는 표시.
GUESS_SUFFIX = "(추정)"


def _style_values(row: dict[str, str], analysis: dict) -> list[str]:
    """쌍 매칭에 쓰는 taxonomy 값. 표시용 꼬리표가 붙지 않은 형태다."""
    source = row.get("style") or analysis.get("style_guess") or ""
    return [value.strip() for value in source.split(";") if value.strip()]


def _style_intent(row: dict[str, str], analysis: dict) -> str:
    """검수자가 "무엇을 의도한 코디인지" 알고 판정하도록 싣는 값.

    수집자가 스타일 폴더로 나눠 모았으면 그 라벨이 곧 의도다. 폴더가 평면이면 의도를
    알 수 없어 이미지를 보고 붙이는데, 그건 검수자가 확인할 수 없는 값이므로 출처를
    드러내야 한다. `(추정)`을 붙여 두면 검수자가 틀렸다고 판단할 때 notes에 적을 수
    있다 — 표시 없이 넣으면 내 판단이 판정 기준으로 굳어 버린다.
    """
    if row.get("style"):
        return row["style"]
    guess = analysis.get("style_guess")
    if guess:
        return f"{guess}{GUESS_SUFFIX}"
    return row.get("style_source_label") or ""


def _local_path(row: dict[str, str], images_dir: Path) -> str:
    return str((images_dir / row["file_name"]).resolve())


def _style_key(row: dict[str, str], analyses: dict[str, dict]) -> frozenset[str]:
    return frozenset(_style_values(row, analyses.get(row["golden_id"], {})))


def build_pairs(
    rows: list[dict[str, str]],
    pair_count: int,
    analyses: dict[str, dict] | None = None,
) -> list[dict[str, str]]:
    """비교 쌍을 고른다. 임베딩 없이 metadata만 쓴다.

    두 가지를 지키려 한다.

    - **공정한 비교부터.** 스타일 의도가 겹치는 쌍을 먼저 쓴다. 의도가 다르면
      검수자는 `context_dependent`를 고를 수밖에 없고 그 표는 점수에서 빠진다.
    - **그래프 연결.** Bradley-Terry 상대 점수는 비교 그래프가 이어져 있어야
      계산된다. 스타일이 다른 묶음끼리는 겹치는 쌍이 아예 없으므로, 묶음을 잇는
      다리 역할의 쌍을 반드시 남겨야 한다.

    그래서 먼저 서로 다른 연결 성분을 잇는 쌍만 골라 전체를 잇고, 남는 자리를
    스타일이 겹치는 쌍으로 채운다.
    """
    analyses = analyses or {}
    parent = list(range(len(rows)))

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

    candidates: list[tuple[tuple[int, int], int, int, str]] = []
    for left in range(len(rows)):
        for right in range(left + 1, len(rows)):
            left_row, right_row = rows[left], rows[right]
            style_overlap = bool(_style_key(left_row, analyses) & _style_key(right_row, analyses))
            same_group = (
                left_row.get("presentation_group") == right_row.get("presentation_group")
            )
            if style_overlap and same_group:
                scope = "MATCHED_STYLE_CONTEXT"
            elif style_overlap:
                scope = "MATCHED_STYLE"
            else:
                # 임베딩이 없어 시각적 근접을 말할 수 없다. 이 쌍의 역할은 스타일
                # 묶음 사이를 잇는 다리뿐이라는 뜻으로 남긴다.
                scope = "VISUAL_BRIDGE"
            candidates.append(
                ((int(style_overlap), int(same_group)), left, right, scope)
            )
    # 우선순위 같으면 인덱스 순. 정렬이 결정적이어야 배치를 늘려도 재현된다.
    candidates.sort(key=lambda item: (-item[0][0], -item[0][1], item[1], item[2]))

    selected: list[tuple[int, int, str]] = []
    used: set[tuple[int, int]] = set()

    # 1단계: 전체를 잇는다. 스타일이 겹치는 쌍을 먼저 보므로, 이을 수 있는 곳은
    # 공정한 쌍으로 이어지고 다리 쌍은 정말 필요한 곳에만 남는다.
    for _, left, right, scope in candidates:
        if union(left, right):
            selected.append((left, right, scope))
            used.add((left, right))

    # 2단계: 남는 자리를 공정한 쌍으로 채운다. 같은 코디를 여러 번 비교할수록
    # 상대 점수가 안정된다.
    for _, left, right, scope in candidates:
        if len(selected) >= pair_count:
            break
        if (left, right) in used or scope == "VISUAL_BRIDGE":
            continue
        selected.append((left, right, scope))
        used.add((left, right))

    pairs: list[dict[str, str]] = []
    for index, (left, right, scope) in enumerate(selected[:pair_count] if pair_count else selected, start=1):
        left_row, right_row = rows[left], rows[right]
        pairs.append(
            {
                "left_index": str(left),
                "right_index": str(right),
                "scope": scope,
                "pair_id": f"pair-{index:04d}",
                "context_id": ";".join(sorted(_style_key(left_row, analyses) & _style_key(right_row, analyses))),
            }
        )
    return pairs


def _read_analysis(path: Path | None) -> dict[str, dict]:
    """golden_id별 관찰·claim·최소 수정. JSONL 한 줄이 이미지 하나다.

    검수표 생성과 분리해 둔다. 관찰 내용을 만드는 일은 이미지당 한 번이면 되는데
    검수표는 배치 구성이나 열이 바뀔 때마다 다시 만든다. 같은 파일에 묶어 두면 다시
    만들 때마다 내용이 날아간다. `analyses.jsonl`과 목적이 같고 출처만 다르다.
    """
    if path is None or not path.exists():
        return {}
    result: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            result[str(row["golden_id"])] = row
    return result


def _detected_items(analysis: dict) -> str:
    return ";".join(
        f"{row.get('region_id', '')}:{row.get('item_name', '')}"
        for row in analysis.get("observations", [])
    )


def build_review_sheets(
    *,
    metadata_csv: Path,
    images_dir: Path,
    out_dir: Path,
    pair_count: int = 120,
    reviewer_label: str = "",
    analysis_jsonl: Path | None = None,
) -> tuple[SheetPaths, dict[str, int]]:
    """metadata CSV와 관찰 JSONL로 검수표 4종을 만든다."""
    rows = _read_metadata(metadata_csv)
    analyses = _read_analysis(analysis_jsonl)
    out_dir.mkdir(parents=True, exist_ok=True)

    observation_path = out_dir / "image_observation_reviews.template.csv"
    with observation_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OBSERVATION_REVIEW_FIELDS)
        writer.writeheader()
        for row in rows:
            blank = {field: "" for field in OBSERVATION_REVIEW_FIELDS}
            blank.update(
                {
                    "reviewer_label": reviewer_label,
                    "golden_id": row["golden_id"],
                    "local_path": _local_path(row, images_dir),
                    "style_intent": _style_intent(row, analyses.get(row["golden_id"], {})),
                    "detected_items": _detected_items(
                        analyses.get(row["golden_id"], {})
                    ),
                }
            )
            writer.writerow(blank)

    claim_path = out_dir / "claim_reviews.template.csv"
    claim_rows = 0
    with claim_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CLAIM_REVIEW_FIELDS)
        writer.writeheader()
        for row in rows:
            analysis = analyses.get(row["golden_id"], {})
            for claim in analysis.get("claims", []):
                blank = {field: "" for field in CLAIM_REVIEW_FIELDS}
                blank.update(
                    {
                        "reviewer_label": reviewer_label,
                        "golden_id": row["golden_id"],
                        "local_path": _local_path(row, images_dir),
                        "claim_id": claim.get("claim_id", ""),
                        "axis": claim.get("axis", ""),
                        "statement": claim.get("statement", ""),
                        "evidence_region_ids": ";".join(
                            claim.get("evidence_region_ids", [])
                        ),
                        "model_relation_polarity": claim.get("relation_polarity", ""),
                        "model_contribution_direction": claim.get(
                            "contribution_direction", ""
                        ),
                    }
                )
                writer.writerow(blank)
                claim_rows += 1

    minimum_edit_path = out_dir / "minimum_edit_reviews.template.csv"
    edit_rows = 0
    with minimum_edit_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MINIMUM_EDIT_REVIEW_FIELDS)
        writer.writeheader()
        for row in rows:
            edit = analyses.get(row["golden_id"], {}).get("minimum_edit")
            if not edit:
                continue
            blank = {field: "" for field in MINIMUM_EDIT_REVIEW_FIELDS}
            blank.update(
                {
                    "reviewer_label": reviewer_label,
                    "golden_id": row["golden_id"],
                    "local_path": _local_path(row, images_dir),
                    "target_region_id": edit.get("target_region_id", ""),
                    "target_attribute": edit.get("target_attribute", ""),
                    "change": edit.get("change", ""),
                    "tested_axis": edit.get("tested_axis", ""),
                    "expected_effect": edit.get("expected_effect", ""),
                }
            )
            writer.writerow(blank)
            edit_rows += 1

    pairs = build_pairs(rows, pair_count, analyses)
    pairwise_path = out_dir / "pairwise_reviews.template.csv"
    with pairwise_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PAIRWISE_FIELDS)
        writer.writeheader()
        for pair in pairs:
            left_row = rows[int(pair["left_index"])]
            right_row = rows[int(pair["right_index"])]
            blank = {field: "" for field in PAIRWISE_FIELDS}
            blank.update(
                {
                    "pair_id": pair["pair_id"],
                    "reviewer_label": reviewer_label,
                    "comparison_scope": pair["scope"],
                    "comparison_axis": COMPARISON_AXIS,
                    "context_id": pair["context_id"],
                    "left_id": left_row["golden_id"],
                    "left_local_path": _local_path(left_row, images_dir),
                    "left_style_intent": _style_intent(left_row, analyses.get(left_row["golden_id"], {})),
                    "right_id": right_row["golden_id"],
                    "right_local_path": _local_path(right_row, images_dir),
                    "right_style_intent": _style_intent(right_row, analyses.get(right_row["golden_id"], {})),
                    "presentation_order": PRESENTATION_ORDER,
                }
            )
            writer.writerow(blank)

    counts = {
        "images": len(rows),
        "analyzed": sum(1 for row in rows if analyses.get(row["golden_id"])),
        "claims": claim_rows,
        "minimum_edits": edit_rows,
        "pairs": len(pairs),
        "bridge_pairs": sum(1 for pair in pairs if pair["scope"] == "VISUAL_BRIDGE"),
    }
    return (
        SheetPaths(
            observation=observation_path,
            claim=claim_path,
            minimum_edit=minimum_edit_path,
            pairwise=pairwise_path,
        ),
        counts,
    )
