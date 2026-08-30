"""ETRI 패션 코디 데이터셋(11번) → Marqo Fashion SigLIP 임베딩 → Qdrant 적재.

실행 (indexer/ 디렉터리 기준):
    python -m old.fashion_indexer                 # 전체 적재
    python -m old.fashion_indexer --limit 32      # 스모크 테스트
    python -m old.fashion_indexer --recreate      # 컬렉션 재생성 후 적재
    python -m old.fashion_indexer --category BL   # 특정 카테고리만

point id가 item_id 기반 uuid5라 재실행해도 중복 없이 덮어써진다.
"""

from __future__ import annotations

import argparse
import io
import logging
import sys

import boto3
from PIL import Image
from qdrant_client.models import PointStruct
from tqdm import tqdm

from util.embedder import FashionSigLIPEmbedder

from . import config
from .etri_dataset import FashionItem, download_images, load_items
from .qdrant_loader import ensure_collection, make_client, point_id, upsert_points

logger = logging.getLogger("indexer")

DATASET_TAG = "etri_fashion_poc_11"


def build_payload(item: FashionItem) -> dict:
    from .etri_dataset import ASPECT_LABELS

    return {
        "dataset": DATASET_TAG,
        "item_id": item.item_id,
        "part": item.part,
        "part_ko": item.part_ko,
        "category": item.category,
        "category_ko": item.category_ko,
        "s3_bucket": config.S3_BUCKET,
        "s3_key": item.image_key,
        "features": {
            ASPECT_LABELS[code]: descs for code, descs in item.features.items()
        },
        "text": item.embedding_text() if item.features else None,
    }


def index_batch(
    batch: list[FashionItem],
    embedder: FashionSigLIPEmbedder,
    s3_client,
) -> tuple[list[PointStruct], int]:
    """한 배치를 임베딩해 PointStruct 목록과 실패 수를 반환한다."""
    raw_images = download_images(batch, config.DOWNLOAD_WORKERS, s3_client)

    valid: list[tuple[FashionItem, Image.Image]] = []
    failed = 0
    for item, raw in zip(batch, raw_images):
        if raw is None:
            failed += 1
            continue
        try:
            valid.append((item, Image.open(io.BytesIO(raw)).convert("RGB")))
        except Exception:
            logger.exception("이미지 디코딩 실패: %s", item.image_key)
            failed += 1

    if not valid:
        return [], failed

    image_vectors = embedder.encode_images([img for _, img in valid])

    # 텍스트는 mdata가 있는 아이템만 임베딩한다
    text_indices = [i for i, (item, _) in enumerate(valid) if item.features]
    text_vectors = {}
    if text_indices:
        encoded = embedder.encode_texts(
            [valid[i][0].embedding_text() for i in text_indices]
        )
        text_vectors = dict(zip(text_indices, encoded))

    points = []
    for i, (item, _) in enumerate(valid):
        vectors = {"image": image_vectors[i].tolist()}
        if i in text_vectors:
            vectors["text"] = text_vectors[i].tolist()
        points.append(
            PointStruct(
                id=point_id(DATASET_TAG, item.item_id),
                vector=vectors,
                payload=build_payload(item),
            )
        )
    return points, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="ETRI 11번 데이터셋 Qdrant 적재")
    parser.add_argument("--limit", type=int, default=0, help="적재 상한 (0=전체)")
    parser.add_argument("--batch-size", type=int, default=config.BATCH_SIZE)
    parser.add_argument("--recreate", action="store_true", help="컬렉션 삭제 후 재생성")
    parser.add_argument("--category", help="특정 카테고리 코드만 적재 (예: BL)")
    args = parser.parse_args()

    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    s3_client = boto3.client("s3")
    items = load_items(s3_client)
    if args.category:
        items = [i for i in items if i.category == args.category.upper()]
    if args.limit:
        items = items[: args.limit]
    if not items:
        logger.error("적재할 아이템이 없습니다.")
        return 1

    embedder = FashionSigLIPEmbedder(
        model_id=config.EMBED_MODEL_ID,
        device=config.DEVICE,
    )
    qdrant = make_client()
    ensure_collection(qdrant, embedder.dim, recreate=args.recreate)

    total_indexed = total_failed = 0
    batches = [
        items[i : i + args.batch_size] for i in range(0, len(items), args.batch_size)
    ]
    for batch in tqdm(batches, desc="indexing", unit="batch"):
        points, failed = index_batch(batch, embedder, s3_client)
        upsert_points(qdrant, points)
        total_indexed += len(points)
        total_failed += failed

    logger.info(
        "적재 완료: %d개 성공 / %d개 실패 → %s (%s)",
        total_indexed,
        total_failed,
        config.QDRANT_COLLECTION,
        config.QDRANT_URL,
    )
    return 0 if total_indexed else 1


if __name__ == "__main__":
    sys.exit(main())
