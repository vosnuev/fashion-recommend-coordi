"""스타일 라벨로 `clusters.jsonl`을 만든다 (이미지 임베딩 대신).

## 왜 임베딩 대신 스타일인가

`clusters.jsonl`은 원칙 합성의 묶음 단위다. `synthesize-principles`가 `(cluster_id, 축)`
조합마다 LLM을 한 번 불러 "이 묶음의 공통 원칙"을 뽑는다.

원래는 FashionSigLIP 임베딩 K-means가 그 묶음을 만들었다. 두 가지 이유로 스타일 라벨을
쓸 수 있게 열어 둔다.

- **검색에서 쓸 수 있다.** 원칙은 `knowledge` 컬렉션에 적재돼 추천 시점에 검색되는데,
  그 컬렉션에는 `style` payload 인덱스가 이미 있다. `cluster-003`은 사용자 질의로
  매칭할 수 없지만 `아메카지`는 된다.
- **원칙 문장이 조건부가 된다.** "cluster-003에서는"이 아니라 "아메카지에서는"이 되고,
  그게 `applies_when`의 취지에 맞는다.

대신 포기하는 것이 있다. 같은 스타일 라벨 안에서도 시각적으로 꽤 다른 코디가 한 묶음이
된다. 임베딩 클러스터는 그걸 갈라 줬다. 어느 쪽이 나은지는 원칙 품질을 보고 판단할
일이라, 이 모듈은 임베딩 경로를 **대체하지 않고 병행**한다.

## selection_role

임베딩 클러스터는 중심에서의 거리로 대표·경계를 골랐다. 스타일 라벨에는 거리 개념이
없으므로 전부 `member`다. 그래서 이 clusters.jsonl로 `analyze`를 돌리려면 `--all`이
필요하다 — 대표만 고르면 한 장도 안 걸린다.
"""

from __future__ import annotations

import csv
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .artifacts import write_jsonl
from .review_manifest import _taxonomy_styles

logger = logging.getLogger("golden_set.style_clusters")

#: 스타일이 없는 코디가 들어가는 묶음. 이름을 남기는 이유는 아래 경고 참고.
UNSTYLED_CLUSTER = "미분류"

#: 한 코디에 허용하는 스타일 수. 셋 이상이면 조건이 아니게 된다.
MAX_STYLES = 2


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            {key: (value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(";") if item.strip()]


def merge_style_labels(
    metadata_rows: list[dict[str, str]],
    label_rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """라벨링 결과를 metadata 행에 얹는다.

    이미 `style`이 있는 행은 덮지 않는다 — 수집자 폴더명에서 온 값이 사람이 이미지를
    보고 붙인 값보다 출처가 분명하다.
    """
    labels = {
        row["golden_id"]: _split(row.get("style", ""))
        for row in label_rows
        if row.get("golden_id")
    }
    known = {row.get("golden_id", "") for row in metadata_rows}
    allowed = _taxonomy_styles()

    unknown_ids = sorted(gid for gid in labels if gid not in known)
    outside_vocab: Counter[str] = Counter()
    too_many: list[str] = []
    filled = 0

    merged: list[dict[str, str]] = []
    for row in metadata_rows:
        updated = dict(row)
        gid = updated.get("golden_id", "")
        values = labels.get(gid)
        if values and not _split(updated.get("style", "")):
            if allowed is not None:
                for value in values:
                    if value not in allowed:
                        outside_vocab[value] += 1
                values = [v for v in values if allowed is None or v in allowed]
            if len(values) > MAX_STYLES:
                too_many.append(gid)
                values = values[:MAX_STYLES]
            if values:
                updated["style"] = ";".join(values)
                filled += 1
        merged.append(updated)

    report = {
        "num_labels": len(labels),
        "num_filled": filled,
        "unknown_golden_ids": unknown_ids,
        "outside_vocabulary": dict(outside_vocab),
        "too_many_styles": too_many,
    }
    if unknown_ids:
        logger.warning(
            "metadata에 없는 golden_id %d건은 무시한다: %s",
            len(unknown_ids), unknown_ids[:10],
        )
    if outside_vocab:
        # 리트리버가 style을 필터 키로 쓴다. 어휘 밖 값은 검색에서 조용히 빠진다.
        logger.warning(
            "taxonomy STYLES 밖의 값은 버린다: %s", dict(outside_vocab),
        )
    if too_many:
        logger.warning(
            "스타일이 %d개를 넘어 앞의 것만 남긴 코디 %d건: %s",
            MAX_STYLES, len(too_many), too_many[:10],
        )
    return merged, report


def build_style_clusters(
    *,
    run_dir: Path,
    metadata_csv: Path,
    style_labels_csv: Path | None = None,
    out_metadata_csv: Path | None = None,
) -> dict[str, Any]:
    metadata_rows = _read_csv(metadata_csv)
    report: dict[str, Any] = {"num_images": len(metadata_rows)}

    if style_labels_csv is not None:
        metadata_rows, merge_report = merge_style_labels(
            metadata_rows, _read_csv(style_labels_csv)
        )
        report["merge"] = merge_report

    if out_metadata_csv is not None:
        out_metadata_csv.parent.mkdir(parents=True, exist_ok=True)
        with out_metadata_csv.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(metadata_rows[0].keys()))
            writer.writeheader()
            writer.writerows(metadata_rows)
        report["metadata_csv"] = str(out_metadata_csv)

    members: dict[str, list[str]] = defaultdict(list)
    rows: list[dict[str, Any]] = []
    for row in metadata_rows:
        gid = row.get("golden_id", "")
        if not gid:
            continue
        styles = _split(row.get("style", ""))
        # 스타일이 여럿이면 첫 번째로 묶는다. 여러 묶음에 넣으면 같은 claim이
        # 두 원칙의 근거가 되어 지지 이미지 수가 부풀려진다.
        cluster = styles[0] if styles else UNSTYLED_CLUSTER
        members[cluster].append(gid)
        rows.append(
            {
                "golden_id": gid,
                "cluster_id": cluster,
                # 스타일 라벨에는 중심 거리가 없어 대표·경계를 고를 수 없다.
                "selection_role": "member",
                "cluster_source": "style_label",
            }
        )

    run_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(run_dir / "clusters.jsonl", rows)

    sizes = {name: len(ids) for name, ids in sorted(members.items())}
    unstyled = sizes.get(UNSTYLED_CLUSTER, 0)
    report.update(
        {
            "clusters_jsonl": str(run_dir / "clusters.jsonl"),
            "num_clusters": len(sizes),
            "cluster_sizes": sizes,
            "num_unstyled": unstyled,
        }
    )
    if unstyled:
        # 미분류가 크면 "무엇에 대한 원칙인지" 알 수 없는 일반론이 나온다.
        # 임베딩 클러스터를 쓰던 이유가 바로 이걸 막는 것이었다.
        logger.warning(
            "스타일이 없는 코디 %d건이 '%s' 묶음으로 들어간다. 이 묶음에서 나오는 "
            "원칙은 조건이 없는 일반론이 되므로, 라벨을 채우거나 원칙 검수에서 "
            "걸러야 한다.",
            unstyled, UNSTYLED_CLUSTER,
        )
    return report
