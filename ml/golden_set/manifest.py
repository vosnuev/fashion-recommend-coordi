"""골든 코디 원본을 안정적인 manifest로 변환한다.

원본의 소유자는 S3 버킷이다(`build_manifest_from_s3`). 로컬 디렉터리 입력
(`build_manifest`)은 테스트와 오프라인 실험용으로만 남겨둔다.

manifest의 `local_path`는 run 디렉터리 안의 캐시 사본을 가리키며, 재현에
필요한 진짜 주소는 `source_bucket`/`source_key`다.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from . import s3io
from .artifacts import write_json, write_jsonl
from .config import GoldenSettings, normalize_dataset_status

IMAGE_EXTENSIONS = s3io.IMAGE_EXTENSIONS


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def difference_hash(path: Path, *, size: int = 8) -> str:
    """EXIF 방향을 반영한 64비트 dHash."""
    with Image.open(path) as image:
        gray = ImageOps.exif_transpose(image).convert("L").resize((size + 1, size))
        # Pillow 14에서 getdata()가 제거될 예정이라 새 API를 우선 사용한다.
        flattened = getattr(gray, "get_flattened_data", gray.getdata)
        pixels = list(flattened())
    bits = []
    for y in range(size):
        start = y * (size + 1)
        bits.extend(pixels[start + x] > pixels[start + x + 1] for x in range(size))
    value = sum(int(bit) << index for index, bit in enumerate(bits))
    return f"{value:0{size * size // 4}x}"


def hamming_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _clean_id(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣_-]+", "-", value.strip()).strip("-")
    return cleaned[:64] or "golden"



#: 성별 표현 그룹의 표준 값. 코디가 어느 쪽 표현인지를 뜻하며, 사람의 정체성이
#: 아니라 착장의 표현을 가리킨다. 리트리버가 하드 필터로 쓰므로 표기가 흔들리면
#: 그대로 검색 누락이 된다 — CSV에 무엇이 적혀 오든 여기서 한 형태로 모은다.
PRESENTATION_MEN = "men"
PRESENTATION_WOMEN = "women"
PRESENTATION_UNISEX = "unisex"

_PRESENTATION_ALIASES = {
    PRESENTATION_MEN: PRESENTATION_MEN,
    "male": PRESENTATION_MEN, "m": PRESENTATION_MEN,
    "man": PRESENTATION_MEN, "남": PRESENTATION_MEN, "남성": PRESENTATION_MEN,
    "남자": PRESENTATION_MEN, "menswear": PRESENTATION_MEN,
    PRESENTATION_WOMEN: PRESENTATION_WOMEN,
    "female": PRESENTATION_WOMEN, "f": PRESENTATION_WOMEN,
    "woman": PRESENTATION_WOMEN, "여": PRESENTATION_WOMEN, "여성": PRESENTATION_WOMEN,
    "여자": PRESENTATION_WOMEN, "womenswear": PRESENTATION_WOMEN,
    PRESENTATION_UNISEX: PRESENTATION_UNISEX,
    "공용": PRESENTATION_UNISEX, "남녀공용": PRESENTATION_UNISEX,
    "무관": PRESENTATION_UNISEX, "neutral": PRESENTATION_UNISEX,
    "any": PRESENTATION_UNISEX, "all": PRESENTATION_UNISEX,
}


def normalize_presentation_group(raw: str) -> str:
    """CSV 표기를 표준 값으로 모은다. 모르는 값은 빈 문자열(미분류).

    빈 문자열은 "아직 라벨을 안 붙였다"는 뜻이고, 성별 필터가 켜지면 그 코디는
    검색에서 빠진다. 임의로 unisex로 메우지 않는 이유가 그것이다 — 미분류를
    공용으로 취급하면 여성 코디가 남성 사용자에게 그대로 나간다.
    """
    return _PRESENTATION_ALIASES.get(str(raw or "").strip().lower(), "")


def _parse_metadata_rows(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        key = (row.get("file_name") or row.get("golden_id") or "").strip()
        if not key:
            raise ValueError("metadata CSV에는 file_name 또는 golden_id가 필요합니다.")
        result[key] = {str(k): str(v or "").strip() for k, v in row.items()}
    return result


def _read_metadata(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return _parse_metadata_rows(list(csv.DictReader(handle)))


def read_metadata_from_s3(bucket: str, key: str) -> dict[str, dict[str, str]]:
    """S3에 올려둔 metadata.csv를 읽는다. 없으면 빈 dict."""
    if not key:
        return {}
    try:
        body = s3io.get_bytes(bucket, key)
    except Exception:  # noqa: BLE001 — 메타데이터는 선택 입력이라 없으면 진행한다
        return {}
    text = body.decode("utf-8-sig")
    return _parse_metadata_rows(list(csv.DictReader(io.StringIO(text))))


def _metadata_for(
    name: str,
    stem: str,
    metadata: dict[str, dict[str, str]],
) -> dict[str, str]:
    return metadata.get(name) or metadata.get(stem) or {}


def _split_values(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[;,|]", value) if item.strip()]


def _build_rows(
    entries: list[tuple[Path, str, str]],
    metadata: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """(로컬경로, 원본이름, 원본주소) 목록을 manifest 행으로 만든다."""
    used_ids: set[str] = set()
    exact_seen: dict[str, str] = {}
    perceptual_seen: list[tuple[str, str]] = []
    rows: list[dict[str, Any]] = []

    for index, (path, source_name, source_uri) in enumerate(entries, start=1):
        name = Path(source_name).name
        stem = Path(source_name).stem
        meta = _metadata_for(name, stem, metadata)
        base_id = _clean_id(meta.get("golden_id") or stem)
        golden_id = base_id
        suffix = 2
        while golden_id in used_ids:
            golden_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(golden_id)

        image_sha = sha256_file(path)
        perceptual = difference_hash(path)
        duplicate_of = exact_seen.get(image_sha, "")
        duplicate_kind = "exact" if duplicate_of else ""
        if not duplicate_of:
            near = next(
                (
                    prior_id
                    for prior_id, prior_hash in perceptual_seen
                    if hamming_distance(perceptual, prior_hash) <= 4
                ),
                "",
            )
            if near:
                duplicate_of = near
                duplicate_kind = "near"
        exact_seen.setdefault(image_sha, golden_id)
        perceptual_seen.append((golden_id, perceptual))

        rows.append(
            {
                "golden_id": golden_id,
                "local_path": str(path.resolve()),
                "source_uri": meta.get("source_uri") or source_uri,
                "source_name": meta.get("source", ""),
                "usage_scope": meta.get("usage_scope", "UNKNOWN").upper(),
                "original_exposable": meta.get("original_exposable", "").lower()
                in {"1", "true", "yes", "y"},
                "image_sha256": image_sha,
                "perceptual_hash": perceptual,
                "duplicate_of": duplicate_of,
                "duplicate_kind": duplicate_kind,
                "split": meta.get("split", "KNOWLEDGE").upper(),
                "presentation_group": normalize_presentation_group(
                    meta.get("presentation_group", "")
                ),
                "metadata": {
                    "style": _split_values(meta.get("style", "")),
                    "season": _split_values(meta.get("season", "")),
                    "occasion": _split_values(meta.get("occasion", "")),
                    "selection_reason": meta.get("selection_reason", ""),
                    "same_shoot_group": meta.get("same_shoot_group", ""),
                },
                "order": index,
            }
        )
    return rows


def _write_manifest(
    *,
    run_dir: Path,
    rows: list[dict[str, Any]],
    dataset_name: str,
    dataset_version: str,
    dataset_status: str = "PILOT",
    source: dict[str, Any],
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(run_dir / "images.jsonl", rows)
    write_json(
        run_dir / "run_manifest.json",
        {
            "run_id": run_dir.name,
            "dataset_name": dataset_name,
            "dataset_version": dataset_version,
            "dataset_status": normalize_dataset_status(dataset_status),
            "status": "PREPARED",
            "num_images": len(rows),
            "num_exact_duplicates": sum(
                row["duplicate_kind"] == "exact" for row in rows
            ),
            "num_near_duplicates": sum(row["duplicate_kind"] == "near" for row in rows),
            "source": source,
        },
    )


def build_manifest(
    *,
    input_dir: Path,
    run_dir: Path,
    dataset_name: str,
    dataset_version: str,
    metadata_csv: Path | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """로컬 디렉터리 입력 (테스트·오프라인 실험 전용)."""
    paths = sorted(
        path
        for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )
    if limit is not None:
        paths = paths[:limit]
    if not paths:
        raise ValueError(f"이미지를 찾을 수 없습니다: {input_dir}")

    entries = [(path, path.name, str(path.resolve())) for path in paths]
    rows = _build_rows(entries, _read_metadata(metadata_csv))
    _write_manifest(
        run_dir=run_dir,
        rows=rows,
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        source={"kind": "local", "input_dir": str(input_dir.resolve())},
    )
    return rows


def build_manifest_from_s3(
    *,
    settings: GoldenSettings,
    run_dir: Path | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """S3 prefix의 코디 원본을 run 디렉터리로 내려받고 manifest를 만든다.

    같은 키를 다시 받지 않으려고 캐시 파일이 이미 있으면 다운로드를 건너뛴다.
    S3 객체가 갱신되는 경우는 상정하지 않는다(골든 원본은 불변으로 다룬다).
    """
    bucket = settings.require_bucket()
    prefix = settings.source_prefix()
    run_dir = run_dir or settings.run_dir
    keys = s3io.list_source_keys(bucket, prefix)
    if limit is not None:
        keys = keys[:limit]
    if not keys:
        raise ValueError(
            f"S3에서 이미지를 찾을 수 없습니다: s3://{bucket}/{prefix}"
        )

    cache_dir = run_dir / "source"
    entries: list[tuple[Path, str, str]] = []
    for key in keys:
        relative = key[len(prefix):] if prefix and key.startswith(prefix) else key
        # prefix 아래 계층 구조를 파일명에 녹여 캐시 이름 충돌을 막는다.
        local = cache_dir / relative.replace("/", "__")
        if not local.exists():
            s3io.download(bucket, key, local)
        entries.append((local, relative, s3io.s3_uri(bucket, key)))

    metadata = read_metadata_from_s3(bucket, settings.s3_metadata_key)
    rows = _build_rows(entries, metadata)
    for row, key in zip(rows, keys, strict=True):
        row["source_bucket"] = bucket
        row["source_key"] = key
    _write_manifest(
        run_dir=run_dir,
        rows=rows,
        dataset_name=settings.dataset_name,
        dataset_version=settings.dataset_version,
        dataset_status=settings.dataset_status,
        source={
            "kind": "s3",
            "bucket": bucket,
            "source_prefix": settings.s3_source_prefix,
            "derived_prefix": settings.derived_prefix(),
        },
    )
    return rows
