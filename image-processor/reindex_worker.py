"""기존 옷장 크롭 이미지와 DB 태그만 사용해 벡터를 다시 생성한다."""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Mapping
from pathlib import Path

import config
from pipeline.embedding import SigLIPBgeEmbedder, caption_from_tags
from services import callback, reindex_queue, s3io

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("reindex-worker")


def normalize_payload(payload: Mapping[str, object]) -> dict[str, object]:
    source = payload.get("source")
    tags = payload.get("tags")
    if not isinstance(source, Mapping) or not isinstance(tags, Mapping):
        raise ValueError("재인덱싱 payload의 source와 tags가 필요합니다.")

    normalized = {
        "item_id": str(payload.get("item_id", "")),
        "user_id": payload.get("user_id"),
        "source_bucket": str(source.get("bucket", "")),
        "source_key": str(source.get("key", "")),
        "source_updated_at": str(payload.get("source_updated_at", "")),
        "embedding_version": str(payload.get("embedding_version", "")),
        "tags": dict(tags),
        "callback_url": str(payload.get("callback_url", "")),
    }
    required = (
        "item_id",
        "source_bucket",
        "source_key",
        "source_updated_at",
        "embedding_version",
        "callback_url",
    )
    if any(not normalized[field] for field in required):
        raise ValueError("재인덱싱 payload의 필수 문자열 값이 비어 있습니다.")
    if normalized["embedding_version"] != config.EMBEDDING_VERSION:
        raise ValueError("큐 작업과 워커의 임베딩 버전이 다릅니다.")
    return normalized


def build_success_callback(job: Mapping[str, object], embedder) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temp_dir:
        local_path = Path(temp_dir) / "wardrobe-item"
        s3io.download(
            str(job["source_bucket"]),
            str(job["source_key"]),
            str(local_path),
        )
        image_bytes = local_path.read_bytes()

    image_vector = embedder.embed_image(image_bytes)
    text_vector = embedder.embed_text(caption_from_tags(dict(job["tags"])))
    if not image_vector or not text_vector:
        raise RuntimeError("이미지 또는 텍스트 임베딩 생성에 실패했습니다.")
    return {
        "item_id": job["item_id"],
        "status": "success",
        "source_updated_at": job["source_updated_at"],
        "embedding_version": embedder.version,
        "error": "",
        "image_vector": image_vector,
        "text_vector": text_vector,
    }


def failed_callback(job: Mapping[str, object], error: str) -> dict[str, object]:
    return {
        "item_id": job["item_id"],
        "status": "failed",
        "source_updated_at": job["source_updated_at"],
        "embedding_version": job["embedding_version"],
        "error": error[:2000],
        "image_vector": [],
        "text_vector": [],
    }


def main() -> None:
    embedder = SigLIPBgeEmbedder()
    logger.info(
        "옷장 재인덱싱 워커 시작 (version=%s queue=%s)",
        embedder.version,
        config.REINDEX_PENDING_KEY,
    )
    reindex_queue.recover_stale()

    while True:
        raw = reindex_queue.fetch()
        if raw is None:
            continue
        item_id = "?"
        job: dict[str, object] = {}
        try:
            job = normalize_payload(json.loads(raw))
            item_id = str(job["item_id"])
            result = build_success_callback(job, embedder)
            callback.post(str(job["callback_url"]), result)
            reindex_queue.ack(raw, item_id)
            logger.info("옷장 재인덱싱 완료: item=%s", item_id)
        except Exception as exc:  # noqa: BLE001 — 아이템 단위 재시도 격리
            logger.exception("옷장 재인덱싱 실패: item=%s", item_id)
            error = f"{type(exc).__name__}: {exc}"
            if reindex_queue.retry_or_dead(raw, item_id, error) and job:
                try:
                    callback.post(
                        str(job.get("callback_url", "")),
                        failed_callback(job, error),
                    )
                except Exception:  # noqa: BLE001 — dead 원본은 Redis에 보존된다
                    logger.exception("옷장 재인덱싱 최종 실패 콜백 실패")


if __name__ == "__main__":
    main()
