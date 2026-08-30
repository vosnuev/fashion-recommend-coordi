"""골든 코디·의상 아이템·승인 원칙을 Qdrant 파생 저장소에 적재한다.

컬렉션 3개를 쓴다.

- `outfit_goldenset` : 코디 1장 = 포인트 1개 (image/text 벡터).
  payload의 `items`가 소속 아이템 포인트로 가는 다리다.
- `goldenset_items`  : 분리된 의상 아이템 1개 = 포인트 1개.
  태그 축을 products/wardrobe와 같게 맞춰 교체 후보 검색이 같은 필터 언어로
  동작한다. payload의 `outfit_point_id`로 코디를 역참조한다.
- `knowledge`        : 사람이 승인한 조건부 원칙 (텍스트 벡터만).

코디 포인트는 쌍대 비교 앵커가 없어도 만든다. 앵커 점수(`human_score` 등)는
있으면 얹는 선택 정보이지 적재 조건이 아니다 — 코디 검색이 앵커 산출보다
먼저 필요하기 때문이다.
"""

from __future__ import annotations

import logging
import re

import os
from pathlib import Path
from typing import Any

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from .artifacts import read_json, read_jsonl, write_json
from .config import GoldenSettings, normalize_dataset_status
from .review_manifest import _taxonomy_styles
from .embedding import build_text_backend, load_embeddings
from .items import load_item_vectors
from .point_ids import point_id

logger = logging.getLogger("golden_set.qdrant_index")

KNOWLEDGE_COLLECTION = "knowledge"
OUTFIT_COLLECTION = "outfit_goldenset"
ITEM_COLLECTION = "goldenset_items"

#: 아이템 payload로 그대로 넘기는 태그 축 (products/wardrobe와 동일)
ITEM_TAG_FIELDS = (
    "category_large",
    "category_small",
    "layer_role",
    "color",
    "pattern",
    "fit",
    "material",
    "sleeve",
    "length",
    "season",
    "style",
)

#: 코디 payload의 아이템 요약에 담는 필드 (교체 대상 고르기에 필요한 최소치)
ITEM_SUMMARY_FIELDS = (
    "item_key",
    "item_name",
    "category_large",
    "category_small",
    "layer_role",
    "color",
    "s3_key",
)


def outfit_point_id(dataset_version: str, golden_id: str) -> str:
    return point_id(f"outfit:{dataset_version}:{golden_id}")


def item_point_id(dataset_version: str, item_key: str) -> str:
    return point_id(f"item:{dataset_version}:{item_key}")


def principle_point_id(dataset_version: str, principle_key: str) -> str:
    return point_id(f"principle:{dataset_version}:{principle_key}")


