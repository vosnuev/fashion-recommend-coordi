"""골든 코디 사진 → 의상 아이템 분리·태깅·임베딩.

아이템 열거기를 새로 만들지 않고 image-processor의 `WardrobePipeline`을
그대로 재사용한다. 구현 선택은 `GOLDEN_ITEM_PIPELINE`(기본은 image-processor의
`WORKER_PIPELINE`) 환경변수가 하므로, image-processor에 `sam3-crop`이 등록되면
이 파일을 고치지 않고 값만 바꿔 교체된다.

이렇게 해야 골든 아이템과 옷장 아이템이 같은 taxonomy 태그·같은 임베딩 모델을
쓴다. "골든 코디의 상의를 옷장 아이템으로 교체"가 성립하려면 두 저장소가 같은
벡터 공간과 같은 필터 언어를 공유해야 한다.

멱등 규칙은 image-processor와 같다. `{derived}/{golden_id}/manifest.json`이
이미 있으면 그 이미지는 건너뛰고 저장된 결과를 재사용한다 — Gemini 이미지
편집이 아이템당 1회씩 도는 가장 비싼 단계라서 재실행 비용이 크다.
"""

from __future__ import annotations

import io
import mimetypes
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import s3io
from .artifacts import read_jsonl, write_json, write_jsonl
from .config import GoldenSettings

#: 저장된 아이템 결과를 재사용해도 되는지 판정하는 계약 버전.
#: 아이템 스키마가 바뀌면 올려서 전량 재처리를 유도한다.
ITEM_SCHEMA_VERSION = "golden-items-v1"

VECTOR_OBJECT_NAME = "item_vectors.npz"

#: 태깅 결과에서 그대로 옮겨 담는 필드 (apps.wardrobe.WardrobeItem과 동일 축)
TAG_FIELDS = (
    "item_name",
    "category_large",
    "category_small",
    "color",
    "pattern",
    "fit",
    "material",
    "sleeve",
    "length",
    "layer_role",
)
TAG_LIST_FIELDS = ("season", "style", "usage")


def ensure_image_processor_importable(settings: GoldenSettings) -> None:
    """image-processor를 import 경로에 올린다.

    image-processor의 모듈들이 최상위 `config`를 import하는 구조라 패키지로
    가져올 수 없다. 대신 디렉터리를 sys.path에 올려 `import config`가
    image-processor/config.py를 가리키게 한다. golden_set은 `.config`를
    상대 import로 쓰므로 이름이 겹치지 않는다.
    """
    path = settings.image_processor_path
    if not (path / "pipeline").is_dir():
        raise FileNotFoundError(
            "image-processor를 찾을 수 없습니다: "
            f"{path} (GOLDEN_IMAGE_PROCESSOR_PATH로 지정하세요)"
        )
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def build_item_pipeline(settings: GoldenSettings):
    ensure_image_processor_importable(settings)
    from pipeline import build_pipeline  # image-processor

    return build_pipeline(settings.item_pipeline)


def item_key(golden_id: str, index: int) -> str:
    return f"{golden_id}#{index:03d}"


