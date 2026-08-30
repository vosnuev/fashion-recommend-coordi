"""사람 검수 결과를 S3로 발행하고 적재 쪽에서 되읽는다.

## 왜 sha256으로 잇는가

검수표의 `golden_id`는 `{수집자}-{성별}-{스타일}-{연번}` 정규화 이름이고
(`review_manifest`가 만든다), S3의 `golden_id`는 업로드 당시의 **원본 파일명**이다
(`001`, `042-2`, 32자 해시…). 둘 사이에는 규칙이 없다 — 원본 파일명은 수집자 사이에서
겹쳐서 S3 쪽이 임의로 `-2`를 붙여 갈랐고, 그 대응표는 어디에도 남아 있지 않다.

이름을 맞추려면 S3의 645건을 정규화 이름으로 다시 올려야 하는데, 아이템 이미지가
코디마다 딸려 있어 재생성 비용이 크다. 다행히 양쪽 모두 같은 원본 사진의 sha256을
들고 있다 — S3는 `manifest.json`의 `image_sha256`, 로컬은 `metadata.csv`의 같은 열이다.
**sha256은 이름 규칙과 무관하게 같은 사진을 가리키는 유일한 값이므로 이걸 조인 키로
쓴다.**

그래서 발행 파일은 `golden_id`가 아니라 `image_sha256`으로 조회하게 만든다. 적재 쪽이
`golden_id`로 찾으면 한 건도 붙지 않고, 그것도 **에러 없이** 안 붙는다.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import s3io
from .artifacts import read_json, read_jsonl
from .config import GoldenSettings

logger = logging.getLogger("golden_set.review_publish")

HUMAN_REVIEW_NAME = "human_review.json"
SCHEMA_VERSION = "golden-human-review-v1"

#: 코디 payload로 넘어가는 앵커 필드. 여기 없는 값은 적재하지 않는다.
ANCHOR_FIELDS = (
    "anchor_graph",
    "anchor_scope",
    "human_score",
    "score_band",
    "score_confidence",
    "comparison_count",
    "reviewer_agreement",
)


def human_review_key(settings: GoldenSettings) -> str:
    """`run_summary.json`과 같은 자리에 둔다 — 버전이 갈리면 검수 결과도 갈린다."""
    return f"{settings.derived_prefix()}/{HUMAN_REVIEW_NAME}"


def load_metadata_shas(metadata_csv: Path) -> dict[str, str]:
    """정규화 golden_id → image_sha256."""
    with metadata_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        return {
            str(row.get("golden_id", "")).strip(): str(
                row.get("image_sha256", "")
            ).strip()
            for row in csv.DictReader(handle)
            if row.get("golden_id") and row.get("image_sha256")
        }


def build_review_payload(
    *,
    run_dir: Path,
    metadata_csv: Path,
    dataset_version: str,
) -> dict[str, Any]:
    """run 디렉터리의 검수 산출물을 sha256으로 묶어 발행 형태로 만든다."""
    shas = load_metadata_shas(metadata_csv)

    anchors = {
        str(row["golden_id"]): row
        for row in read_jsonl(run_dir / "anchor_scores.jsonl")
    }
    validation_path = run_dir / "review_validation.json"
    accepted: list[str] = []
    if validation_path.exists():
        accepted = list(read_json(validation_path).get("accepted_images", []))
    else:
        logger.warning(
            "review_validation.json이 없어 human_verified를 채우지 않는다 "
            "(validate-reviews를 먼저 돌린다): %s",
            validation_path,
        )

    images: dict[str, dict[str, Any]] = {}
    unmatched: list[str] = []
    for golden_id in sorted(set(anchors) | set(accepted)):
        sha = shas.get(golden_id, "")
        if not sha:
            # metadata CSV에 없는 코디. 조인 키가 없으므로 적재 쪽에서 찾을 방법이
            # 없다. 조용히 버리면 "왜 이 코디만 점수가 없지"가 된다.
            unmatched.append(golden_id)
            continue
        entry = images.setdefault(
            sha,
            {
                "image_sha256": sha,
                "review_golden_id": golden_id,
                "human_verified": False,
            },
        )
        if golden_id in accepted:
            entry["human_verified"] = True
        anchor = anchors.get(golden_id)
        if anchor:
            entry.update(
                {name: anchor[name] for name in ANCHOR_FIELDS if name in anchor}
            )
            entry["anchor_reviewer_count"] = anchor.get("reviewer_count", 0)

    if unmatched:
        logger.warning(
            "metadata CSV에서 sha256을 찾지 못한 코디 %d건은 발행에서 빠진다: %s",
            len(unmatched),
            unmatched[:10],
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_version": dataset_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "join_key": "image_sha256",
        "num_images": len(images),
        "num_verified": sum(
            1 for row in images.values() if row.get("human_verified")
        ),
        "num_anchored": sum(1 for row in images.values() if "human_score" in row),
        "unmatched_golden_ids": unmatched,
        "warning": (
            "human_score와 score_band는 같은 anchor_graph 안에서만 비교 가능하다"
        ),
        "images": [images[sha] for sha in sorted(images)],
    }


def publish_review(
    *,
    run_dir: Path,
    metadata_csv: Path,
    settings: GoldenSettings,
    dry_run: bool = False,
) -> tuple[str, dict[str, Any]]:
    payload = build_review_payload(
        run_dir=run_dir,
        metadata_csv=metadata_csv,
        dataset_version=settings.dataset_version,
    )
    key = human_review_key(settings)
    if dry_run:
        return key, payload
    s3io.put_json(settings.require_bucket(), key, payload)
    return key, payload


@dataclass(frozen=True)
class ReviewIndex:
    """sha256으로 조회하는 검수 결과. 없으면 빈 인덱스로 동작한다."""

    by_sha: dict[str, dict[str, Any]] = field(default_factory=dict)
    source: str = ""

    def __len__(self) -> int:
        return len(self.by_sha)

    def payload_for(self, image_sha256: str) -> dict[str, Any]:
        """코디 payload에 얹을 필드. 검수가 없으면 빈 dict.

        점수가 없는 코디에 `human_score=0`을 적지 않는다 — 리트리버에서 0은
        "최하점"이 아니라 "기준선 없음"으로 취급돼야 하고, 미검수와 최하점을
        구분할 수 없게 되면 나중에 앵커를 늘려도 그 차이를 볼 수 없다.
        """
        row = self.by_sha.get(str(image_sha256 or ""))
        if not row:
            return {}
        result = {name: row[name] for name in ANCHOR_FIELDS if name in row}
        result["human_verified"] = bool(row.get("human_verified", False))
        result["human_review_golden_id"] = row.get("review_golden_id", "")
        return result


def load_review(
    *,
    settings: GoldenSettings,
    local_path: Path | None = None,
) -> ReviewIndex:
    """검수 결과를 읽는다. 없으면 빈 인덱스 — 앵커 없이도 적재는 돌아야 한다."""
    if local_path is not None:
        payload = json.loads(local_path.read_text(encoding="utf-8"))
        source = str(local_path)
    else:
        key = human_review_key(settings)
        payload = s3io.get_json(settings.require_bucket(), key)
        source = f"s3://{settings.s3_bucket}/{key}"
        if payload is None:
            logger.warning(
                "사람 검수 결과가 없습니다 (%s). human_score 없이 적재합니다 — "
                "골든 코디 랭킹은 규칙 점수만으로 정해집니다.",
                source,
            )
            return ReviewIndex(source=source)
    by_sha = {
        str(row["image_sha256"]): row
        for row in payload.get("images", [])
        if row.get("image_sha256")
    }
    logger.info(
        "사람 검수 결과 %d건 (검수 통과 %d / 앵커 %d) ← %s",
        len(by_sha),
        sum(1 for row in by_sha.values() if row.get("human_verified")),
        sum(1 for row in by_sha.values() if "human_score" in row),
        source,
    )
    return ReviewIndex(by_sha=by_sha, source=source)
