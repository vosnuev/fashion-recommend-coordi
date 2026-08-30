"""사람 검수 결과를 **재임베딩 없이** 코디 payload에만 반영한다.

`sync_qdrant`는 코디 이미지 벡터를 매번 원본 사진에서 새로 계산한다(S3에 저장된
적이 없다). 645건 기준으로 GPU를 오래 물고 있어야 하는데, 검수 결과가 바뀔 때마다
그걸 다시 도는 것은 낭비다. 벡터는 그대로 두고 payload만 갈아끼우면 되는 일이다.

그래서 여기서는 Qdrant `set_payload`만 쓴다. 모델도 GPU도 필요 없고 S3에서 읽는
것은 manifest(`golden_id` ↔ `image_sha256`)뿐이다.

## 왜 지우는 일까지 하는가

검수를 고쳐서 다시 발행했는데 어떤 코디가 승인에서 빠졌다면, 그 코디에 남아 있던
옛 `human_score`는 사라져야 한다. `set_payload`는 덮어쓰기만 하므로 빠진 코디는
옛 점수를 그대로 들고 있게 된다 — 검수를 되돌렸는데 랭킹은 안 되돌아가는 상태다.
그래서 검수가 없는 코디에서는 검수 키를 명시적으로 지운다.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from qdrant_client import QdrantClient

from . import s3io
from .config import GoldenSettings
from .qdrant_index import (
    OUTFIT_COLLECTION,
    build_client,
    outfit_point_id,
    preflight,
)
from .review_publish import ANCHOR_FIELDS, ReviewIndex
from .tag_manifests import find_manifests

logger = logging.getLogger("golden_set.review_apply")

#: 이 명령이 소유하는 payload 키. 검수가 없어진 코디에서는 전부 지운다.
REVIEW_PAYLOAD_KEYS = (
    *ANCHOR_FIELDS,
    "human_verified",
    "human_review_golden_id",
)

#: set_payload는 포인트마다 값이 달라 한 건씩 부른다. 존재 확인만 배치로 묶는다.
RETRIEVE_BATCH = 256


def apply_review_payload(
    *,
    settings: GoldenSettings,
    review: ReviewIndex,
    client: QdrantClient | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    bucket = settings.require_bucket()
    derived = settings.derived_prefix()
    version = settings.dataset_version

    manifest_keys = find_manifests(bucket, derived)
    if limit:
        manifest_keys = manifest_keys[:limit]
    if not manifest_keys:
        raise ValueError(
            f"manifest가 없습니다 (s3://{bucket}/{derived}). "
            "GOLDEN_S3_OUTPUT_PREFIX/GOLDEN_DATASET_VERSION을 확인하세요."
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        manifests = list(pool.map(lambda key: s3io.get_json(bucket, key), manifest_keys))

    updates: dict[str, dict[str, Any]] = {}
    clears: list[str] = []
    unreadable = 0
    for manifest in manifests:
        if not manifest or not manifest.get("golden_id"):
            unreadable += 1
            continue
        point = outfit_point_id(version, str(manifest["golden_id"]))
        payload = review.payload_for(str(manifest.get("image_sha256", "")))
        if payload:
            updates[point] = payload
        else:
            clears.append(point)

    if review and not updates:
        # 조인 키가 어긋나면 아무 일도 일어나지 않는데 에러는 나지 않는다.
        raise ValueError(
            f"검수 결과 {len(review)}건을 읽었지만 sha256이 일치하는 코디가 "
            "없습니다. 발행에 쓴 metadata CSV가 이 S3 데이터셋과 같은 원본인지 "
            "확인하세요."
        )

    summary: dict[str, Any] = {
        "collection": OUTFIT_COLLECTION,
        "dataset_version": version,
        "num_manifests": len(manifest_keys),
        "num_unreadable_manifests": unreadable,
        "num_reviews_loaded": len(review),
        "num_matched": len(updates),
        "num_cleared_candidates": len(clears),
    }

    if dry_run:
        summary["applied"] = False
        return summary

    client = client or build_client()
    preflight(client)

    present = _existing_points(client, [*updates, *clears])
    missing = [point for point in updates if point not in present]
    if missing:
        # 검수는 붙었는데 Qdrant에 포인트가 없다 = 아직 적재되지 않은 코디다.
        # set_payload는 없는 포인트에 조용히 아무것도 하지 않으므로 여기서 센다.
        logger.warning(
            "검수 결과는 있으나 아직 적재되지 않은 코디 %d건 — sync_qdrant를 "
            "먼저 돌려야 반영됩니다.",
            len(missing),
        )

    applied = 0
    for point, payload in updates.items():
        if point not in present:
            continue
        client.set_payload(
            collection_name=OUTFIT_COLLECTION,
            payload=payload,
            points=[point],
            wait=True,
        )
        applied += 1

    clear_targets = [point for point in clears if point in present]
    if clear_targets:
        client.delete_payload(
            collection_name=OUTFIT_COLLECTION,
            keys=list(REVIEW_PAYLOAD_KEYS),
            points=clear_targets,
            wait=True,
        )

    summary.update(
        {
            "applied": True,
            "num_updated": applied,
            "num_not_indexed": len(missing),
            "num_cleared": len(clear_targets),
        }
    )
    return summary


def _existing_points(client: QdrantClient, points: list[str]) -> set[str]:
    """적재된 point id만 추린다. 없는 포인트에 쓰면 조용한 no-op이 된다."""
    found: set[str] = set()
    for start in range(0, len(points), RETRIEVE_BATCH):
        records = client.retrieve(
            collection_name=OUTFIT_COLLECTION,
            ids=points[start : start + RETRIEVE_BATCH],
            with_payload=False,
            with_vectors=False,
        )
        found.update(str(record.id) for record in records)
    return found