def _mime_for(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "image/jpeg"


def _item_row(
    *,
    golden_id: str,
    index: int,
    processed: Any,
    bucket: str,
    s3_key: str,
    pipeline_key: str,
    embedding_version: str,
) -> dict[str, Any]:
    tags = dict(processed.tags or {})
    missing = tags.pop("_missing_required", []) or []
    enum = processed.enum
    row: dict[str, Any] = {
        "golden_id": golden_id,
        "item_index": index,
        "item_key": item_key(golden_id, index),
        "s3_bucket": bucket,
        "s3_key": s3_key,
        "label_ko": getattr(enum, "label_ko", ""),
        "descriptor_en": getattr(enum, "descriptor_en", ""),
        "view_angle": getattr(enum, "view_angle", ""),
        "occluded_by": list(getattr(enum, "occluded_by", []) or []),
        "bbox": getattr(enum, "bbox", None),
        "missing_required": list(missing),
        "pipeline_key": pipeline_key,
        # 아이템 이미지·캡션은 같은 임베더 쌍이 만들므로 라벨도 같다.
        "image_embedding_version": embedding_version,
        "text_embedding_version": embedding_version,
        "status": "SUCCEEDED" if processed.error is None else "FAILED",
        "error_message": processed.error or "",
        "layer_order": tags.get("layer_order"),
    }
    for field in TAG_FIELDS:
        row[field] = tags.get(field) or ""
    for field in TAG_LIST_FIELDS:
        row[field] = list(tags.get(field) or [])
    # 대분류는 태깅이 비면 열거 단계 값으로 메운다 (필터 축이라 비면 곤란하다).
    if not row["category_large"]:
        row["category_large"] = getattr(enum, "category_large", "") or ""
    return row


def _put_vectors(
    *,
    bucket: str,
    key: str,
    keys: list[str],
    image_vectors: list[list[float]],
    text_vectors: list[list[float]],
) -> None:
    buffer = io.BytesIO()
    np.savez_compressed(
        buffer,
        ids=np.asarray(keys),
        image=np.asarray(image_vectors, dtype=np.float32),
        text=np.asarray(text_vectors, dtype=np.float32),
    )
    s3io.put_bytes(bucket, key, buffer.getvalue(), "application/octet-stream")


def _get_vectors(bucket: str, key: str) -> dict[str, dict[str, list[float]]]:
    try:
        raw = s3io.get_bytes(bucket, key)
    except Exception:  # noqa: BLE001 — 벡터가 없으면 임베딩 없는 아이템으로 취급
        return {}
    with np.load(io.BytesIO(raw), allow_pickle=False) as data:
        ids = [str(value) for value in data["ids"].tolist()]
        image = np.asarray(data["image"], dtype=np.float32)
        text = np.asarray(data["text"], dtype=np.float32)
    return {
        key_: {"image": image[index].tolist(), "text": text[index].tolist()}
        for index, key_ in enumerate(ids)
    }


def _resolve_embedding_version(pipeline: Any, override: str) -> str:
    """아이템 임베딩 버전 라벨.

    override(GOLDEN_EMBEDDING_VERSION)가 있으면 그 값을, 없으면 파이프라인
    임베더의 값을 쓴다. 후자는 image-processor의 WARDROBE_EMBEDDING_VERSION이라
    골든 아이템에 옷장 이름표가 찍힌다 — 그래서 override를 권장한다.
    """
    if override:
        return override
    return getattr(getattr(pipeline, "embedder", None), "version", "") or ""


def _process_one(
    *,
    image: dict[str, Any],
    bucket: str,
    derived: str,
    pipeline: Any,
    embedding_version: str,
) -> tuple[dict[str, Any], dict[str, dict[str, list[float]]]]:
    golden_id = str(image["golden_id"])
    local = Path(str(image["local_path"]))
    payload = local.read_bytes()
    started = time.perf_counter()
    # image-processor의 process()는 (처리한 아이템, 제외한 아이템) 튜플을 준다.
    # 룩북 기능이 들어오면서 반환형이 리스트 → 튜플로 바뀌었고, 그때 이 줄이
    # 조용히 깨졌다 (AttributeError: 'list' object has no attribute 'image_png').
    # 골든셋은 제외 규칙을 쓰지 않으므로 두 번째 값은 버린다.
    result = pipeline.process(payload, _mime_for(local))
    processed_items = result[0] if isinstance(result, tuple) else result
    if processed_items and not hasattr(processed_items[0], "image_png"):
        # 또 바뀌면 루프 한복판의 AttributeError 대신 여기서 이름을 대고 멈춘다.
        raise TypeError(
            "image-processor pipeline.process()의 반환 형태가 또 바뀌었습니다: "
            f"{type(processed_items[0]).__name__}. ml/golden_set/items.py의 "
            "언패킹을 맞추세요."
        )

    rows: list[dict[str, Any]] = []
    vectors: dict[str, dict[str, list[float]]] = {}
    for index, processed in enumerate(processed_items):
        s3_key = ""
        if processed.image_png:
            s3_key = s3io.item_image_key(derived, golden_id, index)
            s3io.put_bytes(bucket, s3_key, processed.image_png, "image/png")
        row = _item_row(
            golden_id=golden_id,
            index=index,
            processed=processed,
            bucket=bucket,
            s3_key=s3_key,
            pipeline_key=pipeline.key,
            embedding_version=embedding_version,
        )
        rows.append(row)
        if processed.image_vector and processed.text_vector:
            vectors[row["item_key"]] = {
                "image": list(processed.image_vector),
                "text": list(processed.text_vector),
            }

    manifest = {
        "golden_id": golden_id,
        # 원본 키를 남긴다. metadata CSV가 golden_id를 파일명과 다르게 지정하면
        # 파일명만으로는 "이 원본이 처리됐는지"를 판정할 수 없다.
        "source_key": str(image.get("source_key", "")),
        "image_sha256": str(image["image_sha256"]),
        "schema_version": ITEM_SCHEMA_VERSION,
        "pipeline_key": pipeline.key,
        "embedding_version": embedding_version,
        "num_items": len(rows),
        "num_failed": sum(row["status"] == "FAILED" for row in rows),
        "latency_seconds": round(time.perf_counter() - started, 3),
        "items": rows,
    }
    if vectors:
        _put_vectors(
            bucket=bucket,
            key=f"{s3io.image_prefix(derived, golden_id)}/{VECTOR_OBJECT_NAME}",
            keys=list(vectors),
            image_vectors=[vectors[key]["image"] for key in vectors],
            text_vectors=[vectors[key]["text"] for key in vectors],
        )
    # manifest는 아이템 업로드가 모두 끝난 뒤 마지막에 쓴다 — 이 파일의 존재가
    # "완료"를 뜻하므로 중간에 죽으면 다음 실행이 다시 처리한다.
    s3io.put_json(bucket, s3io.item_manifest_key(derived, golden_id), manifest)
    return manifest, vectors


def _backfill_embedding_version(
    rows: list[dict[str, Any]], fallback: str
) -> list[dict[str, Any]]:
    """행에 임베딩 버전이 없으면 manifest 상단 값으로 채운다."""
    filled = []
    for row in rows:
        entry = dict(row)
        for field in ("image_embedding_version", "text_embedding_version"):
            if not entry.get(field):
                entry[field] = fallback
        filled.append(entry)
    return filled


def extract_items(
    *,
    run_dir: Path,
    settings: GoldenSettings,
    force: bool = False,
    pipeline: Any | None = None,
) -> list[dict[str, Any]]:
    """manifest의 코디마다 아이템을 분리·태깅·임베딩해 items.jsonl을 만든다.

    `force=False`면 S3에 완료 manifest가 있는 코디는 건너뛴다.
    """
    bucket = settings.require_bucket()
    derived = settings.derived_prefix()
    images = [
        row
        for row in read_jsonl(run_dir / "images.jsonl")
        if row.get("duplicate_kind") != "exact"
    ]
    if not images:
        raise ValueError("아이템을 추출할 이미지가 manifest에 없습니다.")

    rows: list[dict[str, Any]] = []
    vectors: dict[str, dict[str, list[float]]] = {}
    # 이번 실행에서 새로 처리한 코디. 적재 단계가 "이미 있으면 건너뛰기"를
    # 할 때 이 목록만은 예외로 두어야 한다 — 내용이 바뀌었으므로 기존 포인트를
    # 덮어써야 한다.
    processed_golden_ids: list[str] = []
    reused_count = 0
    embedding_version = settings.item_embedding_version

    for image in images:
        golden_id = str(image["golden_id"])
        manifest_key = s3io.item_manifest_key(derived, golden_id)
        cached = None if force else s3io.get_json(bucket, manifest_key)
        if (
            cached
            and cached.get("schema_version") == ITEM_SCHEMA_VERSION
            and cached.get("image_sha256") == str(image["image_sha256"])
        ):
            # 구형 manifest는 행에 임베딩 버전이 없다. manifest 상단 값으로
            # 채워 넣는다 — 이것 때문에 전량 재처리(아이템당 유료 호출)를
            # 시키는 건 비용이 맞지 않는다.
            rows.extend(
                _backfill_embedding_version(
                    cached.get("items", []), cached.get("embedding_version", "")
                )
            )
            vectors.update(
                _get_vectors(
                    bucket,
                    f"{s3io.image_prefix(derived, golden_id)}/{VECTOR_OBJECT_NAME}",
                )
            )
            reused_count += 1
            continue

        if pipeline is None:
            pipeline = build_item_pipeline(settings)
        embedding_version = _resolve_embedding_version(
            pipeline, settings.item_embedding_version
        )
        manifest, fresh = _process_one(
            image=image,
            bucket=bucket,
            derived=derived,
            pipeline=pipeline,
            embedding_version=embedding_version,
        )
        rows.extend(manifest["items"])
        vectors.update(fresh)
        processed_golden_ids.append(golden_id)

    write_jsonl(run_dir / "items.jsonl", rows)
    keys = [key for key in (row["item_key"] for row in rows) if key in vectors]
    # 벡터가 하나도 없으면(WORKER_EMBED_ENABLED=0 등) 빈 2차원 배열로 저장한다.
    # np.asarray([]).reshape(0, -1)은 예외를 던지므로 분기가 필요하다.
    empty = np.zeros((0, 0), dtype=np.float32)
    np.savez_compressed(
        run_dir / "item_embeddings.npz",
        ids=np.asarray(keys),
        image=(
            np.asarray([vectors[key]["image"] for key in keys], dtype=np.float32)
            if keys
            else empty
        ),
        text=(
            np.asarray([vectors[key]["text"] for key in keys], dtype=np.float32)
            if keys
            else empty
        ),
    )
    write_json(
        run_dir / "items.meta.json",
        {
            "schema_version": ITEM_SCHEMA_VERSION,
            "pipeline": settings.item_pipeline,
            "embedding_version": embedding_version,
            "num_images": len(images),
            "num_items": len(rows),
            "num_items_with_vectors": len(keys),
            "processed_images": len(processed_golden_ids),
            "processed_golden_ids": processed_golden_ids,
            "reused_images": reused_count,
        },
    )
    return rows


def load_item_vectors(path: Path) -> tuple[list[str], np.ndarray, np.ndarray]:
    with np.load(path, allow_pickle=False) as data:
        ids = [str(value) for value in data["ids"].tolist()]
        image = np.asarray(data["image"], dtype=np.float32)
        text = np.asarray(data["text"], dtype=np.float32)
    return ids, image, text


def pending_golden_ids(
    *, run_dir: Path, settings: GoldenSettings
) -> list[str]:
    """아직 아이템 처리가 끝나지 않은 코디 목록 (부팅 스캔이 쓴다)."""
    bucket = settings.require_bucket()
    derived = settings.derived_prefix()
    pending: list[str] = []
    for row in read_jsonl(run_dir / "images.jsonl"):
        if row.get("duplicate_kind") == "exact":
            continue
        golden_id = str(row["golden_id"])
        cached = s3io.get_json(bucket, s3io.item_manifest_key(derived, golden_id))
        if not cached or cached.get("schema_version") != ITEM_SCHEMA_VERSION:
            pending.append(golden_id)
        elif cached.get("image_sha256") != str(row["image_sha256"]):
            pending.append(golden_id)
    return pending
