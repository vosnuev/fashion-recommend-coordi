"""옷장 이미지 프로세서(AI Worker) 메인 루프.

설계서(Confluence "옷장 이미지 파이프라인 설계서") 흐름:
  Redis 작업 수신 → S3 원본 다운로드 → 파이프라인(열거·생성·태깅·임베딩)
  → 아이템 이미지 S3 업로드 → manifest 저장 → wardrobe-api 콜백 → ack

핵심 정책:
- Worker는 DB를 직접 수정하지 않는다. DB 등록은 콜백을 받은 wardrobe-api 몫.
- 같은 job_id는 같은 S3 경로를 재사용한다 → manifest가 이미 있으면
  이미지 처리를 건너뛰고 콜백만 재시도한다 (설계서 7장).
- 재시도 초과 시 dead queue로 이동한다 (services/queue.py).

실행: python worker.py
"""
from __future__ import annotations

import json
import logging
import mimetypes
import tempfile
import time
from pathlib import Path

import config
from pipeline import ProcessedItem, build_pipeline
from services import callback, queue, s3io

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("worker")


def normalize_payload(payload: dict) -> dict:
    """wardrobe-api 페이로드와 설계서 제안 페이로드를 모두 수용한다.

    api:  {job_id, user_id, source{bucket,key}, output_prefix, callback_url}
    설계서: {job_id, input{bucket,key}, output{bucket,prefix}, ...}

    exclude_categories는 룩북·캘린더 등록에서 실려 온다 — 사용자가 '입은 옷'으로
    이미 지정해 둔 대분류다. 키가 없으면 빈 목록이 되어 기존 옷장 페이로드와
    동작이 완전히 같다.
    """
    source = payload.get("source") or payload.get("input") or {}
    output_prefix = payload.get("output_prefix") \
        or (payload.get("output") or {}).get("prefix", "")
    out_bucket = (payload.get("output") or {}).get("bucket") or source.get("bucket", "")
    return {
        "job_id": str(payload["job_id"]),
        "user_id": payload.get("user_id"),
        "src_bucket": source.get("bucket", ""),
        "src_key": source.get("key", ""),
        "out_bucket": out_bucket,
        "out_prefix": output_prefix,
        "exclude_categories": list(payload.get("exclude_categories") or []),
        "callback_url": payload.get("callback_url", ""),
    }


def build_manifest(job: dict, pipeline_key: str,
                   items: list[ProcessedItem], total_sec: float,
                   excluded: list | None = None) -> dict:
    """설계서 5장 manifest. 벡터도 포함해 '콜백만 재시도'가 가능하게 한다."""
    ok = [it for it in items if it.ok]
    excluded = excluded or []
    return {
        "schema_version": config.SCHEMA_VERSION,
        "job_id": job["job_id"],
        "pipeline": {"impl": pipeline_key, "version": config.PIPELINE_VERSION,
                     "embedding_version": config.EMBEDDING_VERSION},
        "counts": {"detected": len(items), "succeeded": len(ok),
                   "failed": len(items) - len(ok), "excluded": len(excluded)},
        # 어떤 아이템이 왜 빠졌는지 남긴다. 사용자 눈에는 "사진에 분명 있는데
        # 안 뽑혔다"로 보이므로, 의도된 제외였는지 나중에 확인할 수 있어야 한다.
        "excluded_categories": job.get("exclude_categories", []),
        "excluded_items": [it.meta() for it in excluded],
        "total_sec": round(total_sec, 3),
        "items": [
            {
                "index": it.index,
                "s3_key": s3io.item_key(job["out_prefix"], it.index) if it.ok else None,
                "tags": it.tags,
                "image_vector": it.image_vector,
                "text_vector": it.text_vector,
                "enum": it.enum.meta(),
                "timings_sec": it.timings,
                "error": it.error,
            }
            for it in items
        ],
    }


