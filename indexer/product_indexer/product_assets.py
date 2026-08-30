"""외부 쇼핑 이미지를 검증·정규화하고 S3에 보존한다."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import quote

import requests
from PIL import Image, ImageOps, UnidentifiedImageError


class InvalidProductImage(ValueError):
    """재시도해도 해결되지 않는 이미지 입력 오류."""


class StoredProductImageUnavailable(ValueError):
    """저장된 S3 이미지를 신뢰할 수 없어 원본 URL 복구가 필요한 상태."""


@dataclass(frozen=True)
class PreparedImage:
    image: Image.Image
    checksum: str
    s3_key: str


def _download_bytes(
    session: requests.Session,
    url: str,
    *,
    timeout: int,
    max_bytes: int,
) -> bytes:
    if not url:
        raise InvalidProductImage("상품 image_url이 비어 있습니다.")
    response = session.get(url, timeout=timeout, stream=True)
    response.raise_for_status()

    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            parsed_content_length = int(content_length)
        except ValueError:
            parsed_content_length = None
        if parsed_content_length is not None and parsed_content_length > max_bytes:
            raise InvalidProductImage(
                f"상품 이미지가 최대 허용 크기를 초과합니다: {content_length} bytes"
            )

    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        size += len(chunk)
        if size > max_bytes:
            raise InvalidProductImage(
                f"상품 이미지가 최대 허용 크기를 초과합니다: > {max_bytes} bytes"
            )
        chunks.append(chunk)
    if not chunks:
        raise InvalidProductImage("상품 이미지 응답이 비어 있습니다.")
    return b"".join(chunks)


def _normalize_jpeg(raw: bytes) -> tuple[Image.Image, bytes]:
    try:
        opened = Image.open(BytesIO(raw))
        opened.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidProductImage(
            "다운로드한 파일이 유효한 이미지가 아닙니다."
        ) from exc

    image = ImageOps.exif_transpose(opened)
    if image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        background.alpha_composite(rgba)
        image = background.convert("RGB")
    else:
        image = image.convert("RGB")

    output = BytesIO()
    image.save(output, format="JPEG", quality=95, optimize=True)
    return image, output.getvalue()


def _open_stored_jpeg(raw: bytes) -> Image.Image:
    try:
        opened = Image.open(BytesIO(raw))
        opened.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise StoredProductImageUnavailable(
            "S3에 저장된 파일이 유효한 이미지가 아닙니다."
        ) from exc
    return ImageOps.exif_transpose(opened).convert("RGB")


def download_and_store_image(
    *,
    session: requests.Session,
    s3_client,
    source: str,
    external_product_id: str,
    image_url: str,
    bucket: str,
    key_prefix: str,
    timeout: int,
    max_bytes: int,
) -> PreparedImage:
    """외부 이미지를 정규화해 `{key_prefix}/{상품ID}/{checksum}.jpg`로 보존한다.

    key_prefix는 호출자가 쇼핑몰별로 이미 결정해서 넘긴다(예: products/naver).
    source는 S3 객체 메타데이터에만 기록하고 키 조립에는 쓰지 않는다.
    """
    raw = _download_bytes(
        session,
        image_url,
        timeout=timeout,
        max_bytes=max_bytes,
    )
    image, normalized = _normalize_jpeg(raw)
    checksum = hashlib.sha256(normalized).hexdigest()
    safe_product_id = quote(external_product_id, safe="")
    key_parts = [
        part for part in (key_prefix.strip("/"), safe_product_id) if part
    ]
    s3_key = "/".join(key_parts + [f"{checksum}.jpg"])
    s3_client.put_object(
        Bucket=bucket,
        Key=s3_key,
        Body=normalized,
        ContentType="image/jpeg",
        Metadata={
            "sha256": checksum,
            "source": source,
            "product-id": safe_product_id[:200],
        },
    )
    return PreparedImage(image=image, checksum=checksum, s3_key=s3_key)


def load_stored_image(
    *,
    s3_client,
    bucket: str,
    s3_key: str,
    expected_checksum: str,
    max_bytes: int,
) -> PreparedImage:
    """DB에 체크포인트된 S3 이미지를 검증해 재사용한다."""
    response = s3_client.get_object(Bucket=bucket, Key=s3_key)
    content_length = response.get("ContentLength")
    if content_length is not None and int(content_length) > max_bytes:
        raise StoredProductImageUnavailable(
            f"S3 이미지가 최대 허용 크기를 초과합니다: {content_length} bytes"
        )

    body = response["Body"]
    try:
        raw = body.read(max_bytes + 1)
    finally:
        close = getattr(body, "close", None)
        if close is not None:
            close()
    if not raw:
        raise StoredProductImageUnavailable("S3 이미지가 비어 있습니다.")
    if len(raw) > max_bytes:
        raise StoredProductImageUnavailable(
            f"S3 이미지가 최대 허용 크기를 초과합니다: > {max_bytes} bytes"
        )

    actual_checksum = hashlib.sha256(raw).hexdigest()
    metadata_checksum = (response.get("Metadata") or {}).get("sha256")
    if actual_checksum != expected_checksum:
        raise StoredProductImageUnavailable(
            "S3 이미지와 DB image_checksum이 일치하지 않습니다."
        )
    if metadata_checksum and metadata_checksum != expected_checksum:
        raise StoredProductImageUnavailable(
            "S3 이미지 메타데이터 체크섬이 DB와 일치하지 않습니다."
        )

    return PreparedImage(
        image=_open_stored_jpeg(raw),
        checksum=actual_checksum,
        s3_key=s3_key,
    )
