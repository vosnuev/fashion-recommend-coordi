"""S3 골든셋을 단일 출처로 Qdrant에 전량 반영한다. (독립 실행 스크립트)

    python -m ml.golden_set.sync_qdrant              # 전량 upsert
    python -m ml.golden_set.sync_qdrant --limit 5    # 앞의 5건만 (시험용)
    python -m ml.golden_set.sync_qdrant --dry-run    # 계획만 출력

**실행 위치: GPU 서버.** 코디 이미지 임베딩(FashionSigLIP)과 텍스트 임베딩
(BGE-M3)을 계산하므로 모델이 필요하다. CPU에서도 돌지만 느리다.

runner.py와 다른 점이 핵심이다. 저쪽은 로컬 run 디렉터리(manifest·npz·클러스터)를
읽어 그 실행분만 적재한다. 이 스크립트는 **S3만 읽고 골든셋 전체를 매번 다시
쓴다.** 태그를 새로 붙였거나 payload 구성을 바꿨을 때, 어느 실행에서 만들어졌는지와
무관하게 전량을 같은 상태로 맞추기 위한 도구다.

아이템 벡터는 S3의 item_vectors.npz를 그대로 재사용한다 — 아이템 분리·임베딩은
이미 끝났고 가장 비싼 단계라 다시 계산할 이유가 없다. 코디 이미지 벡터만 원본
사진에서 새로 만든다 (S3에 저장된 적이 없다).
"""

from __future__ import annotations

import argparse
import io
import logging
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from . import s3io
from .config import GoldenSettings, load_project_env
from .embedding import build_image_backend, build_text_backend
from .items import VECTOR_OBJECT_NAME
from .review_publish import ReviewIndex, load_review
from .qdrant_index import (
    ITEM_COLLECTION,
    ITEM_SUMMARY_FIELDS,
    ITEM_TAG_FIELDS,
    OUTFIT_COLLECTION,
    build_client,
    item_point_id,
    outfit_point_id,
    preflight,
)
from .tag_manifests import TAG_FIELD, find_manifests

logger = logging.getLogger("golden_set.sync_qdrant")

#: 한 번에 upsert할 포인트 수. 너무 크면 요청 본문이 수십 MB가 된다.
BATCH = 64


def outfit_text(manifest: dict[str, Any], tags: dict[str, Any]) -> str:
    """코디 텍스트 벡터의 재료.

    태그와 아이템 구성을 이어 붙인다. 검색 질의도 자연어라 같은 표현 공간에
    두는 편이 유리하다.
    """
    pieces = [
        " ".join(tags.get("style", [])),
        " ".join(tags.get("season", [])),
        " ".join(tags.get("occasion", [])),
        tags.get("presentation_group", ""),
    ]
    for item in manifest.get("items", []):
        pieces.append(
            " ".join(
                str(item.get(field, ""))
                for field in ("category_large", "category_small", "color", "fit", "material")
                if item.get(field)
            )
        )
    return " / ".join(piece for piece in pieces if piece.strip())


def load_item_vectors(bucket: str, prefix: str) -> dict[str, dict[str, list[float]]]:
    """S3의 item_vectors.npz를 읽는다. 없으면 빈 dict (아이템 포인트를 건너뛴다)."""
    try:
        raw = s3io.get_bytes(bucket, f"{prefix}/{VECTOR_OBJECT_NAME}")
    except Exception:  # noqa: BLE001 — 벡터가 없어도 코디 포인트는 만든다
        return {}
    with np.load(io.BytesIO(raw), allow_pickle=False) as data:
        ids = [str(value) for value in data["ids"].tolist()]
        image = np.asarray(data["image"], dtype=np.float32)
        text = np.asarray(data["text"], dtype=np.float32)
    return {
        key: {"image": image[index].tolist(), "text": text[index].tolist()}
        for index, key in enumerate(ids)
    }


