"""캘린더 전용 S3 키 생성·저장·조회·삭제 서비스.

DB에는 만료되는 URL이 아니라 S3 key만 저장한다. 사용자에게 이미지를 반환할
때만 presigned GET URL을 생성한다.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import PurePosixPath
from uuid import UUID

import boto3

BUCKET = (
    os.getenv("CALENDAR_S3_BUCKET", "").strip()
    or os.getenv("WARDROBE_S3_BUCKET", "").strip()
)
WARDROBE_BUCKET = os.getenv("WARDROBE_S3_BUCKET", "").strip()
REGION = os.getenv("AWS_REGION", "ap-northeast-2").strip() or "ap-northeast-2"
PRESIGNED_GET_TTL = int(os.getenv("CALENDAR_PRESIGNED_GET_TTL", "3600"))

_SAFE_PATH_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
_IMAGE_CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
}


class CalendarStorageConfigurationError(RuntimeError):
    """캘린더 S3 환경변수가 설정되지 않은 경우."""


def _require_bucket(bucket: str, variable_name: str) -> str:
    bucket = bucket.strip()
    if not bucket:
        raise CalendarStorageConfigurationError(
            f"{variable_name} 환경변수가 설정되지 않았습니다."
        )
    return bucket


@lru_cache(maxsize=1)
def _client():
    """표준 AWS 자격증명 환경변수 또는 IAM Role을 사용하는 S3 client."""

    return boto3.client("s3", region_name=REGION)


def _extension(filename_or_key: str, *, default: str) -> str:
    suffix = PurePosixPath(filename_or_key).suffix.lower()
    if not suffix or len(suffix) > 10:
        return default
    return suffix


def _path_segment(value: int | str | UUID, *, name: str) -> str:
    segment = str(value)
    if not _SAFE_PATH_SEGMENT_PATTERN.fullmatch(segment):
        raise ValueError(f"{name}에 S3 경로로 사용할 수 없는 문자가 포함되어 있습니다.")
    return segment


def calendar_prefix(user_id: int | str, calendar_id: UUID | str) -> str:
    user_segment = _path_segment(user_id, name="user_id")
    calendar_segment = _path_segment(calendar_id, name="calendar_id")
    return f"calendar/{user_segment}/{calendar_segment}/"


def original_key(
    user_id: int | str,
    calendar_id: UUID | str,
    filename: str,
    content_type: str | None = None,
) -> str:
    extension = _IMAGE_CONTENT_TYPE_EXTENSIONS.get(content_type or "")
    if extension is None:
        extension = _extension(filename, default=".jpg")
    return f"{calendar_prefix(user_id, calendar_id)}original{extension}"


def selected_item_key(
    user_id: int | str,
    calendar_id: UUID | str,
    link_id: UUID | str,
    source_key: str,
) -> str:
    extension = _extension(source_key, default=".png")
    return f"{calendar_prefix(user_id, calendar_id)}selected/{link_id}{extension}"


def upload_fileobj(fileobj, key: str, content_type: str | None = None) -> None:
    bucket = _require_bucket(BUCKET, "CALENDAR_S3_BUCKET")
    extra_args = {"ContentType": content_type} if content_type else {}
    _client().upload_fileobj(fileobj, bucket, key, ExtraArgs=extra_args)


def copy_wardrobe_item(source_key: str, destination_key: str) -> None:
    """옷장 이미지를 캘린더 소유 경로로 서버 측 복사한다."""

    source_bucket = _require_bucket(WARDROBE_BUCKET, "WARDROBE_S3_BUCKET")
    destination_bucket = _require_bucket(BUCKET, "CALENDAR_S3_BUCKET")
    _client().copy_object(
        CopySource={"Bucket": source_bucket, "Key": source_key},
        Bucket=destination_bucket,
        Key=destination_key,
    )


def copy_calendar_original_to_wardrobe(
    source_key: str,
    destination_key: str,
) -> None:
    """캘린더 원본을 기존 옷장 worker 입력 경로로 서버 측 복사한다."""

    source_bucket = _require_bucket(BUCKET, "CALENDAR_S3_BUCKET")
    destination_bucket = _require_bucket(WARDROBE_BUCKET, "WARDROBE_S3_BUCKET")
    _client().copy_object(
        CopySource={"Bucket": source_bucket, "Key": source_key},
        Bucket=destination_bucket,
        Key=destination_key,
    )


def presigned_get(key: str, ttl: int = PRESIGNED_GET_TTL) -> str:
    if not key:
        return ""
    bucket = _require_bucket(BUCKET, "CALENDAR_S3_BUCKET")
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=ttl,
    )


def delete_objects(keys: Iterable[str]) -> None:
    """명시적으로 전달된 캘린더 객체를 S3 API 제한에 맞춰 삭제한다."""

    unique_keys = list(dict.fromkeys(key for key in keys if key))
    if not unique_keys:
        return

    bucket = _require_bucket(BUCKET, "CALENDAR_S3_BUCKET")
    for offset in range(0, len(unique_keys), 1000):
        batch = unique_keys[offset : offset + 1000]
        _client().delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
        )


def delete_calendar(user_id: int | str, calendar_id: UUID | str) -> None:
    """정확한 사용자·캘린더 prefix 아래의 모든 객체를 삭제한다."""

    bucket = _require_bucket(BUCKET, "CALENDAR_S3_BUCKET")
    prefix = calendar_prefix(user_id, calendar_id)
    paginator = _client().get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(item["Key"] for item in page.get("Contents", []))
    delete_objects(keys)
