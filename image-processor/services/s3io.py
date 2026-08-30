"""S3 입출력.

- 원본 다운로드는 큐 페이로드의 source(bucket, key) 기준
- 결과물(아이템 크롭·manifest)은 output_prefix 하위에 저장
- 같은 job_id는 항상 같은 경로를 쓴다 → 재실행해도 덮어쓰기(설계서 7장)
- manifest.json은 모든 아이템 업로드가 끝난 뒤 마지막에 저장한다(설계서 5장)
"""
from __future__ import annotations

import io
import json
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

MANIFEST_NAME = "manifest.json"


@lru_cache(maxsize=1)
def _client():
    # 자격증명은 표준 AWS 환경변수 또는 IAM 역할로 주입
    return boto3.client("s3")


def download(bucket: str, key: str, local_path: str) -> None:
    _client().download_file(bucket, key, local_path)


def upload_png(bucket: str, key: str, image) -> None:
    """PIL 이미지를 PNG로 업로드한다."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    _client().upload_fileobj(buf, bucket, key, ExtraArgs={"ContentType": "image/png"})


def put_json(bucket: str, key: str, data: dict) -> None:
    _client().put_object(
        Bucket=bucket, Key=key,
        Body=json.dumps(data, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )


def get_json(bucket: str, key: str) -> dict | None:
    """없으면 None. manifest 존재 여부로 '처리 완료, 콜백만 남음'을 판별한다."""
    try:
        obj = _client().get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise
    return json.loads(obj["Body"].read())


def manifest_key(output_prefix: str) -> str:
    return f"{output_prefix.rstrip('/')}/{MANIFEST_NAME}"


def item_key(output_prefix: str, index: int) -> str:
    return f"{output_prefix.rstrip('/')}/item_{index:03d}.png"
