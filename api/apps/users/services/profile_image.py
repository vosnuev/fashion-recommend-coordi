"""프로필 사진 업로드·조회.

`User.profile_image` 는 소셜 로그인이 넘겨준 **provider 의 사진 URL** 을 담는 자리다.
사용자가 직접 올린 사진은 성격이 다르다 — 우리 S3 에 있고, 만료되는 presigned URL 로만
꺼낼 수 있다. 그래서 URL 을 그대로 넣지 않고 **key 를 따로 저장**하고(profile_image_key)
읽을 때마다 서명한다. 팀의 다른 이미지(옷장·캘린더·룩북)와 같은 원칙이다.

우선순위는 '내가 올린 것 > provider 가 준 것' 이다. 올린 사진을 지우면 provider 사진으로
되돌아간다 — 그래서 업로드해도 profile_image(소셜 URL)를 지우지 않는다.
"""

from __future__ import annotations

import os
from functools import lru_cache
from io import BytesIO
from uuid import uuid4

import boto3
from PIL import Image, ImageOps, UnidentifiedImageError

#: 팀 환경마다 버킷을 하나만 쓰는 경우가 많아, 전용 변수가 없으면 기존 버킷으로 떨어진다.
BUCKET = (
    os.getenv("USERS_S3_BUCKET", "").strip()
    or os.getenv("WARDROBE_S3_BUCKET", "").strip()
    or os.getenv("CALENDAR_S3_BUCKET", "").strip()
)
REGION = os.getenv("AWS_REGION", "ap-northeast-2").strip() or "ap-northeast-2"
PRESIGNED_GET_TTL = int(os.getenv("USERS_PRESIGNED_GET_TTL", "3600"))

#: 아바타는 가장 크게 쓰이는 자리가 84pt 다. 2x 를 감안해도 512 면 충분하고,
#: 원본을 그대로 두면 수 MB 짜리 사진이 매번 오간다.
MAX_EDGE = 512
JPEG_QUALITY = 85
#: 이보다 큰 파일은 받지 않는다(디코딩 전에 막는다 — 압축폭탄 방어).
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class ProfileImageConfigurationError(RuntimeError):
    """S3 환경변수가 없어 업로드를 받을 수 없는 경우."""


class ProfileImageInvalidError(ValueError):
    """이미지로 읽을 수 없거나 허용 크기를 넘은 경우."""


@lru_cache(maxsize=1)
def _client():
    """표준 AWS 자격증명 환경변수 또는 IAM Role 을 사용하는 S3 client."""

    return boto3.client("s3", region_name=REGION)


def _require_bucket() -> str:
    if not BUCKET:
        raise ProfileImageConfigurationError(
            "USERS_S3_BUCKET(또는 WARDROBE_S3_BUCKET)이 설정되지 않았습니다."
        )
    return BUCKET


def normalize(raw: bytes) -> bytes:
    """올라온 파일을 정사각 JPEG 로 정규화한다.

    JPEG 로 통일하는 이유: 아바타는 원형으로 잘라 쓰므로 투명도가 필요 없고,
    HEIC/PNG 를 그대로 두면 브라우저마다 표시가 갈린다. EXIF 도 함께 털어낸다
    (사진에 촬영 위치가 남아 있을 수 있는데, 프로필 사진은 남에게 보이는 자산이다).
    """

    if len(raw) > MAX_UPLOAD_BYTES:
        raise ProfileImageInvalidError("사진은 10MB 까지 올릴 수 있어요.")
    try:
        with Image.open(BytesIO(raw)) as im:
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            # 원형 아바타라 가운데를 정사각으로 잘라야 인물이 치우치지 않는다.
            im = ImageOps.fit(im, (MAX_EDGE, MAX_EDGE), method=Image.LANCZOS)
            buffer = BytesIO()
            im.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ProfileImageInvalidError("이미지 파일을 읽지 못했어요.") from error


def store(user_id: int, raw: bytes) -> str:
    """정규화해 S3 에 올리고 key 를 돌려준다.

    파일명에 uuid 를 넣어 매번 새 key 를 쓴다 — 같은 key 를 덮어쓰면 CDN·브라우저가
    예전 사진을 계속 보여 준다(프로필은 바꾸자마자 바뀐 게 보여야 하는 자리다).
    """

    bucket = _require_bucket()
    key = f"users/{int(user_id)}/profile/{uuid4().hex}.jpg"
    _client().put_object(
        Bucket=bucket, Key=key, Body=normalize(raw), ContentType="image/jpeg"
    )
    return key


def presigned_get(key: str, ttl: int = PRESIGNED_GET_TTL) -> str:
    if not key:
        return ""
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": _require_bucket(), "Key": key},
        ExpiresIn=ttl,
    )


def delete(key: str) -> None:
    """지우기에 실패해도 사용자 흐름은 막지 않는다 — 화면에서 사라지는 게 먼저다."""

    if not key:
        return
    try:
        _client().delete_object(Bucket=_require_bucket(), Key=key)
    except Exception:  # noqa: BLE001 - 정리 실패가 프로필 변경을 되돌릴 이유는 없다
        pass