def build_points(
    *,
    manifest: dict[str, Any],
    manifest_key: str,
    settings: GoldenSettings,
    image_vector: list[float],
    text_vector: list[float],
    item_vectors: dict[str, dict[str, list[float]]],
    image_model: str,
    text_model: str,
    review: ReviewIndex | None = None,
) -> tuple[PointStruct, list[PointStruct]]:
    version = settings.dataset_version
    golden_id = str(manifest["golden_id"])
    tags = manifest.get(TAG_FIELD) or {}
    items = list(manifest.get("items", []))
    bucket = settings.require_bucket()
    exposable = bool(settings.anchor_exposable)

    # 아이템 포인트를 먼저 만들어 코디 payload가 그 id를 담을 수 있게 한다.
    item_points: list[PointStruct] = []
    summaries: list[dict[str, Any]] = []
    for item in items:
        item_key = str(item.get("item_key", ""))
        summary = {field: item.get(field, "") for field in ITEM_SUMMARY_FIELDS}
        summary["point_id"] = item_point_id(version, item_key)
        summaries.append(summary)

        vectors = item_vectors.get(item_key)
        if not vectors:
            # 벡터가 없으면 검색이 안 되므로 포인트를 만들지 않는다. 코디
            # payload의 items에는 남아 화면 구성에는 쓰인다.
            continue
        payload = {field: item.get(field, "") for field in ITEM_TAG_FIELDS}
        payload.update(
            {
                "source": "team_golden_set",
                "dataset_version": version,
                "split": manifest.get("split", "KNOWLEDGE"),
                "exposable": exposable,
                "item_key": item_key,
                "item_name": item.get("item_name", ""),
                "s3_bucket": item.get("s3_bucket", bucket),
                "s3_key": item.get("s3_key", ""),
                "outfit_golden_id": golden_id,
                "outfit_point_id": outfit_point_id(version, golden_id),
                "image_embedding_version": item.get("image_embedding_version", ""),
                "text_embedding_version": item.get("text_embedding_version", ""),
            }
        )
        item_points.append(
            PointStruct(
                id=summary["point_id"],
                vector={"image": vectors["image"], "text": vectors["text"]},
                payload=payload,
            )
        )

    source_key = str(manifest.get("source_key", ""))
    # 사람 검수 결과. golden_id가 아니라 sha256으로 찾는다 — 검수표의 정규화 이름과
    # S3의 원본 파일명 사이에는 규칙이 없다(review_publish 모듈 설명 참고).
    human = (
        review.payload_for(str(manifest.get("image_sha256", "")))
        if review is not None
        else {}
    )
    outfit = PointStruct(
        id=outfit_point_id(version, golden_id),
        vector={"image": image_vector, "text": text_vector},
        payload={
            "source": "team_golden_set",
            "dataset_version": version,
            # status·dataset_status를 함께 쓰는 이유는 qdrant_index.py 참고.
            "status": settings.dataset_status,
            "dataset_status": settings.dataset_status,
            "split": manifest.get("split", "KNOWLEDGE"),
            "golden_id": golden_id,
            # ── 리트리버가 필터·점수에 쓰는 축 ──
            "presentation_group": tags.get("presentation_group", ""),
            "style": tags.get("style", []),
            "season": tags.get("season", []),
            "occasion": tags.get("occasion", []),
            "tag_confidence": tags.get("confidence", 0),
            "tag_schema_version": tags.get("schema_version", ""),
            # ── 사람 검수 (없으면 키 자체가 없다) ──
            # 리트리버는 human_score를 유사도 기준선으로 쓴다. 0을 적어 두면
            # "미검수"와 "최하점"이 같은 값이 되므로 없을 때는 넣지 않는다.
            **human,
            # ── 원본 참조 ──
            "source_bucket": bucket,
            "source_key": source_key,
            "source_uri": s3io.s3_uri(bucket, source_key) if source_key else "",
            "exposable": exposable,
            # ── 아이템으로 가는 다리 ──
            "item_count": len(items),
            "item_keys": [summary["item_key"] for summary in summaries],
            "item_point_ids": [point.id for point in item_points],
            "item_layer_roles": sorted(
                {str(i.get("layer_role", "")) for i in items if i.get("layer_role")}
            ),
            "item_categories": sorted(
                {str(i.get("category_large", "")) for i in items if i.get("category_large")}
            ),
            "items": summaries,
            "image_embedding_version": image_model,
            "text_embedding_version": text_model,
            "manifest_key": manifest_key,
        },
    )
    return outfit, item_points