def callback_payload_from_manifest(manifest: dict) -> dict:
    """manifest → wardrobe-api CallbackSerializer 계약 페이로드."""
    items = []
    for it in manifest["items"]:
        if it.get("error") or not it.get("s3_key") or not it.get("tags"):
            continue
        tags = dict(it["tags"])
        for key in ("season", "usage"):
            values = tags.get(key) or []
            if isinstance(values, str):
                values = [values]
            tags[key] = [
                value.strip()
                for value in values
                if isinstance(value, str) and value.strip()
            ]
        missing = tags.pop("_missing_required", [])
        items.append({
            "s3_key": it["s3_key"],
            **{k: tags.get(k, "" if k not in ("season", "style", "usage") else [])
               for k in ("item_name", "category_large", "category_small",
                         "season", "style", "color", "pattern", "fit",
                         "material", "sleeve", "length", "usage",
                         "layer_role", "layer_order")},
            "seg_meta": {"pipeline": manifest["pipeline"],
                         "enum": it.get("enum"),
                         "missing_required": missing,
                         "timings_sec": it.get("timings_sec")},
            "image_vector": it.get("image_vector") or [],
            "text_vector": it.get("text_vector") or [],
        })
    excluded = manifest.get("counts", {}).get("excluded", 0)
    if items:
        status, error = "success", ""
        failed = manifest["counts"]["failed"]
        if failed:  # API가 partial을 지원하기 전까지는 success + error 메모
            error = f"partial: {failed}개 아이템 처리 실패 (manifest 참조)"
    elif excluded and not manifest["items"]:
        # 룩북에서 입은 옷으로 사진 속 부위를 전부 지정한 경우다. 뽑을 것이
        # 남지 않은 것이 정상이므로 실패로 올리면 안 된다 — 실패로 보내면
        # 사용자가 제대로 등록한 룩이 '이미지 처리 실패'로 표시된다.
        status, error = "success", ""
    else:
        status, error = "failed", "처리 성공한 아이템이 없습니다 (manifest 참조)"
    return {"job_id": manifest["job_id"], "status": status,
            "error": error, "items": items}


def process_job(job: dict, pipeline) -> dict:
    """이미지 처리 → S3 업로드 → manifest 반환. 멱등: manifest 있으면 재사용."""
    m_key = s3io.manifest_key(job["out_prefix"])
    existing = s3io.get_json(job["out_bucket"], m_key)
    if existing is not None:
        logger.info("job %s: manifest 존재 → 처리 생략, 콜백만 재시도", job["job_id"])
        return existing

    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / Path(job["src_key"]).name
        s3io.download(job["src_bucket"], job["src_key"], str(local))
        mime = mimetypes.guess_type(local.name)[0] or "image/jpeg"
        image_bytes = local.read_bytes()

    items, excluded = pipeline.process(
        image_bytes, mime, job.get("exclude_categories", ())
    )

    # 아이템 이미지를 먼저 업로드하고, manifest는 마지막에 저장 (설계서 5장)
    import io as _io

    from PIL import Image

    for it in items:
        if it.ok:
            img = Image.open(_io.BytesIO(it.image_png))
            s3io.upload_png(job["out_bucket"],
                            s3io.item_key(job["out_prefix"], it.index), img)

    manifest = build_manifest(job, pipeline.key, items,
                              time.perf_counter() - t0, excluded)
    s3io.put_json(job["out_bucket"], m_key, manifest)
    return manifest


def main() -> None:
    pipeline = build_pipeline()
    logger.info("파이프라인: %s (%s) | 큐: %s",
                config.PIPELINE_IMPL, config.PIPELINE_VERSION, config.PENDING_KEY)
    queue.recover_stale()

    while True:
        raw = queue.fetch()
        if raw is None:
            continue
        job_id = "?"
        job = {}
        try:
            job = normalize_payload(json.loads(raw))
            job_id = job["job_id"]
            logger.info("job %s 시작 (%s/%s)", job_id, job["src_bucket"], job["src_key"])

            try:
                callback.post(job["callback_url"], {
                    "job_id": job_id, "status": "processing", "error": "", "items": [],
                })
            except Exception:  # noqa: BLE001 — 시작 알림 실패로 GPU 작업을 버리지 않는다
                logger.warning("job %s 처리 시작 콜백 실패", job_id, exc_info=True)

            manifest = process_job(job, pipeline)
            callback.post(job["callback_url"], callback_payload_from_manifest(manifest))

            queue.ack(raw, job_id)
            c = manifest["counts"]
            logger.info("job %s 완료: 검출 %d / 성공 %d / 실패 %d / 제외 %d",
                        job_id, c["detected"], c["succeeded"], c["failed"],
                        c.get("excluded", 0))
        except Exception as e:  # noqa: BLE001 — job 단위 격리 후 재시도/dead 처리
            logger.exception("job %s 실패", job_id)
            error = f"{type(e).__name__}: {e}"
            if queue.retry_or_dead(raw, job_id, error) and job_id != "?":
                try:
                    callback.post(job.get("callback_url", ""), {
                        "job_id": job_id, "status": "failed", "error": error, "items": [],
                    })
                except Exception:  # noqa: BLE001 — dead queue 원본은 보존한다
                    logger.exception("job %s 최종 실패 콜백 실패", job_id)


if __name__ == "__main__":
    main()