def index_run(
    *,
    run_dir: Path,
    settings: GoldenSettings,
    text_backend_name: str = "bge",
    allow_draft: bool = False,
    dry_run: bool = False,
    only_missing: bool = False,
    client: QdrantClient | None = None,
) -> dict[str, Any]:
    run_manifest = read_json(run_dir / "run_manifest.json")
    version = str(run_manifest["dataset_version"])
    dataset_status = normalize_dataset_status(
        run_manifest.get("dataset_status"), default=settings.dataset_status
    )
    images = {row["golden_id"]: row for row in read_jsonl(run_dir / "images.jsonl")}
    clusters = {row["golden_id"]: row for row in read_jsonl(run_dir / "clusters.jsonl")}
    analyses = {row["golden_id"]: row for row in read_jsonl(run_dir / "analyses.jsonl")}
    approved_claims = {
        row["golden_id"]: row.get("claims", [])
        for row in read_jsonl(run_dir / "approved_claims.jsonl")
    }
    anchors = {
        row["golden_id"]: row
        for row in read_jsonl(run_dir / "anchor_scores.jsonl")
    }
    principles = read_jsonl(run_dir / "principles.jsonl")

    image_ids, image_vectors = load_embeddings(run_dir / "image_embeddings.npz")
    image_vector_map = {
        golden_id: image_vectors[index] for index, golden_id in enumerate(image_ids)
    }
    image_embedding_model = str(
        read_json(run_dir / "image_embeddings.meta.json")["model"]
    )

    item_rows = read_jsonl(run_dir / "items.jsonl")
    item_vector_map, item_text_map = _load_item_vector_maps(run_dir)
    items_by_golden: dict[str, list[dict[str, Any]]] = {}
    for row in item_rows:
        items_by_golden.setdefault(str(row["golden_id"]), []).append(row)

    text_backend = build_text_backend(settings, text_backend_name)

    # ── 코디 포인트 ──────────────────────────────────────────
    outfit_ids = [
        golden_id
        for golden_id in sorted(images)
        if golden_id in image_vector_map
        and images[golden_id].get("duplicate_kind") != "exact"
    ]
    outfit_texts = [
        _outfit_text(
            images[golden_id],
            analyses.get(golden_id, {}),
            approved_claims.get(golden_id, []),
            items_by_golden.get(golden_id, []),
        )
        for golden_id in outfit_ids
    ]
    outfit_text_vectors = text_backend.encode_texts(outfit_texts)

    outfit_points = []
    for index, golden_id in enumerate(outfit_ids):
        image = images[golden_id]
        anchor = anchors.get(golden_id, {})
        analysis = analyses.get(golden_id, {}).get("result", {})
        look_tags = analysis.get("look_tags", {})
        items = items_by_golden.get(golden_id, [])
        payload: dict[str, Any] = {
            "source": "team_golden_set",
            "dataset_version": version,
            # 실행 단계(PREPARED/EMBEDDED)와 별개인 데이터셋 공개 상태다.
            # manifest에 없던 구형 run은 현재 GOLDEN_DATASET_STATUS를 따른다.
            #
            # 두 키에 같은 값을 쓴다. 리트리버의 상태 필터는 둘 중 하나만 맞아도
            # 통과시키지만(should), 승격 커맨드(set_goldenset_qdrant_status)는 둘
            # 다 쓴다. 여기서 status만 쓰면 승격 뒤 재적재할 때 dataset_status가
            # 통째로 사라져, 두 키를 각각 보는 도구가 서로 다른 답을 준다.
            "status": dataset_status,
            "dataset_status": dataset_status,
            "split": image.get("split", "KNOWLEDGE"),
            "presentation_group": image.get("presentation_group", ""),
            "style": look_tags.get("style")
            or image.get("metadata", {}).get("style", []),
            "season": look_tags.get("season_cues")
            or look_tags.get("season")
            or image.get("metadata", {}).get("season", []),
            "occasion": image.get("metadata", {}).get("occasion", []),
            "rationale_ko": " | ".join(
                str(row.get("statement", ""))
                for row in approved_claims.get(golden_id, [])
            ),
            "golden_id": golden_id,
            "cluster_id": clusters.get(golden_id, {}).get("cluster_id", ""),
            "source_uri": image.get("source_uri", ""),
            "source_bucket": image.get("source_bucket", ""),
            "source_key": image.get("source_key", ""),
            # 노출 여부는 이미지별 사용권과 운영 스위치를 모두 만족해야 참이다.
            "exposable": bool(
                settings.anchor_exposable and image.get("original_exposable", False)
            ),
            "image_embedding_version": image_embedding_model,
            "text_embedding_version": text_backend.name,
            # ── 아이템으로 가는 다리 ──
            "item_count": len(items),
            "item_keys": [str(row["item_key"]) for row in items],
            "item_point_ids": [
                item_point_id(version, str(row["item_key"])) for row in items
            ],
            "item_layer_roles": sorted(
                {str(row.get("layer_role") or "") for row in items} - {""}
            ),
            "item_categories": sorted(
                {str(row.get("category_large") or "") for row in items} - {""}
            ),
            "items": [
                {field: row.get(field) for field in ITEM_SUMMARY_FIELDS}
                | {"point_id": item_point_id(version, str(row["item_key"]))}
                for row in items
            ],
        }
        if anchor:
            payload |= {
                "score_band": anchor.get("score_band", ""),
                "human_score": float(anchor.get("human_score", 0.0)),
                "score_confidence": float(anchor.get("score_confidence", 0.0)),
                "anchor_scope": anchor.get(
                    "anchor_scope", "Q_OVERALL_STYLE_EXECUTION"
                ),
                "human_axis_scores_1_5": anchor.get("human_axis_scores_1_5", {}),
                "reviewer_count": int(anchor.get("reviewer_count", 0)),
                "comparison_count": int(anchor.get("comparison_count", 0)),
                "reviewer_agreement": float(anchor.get("reviewer_agreement", 0.0)),
            }
        outfit_points.append(
            PointStruct(
                id=outfit_point_id(version, golden_id),
                vector={
                    "image": image_vector_map[golden_id].tolist(),
                    "text": outfit_text_vectors[index].tolist(),
                },
                payload=payload,
            )
        )

    # ── 아이템 포인트 ────────────────────────────────────────
    item_points = []
    for row in item_rows:
        key = str(row["item_key"])
        if row.get("status") != "SUCCEEDED":
            continue
        if key not in item_vector_map or key not in item_text_map:
            continue
        golden_id = str(row["golden_id"])
        payload = {
            "source": "team_golden_set",
            "dataset_version": version,
            "item_key": key,
            "item_index": int(row.get("item_index", 0)),
            "item_name": row.get("item_name", ""),
            "label_ko": row.get("label_ko", ""),
            "layer_order": row.get("layer_order"),
            "bbox": row.get("bbox"),
            "s3_bucket": row.get("s3_bucket", ""),
            "s3_key": row.get("s3_key", ""),
            "pipeline_key": row.get("pipeline_key", ""),
            "missing_required": row.get("missing_required", []),
            # ── 코디로 가는 역참조 ──
            "outfit_golden_id": golden_id,
            "outfit_point_id": outfit_point_id(version, golden_id),
            "split": images.get(golden_id, {}).get("split", "KNOWLEDGE"),
            "exposable": bool(
                settings.anchor_exposable
                and images.get(golden_id, {}).get("original_exposable", False)
            ),
            "image_embedding_version": row.get("image_embedding_version", ""),
            "text_embedding_version": row.get("text_embedding_version", ""),
        }
        for field in ITEM_TAG_FIELDS:
            payload[field] = row.get(field) if row.get(field) is not None else ""
        item_points.append(
            PointStruct(
                id=item_point_id(version, key),
                vector={
                    "image": item_vector_map[key].tolist(),
                    "text": item_text_map[key].tolist(),
                },
                payload=payload,
            )
        )

    # ── 원칙 포인트 ──────────────────────────────────────────
    allowed_statuses = {"APPROVED", "DRAFT"} if allow_draft else {"APPROVED"}
    eligible_principles = [
        row for row in principles if row.get("status") in allowed_statuses
    ]
    principle_vectors = text_backend.encode_texts(
        [_principle_text(row) for row in eligible_principles]
    )
    principle_points = [
        PointStruct(
            id=principle_point_id(version, row["principle_key"]),
            vector={"text": principle_vectors[index].tolist()},
            payload={
                "knowledge_type": "golden_principle",
                "dimension": row.get("axis") or row.get("dimension", ""),
                "axis": row.get("axis") or row.get("dimension", ""),
                "status": row.get("status", "DRAFT"),
                "knowledge_role": row.get("knowledge_role", "NEEDS_COUNTEREXAMPLE"),
                "principle_type": row.get("principle_type", "SOFT_PRINCIPLE"),
                "eligible_for_scoring": bool(row.get("eligible_for_scoring", False)),
                "source": "team_golden_set",
                "dataset_version": version,
                "style": _principle_styles(row),
                "principle_key": row["principle_key"],
                "statement": row.get("statement", ""),
                "applies_when": row.get("applies_when", []),
                "exceptions": row.get("exceptions", []),
                "confidence": row.get("confidence", 0.0),
                "support_image_count": int(row.get("support_image_count", 0)),
                "comparison_evidence_count": int(
                    row.get("comparison_evidence_count", 0)
                ),
                "reviewer_count": int(row.get("reviewer_count", 0)),
                "reviewer_agreement": float(row.get("reviewer_agreement", 0.0)),
                "evidence": row.get("evidence", []),
                "embedding_version": text_backend.name,
            },
        )
        for index, row in enumerate(eligible_principles)
    ]

    summary = {
        "dataset_version": version,
        "dataset_status": dataset_status,
        "outfit_points": len(outfit_points),
        "item_points": len(item_points),
        "principle_points": len(principle_points),
        "outfits_with_anchor": sum(1 for gid in outfit_ids if gid in anchors),
        "items_without_vector": len(item_rows) - len(item_points),
        "principle_statuses": sorted(
            {str(row.get("status", "DRAFT")) for row in eligible_principles}
        ),
        "dry_run": dry_run,
        "image_embedding_version": image_embedding_model,
        "text_embedding_version": text_backend.name,
        "exposable": settings.anchor_exposable,
    }
    write_json(run_dir / "qdrant_index_plan.json", summary)
    if dry_run:
        return summary

    if text_backend.name.startswith("deterministic-") or image_embedding_model.startswith(
        "deterministic-"
    ):
        raise RuntimeError(
            "deterministic 테스트 벡터는 실제 Qdrant에 적재할 수 없습니다. "
            "FashionSigLIP·BGE-M3로 prepare/index를 다시 실행하세요."
        )

    qdrant = client or build_client()
    _assert_collections(qdrant)

    # S3에는 있는데 Qdrant에 없는 포인트만 올린다. 적재가 중간에 끊겼거나
    # (네트워크 단절 등) 컬렉션을 지웠다 다시 만든 경우, 아이템 분리를 다시
    # 돌리지 않고 이 경로만으로 복구된다.
    forced = _forced_point_ids(run_dir, version) if only_missing else set()
    written: dict[str, int] = {}
    skipped: dict[str, int] = {}
    for collection, points in (
        (KNOWLEDGE_COLLECTION, principle_points),
        (OUTFIT_COLLECTION, outfit_points),
        (ITEM_COLLECTION, item_points),
    ):
        if not points:
            continue
        target = points
        if only_missing:
            # 이번 실행에서 새로 처리한 코디는 내용이 바뀌었으므로 존재 여부와
            # 무관하게 덮어쓴다. 나머지만 "없는 것"으로 좁힌다.
            candidates = [point for point in points if str(point.id) not in forced]
            present = _existing_point_ids(
                qdrant, collection, [str(point.id) for point in candidates]
            )
            target = [
                point
                for point in points
                if str(point.id) in forced or str(point.id) not in present
            ]
            skipped[collection] = len(points) - len(target)
        written[collection] = len(target)
        if target:
            qdrant.upsert(collection_name=collection, points=target, wait=True)

    summary["upserted"] = written
    if only_missing:
        summary["skipped_existing"] = skipped
        summary["only_missing"] = True
    return summary


