"""골든셋 S3 입출력.

원본 코디 사진은 S3 버킷의 지정 prefix가 소유한다(로컬 경로가 아니다).
파생물은 `{output_prefix}/{dataset_version}/{golden_id}/` 아래에 모으고,
그 안의 `manifest.json` 존재 여부가 "이 사진은 이미 처리됨"의 단일 판단
기준이다 — image-processor의 멱등 규칙과 같은 방식이다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import boto3
from botocore.exceptions import ClientError

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ITEM_MANIFEST_NAME = "manifest.json"


@lru_cache(maxsize=1)
def client():
    """자격증명은 표준 AWS 환경변수 또는 IAM 역할로 주입한다."""
    return boto3.client("s3")


def list_source_keys(bucket: str, prefix: str) -> list[str]:
    """prefix 아래의 이미지 객체 키를 이름순으로 돌려준다."""
    keys: list[str] = []
    for page in _paginate(bucket, prefix):
        for item in page.get("Contents", []):
            key = str(item["Key"])
            if key.endswith("/"):
                continue
            if Path(key).suffix.lower() in IMAGE_EXTENSIONS:
                keys.append(key)
    keys.sort()
    return keys


def _paginate(bucket: str, prefix: str) -> Iterator[dict[str, Any]]:
    paginator = client().get_paginator("list_objects_v2")
    yield from paginator.paginate(Bucket=bucket, Prefix=prefix)


def download(bucket: str, key: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    client().download_file(bucket, key, str(dest))
    return dest


def get_bytes(bucket: str, key: str) -> bytes:
    return client().get_object(Bucket=bucket, Key=key)["Body"].read()


def put_bytes(bucket: str, key: str, data: bytes, content_type: str) -> str:
    client().put_object(
        Bucket=bucket, Key=key, Body=data, ContentType=content_type
    )
    return key


def put_json(bucket: str, key: str, value: Any) -> str:
    return put_bytes(
        bucket,
        key,
        json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"),
        "application/json",
    )


def get_json(bucket: str, key: str) -> Any | None:
    """없으면 None. 처리 완료 여부 판별에 쓴다."""
    try:
        body = client().get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as error:
        if _is_missing(error):
            return None
        raise
    return json.loads(body)


def presigned_url(bucket: str, key: str, *, expires_seconds: int = 600) -> str:
    """브라우저가 직접 볼 수 있는 임시 URL. 확인용 웹 페이지가 쓴다.

    버킷을 공개로 열지 않고도 원본·아이템 이미지를 미리 볼 수 있게 한다.
    만료가 짧아 URL이 새어도 오래 쓸 수 없다.
    """
    return client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_seconds,
    )


def list_keys(bucket: str, prefix: str) -> list[str]:
    """prefix 아래 모든 객체 키 (확장자 필터 없음)."""
    keys: list[str] = []
    for page in _paginate(bucket, prefix):
        keys.extend(
            str(item["Key"])
            for item in page.get("Contents", [])
            if not str(item["Key"]).endswith("/")
        )
    return keys


def exists(bucket: str, key: str) -> bool:
    try:
        client().head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if _is_missing(error):
            return False
        raise
    return True


def _is_missing(error: ClientError) -> bool:
    return str(error.response.get("Error", {}).get("Code")) in {
        "404",
        "NoSuchKey",
        "NotFound",
    }


# ── 키 규칙 (한 곳에서만 만든다) ────────────────────────────────


def image_prefix(derived_prefix: str, golden_id: str) -> str:
    return f"{derived_prefix}/{golden_id}"


def item_manifest_key(derived_prefix: str, golden_id: str) -> str:
    return f"{image_prefix(derived_prefix, golden_id)}/{ITEM_MANIFEST_NAME}"


def item_image_key(derived_prefix: str, golden_id: str, index: int) -> str:
    return f"{image_prefix(derived_prefix, golden_id)}/item_{index:03d}.png"


def s3_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"
