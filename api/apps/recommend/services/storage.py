"""코디 평가 원본 사진 S3 저장.

설계 결정은 wardrobe/services/storage.py와 동일하다 — 이미지 바이너리는 DB에
넣지 않고 S3에 두고 키(참조)만 저장한다. 버킷은 옷장과 분리할 수 있게
`OUTFIT_S3_BUCKET`을 먼저 보고, 없으면 옷장 버킷을 재사용한다
(팀 로컬 환경에서 env를 추가하지 않아도 동작하도록).

키 구조: outfits/{user_id|anonymous}/{analysis_id}/original.<ext>
"""

from __future__ import annotations

import os
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def bucket() -> str:
    """버킷명은 호출 시점에 읽는다 (테스트에서 환경변수 오버라이드 가능)."""
    return os.getenv("OUTFIT_S3_BUCKET", "") or os.getenv("WARDROBE_S3_BUCKET", "")


def is_configured() -> bool:
    """버킷이 지정되지 않은 환경(로컬 등)에서는 업로드를 건너뛴다."""
    return bool(bucket())


REGION = os.getenv("AWS_REGION", "ap-northeast-2")
PRESIGNED_GET_TTL = int(os.getenv("OUTFIT_PRESIGNED_GET_TTL", "3600"))
S3_CONNECT_TIMEOUT = int(os.getenv("OUTFIT_S3_CONNECT_TIMEOUT", "5"))
S3_READ_TIMEOUT = int(os.getenv("OUTFIT_S3_READ_TIMEOUT", "15"))


@lru_cache(maxsize=1)
def _client():
    # 자격증명은 표준 AWS 환경변수(AWS_ACCESS_KEY_ID 등) 또는 IAM 역할로 주입.
    # 업로드는 사용자 요청을 붙잡고 있는 동기 구간이라, 기본값(60s x 5회 재시도)으로
    # 두면 S3가 느릴 때 요청이 몇 분씩 매달린다. 짧게 끊고 포기하는 편이 낫다
    # — 사진 업로드는 실패해도 평가를 막지 않는 best-effort 단계다.
    return boto3.client(
        "s3",
        region_name=REGION,
        config=Config(
            connect_timeout=S3_CONNECT_TIMEOUT,
            read_timeout=S3_READ_TIMEOUT,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def original_key(user_id: int | str | None, analysis_id: str, filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower() or ".jpg"
    owner = user_id if user_id is not None else "anonymous"
    return f"outfits/{owner}/{analysis_id}/original{ext}"


def owner_key(user_id: int | str, analysis_id: str, current_key: str) -> str:
    """익명 프리픽스에 있는 키를 소유자 프리픽스로 바꾼 키를 만든다.

    확장자는 기존 키에서 그대로 가져온다 (원본 파일명을 다시 볼 필요가 없다).
    """
    ext = os.path.splitext(current_key)[1].lower() or ".jpg"
    return f"outfits/{user_id}/{analysis_id}/original{ext}"


def move(old_key: str, new_key: str) -> None:
    """같은 버킷 안에서 객체를 옮긴다 (서버 사이드 복사 후 원본 삭제).

    바이트가 우리 서버를 거치지 않는다. 복사가 성공한 뒤에만 원본을 지우므로,
    중간에 실패해도 사진이 사라지는 일은 없다 (최악의 경우 사본이 둘 남는다).
    """
    client = _client()
    target = bucket()
    client.copy_object(
        Bucket=target, Key=new_key, CopySource={"Bucket": target, "Key": old_key}
    )
    client.delete_object(Bucket=target, Key=old_key)


def upload_fileobj(fileobj, key: str, content_type: str | None = None) -> None:
    extra = {"ContentType": content_type} if content_type else None
    _client().upload_fileobj(fileobj, bucket(), key, ExtraArgs=extra or {})


def download(key: str) -> bytes:
    """워커가 평가 대상 사진을 읽어온다. 실패 시 botocore 예외를 그대로 올린다."""
    return _client().get_object(Bucket=bucket(), Key=key)["Body"].read()


def presigned_get(key: str, ttl: int = PRESIGNED_GET_TTL) -> str:
    return _client().generate_presigned_url(
        "get_object", Params={"Bucket": bucket(), "Key": key}, ExpiresIn=ttl
    )


def presigned_get_for(bucket_name: str, key: str, ttl: int = PRESIGNED_GET_TTL) -> str:
    """다른 버킷의 객체에 대한 조회 URL.

    골든셋 산출물은 이 앱의 버킷(OUTFIT_S3_BUCKET/WARDROBE_S3_BUCKET)이 아니라
    GOLDEN_S3_BUCKET에 있다. 버킷을 인자로 받아 같은 자격증명·리전으로 서명한다.

    URL은 만료되므로 **조회 시점에** 만들어야 한다. DB나 벡터 payload에 미리
    구워 넣으면 며칠 뒤 죽은 링크가 남는다.
    """
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket_name, "Key": key},
        ExpiresIn=ttl,
    )


def exists_for(bucket_name: str, key: str) -> bool:
    """다른 버킷의 객체 존재 여부. 404면 False, 그 밖의 오류는 그대로 올린다.

    권한 문제(403)를 '없음'으로 삼키면 매번 다시 만들게 되므로 구분한다.
    """
    try:
        _client().head_object(Bucket=bucket_name, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise
    return True


def put_bytes_for(
    bucket_name: str, key: str, data: bytes, content_type: str = "image/png"
) -> None:
    """다른 버킷에 객체를 올린다 (골든셋 산출물용)."""
    if not bucket_name or not key:
        raise ValueError("S3 bucket과 key가 모두 필요합니다.")
    if not data:
        raise ValueError("저장할 이미지 데이터가 필요합니다.")
    _client().put_object(
        Bucket=bucket_name,
        Key=key,
        Body=data,
        ContentType=content_type,
        CacheControl="private, max-age=31536000, immutable",
    )


def download_for(
    bucket_name: str,
    key: str,
    *,
    max_bytes: int | None = None,
) -> bytes:
    """다른 버킷의 객체를 선택적으로 크기 제한을 두고 읽는다."""
    if not bucket_name or not key:
        raise ValueError("S3 bucket과 key가 모두 필요합니다.")
    if max_bytes is not None and max_bytes < 1:
        raise ValueError("max_bytes는 1 이상이어야 합니다.")

    body = _client().get_object(Bucket=bucket_name, Key=key)["Body"]
    try:
        data = body.read(max_bytes + 1 if max_bytes is not None else None)
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()
    if max_bytes is not None and len(data) > max_bytes:
        raise ValueError(f"S3 이미지가 허용 크기 {max_bytes} bytes를 초과합니다.")
    return data


def metadata_for(bucket_name: str, key: str) -> dict | None:
    """결정적 렌더 키에 저장된 객체의 캐시 복원용 메타데이터를 조회한다."""
    if not bucket_name or not key:
        return None
    try:
        response = _client().head_object(Bucket=bucket_name, Key=key)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise
    return {
        "content_type": str(response.get("ContentType") or "image/jpeg"),
        "content_length": int(response.get("ContentLength") or 0),
    }