def _load_item_vector_maps(
    run_dir: Path,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    path = run_dir / "item_embeddings.npz"
    if not path.exists():
        return {}, {}
    keys, image, text = load_item_vectors(path)
    if not keys:
        return {}, {}
    return (
        {key: image[index] for index, key in enumerate(keys)},
        {key: text[index] for index, key in enumerate(keys)},
    )


def _principle_text(row: dict[str, Any]) -> str:
    applies_when = row.get("applies_when", {})
    if isinstance(applies_when, dict):
        applies_text = ", ".join(
            f"{key}={','.join(str(item) for item in value)}"
            for key, value in applies_when.items()
            if value
        )
    else:
        applies_text = ", ".join(str(item) for item in applies_when)
    return "\n".join(
        [
            f"판단 축: {row.get('axis') or row.get('dimension', '')}",
            f"원칙: {row.get('statement', '')}",
            "적용 조건: " + applies_text,
            "예외: " + ", ".join(row.get("exceptions", [])),
            f"지식 역할: {row.get('knowledge_role', '')}",
        ]
    )


def _outfit_text(
    image: dict[str, Any],
    analysis: dict[str, Any],
    approved_claims: list[dict[str, Any]],
    items: list[dict[str, Any]],
) -> str:
    """코디 텍스트 벡터의 입력.

    승인된 claim이 아직 없어도(검수 전) 아이템 구성만으로 검색이 되도록
    아이템 라벨을 함께 넣는다.
    """
    result = analysis.get("result", {})
    tags = result.get("look_tags", {})
    metadata = image.get("metadata", {})
    item_text = ", ".join(
        " ".join(
            str(row.get(field) or "")
            for field in ("layer_role", "color", "category_small", "item_name")
        ).strip()
        for row in items
    )
    return "\n".join(
        [
            "스타일: "
            + ", ".join(tags.get("style", []) or metadata.get("style", [])),
            "계절 단서: "
            + ", ".join(
                tags.get("season_cues", [])
                or tags.get("season", [])
                or metadata.get("season", [])
            ),
            "상황: " + ", ".join(metadata.get("occasion", [])),
            "색상: " + ", ".join(tags.get("colors", [])),
            f"실루엣: {tags.get('overall_silhouette', '')}",
            "구성 아이템: " + item_text,
            "사람 승인 근거: "
            + " | ".join(str(row.get("statement", "")) for row in approved_claims),
        ]
    )


#: 임베딩 클러스터가 만든 합성 id. 스타일 이름이 아니라 필터 키로 쓸 수 없다.
_SYNTHETIC_CLUSTER = re.compile(r"^cluster-\d+$")


def _principle_styles(row: dict[str, Any]) -> list[str]:
    """payload의 `style`. 리트리버가 **필터 키**로 쓰므로 taxonomy 값이어야 한다.

    LLM이 `style_intents`에 스타일 이름 대신 효과 설명을 채우는 일이 잦다 —
    댄디 클러스터의 원칙에 `['단조로움 피하기', '시각적 대비감 부여']`가 들어가는 식.
    실제로 승인된 원칙 53건에서 고유값 66개 중 taxonomy 안에 있는 건 6개뿐이었다.
    그대로 실으면 "댄디"로 거를 때 댄디 원칙이 하나도 안 잡힌다.

    그래서 스타일 라벨링으로 만든 `cluster_id`를 1순위로 쓴다. 사람이 붙인 라벨에서
    온 값이라 항상 taxonomy 안에 있다. `style_intents`는 taxonomy에 있는 것만 더한다.

    걸러진 문장은 버려지지 않는다 — payload의 `applies_when`에 원본 그대로 남는다.
    필터 키에서만 빠질 뿐이라 설명·표시에는 계속 쓸 수 있다.
    """
    allowed = _taxonomy_styles()
    values: list[str] = []

    cluster = str(row.get("cluster_id", "")).strip()
    if cluster and not _SYNTHETIC_CLUSTER.match(cluster):
        if allowed is None or cluster in allowed:
            values.append(cluster)

    applies_when = row.get("applies_when", {})
    if isinstance(applies_when, dict):
        intents = [str(value).strip() for value in applies_when.get("style_intents", [])]
    else:
        intents = []
        for condition in applies_when:
            if isinstance(condition, str) and condition.startswith("style:"):
                intents.extend(
                    value.strip()
                    for value in condition.removeprefix("style:").split(",")
                    if value.strip()
                )
    if allowed is None:
        # 검증할 수 없으면 intents를 싣지 않는다. 필터 키에 자유문장이 들어가는 쪽이
        # 몇 건 놓치는 것보다 나쁘다 — 조용히 검색에서 빠지기 때문이다.
        if intents:
            logger.warning(
                "taxonomy STYLES를 읽을 수 없어 style_intents %d건을 필터 키에서 "
                "제외한다. applies_when에는 그대로 남는다.",
                len(intents),
            )
        return values
    for value in intents:
        if value and value in allowed and value not in values:
            values.append(value)
    return values


#: retrieve 한 번에 물어보는 포인트 수. Qdrant는 상한이 넉넉하지만 URL·본문
#: 크기와 타임아웃을 감안해 보수적으로 자른다.
_EXISTS_BATCH = 256


def _existing_point_ids(
    client: QdrantClient, collection: str, ids: list[str]
) -> set[str]:
    """주어진 ID 중 컬렉션에 이미 있는 것만 돌려준다.

    retrieve는 없는 ID를 조용히 빼고 돌려주므로, 반환된 집합의 여집합이 곧
    미적재분이다. 벡터·payload는 받지 않아 응답이 가볍다.
    """
    found: set[str] = set()
    for start in range(0, len(ids), _EXISTS_BATCH):
        chunk = ids[start : start + _EXISTS_BATCH]
        records = client.retrieve(
            collection_name=collection,
            ids=chunk,
            with_payload=False,
            with_vectors=False,
        )
        found.update(str(record.id) for record in records)
    return found


def _forced_point_ids(run_dir: Path, dataset_version: str) -> set[str]:
    """이번 실행에서 새로 처리한 코디의 포인트 ID (코디 + 소속 아이템).

    내용이 바뀌었으므로 "이미 있으면 건너뛰기"의 예외다. items.meta.json에
    목록이 없으면(구형 실행) 빈 집합이라 모든 포인트가 존재 검사를 거친다.
    """
    meta_path = run_dir / "items.meta.json"
    # 아이템 단계를 아예 돌리지 않은 run에도 index_run은 걸 수 있다.
    # read_json은 없는 파일에서 예외를 던지므로 먼저 막는다.
    meta = read_json(meta_path) if meta_path.exists() else {}
    fresh = {str(value) for value in meta.get("processed_golden_ids", [])}
    if not fresh:
        return set()
    forced = {outfit_point_id(dataset_version, golden_id) for golden_id in fresh}
    for row in read_jsonl(run_dir / "items.jsonl"):
        if str(row.get("golden_id", "")) in fresh:
            forced.add(item_point_id(dataset_version, str(row["item_key"])))
    return forced


def build_client() -> QdrantClient:
    """환경변수로 Qdrant 클라이언트를 만든다 (index_run과 preflight 공용).

    `port=None`은 필수다. 이걸 빼면 qdrant-client가 URL에 포트가 없을 때 **스킴과
    무관하게 6333**을 붙여서, 443만 열린 엔드포인트(Cloudflare 터널 등)에서 TCP
    타임아웃이 난다. `healthz`는 붙는데 클라이언트만 죽어 원인을 찾기 어렵다.
    api/apps/recommend/services/qdrant.py도 같은 이유로 같은 인자를 쓴다.
    """
    return QdrantClient(
        url=os.getenv("QDRANT_URL", "http://localhost:6333"),
        api_key=os.getenv("QDRANT_API_KEY") or None,
        timeout=int(os.getenv("QDRANT_TIMEOUT", "30")),
        port=None,
        prefer_grpc=False,
    )


def preflight(client: QdrantClient | None = None) -> None:
    """적재 전제 조건을 미리 확인한다 — 비싼 단계를 태우기 전에 부른다.

    아이템 분리는 코디 한 장당 Gemini를 여러 번 호출한다. 그 뒤에야 Qdrant를
    처음 만지면, 접속이 막혀 있거나 컬렉션이 없을 때 수십 분과 API 비용을
    전부 버리고 마지막 줄에서 죽는다. 실제로 그렇게 한 번 날렸다.
    """
    qdrant = client or build_client()
    try:
        qdrant.get_collections()
    except Exception as exc:
        raise RuntimeError(
            f"Qdrant에 접속할 수 없습니다 (QDRANT_URL={os.getenv('QDRANT_URL', '(미설정)')}). "
            "GPU 호스트에서 그 주소로 실제 경로가 있는지 확인하세요 — "
            f"컨테이너 네트워크 내부 이름(http://qdrant:6333)은 다른 호스트에서 닿지 않습니다: {exc}"
        ) from exc
    _assert_collections(qdrant)


def _assert_collections(client: QdrantClient) -> None:
    missing = [
        name
        for name in (KNOWLEDGE_COLLECTION, OUTFIT_COLLECTION, ITEM_COLLECTION)
        if not client.collection_exists(name)
    ]
    if missing:
        raise RuntimeError(
            "Qdrant 컬렉션이 없습니다. 먼저 Django의 init_qdrant를 실행하세요: "
            + ", ".join(missing)
        )


def index_principles_only(
    *,
    run_dir: Path,
    settings: GoldenSettings,
    text_backend_name: str = "bge",
    allow_draft: bool = False,
    dry_run: bool = False,
    client: QdrantClient | None = None,
) -> dict[str, Any]:
    """승인된 원칙만 `knowledge` 컬렉션에 적재한다.

    `index_run`은 코디·아이템·원칙을 한 번에 처리하느라 `images.jsonl`,
    `image_embeddings.npz`, `items.jsonl`을 요구한다. 검수 경로(`review-intake`)로
    만든 run에는 그 산출물이 없다 — 검수에 필요한 것만 들어 있기 때문이다.

    원칙은 텍스트 임베딩만 쓰고 이미지 벡터와 무관하다. 그래서 이미지 파이프라인을
    다 돌리지 않고도 원칙을 검색 가능하게 만들 수 있다. 이 함수는 그 경로다.

    `dataset_version`은 `run_manifest.json`이 없으면 run 디렉터리 이름에서 얻는다.
    포인트 id가 이 값으로 결정되므로, 나중에 같은 run을 본 파이프라인으로 다시
    적재해도 같은 id를 덮어쓴다.
    """
    manifest_path = run_dir / "run_manifest.json"
    if manifest_path.exists():
        run_manifest = read_json(manifest_path)
        version = str(run_manifest["dataset_version"])
        dataset_status = normalize_dataset_status(
            run_manifest.get("dataset_status"), default=settings.dataset_status
        )
    else:
        version = run_dir.name
        dataset_status = normalize_dataset_status(None, default=settings.dataset_status)

    principles = read_jsonl(run_dir / "principles.jsonl")
    allowed_statuses = {"APPROVED", "DRAFT"} if allow_draft else {"APPROVED"}
    eligible = [row for row in principles if row.get("status") in allowed_statuses]
    if not eligible:
        raise ValueError(
            "적재할 원칙이 없습니다. approve를 먼저 실행했는지, "
            "knowledge_role이 NEEDS_COUNTEREXAMPLE로만 남지 않았는지 확인하세요."
        )

    text_backend = build_text_backend(settings, text_backend_name)
    vectors = text_backend.encode_texts([_principle_text(row) for row in eligible])
    points = [
        PointStruct(
            id=principle_point_id(version, row["principle_key"]),
            vector={"text": vectors[index].tolist()},
            payload={
                "knowledge_type": "golden_principle",
                "dimension": row.get("axis") or row.get("dimension", ""),
                "axis": row.get("axis") or row.get("dimension", ""),
                "status": row.get("status", "DRAFT"),
                "knowledge_role": row.get("knowledge_role", "NEEDS_COUNTEREXAMPLE"),
                "principle_type": row.get("principle_type", "SOFT_PRINCIPLE"),
                "eligible_for_scoring": bool(row.get("eligible_for_scoring", False)),
                "source": "team_golden_set",
                "dataset_version": version,
                "dataset_status": dataset_status,
                "style": _principle_styles(row),
                "principle_key": row["principle_key"],
                "statement": row.get("statement", ""),
                "applies_when": row.get("applies_when", []),
                "exceptions": row.get("exceptions", []),
                "confidence": row.get("confidence", 0.0),
                "support_image_count": int(row.get("support_image_count", 0)),
                "comparison_evidence_count": int(
                    row.get("comparison_evidence_count", 0)
                ),
                "reviewer_count": int(row.get("reviewer_count", 0)),
                "reviewer_agreement": float(row.get("reviewer_agreement", 0.0)),
                "evidence": row.get("evidence", []),
                "embedding_version": text_backend.name,
            },
        )
        for index, row in enumerate(eligible)
    ]

    summary: dict[str, Any] = {
        "dataset_version": version,
        "dataset_status": dataset_status,
        "principle_points": len(points),
        "principle_statuses": sorted(
            {str(row.get("status", "DRAFT")) for row in eligible}
        ),
        "knowledge_roles": sorted(
            {str(row.get("knowledge_role", "")) for row in eligible}
        ),
        "styles": sorted({s for row in eligible for s in _principle_styles(row)}),
        "text_embedding_version": text_backend.name,
        "dry_run": dry_run,
        "only": "knowledge",
    }
    write_json(run_dir / "qdrant_index_plan.knowledge.json", summary)
    if dry_run:
        return summary

    if text_backend.name.startswith("deterministic-"):
        raise RuntimeError(
            "deterministic 테스트 벡터는 실제 Qdrant에 적재할 수 없습니다. "
            "--text-backend bge로 다시 실행하세요."
        )

    qdrant = client or build_client()
    _assert_collections(qdrant)
    qdrant.upsert(collection_name=KNOWLEDGE_COLLECTION, points=points, wait=True)
    summary["upserted"] = {KNOWLEDGE_COLLECTION: len(points)}
    return summary
