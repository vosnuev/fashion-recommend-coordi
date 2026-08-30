"""추천 카드 아이템 사진을 앱이 바로 걸 수 있는 URL로 편다.

`image_ref`는 출처마다 모양이 다르다 — 옷장·골든셋은 비공개 S3 키이고, 상품도
인덱싱 때 우리 버킷에 복사해 둔 키가 먼저 들어온다(원본 쇼핑몰 주소는 스냅샷의
`image_url`에 남아 있다). 앱은 http(s) 주소만 그릴 수 있으므로 조회 시점에
presigned URL로 바꿔 내려준다.

⚠️ 서명 URL은 만료되므로 DB나 벡터 payload에 미리 구워 두지 않는다
(`storage.presigned_get_for` 주석과 같은 이유). 실패해도 None만 돌려주고 카드는
그대로 그린다 — 사진 하나 때문에 추천 응답 전체를 깨뜨리지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

from apps.recommend.services import storage

logger = logging.getLogger(__name__)

#: 아이템 스냅샷에서 버킷·키를 찾을 때 볼 열쇠들. 출처마다 이름이 다르다
#: (상품 인덱서는 image_s3_*, 옷장 벡터는 s3_key).
_BUCKET_KEYS = ("image_s3_bucket", "s3_bucket", "source_bucket")
_IMAGE_KEYS = ("image_s3_key", "s3_key")
#: 우리 버킷에서 못 찾았을 때 마지막으로 볼 원본 주소.
_URL_KEYS = ("image_url", "thumbnail_url")

#: 출처별 버킷. 렌더러와 같은 규칙이다 (mixed_outfit_render._default_bucket)
#: — 같은 이미지를 가리켜야 하기 때문이다.
_DEFAULT_BUCKETS = {
    "WARDROBE": "OUTFIT_RENDER_WARDROBE_BUCKET",
    "PRODUCT": "OUTFIT_RENDER_PRODUCT_BUCKET",
    "GOLDENSET_ITEM": "OUTFIT_RENDER_GOLDENSET_BUCKET",
}


def _text(snapshot: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = snapshot.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _default_bucket(source_type: str) -> str:
    setting_name = _DEFAULT_BUCKETS.get(source_type)
    return getattr(settings, setting_name, "") if setting_name else ""


def image_url_for(item) -> str | None:
    """카드 아이템 하나의 표시용 이미지 URL. 만들 수 없으면 None.

    우선순위:
    1. `image_ref`가 이미 http(s)면 그대로 (옛 데이터·외부 URL)
    2. 스냅샷/참조의 S3 키 → presigned URL (우리가 복사해 둔 사본이라 가장 안정적)
    3. 스냅샷의 원본 쇼핑몰 이미지 주소 (S3 사본이 없는 상품)

    ⚠️ **버킷은 지금 설정이 이긴다.** 스냅샷의 `image_s3_bucket`은 상품을
    인덱싱하던 시점의 인덱서 env가 Qdrant payload를 거쳐 박힌 값이라, 버킷을
    옮기면 그 즉시 낡은 값이 된다(예: 이미지는 skn28-cozy3에 있는데 skn28-cozy로 서명 → 404).
    키는 그대로 쓰고 버킷만 현재 환경 값으로 갈아끼운다 — 이미지가 어느 버킷에
    있는지는 배포 환경이 알지, 예전 추천 기록이 알 수 없다. 설정이 비었을 때만
    스냅샷 버킷으로 물러선다.
    """
    ref = (item.image_ref or "").strip()
    if _is_url(ref):
        return ref

    snapshot = item.item_snapshot if isinstance(item.item_snapshot, dict) else {}
    key = _text(snapshot, *_IMAGE_KEYS) or ref
    bucket = _default_bucket(item.source_type) or _text(snapshot, *_BUCKET_KEYS)
    if key and bucket:
        try:
            return storage.presigned_get_for(
                bucket,
                key,
                ttl=settings.OUTFIT_RENDER_PRESIGNED_GET_TTL_SECONDS,
            )
        except Exception:  # noqa: BLE001 — 사진 하나가 카드를 깨뜨리지 않게 한다
            logger.warning(
                "추천 아이템 이미지 presigned URL 발급 실패: bucket=%s key=%s",
                bucket,
                key,
                exc_info=True,
            )

    fallback = _text(snapshot, *_URL_KEYS)
    return fallback if _is_url(fallback) else None
