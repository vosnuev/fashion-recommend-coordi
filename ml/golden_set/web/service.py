"""웹 페이지에 내려줄 상태를 S3·Qdrant에서 모은다.

설계 전제 두 가지.

1. run 디렉터리는 없을 수 있다 (웹은 API 서버, 임베딩은 GPU 서버).
   그래서 진행률·아이템 정보는 전부 S3의 per-image manifest에서 만든다.
   러너가 끝날 때 남기는 `run_summary.json`이 있으면 임베딩 메타를 얹는다.
2. 어떤 원격 조회도 페이지를 죽이지 않는다. 실패한 구획은 `error` 문자열을
   담아 돌려주고 나머지는 정상 표시한다 — 확인용 화면이 원격 장애 때문에
   아무것도 못 보여주면 쓸모가 없다.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .. import s3io
from ..artifacts import read_json
from ..config import GoldenSettings
from ..items import ITEM_SCHEMA_VERSION
from ..qdrant_index import (
    ITEM_COLLECTION,
    KNOWLEDGE_COLLECTION,
    OUTFIT_COLLECTION,
    item_point_id,
    outfit_point_id,
)

logger = logging.getLogger("golden_set.web.service")

RUN_SUMMARY_NAME = "run_summary.json"

#: 코디 목록에서 아이템마다 보여줄 필드 (표에 넣기 좋은 최소치)
ITEM_LIST_FIELDS = (
    "item_key",
    "item_index",
    "item_name",
    "label_ko",
    "category_large",
    "category_small",
    "layer_role",
    "color",
    "pattern",
    "material",
    "season",
    "style",
    "status",
    "error_message",
    "s3_key",
)


def run_summary_key(settings: GoldenSettings) -> str:
    return f"{settings.derived_prefix()}/{RUN_SUMMARY_NAME}"


def publish_run_summary(settings: GoldenSettings, summary: dict[str, Any]) -> str:
    """러너가 한 사이클을 끝낸 뒤 요약을 S3에 남긴다.

    웹이 GPU 호스트의 run 디렉터리를 못 보므로, 임베딩 메타(모델·신규/재사용
    건수)를 전달하는 유일한 통로다.
    """
    key = run_summary_key(settings)
    s3io.put_json(settings.require_bucket(), key, summary)
    return key


def _safe(section: str, func, default: Any) -> Any:
    """원격 조회 실패를 구획 단위로 격리한다."""
    try:
        return func()
    except Exception as error:  # noqa: BLE001 — 확인용 화면은 부분 실패를 견뎌야 한다
        logger.warning("%s 조회 실패: %s", section, error)
        return {"error": f"{type(error).__name__}: {error}", **(default or {})}


def _load_manifests(settings: GoldenSettings) -> dict[str, dict[str, Any]]:
    """코디별 완료 manifest를 전부 읽는다 (아이템 정보의 원천)."""
    bucket = settings.require_bucket()
    derived = settings.derived_prefix()
    manifests: dict[str, dict[str, Any]] = {}
    for key in s3io.list_keys(bucket, f"{derived}/"):
        if not key.endswith(f"/{s3io.ITEM_MANIFEST_NAME}"):
            continue
        payload = s3io.get_json(bucket, key)
        if isinstance(payload, dict) and payload.get("golden_id"):
            manifests[str(payload["golden_id"])] = payload
    return manifests


def _pending_source_keys(
    source_keys: list[str], manifests: dict[str, dict[str, Any]]
) -> list[str]:
    """아직 아이템 처리가 안 된 원본 키.

    manifest의 `source_key`로 맞추는 게 정확하다. metadata CSV가 golden_id를
    파일명과 다르게 지정하면 파일명(stem) 비교만으로는 이미 처리된 원본이
    영영 "대기"로 보인다. 구형 manifest에는 source_key가 없어 stem으로 폴백한다.
    """
    claimed_keys = {
        str(row.get("source_key")) for row in manifests.values() if row.get("source_key")
    }
    claimed_stems = set(manifests)
    return [
        key
        for key in source_keys
        if key not in claimed_keys and Path(key).stem not in claimed_stems
    ]


def source_progress(settings: GoldenSettings) -> dict[str, Any]:
    """S3 원본 수 대비 아이템 처리 완료 수."""
    bucket = settings.require_bucket()
    source_keys = s3io.list_source_keys(bucket, settings.source_prefix())
    manifests = _load_manifests(settings)
    done = [
        golden_id
        for golden_id, row in manifests.items()
        if row.get("schema_version") == ITEM_SCHEMA_VERSION
    ]
    stale = sorted(set(manifests) - set(done))
    return {
        "bucket": bucket,
        "source_prefix": settings.source_prefix(),
        "derived_prefix": settings.derived_prefix(),
        "source_count": len(source_keys),
        "processed_count": len(done),
        "pending_count": len(_pending_source_keys(source_keys, manifests)),
        "stale_schema_count": len(stale),
        "item_schema_version": ITEM_SCHEMA_VERSION,
    }


def run_summary(settings: GoldenSettings) -> dict[str, Any]:
    """러너가 S3에 남긴 마지막 사이클 요약. 없으면 빈 dict."""
    payload = s3io.get_json(settings.require_bucket(), run_summary_key(settings))
    return payload if isinstance(payload, dict) else {}


def local_run_artifacts(settings: GoldenSettings) -> dict[str, Any]:
    """run 디렉터리가 같은 호스트에 있으면 보조 정보로 읽는다."""
    run_dir: Path = settings.run_dir
    if not run_dir.is_dir():
        return {"available": False, "run_dir": str(run_dir)}
    result: dict[str, Any] = {"available": True, "run_dir": str(run_dir)}
    for name, key in (
        ("run_manifest.json", "run_manifest"),
        ("image_embeddings.meta.json", "image_embedding"),
        ("items.meta.json", "items"),
        ("qdrant_index_plan.json", "index_plan"),
    ):
        path = run_dir / name
        if path.exists():
            try:
                result[key] = read_json(path)
            except (ValueError, OSError):
                result[key] = None
    return result


def qdrant_counts(settings: GoldenSettings) -> dict[str, Any]:
    """컬렉션별 이 데이터셋 버전의 포인트 수."""
    from qdrant_client import QdrantClient
    from qdrant_client import models as qm

    client = QdrantClient(
        url=os.getenv("QDRANT_URL", "http://localhost:6333"),
        api_key=os.getenv("QDRANT_API_KEY") or None,
        timeout=int(os.getenv("QDRANT_TIMEOUT", "30")),
    )
    version_filter = qm.Filter(
        must=[
            qm.FieldCondition(
                key="dataset_version",
                match=qm.MatchValue(value=settings.dataset_version),
            )
        ]
    )
    counts: dict[str, Any] = {"url": os.getenv("QDRANT_URL", "http://localhost:6333")}
    for name in (OUTFIT_COLLECTION, ITEM_COLLECTION, KNOWLEDGE_COLLECTION):
        if not client.collection_exists(name):
            counts[name] = None  # 아직 init_qdrant 전
            continue
        counts[name] = int(
            client.count(
                collection_name=name, count_filter=version_filter, exact=True
            ).count
        )
    return counts


def outfit_rows(settings: GoldenSettings) -> list[dict[str, Any]]:
    """코디 목록 — 아이템 개수·역할 요약까지."""
    bucket = settings.require_bucket()
    source_keys = s3io.list_source_keys(bucket, settings.source_prefix())
    manifests = _load_manifests(settings)

    rows: list[dict[str, Any]] = []
    for golden_id, manifest in sorted(manifests.items()):
        items = manifest.get("items", []) or []
        rows.append(
            {
                "golden_id": golden_id,
                "processed": True,
                "schema_version": manifest.get("schema_version", ""),
                "stale_schema": manifest.get("schema_version") != ITEM_SCHEMA_VERSION,
                "pipeline_key": manifest.get("pipeline_key", ""),
                "embedding_version": manifest.get("embedding_version", ""),
                "image_sha256": manifest.get("image_sha256", ""),
                "latency_seconds": manifest.get("latency_seconds"),
                "item_count": len(items),
                "failed_count": int(manifest.get("num_failed", 0)),
                "layer_roles": sorted(
                    {str(row.get("layer_role") or "") for row in items} - {""}
                ),
                "categories": sorted(
                    {str(row.get("category_large") or "") for row in items} - {""}
                ),
                "outfit_point_id": outfit_point_id(
                    settings.dataset_version, golden_id
                ),
            }
        )

    # 아직 처리되지 않은 원본도 목록에 보여준다 — 진행 상황 확인이 목적이다.
    for key in _pending_source_keys(source_keys, manifests):
        rows.append(
            {
                "golden_id": Path(key).stem,
                "processed": False,
                "source_key": key,
                "item_count": 0,
                "failed_count": 0,
                "layer_roles": [],
                "categories": [],
            }
        )
    return rows


def outfit_detail(settings: GoldenSettings, golden_id: str) -> dict[str, Any]:
    """코디 한 장의 아이템 전체 + 미리보기 URL."""
    bucket = settings.require_bucket()
    derived = settings.derived_prefix()
    manifest = s3io.get_json(bucket, s3io.item_manifest_key(derived, golden_id))
    if not isinstance(manifest, dict):
        return {"golden_id": golden_id, "found": False}

    items = []
    for row in manifest.get("items", []) or []:
        entry = {field: row.get(field) for field in ITEM_LIST_FIELDS}
        entry["point_id"] = item_point_id(
            settings.dataset_version, str(row.get("item_key", ""))
        )
        entry["preview_url"] = (
            s3io.presigned_url(bucket, str(row["s3_key"]))
            if row.get("s3_key")
            else None
        )
        items.append(entry)

    return {
        "golden_id": golden_id,
        "found": True,
        "schema_version": manifest.get("schema_version", ""),
        "pipeline_key": manifest.get("pipeline_key", ""),
        "embedding_version": manifest.get("embedding_version", ""),
        "image_sha256": manifest.get("image_sha256", ""),
        "latency_seconds": manifest.get("latency_seconds"),
        "outfit_point_id": outfit_point_id(settings.dataset_version, golden_id),
        "source_preview_url": _source_preview(settings, golden_id),
        "items": items,
    }


def _source_preview(settings: GoldenSettings, golden_id: str) -> str | None:
    """원본 코디 사진 미리보기. 키를 못 찾으면 None."""
    bucket = settings.require_bucket()
    prefix = settings.source_prefix()
    for key in s3io.list_source_keys(bucket, prefix):
        if Path(key).stem == golden_id:
            return s3io.presigned_url(bucket, key)
    return None


def collect_status(settings: GoldenSettings) -> dict[str, Any]:
    """페이지 상단이 쓰는 종합 상태."""
    return {
        "dataset": {
            "name": settings.dataset_name,
            "version": settings.dataset_version,
            "item_pipeline": settings.item_pipeline,
            "anchor_exposable": settings.anchor_exposable,
            "auto_index": settings.auto_index,
            "scan_interval_seconds": settings.scan_interval_seconds,
        },
        "source": _safe("source", lambda: source_progress(settings), {}),
        "run": _safe("run_summary", lambda: run_summary(settings), {}),
        "qdrant": _safe("qdrant", lambda: qdrant_counts(settings), {}),
        "local": _safe("local", lambda: local_run_artifacts(settings), {}),
    }
