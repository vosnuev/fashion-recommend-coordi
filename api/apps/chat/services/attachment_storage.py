"""채팅 첨부 이미지를 비공개 S3에 저장하고 제한된 조회 URL을 발급한다."""

from __future__ import annotations

from functools import lru_cache

import boto3
from botocore.config import Config
from django.conf import settings

_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
}


def bucket() -> str:
    return settings.CHAT_ATTACHMENT_S3_BUCKET


def is_configured() -> bool:
    return bool(bucket())


@lru_cache(maxsize=1)
def _client():
    return boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
        config=Config(
            connect_timeout=settings.CHAT_ATTACHMENT_S3_CONNECT_TIMEOUT_SECONDS,
            read_timeout=settings.CHAT_ATTACHMENT_S3_READ_TIMEOUT_SECONDS,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def attachment_key(identity_id, attachment_id, mime_type: str) -> str:
    try:
        extension = _EXTENSIONS[mime_type]
    except KeyError as exc:
        raise ValueError("지원하지 않는 채팅 첨부 이미지 형식입니다.") from exc
    return f"chat/{identity_id}/attachments/{attachment_id}.{extension}"


def upload_fileobj(fileobj, key: str, mime_type: str) -> None:
    if not is_configured():
        raise RuntimeError("CHAT_ATTACHMENT_S3_BUCKET이 설정되지 않았습니다.")
    _client().upload_fileobj(
        fileobj,
        bucket(),
        key,
        ExtraArgs={"ContentType": mime_type},
    )


def delete_object(key: str) -> None:
    if not is_configured() or not key:
        return
    _client().delete_object(Bucket=bucket(), Key=key)


def download_bytes(key: str, *, max_bytes: int) -> bytes:
    """비공개 객체를 제한 크기까지만 읽어 분석 워커에 전달한다."""
    if not is_configured():
        raise RuntimeError("CHAT_ATTACHMENT_S3_BUCKET이 설정되지 않았습니다.")
    response = _client().get_object(Bucket=bucket(), Key=key)
    body = response["Body"]
    try:
        data = body.read(max_bytes + 1)
    finally:
        body.close()
    if len(data) > max_bytes:
        raise ValueError("채팅 첨부 이미지가 허용 크기를 초과했습니다.")
    return data


def presigned_get(key: str) -> str | None:
    if not is_configured() or not key:
        return None
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket(), "Key": key},
        ExpiresIn=settings.CHAT_ATTACHMENT_PRESIGNED_GET_TTL_SECONDS,
    )
