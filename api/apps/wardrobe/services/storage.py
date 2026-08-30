"""S3 저장소 접근 (원본 업로드 · presigned URL 발급).

설계 결정: 이미지 바이너리는 서비스 간 직접 전달하지 않는다.
- 메인 API가 원본을 S3에 선업로드하고 이후에는 키(참조)만 전달
- 버킷은 비공개, 프론트 노출은 presigned GET으로만
키 구조: wardrobe/{user_id}/{job_id}/original.<ext> | item_XX.png
"""
from __future__ import annotations

import ipaddress
import io
import os
import socket
from collections.abc import Iterable
from functools import lru_cache
from urllib.parse import urljoin, urlparse

import boto3
import requests

BUCKET = os.getenv("WARDROBE_S3_BUCKET", "")
REGION = os.getenv("AWS_REGION", "ap-northeast-2")
PRESIGNED_GET_TTL = int(os.getenv("WARDROBE_PRESIGNED_GET_TTL", "3600"))


class RemoteImageError(ValueError):
    pass


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RemoteImageError("http 또는 https 이미지 주소가 필요합니다.")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = socket.getaddrinfo(parsed.hostname, port)
    except OSError as exc:
        raise RemoteImageError("이미지 주소를 확인할 수 없습니다.") from exc
    if any(not ipaddress.ip_address(info[4][0]).is_global for info in addresses):
        raise RemoteImageError("내부 네트워크 이미지 주소는 사용할 수 없습니다.")


def _image_type(header: bytes) -> tuple[str, str] | None:
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if header[4:8] == b"ftyp" and header[8:12] in {b"heic", b"heix", b"hevc", b"hevx", b"mif1"}:
        return "image/heic", ".heic"
    return None


def fetch_remote_image(url: str, max_bytes: int) -> tuple[io.BytesIO, str, str, int]:
    current = url
    for _ in range(4):
        _validate_public_url(current)
        try:
            with requests.get(
                current,
                stream=True,
                allow_redirects=False,
                timeout=(5, 20),
                headers={"User-Agent": "SKN28-Wardrobe-Importer/1.0"},
            ) as response:
                if response.is_redirect or response.is_permanent_redirect:
                    location = response.headers.get("Location")
                    if not location:
                        raise RemoteImageError("잘못된 이미지 리다이렉트입니다.")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                declared = int(response.headers.get("Content-Length", "0") or 0)
                if declared > max_bytes:
                    raise RemoteImageError("이미지 용량 제한을 초과했습니다.")
                data = bytearray()
                for chunk in response.iter_content(64 * 1024):
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise RemoteImageError("이미지 용량 제한을 초과했습니다.")
        except requests.RequestException as exc:
            raise RemoteImageError("이미지를 다운로드하지 못했습니다.") from exc

        image_type = _image_type(bytes(data[:16]))
        if image_type is None:
            raise RemoteImageError("지원하지 않는 이미지 형식입니다.")
        content_type, extension = image_type
        return io.BytesIO(data), content_type, extension, len(data)
    raise RemoteImageError("이미지 리다이렉트가 너무 많습니다.")

@lru_cache(maxsize=1)
def _client():
    # 자격증명은 표준 AWS 환경변수(AWS_ACCESS_KEY_ID 등) 또는 IAM 역할로 주입
    return boto3.client("s3", region_name=REGION)


def original_key(user_id: int | str, job_id: str, filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower() or ".jpg"
    return f"wardrobe/{user_id}/{job_id}/original{ext}"


def output_prefix(user_id: int | str, job_id: str) -> str:
    """이미지 프로세서가 아이템 크롭을 업로드할 프리픽스."""
    return f"wardrobe/{user_id}/{job_id}/"


def upload_fileobj(fileobj, key: str, content_type: str | None = None) -> None:
    extra = {"ContentType": content_type} if content_type else None
    _client().upload_fileobj(
        fileobj, BUCKET, key, ExtraArgs=extra or {}
    )


def delete_object(key: str) -> None:
    _client().delete_object(Bucket=BUCKET, Key=key)


def presigned_get(key: str, ttl: int = PRESIGNED_GET_TTL) -> str:
    return _client().generate_presigned_url(
        "get_object", Params={"Bucket": BUCKET, "Key": key}, ExpiresIn=ttl
    )


def delete_objects(keys: Iterable[str]) -> None:
    """DB 저장 실패 시 명시된 옷장 S3 객체만 정리한다."""
    unique_keys = list(dict.fromkeys(key for key in keys if key))
    for offset in range(0, len(unique_keys), 1000):
        batch = unique_keys[offset : offset + 1000]
        _client().delete_objects(
            Bucket=BUCKET,
            Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
        )