def _upsert(client: QdrantClient, collection: str, points: list[PointStruct]) -> None:
    for start in range(0, len(points), BATCH):
        client.upsert(
            collection_name=collection, points=points[start : start + BATCH], wait=True
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="S3 골든셋 전체를 Qdrant에 반영한다 (GPU 서버에서 실행)"
    )
    parser.add_argument("--limit", type=int, help="처리할 최대 코디 수 (시험용)")
    parser.add_argument("--dry-run", action="store_true", help="적재 없이 계획만 출력")
    parser.add_argument(
        "--image-backend", choices=["fashion", "deterministic"], default="fashion"
    )
    parser.add_argument("--text-backend", choices=["bge", "deterministic"], default="bge")
    parser.add_argument(
        "--require-tags",
        action="store_true",
        help="태그가 없는 코디는 건너뛴다 (기본은 빈 태그로라도 적재)",
    )
    parser.add_argument(
        "--human-review",
        type=Path,
        help="사람 검수 결과 JSON 경로. 생략하면 S3에서 읽는다 (개발용 우회)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    load_project_env()
    settings = GoldenSettings.from_env()
    bucket = settings.require_bucket()
    derived = settings.derived_prefix()

    review = load_review(settings=settings, local_path=args.human_review)

    manifest_keys = find_manifests(bucket, derived)
    if args.limit:
        manifest_keys = manifest_keys[: args.limit]
    logger.info("코디 %d건 (s3://%s/%s)", len(manifest_keys), bucket, derived)
    if not manifest_keys:
        logger.warning(
            "manifest가 없습니다. GOLDEN_S3_OUTPUT_PREFIX/GOLDEN_DATASET_VERSION을 "
            "확인하세요."
        )
        return

    client: QdrantClient | None = None
    if not args.dry_run:
        client = build_client()
        # 임베딩을 다 계산한 뒤 마지막에 죽으면 그 시간을 통째로 버린다.
        preflight(client)
        logger.info("Qdrant 선검사 통과")

    image_backend = build_image_backend(settings, args.image_backend)
    text_backend = build_text_backend(settings, args.text_backend)

    # 모아 두었다가 마지막에 한 번에 올리지 않는다. 코디 수만큼 임베딩을 메모리에
    # 쌓게 되고, 무엇보다 중간에 끊기면 그때까지 계산한 게 통째로 날아간다.
    # 배치가 차면 바로 올려 진행분이 남게 한다 — point id가 결정적이라 다시
    # 돌려도 같은 자리에 덮어쓰므로 중복이 생기지 않는다.
    pending_outfits: list[PointStruct] = []
    pending_items: list[PointStruct] = []
    written_outfits = written_items = 0
    untagged = skipped = matched_reviews = 0

    def flush(force: bool = False) -> None:
        nonlocal written_outfits, written_items
        if args.dry_run:
            return
        if force or len(pending_outfits) >= BATCH:
            if pending_outfits:
                _upsert(client, OUTFIT_COLLECTION, pending_outfits)
                written_outfits += len(pending_outfits)
                pending_outfits.clear()
            if pending_items:
                _upsert(client, ITEM_COLLECTION, pending_items)
                written_items += len(pending_items)
                pending_items.clear()
            logger.info("적재 진행: 코디 %d / 아이템 %d", written_outfits, written_items)

    with tempfile.TemporaryDirectory() as temp:
        workdir = Path(temp)
        for index, key in enumerate(manifest_keys, start=1):
            manifest = s3io.get_json(bucket, key)
            if not manifest or not manifest.get("golden_id"):
                logger.warning("건너뜀 (읽을 수 없음): %s", key)
                skipped += 1
                continue
            golden_id = str(manifest["golden_id"])
            tags = manifest.get(TAG_FIELD) or {}
            if not tags:
                untagged += 1
                if args.require_tags:
                    skipped += 1
                    continue

            source_key = str(manifest.get("source_key", ""))
            if not source_key:
                logger.warning("건너뜀 (source_key 없음): %s", golden_id)
                skipped += 1
                continue

            local = workdir / Path(source_key).name
            s3io.download(bucket, source_key, local)
            image_vector = image_backend.encode_paths([local])[0].tolist()
            local.unlink(missing_ok=True)
            text_vector = text_backend.encode_texts(
                [outfit_text(manifest, tags)]
            )[0].tolist()

            outfit, items = build_points(
                manifest=manifest,
                manifest_key=key,
                settings=settings,
                image_vector=image_vector,
                text_vector=text_vector,
                item_vectors=load_item_vectors(
                    bucket, s3io.image_prefix(derived, golden_id)
                ),
                image_model=image_backend.name,
                text_model=text_backend.name,
                review=review,
            )
            if outfit.payload.get("human_review_golden_id"):
                matched_reviews += 1
            pending_outfits.append(outfit)
            pending_items.extend(items)
            logger.info(
                "[%d/%d] %s: 아이템 %d개 / 성별표현 %s",
                index, len(manifest_keys), golden_id, len(items),
                tags.get("presentation_group") or "(미분류)",
            )
            flush()

        flush(force=True)

    if review and not matched_reviews:
        # 조인 키가 어긋나면 한 건도 안 붙는데 에러가 나지 않는다. 이 경고가
        # 없으면 "적재는 성공했는데 점수가 없다"를 한참 뒤에 발견하게 된다.
        logger.warning(
            "사람 검수 결과 %d건을 읽었지만 sha256이 일치하는 코디가 없습니다. "
            "발행에 쓴 metadata CSV가 이 S3 데이터셋과 같은 원본인지 확인하세요.",
            len(review),
        )
    elif review:
        logger.info(
            "사람 검수 반영: 코디 %d건 (읽은 검수 %d건)", matched_reviews, len(review)
        )
    logger.info(
        "완료: 코디 %d / 아이템 %d / 태그 없음 %d / 건너뜀 %d",
        written_outfits or len(pending_outfits),
        written_items or len(pending_items),
        untagged,
        skipped,
    )
    if untagged:
        logger.warning(
            "태그가 없는 코디 %d건. 성별이 등록된 사용자에게는 이 코디들이 검색에서 "
            "걸러집니다 — API 서버에서 태깅 스크립트를 먼저 돌리세요.",
            untagged,
        )
    if args.dry_run:
        logger.info("dry-run: 적재하지 않았습니다.")


if __name__ == "__main__":
    main()
