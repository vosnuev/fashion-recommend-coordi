"""룩북 전용 S3 키 생성·저장·조회·삭제 서비스.

캘린더(style_calendar/services/storage.py)와 같은 원칙을 따른다.
- DB에는 만료되는 URL이 아니라 S3 key만 저장한다. 사용자에게 이미지를 줄 때만
  presigned GET을 만든다.
- 룩북이 보여 주는 이미지는 룩북 소유 경로에 복사해 둔다. 옷장 아이템을 지워도
  올려 둔 룩이 빈칸이 되지 않게 하기 위해서다.

버킷은 LOOKBOOK_S3_BUCKET → CALENDAR_S3_BUCKET → WARDROBE_S3_BUCKET 순으로
떨어진다. 팀 환경마다 버킷을 하나만 쓰는 경우가 많아 새 변수를 필수로 만들면
기존 배포가 전부 깨진다.
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
    os.getenv("LOOKBOOK_S3_BUCKET", "").strip()
    or os.getenv("CALENDAR_S3_BUCKET", "").strip()
    or os.getenv("WARDROBE_S3_BUCKET", "").strip()
)
WARDROBE_BUCKET = os.getenv("WARDROBE_S3_BUCKET", "").strip()
CALENDAR_BUCKET = (
    os.getenv("CALENDAR_S3_BUCKET", "").strip()
    or os.getenv("WARDROBE_S3_BUCKET", "").strip()
)
REGION = os.getenv("AWS_REGION", "ap-northeast-2").strip() or "ap-northeast-2"
PRESIGNED_GET_TTL = int(os.getenv("LOOKBOOK_PRESIGNED_GET_TTL", "3600"))

_SAFE_PATH_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
_IMAGE_CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
}


class LookbookStorageConfigurationError(RuntimeError):
    """룩북 S3 환경변수가 설정되지 않은 경우."""


def _require_bucket(bucket: str, variable_name: str) -> str:
    bucket = bucket.strip()
    if not bucket:
        raise LookbookStorageConfigurationError(
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


def lookbook_prefix(user_id: int | str, lookbook_id: UUID | str) -> str:
    user_segment = _path_segment(user_id, name="user_id")
    lookbook_segment = _path_segment(lookbook_id, name="lookbook_id")
    return f"lookbook/{user_segment}/{lookbook_segment}/"


def original_key(
    user_id: int | str,
    lookbook_id: UUID | str,
    filename: str,
    content_type: str | None = None,
) -> str:
    extension = _IMAGE_CONTENT_TYPE_EXTENSIONS.get(content_type or "")
    if extension is None:
        extension = _extension(filename, default=".jpg")
    return f"{lookbook_prefix(user_id, lookbook_id)}original{extension}"


def selected_item_key(
    user_id: int | str,
    lookbook_id: UUID | str,
    link_id: UUID | str,
    source_key: str,
) -> str:
    extension = _extension(source_key, default=".png")
    return f"{lookbook_prefix(user_id, lookbook_id)}items/{link_id}{extension}"


def upload_fileobj(fileobj, key: str, content_type: str | None = None) -> None:
    bucket = _require_bucket(BUCKET, "LOOKBOOK_S3_BUCKET")
    extra_args = {"ContentType": content_type} if content_type else {}
    _client().upload_fileobj(fileobj, bucket, key, ExtraArgs=extra_args)


def copy_wardrobe_item(source_key: str, destination_key: str) -> None:
    """옷장 이미지를 룩북 소유 경로로 서버 측 복사한다."""

    source_bucket = _require_bucket(WARDROBE_BUCKET, "WARDROBE_S3_BUCKET")
    destination_bucket = _require_bucket(BUCKET, "LOOKBOOK_S3_BUCKET")
    _client().copy_object(
        CopySource={"Bucket": source_bucket, "Key": source_key},
        Bucket=destination_bucket,
        Key=destination_key,
    )


def copy_original_to_wardrobe(source_key: str, destination_key: str) -> None:
    """룩 사진 원본을 기존 옷장 worker 입력 경로로 서버 측 복사한다."""

    source_bucket = _require_bucket(BUCKET, "LOOKBOOK_S3_BUCKET")
    destination_bucket = _require_bucket(WARDROBE_BUCKET, "WARDROBE_S3_BUCKET")
    _client().copy_object(
        CopySource={"Bucket": source_bucket, "Key": source_key},
        Bucket=destination_bucket,
        Key=destination_key,
    )


def copy_original_to_calendar(source_key: str, destination_key: str) -> None:
    """'캘린더에도 기록'을 켠 경우, 룩 사진을 캘린더 소유 경로로 복사한다.

    캘린더는 자기 버킷 키만 presign하므로(style_calendar/services/storage.py),
    룩북 키를 그대로 넘기면 캘린더 화면의 이미지가 깨진다.
    """

    source_bucket = _require_bucket(BUCKET, "LOOKBOOK_S3_BUCKET")
    destination_bucket = _require_bucket(CALENDAR_BUCKET, "CALENDAR_S3_BUCKET")
    _client().copy_object(
        CopySource={"Bucket": source_bucket, "Key": source_key},
        Bucket=destination_bucket,
        Key=destination_key,
    )


def presigned_get(key: str, ttl: int = PRESIGNED_GET_TTL) -> str:
    if not key:
        return ""
    bucket = _require_bucket(BUCKET, "LOOKBOOK_S3_BUCKET")
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=ttl,
    )


def presigned_get_in(bucket: str, key: str, ttl: int = PRESIGNED_GET_TTL) -> str:
    """다른 버킷에 있는 객체에 서명한다. 버킷이 비면 룩북 버킷으로 떨어진다.

    오늘의 룩에서 담은 골든 코디는 이미지를 룩북 버킷으로 **복사하지 않는다**.
    같은 코디를 담은 사용자 수만큼 같은 사진이 복제되는데, 골든셋 이미지는
    코디당 한 장을 모두가 공유하는 자산이라 그 복제가 순수한 낭비다. 대신
    버킷과 키를 함께 저장해 두고 조회 시점에 그 버킷으로 서명한다.

    서명 실패는 여기서 삼키지 않는다 — 호출부(시리얼라이저)가 목록 전체를
    500으로 만들지 않도록 감싼다.
    """

    if not key:
        return ""
    if not bucket.strip():
        return presigned_get(key, ttl)
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket.strip(), "Key": key},
        ExpiresIn=ttl,
    )


def delete_objects(keys: Iterable[str]) -> None:
    """명시적으로 전달된 룩북 객체를 S3 API 제한에 맞춰 삭제한다."""

    unique_keys = list(dict.fromkeys(key for key in keys if key))
    if not unique_keys:
        return

    bucket = _require_bucket(BUCKET, "LOOKBOOK_S3_BUCKET")
    for offset in range(0, len(unique_keys), 1000):
        batch = unique_keys[offset : offset + 1000]
        _client().delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
        )


def delete_lookbook(user_id: int | str, lookbook_id: UUID | str) -> None:
    """정확한 사용자·룩북 prefix 아래의 모든 객체를 삭제한다."""

    bucket = _require_bucket(BUCKET, "LOOKBOOK_S3_BUCKET")
    prefix = lookbook_prefix(user_id, lookbook_id)
    paginator = _client().get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(item["Key"] for item in page.get("Contents", []))
    delete_objects(keys)
