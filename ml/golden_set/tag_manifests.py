"""S3의 골든 코디 manifest에 검색용 태그를 붙인다. (독립 실행 스크립트)

    python -m ml.golden_set.tag_manifests                # 미태깅분만
    python -m ml.golden_set.tag_manifests --force        # 전량 다시 태깅
    python -m ml.golden_set.tag_manifests --limit 5      # 앞의 5건만 (시험용)
    python -m ml.golden_set.tag_manifests --dry-run      # 호출만 하고 저장 안 함

**실행 위치: API 서버.** Gemini API와 S3만 쓰므로 GPU가 필요 없다.

S3가 단일 출처다. `{derived}/*/manifest.json`을 훑어 각 manifest의 `source_key`로
원본 사진을 내려받고, Gemini가 붙인 태그를 같은 manifest에 되쓴다. 로컬 run
디렉터리를 보지 않으므로 GPU 호스트의 상태와 무관하게 몇 번이든 돌릴 수 있다.

기존 러너(runner.py)와 겹치지 않는다. 저쪽은 아이템 분리·임베딩까지 하는 무거운
파이프라인이고, 이 스크립트는 이미 만들어진 manifest에 태그만 얹는다.
"""

from __future__ import annotations

import argparse
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from . import look_tags, s3io
from .config import GoldenSettings, load_project_env
from .gemini import GeminiStructuredClient

logger = logging.getLogger("golden_set.tag_manifests")

#: manifest 안에서 태그가 들어가는 자리
TAG_FIELD = "look_tags"


def find_manifests(bucket: str, derived: str) -> list[str]:
    """{derived}/*/manifest.json 키를 모은다."""
    suffix = "/" + s3io.ITEM_MANIFEST_NAME
    return [key for key in s3io.list_keys(bucket, derived + "/") if key.endswith(suffix)]


def needs_tagging(manifest: dict[str, Any], *, force: bool) -> bool:
    if force:
        return True
    tags = manifest.get(TAG_FIELD)
    if not tags:
        return True
    # 어휘나 프롬프트가 바뀌면 스키마 버전이 올라가고, 그때 다시 태깅한다.
    return tags.get("schema_version") != look_tags.LOOK_TAG_SCHEMA_VERSION


def tag_one(
    *,
    client: GeminiStructuredClient,
    bucket: str,
    manifest: dict[str, Any],
    schema: dict[str, Any],
    workdir: Path,
) -> dict[str, Any]:
    """원본 사진을 보고 태그를 만든다. manifest는 건드리지 않고 태그만 돌려준다."""
    source_key = str(manifest.get("source_key", ""))
    if not source_key:
        raise ValueError("manifest에 source_key가 없습니다 (구형 manifest)")

    local = workdir / Path(source_key).name
    s3io.download(bucket, source_key, local)
    raw = client.analyze_image(
        image_path=local,
        prompt=look_tags.PROMPT,
        system_instruction=look_tags.SYSTEM_INSTRUCTION,
        schema=schema,
    )
    local.unlink(missing_ok=True)

    tags = look_tags.normalize(raw)
    tags["schema_version"] = look_tags.LOOK_TAG_SCHEMA_VERSION
    tags["model"] = client.settings.gemini_model
    return tags


def main() -> None:
    parser = argparse.ArgumentParser(
        description="S3 골든 manifest에 presentation_group·style·season·occasion을 붙인다"
    )
    parser.add_argument("--force", action="store_true", help="이미 태깅된 것도 다시")
    parser.add_argument("--limit", type=int, help="처리할 최대 건수 (시험용)")
    parser.add_argument(
        "--dry-run", action="store_true", help="결과를 출력만 하고 S3에 쓰지 않는다"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    load_project_env()
    settings = GoldenSettings.from_env()
    bucket = settings.require_bucket()
    derived = settings.derived_prefix()

    manifest_keys = find_manifests(bucket, derived)
    logger.info("manifest %d건 (s3://%s/%s)", len(manifest_keys), bucket, derived)
    if not manifest_keys:
        logger.warning(
            "manifest가 없습니다. 아이템 분리가 끝났는지, "
            "GOLDEN_S3_OUTPUT_PREFIX/GOLDEN_DATASET_VERSION이 맞는지 확인하세요."
        )
        return

    client = GeminiStructuredClient(settings)
    schema = look_tags.build_schema()

    tagged = skipped = failed = 0
    with tempfile.TemporaryDirectory() as temp:
        workdir = Path(temp)
        for key in manifest_keys:
            if args.limit and tagged >= args.limit:
                break
            manifest = s3io.get_json(bucket, key)
            if not manifest:
                logger.warning("읽을 수 없는 manifest: %s", key)
                failed += 1
                continue
            golden_id = manifest.get("golden_id", "?")

            if not needs_tagging(manifest, force=args.force):
                skipped += 1
                continue

            try:
                tags = tag_one(
                    client=client,
                    bucket=bucket,
                    manifest=manifest,
                    schema=schema,
                    workdir=workdir,
                )
            except Exception as exc:  # noqa: BLE001
                # 한 장의 실패가 나머지를 막지 않는다. 다음 실행에서 다시 시도된다.
                logger.exception("태깅 실패 %s: %s", golden_id, exc)
                failed += 1
                continue

            logger.info(
                "%s → %s / style=%s / season=%s / occasion=%s (확신 %s)",
                golden_id,
                tags["presentation_group"] or "(미분류)",
                tags["style"],
                tags["season"],
                tags["occasion"],
                tags["confidence"],
            )
            tagged += 1

            if args.dry_run:
                continue
            manifest[TAG_FIELD] = tags
            s3io.put_json(bucket, key, manifest)

    logger.info(
        "완료: 태깅 %d / 건너뜀 %d / 실패 %d%s",
        tagged, skipped, failed, " (dry-run: 저장 안 함)" if args.dry_run else "",
    )
    if not args.dry_run and tagged:
        logger.info("다음 단계: ./run_goldenset_sync.sh (GPU 서버)에서 Qdrant에 반영")


if __name__ == "__main__":
    main()
